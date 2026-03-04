"""
scrape_silvermoon_vendors.py - Scrape Wowhead NPC pages for Silvermoon City
decor vendors to find their complete item lists.

For each known Silvermoon vendor NPC, fetches the Wowhead NPC page and extracts
all item IDs from the "sells" section. Cross-references against enriched_catalog.json
to find items that need their vendor/zone updated to Silvermoon City.

Output: data/silvermoon_vendors.json
"""

import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
ENRICHED_CATALOG = DATA_DIR / "enriched_catalog.json"
OUTPUT_FILE = DATA_DIR / "silvermoon_vendors.json"

REQUEST_DELAY = 1.5  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
}

# Known Silvermoon City decor vendors
SILVERMOON_VENDORS = [
    {
        "name": "Dethelin",
        "npcID": 250982,
        "title": "Purloined Decor",
        "x": 52.51,
        "y": 47.27,
    },
    {
        "name": "Dennia Silvertongue",
        "npcID": 256828,
        "title": "Decor Specialist",
        "x": 51.01,
        "y": 56.51,
    },
    {
        "name": "Corlen Hordralin",
        "npcID": 252915,
        "title": None,
        "x": 44.0,
        "y": 62.4,
    },
    {
        "name": "Hesta Forlath",
        "npcID": 252916,
        "title": None,
        "x": 44.0,
        "y": 62.4,
    },
    {
        "name": "Telemancer Astrandis",
        "npcID": 242399,
        "title": None,
        "x": 52.5,
        "y": 79.0,
    },
    {
        "name": "Irodalmin",
        "npcID": 256026,
        "title": None,
        "x": 48.2,
        "y": 51.6,
    },
    {
        "name": "Naleidea Rivergleam",
        "npcID": 242398,
        "title": None,
        "x": 52.6,
        "y": 78.0,
    },
    {
        "name": "Nael Silvertongue",
        "npcID": 251091,
        "title": None,
        "x": 50.6,
        "y": 56.2,
    },
]

SILVERMOON_MAP_ID = 2393

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("scrape_silvermoon_vendors")


# ---------------------------------------------------------------------------
# Cache helpers (matching enrich_catalog.py conventions)
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


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get(url: str) -> Optional[str]:
    """Fetch a URL with rate limiting. Returns response text or None."""
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        _last_request_time = time.time()

        if resp.status_code == 429:
            logger.warning("Rate limited (429). Waiting 30 seconds...")
            time.sleep(30)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            _last_request_time = time.time()

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.text

    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Listview data extraction (from enrich_catalog.py)
# ---------------------------------------------------------------------------

