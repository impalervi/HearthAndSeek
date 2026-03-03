"""
scrape_wowdb.py - Scrape WoWDB's housing decor database.

Targets the WoWDB Housing Decor catalog filtered by quest sources:
  https://housing.wowdb.com/decor/?source_types=Quest

The catalog is paginated (14 pages at ~24 items per page as of writing).
Each page returns an HTML listing of decor items with metadata including
category, subcategory, budget, quest source, vendor NPC, currency, tags,
and interior/exterior placement.

Strategy:
  1. Fetch each paginated list page.
  2. Parse the item cards/rows from the HTML.
  3. Optionally follow individual item detail pages for richer data.
  4. Save all items to data/wowdb_quests.json.

Output: data/wowdb_quests.json
"""

import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = "https://housing.wowdb.com/decor/"
QUERY_PARAMS = "?source_types=Quest"
TOTAL_PAGES = 14
ITEMS_PER_PAGE = 24

OUTPUT_DIR = Path(__file__).resolve().parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "wowdb_quests.json"

HEADERS = {
    "User-Agent": (
        "HearthAndSeek-Scraper/0.1 "
        "(+https://github.com/ImpalerV/HearthAndSeek; educational WoW addon project)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 1.5  # seconds between requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scrape_wowdb")


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

def _extract_decor_id_from_url(url: str) -> int | None:
    """
    Extract decor ID from a WoWDB URL like '/decor/12345/some-slug/'.
    """
    match = re.search(r"/decor/(\d+)/", url)
    return int(match.group(1)) if match else None


def _clean_text(text: str | None) -> str:
    """Strip and normalize whitespace in a text string."""
    if text is None:
        return ""
    return re.sub(r"\s+", " ", text.strip())


def _extract_tags(element: Tag) -> dict[str, list[str]]:
    """
    Extract tag metadata from an item element.

    WoWDB tags items with categories like culture, size, style, theme.
    These are typically displayed as badge/label elements within the item card.
    """
    tags: dict[str, list[str]] = {
        "culture": [],
        "size": [],
        "style": [],
        "theme": [],
    }

    # Look for tag elements - WoWDB uses various class patterns
    tag_elements = element.find_all(
        class_=re.compile(r"tag|badge|label|chip", re.IGNORECASE)
    )
    for tag_el in tag_elements:
        tag_text = _clean_text(tag_el.get_text())
        if not tag_text:
            continue

        # Try to categorize the tag based on known patterns or data attributes
        category = tag_el.get("data-category", "").lower()
        if category in tags:
            tags[category].append(tag_text)
        else:
            # Heuristic: try to categorize by known tag values
            tag_lower = tag_text.lower()
            if tag_lower in (
                "human", "dwarven", "elven", "orcish", "tauren", "forsaken",
                "gnomish", "trollish", "draenei", "blood elf", "goblin",
                "pandaren", "nightborne", "void elf", "zandalari",
            ):
                tags["culture"].append(tag_text)
            elif tag_lower in ("small", "medium", "large", "tiny"):
                tags["size"].append(tag_text)
            elif tag_lower in (
                "rustic", "elegant", "arcane", "nature", "dark", "industrial",
                "nautical", "festive", "military",
            ):
                tags["style"].append(tag_text)
            else:
                tags["theme"].append(tag_text)

    return tags


# ---------------------------------------------------------------------------
# Page-level parsing
# ---------------------------------------------------------------------------

def _parse_list_page(html: str) -> list[dict[str, Any]]:
    """
    Parse a single WoWDB decor listing page and extract item records.

    WoWDB (housing.wowdb.com) uses a Bootstrap card grid layout. Each item
    is a ``<div class="card item-card h-100" data-item-id="...">`` containing:
      - Item name in ``<div class="item-card-category-bar item-name-bar">``
      - Category breadcrumb in ``<div class="... category-bar ...">``
      - Budget cost in ``<span class="... budget-display ...">``
      - Sources in ``<div class="... item-card-sources">``
      - Interior/Exterior badge in a ``<span class="badge ...">``
    """
    soup = BeautifulSoup(html, "lxml")
    items: list[dict[str, Any]] = []

    # Primary selector: the exact container class used by WoWDB
    item_containers = soup.find_all("div", class_="item-card")

    # If the primary selector fails, fall back to broader searches
    if not item_containers:
        item_containers = (
            soup.find_all("div", class_=re.compile(r"decor-item|listing-item", re.I))
            or soup.find_all("tr", class_=re.compile(r"decor|item", re.I))
        )

    # If still nothing, try to find parent containers of decor links
    if not item_containers:
        logger.debug("No item card containers found, searching for item links directly.")
        item_containers = _find_item_containers_fallback(soup)

    for container in item_containers:
        record = _parse_item_container(container)
        if record and record.get("decor_name"):
            items.append(record)

    # Fallback: parse the entire page for decor links if structured parsing fails
    if not items:
        items = _parse_page_fallback(soup)

    return items


def _find_item_containers_fallback(soup: BeautifulSoup) -> list[Tag]:
    """
    Fallback strategy: find parent containers of decor links.
    """
    containers = []
    seen_parents: set[int] = set()

    for link in soup.find_all("a", href=re.compile(r"/decor/\d+/")):
        parent = link.parent
        if parent and id(parent) not in seen_parents:
            seen_parents.add(id(parent))
            containers.append(parent)

    return containers


def _parse_item_container(container: Tag) -> dict[str, Any]:
    """
    Parse a single item card container into a structured record.

    Expected HTML structure (housing.wowdb.com as of 2025):

    .. code-block:: html

        <div class="card item-card h-100" data-item-id="1211">
          <!-- image, budget badge, interior/exterior badge -->
          <div class="item-card-category-bar item-name-bar">
            <h6><a href="/decor/9052/admirals-bed-9052/" class="... quality-uncommon">
              Admiral's Bed</a></h6>
          </div>
          <div class="item-card-category-bar category-bar small text-muted">
            <a href="/decor/furnishings/">Furnishings</a>
            <span>›</span>
            <a href="/decor/furnishings/beds/">Beds</a>
          </div>
          <div class="card-body ...">
            <div class="... item-card-sources">
              <!-- Quest source row -->
              <div class="mb-1 mb-2">
                <small>
                  <img ... title="Quest">
                  <a href="https://beta.wowdb.com/quests/53720">Allegiance of Kul Tiras</a>
                  <span class="text-muted fst-italic">(Stormwind City, Boralus Harbor)</span>
                </small>
              </div>
              <!-- Vendor row -->
              <div class="mb-1">
                <small>
                  <img ... title="Vendor">
                  <a href="https://beta.wowdb.com/npcs/252345">Pearl Barlow</a>
                  <span class="text-muted fst-italic">(Tiragarde Sound)</span>
                </small>
              </div>
            </div>
          </div>
        </div>
    """
    record: dict[str, Any] = {
        "decor_name": None,
        "decor_id": None,
        "category": None,
        "subcategory": None,
        "budget_cost": None,
        "quest_source": None,
        "quest_zone": None,
        "vendor_npc": None,
        "currency_cost": None,
        "interior_exterior": None,
        "tags": {"culture": [], "size": [], "style": [], "theme": []},
        "detail_url": None,
    }

    # ------------------------------------------------------------------
    # Item name & ID  (from the name-bar link to /decor/<id>/<slug>/)
    # ------------------------------------------------------------------
    name_bar = container.find("div", class_="item-name-bar")
    if name_bar:
        decor_link = name_bar.find("a", href=re.compile(r"/decor/\d+/"))
        if decor_link:
            record["decor_name"] = _clean_text(decor_link.get_text())
            href = decor_link.get("href", "")
            record["decor_id"] = _extract_decor_id_from_url(href)
            record["detail_url"] = href
    else:
        # Fallback: any decor link in the container
        decor_link = container.find("a", href=re.compile(r"/decor/\d+/"))
        if decor_link:
            record["decor_name"] = _clean_text(decor_link.get_text())
            href = decor_link.get("href", "")
            record["decor_id"] = _extract_decor_id_from_url(href)
            record["detail_url"] = href

    # ------------------------------------------------------------------
    # Category / Subcategory  (from the category-bar breadcrumb links)
    # ------------------------------------------------------------------
    cat_bar = container.find("div", class_="category-bar")
    if cat_bar:
        cat_links = cat_bar.find_all("a")
        if len(cat_links) >= 1:
            record["category"] = _clean_text(cat_links[0].get_text())
        if len(cat_links) >= 2:
            record["subcategory"] = _clean_text(cat_links[1].get_text())

    # ------------------------------------------------------------------
    # Budget cost  (from <span class="... budget-display ...">)
    # The span contains an <img alt="Budget"> followed by a number.
    # ------------------------------------------------------------------
    budget_el = container.find(class_=re.compile(r"budget-display"))
    if budget_el:
        budget_text = budget_el.get_text()
        budget_match = re.search(r"(\d+)", budget_text)
        if budget_match:
            record["budget_cost"] = int(budget_match.group(1))

    # ------------------------------------------------------------------
    # Sources section  (quest, vendor, currency)
    # Each source type is identified by an <img> icon with a title attr:
    #   title="Quest"  -> quest row
    #   title="Vendor"  -> vendor row
    #   title="Cost"    -> currency row
    # The zone is in a <span class="text-muted fst-italic"> after the
    # source link, containing text like "(Zone1, Zone2)".
    # ------------------------------------------------------------------
    sources_div = container.find(class_="item-card-sources")
    if sources_div:
        # Find all source icon images to identify source types
        source_icons = sources_div.find_all("img", class_="source-icon")
        for icon in source_icons:
            source_type = (icon.get("title") or icon.get("alt") or "").strip().lower()
            # The icon's parent <small> contains the link and zone span
            parent_small = icon.find_parent("small")
            if not parent_small:
                continue

            if source_type == "quest":
                # Quest name: prefer the <a> link to wowdb.com/quests/...
                quest_link = parent_small.find(
                    "a", href=re.compile(r"/quests/\d+")
                )
                if quest_link:
                    record["quest_source"] = _clean_text(quest_link.get_text())
                else:
                    # Some quests are plain text (no link). Extract all
                    # direct text from the <small>, excluding child elements
                    # like <img>, <span>, and <a>.
                    quest_text_parts = []
                    for child in parent_small.children:
                        if isinstance(child, str):
                            part = child.strip()
                            if part:
                                quest_text_parts.append(part)
                    quest_name = " ".join(quest_text_parts)
                    if quest_name:
                        record["quest_source"] = quest_name

                # Quest zone from the italic span after the quest link
                zone_span = parent_small.find(
                    "span", class_=re.compile(r"fst-italic|text-muted")
                )
                if zone_span:
                    zone_text = _clean_text(zone_span.get_text())
                    # Strip surrounding parentheses: "(Zone1, Zone2)" -> "Zone1, Zone2"
                    zone_text = zone_text.strip("()")
                    if zone_text:
                        record["quest_zone"] = zone_text

            elif source_type == "vendor":
                # Vendor NPC name from the <a> link to wowdb.com/npcs/...
                npc_link = parent_small.find(
                    "a", href=re.compile(r"/npcs/\d+")
                )
                if npc_link:
                    record["vendor_npc"] = _clean_text(npc_link.get_text())

            elif source_type == "cost":
                # Currency: amount + currency name
                cost_text = _clean_text(parent_small.get_text())
                if cost_text:
                    record["currency_cost"] = cost_text

    # Fallback for sources if the icon-based approach found nothing
    if not record["quest_source"]:
        quest_el = container.find("a", href=re.compile(r"/quests/\d+"))
        if quest_el:
            record["quest_source"] = _clean_text(quest_el.get_text())
    if not record["vendor_npc"]:
        npc_el = container.find("a", href=re.compile(r"/npcs/\d+"))
        if npc_el:
            record["vendor_npc"] = _clean_text(npc_el.get_text())

    # ------------------------------------------------------------------
    # Quest zone fallback: if the icon-based approach didn't find a zone,
    # look for any italic span with parenthesized text near a quest link.
    # ------------------------------------------------------------------
    if not record["quest_zone"] and sources_div:
        for italic_span in sources_div.find_all("span", class_="fst-italic"):
            span_text = _clean_text(italic_span.get_text())
            if span_text.startswith("(") and span_text.endswith(")"):
                zone_text = span_text.strip("()")
                if zone_text:
                    record["quest_zone"] = zone_text
                    break

    # ------------------------------------------------------------------
    # Interior / Exterior  (from badge element in the image overlay area)
    # e.g. <span class="badge ...">Interior Only</span>
    # ------------------------------------------------------------------
    placement_badge = container.find(
        "span", class_="badge",
        string=re.compile(r"interior|exterior", re.I)
    )
    if placement_badge:
        badge_text = _clean_text(placement_badge.get_text()).lower()
        if "interior" in badge_text and "exterior" in badge_text:
            record["interior_exterior"] = "Both"
        elif "interior" in badge_text:
            record["interior_exterior"] = "Interior"
        elif "exterior" in badge_text:
            record["interior_exterior"] = "Exterior"

    # ------------------------------------------------------------------
    # Tags
    # ------------------------------------------------------------------
    record["tags"] = _extract_tags(container)

    return record


def _parse_page_fallback(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    Last-resort fallback: extract any decor links found on the page.
    This produces minimal records but ensures we capture at least the item
    names and IDs even if the page structure is unexpected.
    """
    items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for link in soup.find_all("a", href=re.compile(r"/decor/\d+/")):
        href = link.get("href", "")
        decor_id = _extract_decor_id_from_url(href)

        if decor_id is None or decor_id in seen_ids:
            continue
        seen_ids.add(decor_id)

        name = _clean_text(link.get_text())
        if not name:
            continue

        items.append({
            "decor_name": name,
            "decor_id": decor_id,
            "category": None,
            "subcategory": None,
            "budget_cost": None,
            "quest_source": None,
            "quest_zone": None,
            "vendor_npc": None,
            "currency_cost": None,
            "interior_exterior": None,
            "tags": {"culture": [], "size": [], "style": [], "theme": []},
            "detail_url": href,
        })

    return items


# ---------------------------------------------------------------------------
# Pagination and main scraping loop
# ---------------------------------------------------------------------------

def fetch_page(page: int) -> str | None:
    """Fetch a single paginated list page from WoWDB."""
    if page == 1:
        url = f"{BASE_URL}{QUERY_PARAMS}"
    else:
        url = f"{BASE_URL}{QUERY_PARAMS}&page={page}"

    logger.info("Fetching page %d: %s", page, url)
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        logger.info("Page %d: received %d bytes", page, len(response.text))
        return response.text
    except requests.RequestException as exc:
        logger.error("Failed to fetch page %d: %s", page, exc)
        return None


def scrape_all_pages() -> list[dict[str, Any]]:
    """
    Scrape all paginated pages of the WoWDB quest decor catalog.

    The scraper detects the actual number of pages by checking for
    pagination controls on the first page. Falls back to TOTAL_PAGES
    if detection fails.
    """
    all_items: list[dict[str, Any]] = []
    seen_ids: set[int] = set()

    for page in range(1, TOTAL_PAGES + 1):
        html = fetch_page(page)
        if html is None:
            logger.warning("Skipping page %d due to fetch failure.", page)
            continue

        items = _parse_list_page(html)
        logger.info("Page %d: parsed %d items", page, len(items))

        # Deduplicate by decor_id
        new_count = 0
        for item in items:
            did = item.get("decor_id")
            if did and did not in seen_ids:
                seen_ids.add(did)
                all_items.append(item)
                new_count += 1
            elif did is None:
                all_items.append(item)
                new_count += 1

        logger.info("Page %d: %d new unique items (total: %d)", page, new_count, len(all_items))

        # If we got zero items from a page, we may have hit the end
        if not items:
            logger.info("Page %d returned no items. Stopping pagination.", page)
            break

        # Polite delay
        if page < TOTAL_PAGES:
            time.sleep(REQUEST_DELAY)

    return all_items


def main() -> None:
    """Main entry point: scrape all WoWDB quest decor pages and save to JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=== Scraping WoWDB Quest Decor Catalog ===")
    items = scrape_all_pages()

    # Remove the detail_url field from output (internal use only)
    for item in items:
        item.pop("detail_url", None)

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(items, fh, indent=2, ensure_ascii=False)

    logger.info("Saved %d items to %s", len(items), OUTPUT_FILE)

    if not items:
        logger.warning(
            "No items were extracted from WoWDB. The page structure may have "
            "changed or the site may require JavaScript rendering. Check "
            "Scraper_Notes.md for the manual entry fallback format."
        )

    logger.info("WoWDB scraping complete.")


if __name__ == "__main__":
    main()
