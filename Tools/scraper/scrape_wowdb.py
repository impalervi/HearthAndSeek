#!/usr/bin/env python3
"""Scrape housing.wowdb.com for themed collection data.

Two modes:
  --sets   Scrape community-curated sets (listing pages + quality set details)
  --items  Scrape per-item culture/style tags for our catalog items

Both modes cache responses in data/wowdb_cache/ and report progress every 10%.

Usage:
  python scrape_wowdb.py --sets          # Scrape community sets (~2 min cached)
  python scrape_wowdb.py --items         # Scrape per-item tags (~55 min uncached)
  python scrape_wowdb.py --all           # Both modes
  python scrape_wowdb.py --items --no-cache  # Force re-fetch

Output:
  data/wowdb_sets.json       — Community set metadata, decorID lists, tags
  data/wowdb_item_tags.json  — Per-item culture/style/class/theme tags

See also:
  scrape_wowdb_quests.py — Scrapes quest-sourced decor items (seed data for enrichment).
  compute_item_themes.py — Combines scrape outputs into final theme scores.
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("scrape_wowdb")

DATA_DIR = Path(__file__).parent / "data"
CACHE_DIR = DATA_DIR / "wowdb_cache"
CATALOG_PATH = DATA_DIR / "enriched_catalog.json"

SETS_OUTPUT = DATA_DIR / "wowdb_sets.json"
ITEMS_OUTPUT = DATA_DIR / "wowdb_item_tags.json"

BASE_URL = "https://housing.wowdb.com"
RATE_LIMIT_SECONDS = 2.0
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "HearthAndSeek-DataPipeline/1.0 "
        "(WoW Housing Addon; +https://github.com/ImpalerV/HearthAndSeek)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Minimum thresholds for quality sets
MIN_LIKES = 5
MIN_ITEMS = 5

# Known tag categories for classification
CULTURE_TAGS = {
    "alliance", "blood elf", "bronzebeard dwarf", "dark iron dwarf",
    "dracthyr", "draenei", "dwarven", "earthen-dornish", "earthen",
    "elven", "gilnean", "gnomish", "goblin", "haranir", "horde",
    "human", "kul tiran", "night elf", "nightborne", "orcish",
    "pandaren", "tauren", "troll", "undead", "void elf", "vrykul",
    "vulpera", "wildhammer dwarf", "zandalari troll", "worgen",
}
STYLE_TAGS = {
    "bold", "casual", "cozy", "cute", "elegant", "fae", "fel",
    "lavish", "light", "magical", "mechanical", "nature", "pirate",
    "romantic", "simple", "spooky", "void", "whimsical",
}
CLASS_TAGS = {
    "death knight", "demon hunter", "druid", "evoker", "hunter",
    "mage", "monk", "paladin", "priest", "rogue", "shaman",
    "warlock", "warrior",
}
ROOM_TAGS = {
    "armory", "attic", "basement", "bathroom", "bedroom",
    "breakfast room", "brewery", "chapel", "closet", "conservatory",
    "dining room", "dungeon", "family room", "foyer", "greenhouse",
    "hallway", "kitchen", "library", "living room", "lounge",
    "nursery", "observatory", "pantry", "sitting room", "storage",
    "sunroom", "tavern", "throne room", "trophy room", "vault",
    "wine cellar", "workshop",
}
SEASONAL_TAGS = {"fall", "spring", "summer", "winter"}


def classify_tag(tag_name: str) -> str:
    """Classify a tag into a category."""
    lower = tag_name.lower().strip()
    if lower in CULTURE_TAGS:
        return "culture"
    if lower in STYLE_TAGS:
        return "style"
    if lower in CLASS_TAGS:
        return "class"
    if lower in ROOM_TAGS:
        return "room"
    if lower in SEASONAL_TAGS:
        return "seasonal"
    return "other"


# ---------------------------------------------------------------------------
# Caching & HTTP
# ---------------------------------------------------------------------------

def cache_key(url: str) -> Path:
    """Generate a cache file path for a URL."""
    h = hashlib.md5(url.encode()).hexdigest()[:12]
    slug = re.sub(r"[^a-zA-Z0-9]", "_", url.split("//", 1)[-1])[:80]
    return CACHE_DIR / f"{slug}_{h}.html"


_use_cache = True  # module-level flag


def fetch_page(url: str) -> str | None:
    """Fetch a page with caching and rate limiting."""
    cache_path = cache_key(url)

    if _use_cache and cache_path.exists():
        return cache_path.read_text(encoding="utf-8")

    time.sleep(RATE_LIMIT_SECONDS)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 429:
            logger.warning("Rate limited on %s, waiting 30s...", url)
            time.sleep(30)
            resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 404:
            logger.debug("404 for %s (item may not exist on site)", url)
            # Cache 404s too to avoid re-fetching
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            cache_path.write_text("<!-- 404 -->", encoding="utf-8")
            return None
        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None
        html = resp.text
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(html, encoding="utf-8")
        return html
    except requests.RequestException as e:
        logger.error("Request failed for %s: %s", url, e)
        return None


def progress_report(current: int, total: int, label: str,
                    last_pct: list[int]) -> None:
    """Report progress every 10%."""
    if total == 0:
        return
    pct = int(current / total * 100)
    threshold = (pct // 10) * 10
    if threshold > 0 and threshold not in last_pct:
        last_pct.append(threshold)
        logger.info("  [%s] %d%% (%d/%d)", label, pct, current, total)


# ---------------------------------------------------------------------------
# Set listing parsing
# ---------------------------------------------------------------------------

def parse_set_listing_page(html: str) -> list[dict]:
    """Parse a set listing page, extracting set metadata.

    Each set is a Bootstrap card inside a .col div:
      <div class="col">
        <div class="card h-100">
          <a href="/sets/{id}/{slug}/" class="text-decoration-none">
            ...
            <div class="card-body">
              <h5 class="card-title ...">Set Name</h5>
              <p ...>by <span class="text-info">Author</span></p>
              <div class="d-flex ...">
                <span><i class="bi bi-collection me-1"></i>46 items</span>
                <span><i class="bi bi-heart me-1"></i>565</span>
              </div>
            </div>
          </a>
        </div>
      </div>
    """
    soup = BeautifulSoup(html, "lxml")
    sets_found = []
    seen_ids = set()

    for link in soup.find_all("a", href=re.compile(r"/sets/\d+/")):
        href = link.get("href", "")
        m = re.match(r"/sets/(\d+)/", href)
        if not m:
            continue
        set_id = int(m.group(1))
        if set_id in seen_ids:
            continue
        seen_ids.add(set_id)

        # Name from h5.card-title
        h5 = link.find("h5")
        name = h5.get_text(strip=True) if h5 else ""

        # Stats are in spans inside the card-body
        text = link.get_text(" ", strip=True)

        # Item count: "N items"
        items_match = re.search(r"(\d+)\s*items?", text)
        item_count = int(items_match.group(1)) if items_match else 0

        # Likes: number after heart icon — appears as last standalone number
        # in the card text (after "N items")
        likes = 0
        # The heart span contains just the number after the icon
        spans = link.find_all("span")
        for span in spans:
            span_text = span.get_text(strip=True)
            # Heart icon span: just a number (no "items" text)
            if span.find("i", class_=re.compile(r"bi-heart")):
                num_match = re.search(r"(\d+)", span_text)
                if num_match:
                    likes = int(num_match.group(1))
                    break

        # Fallback: last number in text after items count
        if likes == 0:
            all_nums = re.findall(r"(\d+)", text)
            if len(all_nums) >= 2:
                likes = int(all_nums[-1])

        sets_found.append({
            "set_id": set_id,
            "name": name,
            "likes": likes,
            "item_count": item_count,
        })

    return sets_found


def get_total_pages(html: str) -> int:
    """Extract total page count from pagination links."""
    soup = BeautifulSoup(html, "lxml")
    # Links use &page=N or ?page=N — match either
    page_links = soup.find_all("a", href=re.compile(r"page=\d+"))
    max_page = 1
    for link in page_links:
        m = re.search(r"page=(\d+)", link.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))
    return max_page


# ---------------------------------------------------------------------------
# Set detail parsing
# ---------------------------------------------------------------------------

def parse_set_detail(html: str) -> dict:
    """Parse a set detail page for decorIDs and tags."""
    soup = BeautifulSoup(html, "lxml")
    result = {"decorIDs": [], "tags": [], "likes": 0}

    # Extract decorIDs from /decor/{id}/ links
    seen_ids = set()
    for link in soup.find_all("a", href=re.compile(r"/decor/\d+/")):
        m = re.match(r"/decor/(\d+)/", link.get("href", ""))
        if m:
            did = int(m.group(1))
            if did not in seen_ids:
                seen_ids.add(did)
                result["decorIDs"].append(did)

    # Extract tags from /sets/?tags= links
    for link in soup.find_all("a", href=re.compile(r"/sets/\?tags=")):
        tag_text = link.get_text(strip=True)
        if tag_text and tag_text not in result["tags"]:
            result["tags"].append(tag_text)

    # Also look for tag-like elements (badges, pills, etc.)
    for el in soup.find_all(class_=re.compile(r"tag|badge|pill", re.I)):
        tag_text = el.get_text(strip=True)
        if tag_text and tag_text not in result["tags"] and len(tag_text) < 30:
            result["tags"].append(tag_text)

    # Extract likes count
    text = soup.get_text()
    likes_match = re.search(r"(\d+)\s*likes?", text, re.I)
    if likes_match:
        result["likes"] = int(likes_match.group(1))

    return result


# ---------------------------------------------------------------------------
# Item tag parsing
# ---------------------------------------------------------------------------

def parse_item_tags(html: str) -> dict:
    """Parse an individual decor item page for culture/style tags.

    Tags have CSS classes that identify their type:
      tag-culture, tag-{race}  → Culture
      tag-style                → Style (also includes some culture tags)
      tag-theme                → Additional style/theme
      tag-expansion, tag-size, tag-tertiary_categories → Ignored
    """
    result = {"culture": [], "style": [], "class": [], "theme": []}

    if not html or html.strip() == "<!-- 404 -->":
        return result

    soup = BeautifulSoup(html, "lxml")

    # CSS class approach: each tag <a> has "tag tag-{type} ..." classes
    for a_tag in soup.find_all("a", class_=re.compile(r"\btag\b")):
        tag_text = a_tag.get_text(strip=True)
        if not tag_text or len(tag_text) > 40:
            continue

        classes = set(a_tag.get("class", []))

        # Determine category from CSS class
        if "tag-culture" in classes:
            if tag_text not in result["culture"]:
                result["culture"].append(tag_text)
            continue

        # Race-specific classes (tag-human, tag-elven, tag-orcish, etc.)
        race_classes = classes & {
            "tag-human", "tag-elven", "tag-orcish", "tag-dwarven",
            "tag-gnomish", "tag-goblin", "tag-tauren", "tag-troll",
            "tag-undead", "tag-draenei", "tag-pandaren", "tag-nightborne",
            "tag-night-elf", "tag-blood-elf", "tag-void-elf",
            "tag-dark-iron-dwarf", "tag-gilnean", "tag-vulpera",
            "tag-dracthyr", "tag-earthen", "tag-haranir", "tag-vrykul",
        }
        if race_classes:
            if tag_text not in result["culture"]:
                result["culture"].append(tag_text)
            continue

        if "tag-style" in classes:
            # Style section sometimes includes faction/culture tags
            cat = classify_tag(tag_text)
            if cat == "culture":
                if tag_text not in result["culture"]:
                    result["culture"].append(tag_text)
            elif cat == "class":
                if tag_text not in result["class"]:
                    result["class"].append(tag_text)
            else:
                if tag_text not in result["style"]:
                    result["style"].append(tag_text)
            continue

        if "tag-theme" in classes:
            if tag_text not in result["theme"]:
                result["theme"].append(tag_text)
            continue

        # Skip non-theme tags (expansion, size, tertiary)
        skip_classes = {
            "tag-expansion", "tag-size", "tag-tertiary_categories",
            "tag-midnight", "tag-warlords-of-draenor", "tag-the-war-within",
        }
        if classes & skip_classes:
            continue

        # Fallback: classify by known tag lists
        cat = classify_tag(tag_text)
        if cat == "culture" and tag_text not in result["culture"]:
            result["culture"].append(tag_text)
        elif cat == "style" and tag_text not in result["style"]:
            result["style"].append(tag_text)
        elif cat == "class" and tag_text not in result["class"]:
            result["class"].append(tag_text)

    return result


# ---------------------------------------------------------------------------
# Main scraping flows
# ---------------------------------------------------------------------------

def scrape_sets() -> None:
    """Scrape community sets: listing pages + quality set details."""
    logger.info("=== Phase 1: Scraping Set Listings ===")

    first_url = f"{BASE_URL}/sets/?page=1&sort=liked"
    first_html = fetch_page(first_url)
    if not first_html:
        logger.error("Failed to fetch first listing page")
        return

    total_pages = get_total_pages(first_html)
    logger.info("  Found %d listing pages to scan", total_pages)

    all_sets: dict[int, dict] = {}
    last_pct: list[int] = []

    for page_num in range(1, total_pages + 1):
        url = f"{BASE_URL}/sets/?page={page_num}&sort=liked"
        html = fetch_page(url)
        if not html:
            continue

        page_sets = parse_set_listing_page(html)
        for s in page_sets:
            sid = s["set_id"]
            if sid not in all_sets:
                all_sets[sid] = s

        progress_report(page_num, total_pages, "listings", last_pct)

    logger.info("  Collected %d unique sets from listings", len(all_sets))

    # Filter to quality sets (AND: must have both engagement and content)
    quality_sets = {
        sid: s for sid, s in all_sets.items()
        if s["likes"] >= MIN_LIKES and s["item_count"] >= MIN_ITEMS
    }

    # Fallback if likes parsing failed
    if len(quality_sets) < 30:
        logger.warning(
            "Only %d quality sets found — likes parsing may be incomplete. "
            "Taking top 500 sets by listing order instead.",
            len(quality_sets),
        )
        sorted_sets = list(all_sets.items())[:500]
        quality_sets = dict(sorted_sets)

    logger.info("=== Phase 2: Scraping %d Set Details ===", len(quality_sets))
    last_pct = []
    scraped = 0
    total_quality = len(quality_sets)

    for sid, meta in quality_sets.items():
        slug = re.sub(r"[^a-z0-9]+", "-", meta["name"].lower()).strip("-")
        url = f"{BASE_URL}/sets/{sid}/{slug}-{sid}/"
        html = fetch_page(url)

        if html:
            detail = parse_set_detail(html)
            meta["decorIDs"] = detail["decorIDs"]
            if detail["tags"]:
                meta["tags"] = detail["tags"]
            else:
                meta["tags"] = []
            if detail["likes"] > meta.get("likes", 0):
                meta["likes"] = detail["likes"]
        else:
            meta["decorIDs"] = []
            meta["tags"] = []

        scraped += 1
        progress_report(scraped, total_quality, "set details", last_pct)

    # Save
    output = {
        "metadata": {
            "scraped": time.strftime("%Y-%m-%d"),
            "total_sets_found": len(all_sets),
            "quality_sets_scraped": len(quality_sets),
        },
        "sets": {str(sid): s for sid, s in quality_sets.items()},
    }
    SETS_OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info("Saved %d sets to %s", len(quality_sets), SETS_OUTPUT)

    # Stats
    sets_with_tags = sum(1 for s in quality_sets.values() if s.get("tags"))
    sets_with_items = sum(1 for s in quality_sets.values() if s.get("decorIDs"))
    total_refs = sum(len(s.get("decorIDs", [])) for s in quality_sets.values())
    unique_ids = set()
    for s in quality_sets.values():
        unique_ids.update(s.get("decorIDs", []))

    logger.info("  Sets with tags: %d / %d", sets_with_tags, len(quality_sets))
    logger.info("  Sets with items: %d / %d", sets_with_items, len(quality_sets))
    logger.info("  Total item refs: %d, unique decorIDs: %d", total_refs, len(unique_ids))


def scrape_items() -> None:
    """Scrape per-item culture/style tags for catalog items."""
    logger.info("=== Scraping Item Tags ===")

    if not CATALOG_PATH.exists():
        logger.error("enriched_catalog.json not found at %s", CATALOG_PATH)
        return

    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)

    decor_ids = sorted(set(item["decorID"] for item in catalog if "decorID" in item))
    logger.info("  Scanning %d decorIDs from catalog", len(decor_ids))

    items_data: dict[str, dict] = {}
    items_with_tags = 0
    last_pct: list[int] = []

    for idx, did in enumerate(decor_ids):
        url = f"{BASE_URL}/decor/{did}/"
        html = fetch_page(url)

        if html and html.strip() != "<!-- 404 -->":
            tags = parse_item_tags(html)
            items_data[str(did)] = tags
            if tags["culture"] or tags["style"] or tags["class"] or tags["theme"]:
                items_with_tags += 1
        else:
            items_data[str(did)] = {
                "culture": [], "style": [], "class": [], "theme": [],
            }

        progress_report(idx + 1, len(decor_ids), "items", last_pct)

    # Save
    output = {
        "metadata": {
            "scraped": time.strftime("%Y-%m-%d"),
            "total_items": len(decor_ids),
            "items_with_tags": items_with_tags,
            "items_without_tags": len(decor_ids) - items_with_tags,
        },
        "items": items_data,
    }
    ITEMS_OUTPUT.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info("Saved %d items to %s", len(items_data), ITEMS_OUTPUT)
    logger.info("  With tags: %d, without: %d", items_with_tags,
                len(decor_ids) - items_with_tags)


def main():
    parser = argparse.ArgumentParser(
        description="Scrape housing.wowdb.com for theme data"
    )
    parser.add_argument("--sets", action="store_true",
                        help="Scrape community-curated sets")
    parser.add_argument("--items", action="store_true",
                        help="Scrape per-item culture/style tags")
    parser.add_argument("--all", action="store_true",
                        help="Scrape both sets and items")
    parser.add_argument("--no-cache", action="store_true",
                        help="Ignore cached responses")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Verbose logging")
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.sets and not args.items and not args.all:
        parser.print_help()
        sys.exit(1)

    if args.no_cache:
        global _use_cache
        _use_cache = False

    if args.sets or args.all:
        scrape_sets()

    if args.items or args.all:
        scrape_items()

    # Stamp cache metadata (non-fatal if it fails)
    try:
        from pipeline_metadata import stamp_after_scrape
        wdb_files = len([f for f in CACHE_DIR.iterdir() if f.suffix == ".html"])
        stamp_after_scrape(CACHE_DIR, source="wowdb (housing.wowdb.com)", total_files=wdb_files)
        logger.info("Cache metadata updated (%d files in wowdb_cache)", wdb_files)
    except Exception as exc:
        logger.warning("Failed to update cache metadata: %s", exc)

    logger.info("Done!")


if __name__ == "__main__":
    main()
