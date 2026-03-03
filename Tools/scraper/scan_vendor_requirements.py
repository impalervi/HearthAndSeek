"""
scan_vendor_requirements.py — Scan Wowhead tooltips for vendor-gated requirements.

Checks all items in CatalogData.lua for hidden vendor prerequisites:
  - Achievement requirements ("Complete the achievement 'X'")
  - Quest requirements ("Complete the quest 'X'")

Uses the Wowhead tooltip API (nether.wowhead.com/tooltip/item/<id>) which
is lightweight and fast. Results are cached to avoid re-fetching.

Output: data/vendor_requirements.json
"""

import json
import hashlib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scan_vendor_requirements")

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
CATALOG_LUA = SCRIPT_DIR.parent.parent / "Data" / "CatalogData.lua"
OUTPUT_FILE = DATA_DIR / "vendor_requirements.json"
ENRICHED_CATALOG = DATA_DIR / "enriched_catalog.json"

REQUEST_DELAY = 1.0  # seconds between requests
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_last_request_time = 0.0


# ---------------------------------------------------------------------------
# Cache helpers (shared with enrich_catalog.py)
# ---------------------------------------------------------------------------

def _cache_key(prefix: str, name: str) -> str:
    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{prefix}_{readable}_{safe}.json"


def cache_get(prefix: str, name: str) -> Optional[Any]:
    path = CACHE_DIR / _cache_key(prefix, name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_put(prefix: str, name: str, data: Any) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(prefix, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _rate_limited_get(url: str) -> Optional[str]:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        _last_request_time = time.time()
        if resp.status_code == 429:
            logger.warning("Rate limited (429). Waiting 30s...")
            time.sleep(30)
            resp = requests.get(url, headers=HEADERS, timeout=20)
            _last_request_time = time.time()
        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None
        return resp.text
    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Tooltip parsing
# ---------------------------------------------------------------------------

# Patterns for vendor requirements in Wowhead tooltip HTML
# After normalization (\" → "), tooltips use "..." as delimiters.
# Names may contain apostrophes (e.g. "That's Val'sharah Folks!"),
# so the capture group must only exclude " and <, NOT '.
ACH_PATTERN = re.compile(
    r"""[Cc]omplete\s+the\s+achievement\s+"([^"<]+)""",
    re.IGNORECASE,
)
QUEST_PATTERN = re.compile(
    r"""[Cc]omplete\s+the\s+quest\s+"([^"<]+)""",
    re.IGNORECASE,
)
# Also match HTML-entity encoded quotes (&#x27; / &#39; / &apos;)
# Don't include literal ' as delimiter — names may contain apostrophes.
ACH_PATTERN_HTML = re.compile(
    r"""[Cc]omplete\s+the\s+achievement\s+(?:&#x27;|&#39;|&apos;)(.*?)(?:&#x27;|&#39;|&apos;)""",
    re.IGNORECASE,
)
QUEST_PATTERN_HTML = re.compile(
    r"""[Cc]omplete\s+the\s+quest\s+(?:&#x27;|&#39;|&apos;)(.*?)(?:&#x27;|&#39;|&apos;)""",
    re.IGNORECASE,
)


def fetch_item_tooltip(item_id: int) -> Optional[str]:
    """Fetch the Wowhead tooltip HTML for an item."""
    cached = cache_get("item_tooltip", str(item_id))
    if cached is not None:
        return cached.get("html", "")

    url = f"https://nether.wowhead.com/tooltip/item/{item_id}"
    html = _rate_limited_get(url)
    if html is None:
        cache_put("item_tooltip", str(item_id), {"html": "", "error": True})
        return ""

    cache_put("item_tooltip", str(item_id), {"html": html})
    return html


def parse_requirements(html: str) -> dict:
    """Extract achievement and quest requirements from tooltip HTML."""
    result = {"achievement": None, "quest": None}

    # Normalize escaped quotes from JSON-encoded HTML (e.g. \" -> ")
    html = html.replace('\\"', '"')

    # Try HTML-entity patterns first (more specific)
    m = ACH_PATTERN_HTML.search(html)
    if m:
        result["achievement"] = m.group(1).strip()
    else:
        m = ACH_PATTERN.search(html)
        if m:
            result["achievement"] = m.group(1).strip()

    m = QUEST_PATTERN_HTML.search(html)
    if m:
        result["quest"] = m.group(1).strip()
    else:
        m = QUEST_PATTERN.search(html)
        if m:
            result["quest"] = m.group(1).strip()

    return result


# ---------------------------------------------------------------------------
# Load item data
# ---------------------------------------------------------------------------

def load_items_from_catalog() -> list[dict]:
    """Load all items from enriched_catalog.json."""
    items = []
    catalog = json.load(open(ENRICHED_CATALOG, "r", encoding="utf-8"))
    for item in catalog:
        item_id = item.get("itemID")
        if item_id:
            items.append({
                "itemID": item_id,
                "decorID": item.get("decorID"),
                "name": item.get("name", "?"),
            })
    return items


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    items = load_items_from_catalog()
    logger.info("Loaded %d items from enriched catalog", len(items))

    # Count how many are already cached
    cached_count = 0
    for item in items:
        if cache_get("item_tooltip", str(item["itemID"])) is not None:
            cached_count += 1
    uncached = len(items) - cached_count
    logger.info("Cached: %d, Need to fetch: %d", cached_count, uncached)
    if uncached > 0:
        est_time = uncached * REQUEST_DELAY
        logger.info("Estimated time: %.0f seconds (%.1f minutes)", est_time, est_time / 60)

    # Scan all items
    results = {}
    found_ach = 0
    found_quest = 0
    errors = 0

    for i, item in enumerate(items):
        item_id = item["itemID"]
        if (i + 1) % 100 == 0:
            logger.info("Progress: %d/%d (found %d achievements, %d quests)",
                        i + 1, len(items), found_ach, found_quest)

        html = fetch_item_tooltip(item_id)
        if not html:
            errors += 1
            continue

        reqs = parse_requirements(html)
        if reqs["achievement"] or reqs["quest"]:
            results[str(item["decorID"])] = {
                "decorID": item["decorID"],
                "itemID": item_id,
                "name": item["name"],
                "unlockAchievement": reqs["achievement"],
                "unlockQuest": reqs["quest"],
            }
            if reqs["achievement"]:
                found_ach += 1
            if reqs["quest"]:
                found_quest += 1

    logger.info("Scan complete: %d items, %d errors", len(items), errors)
    logger.info("Found requirements: %d achievement, %d quest", found_ach, found_quest)

    # Write output
    output = {
        "metadata": {
            "total_items_scanned": len(items),
            "items_with_achievement_req": found_ach,
            "items_with_quest_req": found_quest,
            "errors": errors,
        },
        "requirements": results,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("Written: %s", OUTPUT_FILE)

    # Print summary
    print(f"\n=== VENDOR REQUIREMENT SCAN RESULTS ===")
    print(f"Items scanned: {len(items)}")
    print(f"Items with achievement requirements: {found_ach}")
    print(f"Items with quest requirements: {found_quest}")
    print(f"Errors: {errors}")

    if results:
        print(f"\n=== ITEMS WITH REQUIREMENTS ===")
        print(f"{'decorID':<8} {'Name':<45} {'Type':<12} {'Requirement'}")
        print("-" * 110)
        for decor_id, data in sorted(results.items(), key=lambda x: x[1]["name"]):
            req_type = "Achievement" if data["unlockAchievement"] else "Quest"
            req_name = data["unlockAchievement"] or data["unlockQuest"]
            print(f"{decor_id:<8} {data['name']:<45} {req_type:<12} {req_name}")


if __name__ == "__main__":
    main()