def _extract_listview_data(html: str, listview_id: str) -> list[dict]:
    """
    Extract data array from a Wowhead Listview block with the given id.

    Matches patterns like:
        new Listview({template: 'item', id: 'sells', ..., data: [{...}, ...]});
    """
    id_pattern = (
        r"new\s+Listview\(\s*\{.*?"
        r"""id:\s*['"]""" + re.escape(listview_id) + r"""['"]"""
    )
    id_match = re.search(id_pattern, html, re.DOTALL)
    if not id_match:
        return []

    rest = html[id_match.end():]
    data_match = re.search(r'data:\s*\[', rest)
    if not data_match:
        return []

    arr_start = rest.index('[', data_match.start())
    depth = 0
    arr_end = None
    in_string = False
    escape_next = False
    for i in range(arr_start, len(rest)):
        c = rest[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                arr_end = i + 1
                break

    if arr_end is None:
        return []

    json_str = rest[arr_start:arr_end]
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    return []


def _extract_gatherer_item_ids(html: str) -> set[int]:
    """
    Extract item IDs from WH.Gatherer.addData(3, ..., {...}) calls.
    Type 3 = items in Wowhead's gatherer system.
    """
    item_ids = set()
    # Match WH.Gatherer.addData(3, N, {id1: {...}, id2: {...}, ...})
    pattern = re.compile(r'WH\.Gatherer\.addData\(3,\s*\d+,\s*(\{.*?\})\)')
    for match in pattern.finditer(html):
        try:
            data = json.loads(match.group(1))
            for key in data:
                try:
                    item_ids.add(int(key))
                except (ValueError, TypeError):
                    pass
        except json.JSONDecodeError:
            pass
    return item_ids


# ---------------------------------------------------------------------------
# NPC page scraping
# ---------------------------------------------------------------------------

def scrape_npc_sells(npc_id: int, npc_name: str) -> list[dict]:
    """
    Scrape the Wowhead NPC page to get all items sold by this NPC.

    Fetches https://www.wowhead.com/npc={npcID} and extracts items from:
    1. The 'sells' Listview block (primary source)
    2. WH.Gatherer.addData type 3 (fallback for item IDs)

    Returns list of item dicts with at minimum {id, name}.
    """
    cache_key = f"npc_sells_{npc_id}"
    cached = cache_get("npc_sells", str(npc_id))
    if cached is not None:
        return cached

    url = f"https://www.wowhead.com/npc={npc_id}"
    logger.info("  Fetching %s ...", url)
    html = _rate_limited_get(url)

    if not html:
        cache_put("npc_sells", str(npc_id), [])
        return []

    # Strategy 1: Extract from Listview 'sells' block
    items = _extract_listview_data(html, "sells")
    if items:
        logger.info("  Found %d items in 'sells' Listview for %s (npcID=%d)",
                     len(items), npc_name, npc_id)
        cache_put("npc_sells", str(npc_id), items)
        return items

    # Strategy 2: Try 'sells-recipe' as well (some NPCs use this id)
    items = _extract_listview_data(html, "sells-recipe")
    if items:
        logger.info("  Found %d items in 'sells-recipe' Listview for %s (npcID=%d)",
                     len(items), npc_name, npc_id)
        cache_put("npc_sells", str(npc_id), items)
        return items

    # Strategy 3: Extract from Gatherer data (less structured, just IDs)
    gatherer_ids = _extract_gatherer_item_ids(html)
    if gatherer_ids:
        items = [{"id": iid, "name": f"item_{iid}"} for iid in sorted(gatherer_ids)]
        logger.info("  Found %d item IDs from Gatherer data for %s (npcID=%d)",
                     len(items), npc_name, npc_id)
        cache_put("npc_sells", str(npc_id), items)
        return items

    # Strategy 4: Regex fallback for "id":XXXXX patterns in sells-related context
    # Look for all unique item IDs referenced on the page in sell contexts
    sell_section = ""
    sell_match = re.search(r'(?:sells|vendor|sold)', html, re.IGNORECASE)
    if sell_match:
        # Get a chunk of the page around the sells section
        start = max(0, sell_match.start() - 1000)
        end = min(len(html), sell_match.end() + 50000)
        sell_section = html[start:end]

    # Try broader pattern on the full page
    id_pattern = re.compile(r'"id"\s*:\s*(\d+)')
    found_ids = set()
    for match in id_pattern.finditer(sell_section or html):
        found_ids.add(int(match.group(1)))

    if found_ids:
        items = [{"id": iid, "name": f"item_{iid}"} for iid in sorted(found_ids)]
        logger.info("  Found %d item IDs via regex fallback for %s (npcID=%d)",
                     len(items), npc_name, npc_id)
        # Don't cache regex fallback - it's noisy
        # cache_put("npc_sells", str(npc_id), items)
        return items

    logger.warning("  No sell data found for %s (npcID=%d)", npc_name, npc_id)
    cache_put("npc_sells", str(npc_id), [])
    return []


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load enriched catalog
    if not ENRICHED_CATALOG.exists():
        logger.error("Enriched catalog not found: %s", ENRICHED_CATALOG)
        sys.exit(1)

    with open(ENRICHED_CATALOG, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    logger.info("Loaded %d entries from enriched_catalog.json", len(catalog))

    # Build lookup maps
    # itemID -> catalog entry
    item_id_to_entry: dict[int, dict] = {}
    for entry in catalog:
        iid = entry.get("itemID")
        if iid:
            item_id_to_entry[iid] = entry

    # decorID -> catalog entry
    decor_id_to_entry: dict[int, dict] = {}
    for entry in catalog:
        did = entry.get("decorID")
        if did:
            decor_id_to_entry[did] = entry

    logger.info("Built lookup maps: %d by itemID, %d by decorID",
                len(item_id_to_entry), len(decor_id_to_entry))

    # -----------------------------------------------------------------------
    # Scrape each vendor
    # -----------------------------------------------------------------------
    all_vendor_results: dict[str, dict] = {}  # npcName -> {items, ...}
    all_sold_item_ids: dict[int, list[dict]] = {}  # itemID -> list of vendors

    for vendor in SILVERMOON_VENDORS:
        npc_name = vendor["name"]
        npc_id = vendor["npcID"]

        logger.info("")
        logger.info("=" * 60)
        logger.info("Scraping vendor: %s (npcID=%d)", npc_name, npc_id)
        logger.info("=" * 60)

        sold_items = scrape_npc_sells(npc_id, npc_name)

        # Extract item IDs from the sold items
        item_ids = set()
        item_details = []
        for item in sold_items:
            iid = item.get("id")
            if iid:
                item_ids.add(iid)
                item_details.append({
                    "itemID": iid,
                    "name": item.get("name", f"item_{iid}"),
                    "quality": item.get("quality"),
                })

        all_vendor_results[npc_name] = {
            "npcID": npc_id,
            "title": vendor.get("title"),
            "x": vendor["x"],
            "y": vendor["y"],
            "totalItemsFound": len(item_ids),
            "itemIDs": sorted(item_ids),
            "itemDetails": item_details,
        }

        # Track which vendors sell each item
        for iid in item_ids:
            if iid not in all_sold_item_ids:
                all_sold_item_ids[iid] = []
            all_sold_item_ids[iid].append(vendor)

        logger.info("  Total items found: %d", len(item_ids))

    # -----------------------------------------------------------------------
    # Cross-reference with enriched catalog
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("Cross-referencing with enriched catalog")
    logger.info("=" * 60)

    overrides: dict[str, dict] = {}
    already_silvermoon = 0
    not_in_catalog = 0
    needs_update = 0
    already_correct_vendor = 0

    for item_id, vendors in sorted(all_sold_item_ids.items()):
        catalog_entry = item_id_to_entry.get(item_id)

        if not catalog_entry:
            not_in_catalog += 1
            continue

        decor_id = catalog_entry.get("decorID")
        current_zone = catalog_entry.get("zone", "")
        current_vendor = catalog_entry.get("vendor", "")
        item_name = catalog_entry.get("name", "?")

        # Classify the item for stats, but ALWAYS create an override
        # so the pipeline can set proper npcID/coords and remove factionVendors
        vendor_info = vendors[0]  # Use first vendor found
        if current_zone == "Silvermoon City" and current_vendor == vendor_info["name"]:
            already_correct_vendor += 1
        elif current_zone == "Silvermoon City":
            already_silvermoon += 1
        else:
            needs_update += 1

        decor_key = str(decor_id)

        overrides[decor_key] = {
            "decorID": decor_id,
            "itemID": item_id,
            "itemName": item_name,
            "vendorName": vendor_info["name"],
            "npcID": vendor_info["npcID"],
            "npcX": vendor_info["x"],
            "npcY": vendor_info["y"],
            "zone": "Silvermoon City",
            "mapID": SILVERMOON_MAP_ID,
            "originalVendor": current_vendor or None,
            "originalZone": current_zone or None,
        }

    logger.info("Cross-reference results:")
    logger.info("  Items found across all vendors:   %d", len(all_sold_item_ids))
    logger.info("  Items in our catalog:             %d", len(all_sold_item_ids) - not_in_catalog)
    logger.info("  Not in catalog (non-decor items): %d", not_in_catalog)
    logger.info("  Already correct vendor+zone:      %d", already_correct_vendor)
    logger.info("  Already Silvermoon (diff vendor):  %d", already_silvermoon)
    logger.info("  Need zone/vendor update:          %d", needs_update)

    # -----------------------------------------------------------------------
    # Build summary
    # -----------------------------------------------------------------------
    summary = {
        "totalVendorsScraped": len(SILVERMOON_VENDORS),
        "vendorBreakdown": {},
        "totalUniqueItemIDs": len(all_sold_item_ids),
        "itemsInCatalog": len(all_sold_item_ids) - not_in_catalog,
        "itemsNotInCatalog": not_in_catalog,
        "alreadySilvermoonCorrectVendor": already_correct_vendor,
        "alreadySilvermoonDiffVendor": already_silvermoon,
        "needsUpdate": needs_update,
    }

    for vendor in SILVERMOON_VENDORS:
        name = vendor["name"]
        result = all_vendor_results.get(name, {})
        # Count how many of this vendor's items are in our catalog
        vendor_item_ids = set(result.get("itemIDs", []))
        in_catalog = sum(1 for iid in vendor_item_ids if iid in item_id_to_entry)

        summary["vendorBreakdown"][name] = {
            "npcID": vendor["npcID"],
            "totalItemsOnWowhead": result.get("totalItemsFound", 0),
            "itemsInOurCatalog": in_catalog,
        }

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    output = {
        "overrides": overrides,
        "summary": summary,
        "vendorDetails": {
            name: {
                "npcID": data["npcID"],
                "x": data["x"],
                "y": data["y"],
                "totalItems": data["totalItemsFound"],
                "itemIDs": data["itemIDs"],
            }
            for name, data in all_vendor_results.items()
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("Output written to: %s", OUTPUT_FILE)

    # -----------------------------------------------------------------------
    # Print summary
    # -----------------------------------------------------------------------
    print("\n" + "=" * 70)
    print("SILVERMOON VENDOR SCRAPE SUMMARY")
    print("=" * 70)

    print(f"\n{'Vendor':<30} {'NPC ID':<10} {'Items Found':<14} {'In Catalog'}")
    print("-" * 70)
    total_found = 0
    total_in_catalog = 0
    for vendor in SILVERMOON_VENDORS:
        name = vendor["name"]
        bd = summary["vendorBreakdown"].get(name, {})
        found = bd.get("totalItemsOnWowhead", 0)
        in_cat = bd.get("itemsInOurCatalog", 0)
        total_found += found
        total_in_catalog += in_cat
        print(f"  {name:<28} {vendor['npcID']:<10} {found:<14} {in_cat}")
    print("-" * 70)
    print(f"  {'TOTAL':<28} {'':<10} {total_found:<14} {total_in_catalog}")

    print(f"\nUnique item IDs across all vendors: {summary['totalUniqueItemIDs']}")
    print(f"Items in our decor catalog:         {summary['itemsInCatalog']}")
    print(f"Items NOT in catalog (non-decor):   {summary['itemsNotInCatalog']}")

    print(f"\nAlready Silvermoon (correct vendor): {summary['alreadySilvermoonCorrectVendor']}")
    print(f"Already Silvermoon (diff vendor):    {summary['alreadySilvermoonDiffVendor']}")
    print(f"Items needing zone/vendor UPDATE:    {summary['needsUpdate']}")

    if overrides:
        print(f"\n{'decorID':<10} {'Item Name':<40} {'New Vendor':<25} {'Old Vendor':<25} {'Old Zone'}")
        print("-" * 130)
        for decor_id, data in sorted(overrides.items(), key=lambda x: x[1].get("itemName", "")):
            print(f"  {decor_id:<8} {data['itemName']:<40} "
                  f"{data['vendorName']:<25} "
                  f"{(data.get('originalVendor') or 'N/A'):<25} "
                  f"{data.get('originalZone') or 'N/A'}")

    print(f"\nOutput: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
