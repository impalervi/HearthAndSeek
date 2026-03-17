#!/usr/bin/env python3
"""
enrich_wowhead_extra.py - One-pass Wowhead scan to collect additional data
for each decor item: alternative sources, drop rates, profession skill
requirements, patch added, and vendor buy costs (cross-reference).

Fetches each item's Wowhead page and extracts all data in ONE pass per item.

Input:  data/enriched_catalog.json
Output: data/enriched_catalog_extra.json

All responses are cached in data/wowhead_cache/ so re-runs skip previously
fetched items.

Usage:
    python enrich_wowhead_extra.py
    python enrich_wowhead_extra.py --limit 10     # process only first 10 items
    python enrich_wowhead_extra.py --force         # re-fetch cached items
"""

import argparse
import json
import logging
import re
import sys
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
INPUT_FILE = DATA_DIR / "enriched_catalog.json"
OUTPUT_FILE = DATA_DIR / "enriched_catalog_extra.json"

REQUEST_DELAY = 2.0  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_wowhead_extra")

# ---------------------------------------------------------------------------
# Wowhead sourcemore type mapping
# ---------------------------------------------------------------------------
SOURCEMORE_TYPE_MAP = {
    1: "NPC",         # NPC (vendor or drop)
    2: "Treasure",    # Object / treasure chest
    4: "Container",   # Container (lockbox, etc.)
    5: "Quest",       # Quest reward
    6: "Profession",  # Spell/recipe (profession craft)
    10: "Starter",    # Starter gear
    12: "Item",       # Item (created from another item)
}


# ---------------------------------------------------------------------------
# Cache helpers (reuse pattern from enrich_catalog.py)
# ---------------------------------------------------------------------------

def _cache_key(item_id: int) -> str:
    return f"item_extra_{item_id}.json"


def cache_get(item_id: int) -> Optional[dict]:
    path = CACHE_DIR / _cache_key(item_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_put(item_id: int, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(item_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get(url: str) -> Optional[str]:
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

        if resp.status_code == 404:
            logger.debug("404 for %s", url)
            return None

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.text

    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Listview data extraction (reused from enrich_catalog.py)
# ---------------------------------------------------------------------------

def _extract_listview_data(html: str, listview_id: str) -> list[dict]:
    """Extract data array from a Wowhead Listview block with the given id."""
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


# ---------------------------------------------------------------------------
# Data extractors
# ---------------------------------------------------------------------------

def extract_sourcemore(html: str) -> list[dict]:
    """
    Extract sourcemore array from Wowhead page JavaScript.

    Wowhead embeds source data in:
      WH.Gatherer.addData(ITEM_TYPE, ID, {"sourcemore":[...],...});
    or in g_items[ID] = {"sourcemore":[...],...};
    """
    sources = []
    seen = set()

    # Pattern 1: WH.Gatherer.addData blocks
    for m in re.finditer(r'"sourcemore"\s*:\s*\[([^\]]*)\]', html):
        try:
            arr = json.loads("[" + m.group(1) + "]")
            for entry in arr:
                if isinstance(entry, dict):
                    t = entry.get("t")
                    ti = entry.get("ti")  # type instance ID (npcID, questID, etc.)
                    n = entry.get("n")    # name
                    source_type = SOURCEMORE_TYPE_MAP.get(t, f"Unknown({t})")
                    key = (source_type, ti or n or "")
                    if key not in seen:
                        seen.add(key)
                        src = {"sourceType": source_type}
                        if n:
                            src["sourceDetail"] = n
                        if ti:
                            src["sourceID"] = ti
                        sources.append(src)
        except (json.JSONDecodeError, TypeError):
            continue

    return sources


def extract_drop_rate(html: str) -> Optional[float]:
    """
    Extract drop rate from the 'dropped-by' Listview block.

    Each NPC entry has 'count' and 'outof' fields. Take the highest drop rate.
    Some entries have 'percentOverride' instead.
    """
    entries = _extract_listview_data(html, "dropped-by")
    if not entries:
        return None

    best_rate = 0.0
    for entry in entries:
        percent = entry.get("percentOverride")
        if percent is not None and isinstance(percent, (int, float)):
            best_rate = max(best_rate, float(percent))
            continue

        count = entry.get("count", 0)
        outof = entry.get("outof", 0)
        if outof > 0 and count > 0:
            rate = (count / outof) * 100
            best_rate = max(best_rate, rate)

    return round(best_rate, 2) if best_rate > 0 else None


def extract_profession_skill(html: str) -> Optional[str]:
    """
    Extract profession skill from the 'created-by' Listview block.

    Each spell entry has 'name' (spell name) and 'learnedat' (skill level).
    Format: "Inscription (50)"
    """
    entries = _extract_listview_data(html, "created-by")
    if not entries:
        return None

    for entry in entries:
        name = entry.get("name", "")
        learnedat = entry.get("learnedat", 0)
        if name and learnedat:
            return f"{name} ({learnedat})"
        elif name:
            return name

    return None


def extract_vendor_costs(html: str) -> list[dict]:
    """
    Extract vendor buy costs from the 'sold-by' Listview block.

    Each NPC entry has a 'cost' field:
      [goldInCopper, [[currencyID, amount], ...]]
    or just [goldInCopper] when gold-only.
    """
    entries = _extract_listview_data(html, "sold-by")
    if not entries:
        return []

    # Take the first vendor's cost (they should all be the same)
    for entry in entries:
        cost = entry.get("cost")
        if not cost or not isinstance(cost, list):
            continue

        costs = []
        gold_copper = cost[0] if len(cost) > 0 else 0
        if gold_copper and isinstance(gold_copper, (int, float)) and gold_copper > 0:
            # Convert copper to gold
            gold = gold_copper / 10000
            costs.append({
                "amount": int(gold) if gold == int(gold) else gold,
                "currencyID": 0,  # 0 = gold
            })

        if len(cost) > 1 and isinstance(cost[1], list):
            for currency_entry in cost[1]:
                if isinstance(currency_entry, list) and len(currency_entry) >= 2:
                    costs.append({
                        "currencyID": currency_entry[0],
                        "amount": currency_entry[1],
                    })

        if costs:
            return costs

    return []


def extract_patch_added(html: str) -> Optional[str]:
    """
    Extract the patch version when the item was added.

    Wowhead embeds this in:
      "added":"11.1.0" or added: "11.1.0"
    Or in the tooltip as "Added in patch X.Y.Z"
    """
    # Pattern 1: JSON field "added":"X.Y.Z"
    m = re.search(r'"added"\s*:\s*"(\d+\.\d+\.\d+)"', html)
    if m:
        return m.group(1)

    # Pattern 2: data-added="XXXXX" (build number, not useful)
    # Pattern 3: "Added in patch X.Y.Z" in tooltip text
    m = re.search(r'Added in patch (\d+\.\d+\.\d+)', html)
    if m:
        return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_item(item_id: int, force: bool = False) -> Optional[dict]:
    """Fetch and extract all extra data for a single item."""
    if not force:
        cached = cache_get(item_id)
        if cached is not None:
            return cached

    url = f"https://www.wowhead.com/item={item_id}"
    html = _rate_limited_get(url)
    if not html:
        result = {"itemID": item_id, "error": "fetch_failed"}
        cache_put(item_id, result)
        return result

    result = {
        "itemID": item_id,
        "additionalSources": extract_sourcemore(html),
        "dropRate": extract_drop_rate(html),
        "professionSkill": extract_profession_skill(html),
        "wowheadVendorCosts": extract_vendor_costs(html),
        "patchAdded": extract_patch_added(html),
    }

    cache_put(item_id, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-pass Wowhead scan for extra item data.",
    )
    parser.add_argument(
        "--limit", "-l", type=int, default=0,
        help="Process only the first N items (0 = all).",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Re-fetch items even if cached.",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="Skip the first N items.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_FILE.exists():
        logger.error("Input file not found: %s", INPUT_FILE)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    logger.info("Loaded %d items from %s", len(catalog), INPUT_FILE)

    # Filter to items with valid itemIDs
    items_to_process = [
        item for item in catalog
        if item.get("itemID") and item["itemID"] > 0
    ]
    logger.info("Items with valid itemID: %d", len(items_to_process))

    if args.offset:
        items_to_process = items_to_process[args.offset:]
        logger.info("Skipping first %d items, %d remaining", args.offset, len(items_to_process))

    if args.limit:
        items_to_process = items_to_process[:args.limit]
        logger.info("Processing first %d items only", len(items_to_process))

    # Check how many are already cached
    cached_count = sum(1 for item in items_to_process if cache_get(item["itemID"]) is not None)
    need_fetch = len(items_to_process) - cached_count if not args.force else len(items_to_process)
    logger.info("Already cached: %d, Need to fetch: %d", cached_count, need_fetch)
    if need_fetch > 0:
        est_time = need_fetch * REQUEST_DELAY
        logger.info("Estimated time: %.0f seconds (%.1f minutes)", est_time, est_time / 60)

    # Process items
    results: dict[int, dict] = {}
    stats = {
        "total": len(items_to_process),
        "cached": 0,
        "fetched": 0,
        "errors": 0,
        "with_sources": 0,
        "with_drop_rate": 0,
        "with_profession": 0,
        "with_vendor_cost": 0,
        "with_patch": 0,
    }

    for i, item in enumerate(items_to_process, 1):
        item_id = item["itemID"]
        decor_id = item.get("decorID", "?")

        # Check cache first
        if not args.force:
            cached = cache_get(item_id)
            if cached is not None:
                results[item_id] = cached
                stats["cached"] += 1
                # Tally stats from cache
                if cached.get("additionalSources"):
                    stats["with_sources"] += 1
                if cached.get("dropRate"):
                    stats["with_drop_rate"] += 1
                if cached.get("professionSkill"):
                    stats["with_profession"] += 1
                if cached.get("wowheadVendorCosts"):
                    stats["with_vendor_cost"] += 1
                if cached.get("patchAdded"):
                    stats["with_patch"] += 1
                continue

        logger.info("[%d/%d] Fetching itemID=%d (decorID=%s) %s",
                    i, stats["total"], item_id, decor_id, item.get("name", ""))

        result = process_item(item_id, force=args.force)

        if result and not result.get("error"):
            results[item_id] = result
            stats["fetched"] += 1
            if result.get("additionalSources"):
                stats["with_sources"] += 1
            if result.get("dropRate"):
                stats["with_drop_rate"] += 1
            if result.get("professionSkill"):
                stats["with_profession"] += 1
            if result.get("wowheadVendorCosts"):
                stats["with_vendor_cost"] += 1
            if result.get("patchAdded"):
                stats["with_patch"] += 1
        else:
            stats["errors"] += 1

    # Write merged output
    output = {
        "metadata": {
            "total_items": stats["total"],
            "items_fetched": stats["fetched"],
            "items_cached": stats["cached"],
            "items_errored": stats["errors"],
        },
        "items": results,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("=" * 60)
    logger.info("ENRICHMENT EXTRA SUMMARY")
    logger.info("=" * 60)
    logger.info("Total items:          %d", stats["total"])
    logger.info("Fetched from Wowhead: %d", stats["fetched"])
    logger.info("Loaded from cache:    %d", stats["cached"])
    logger.info("Errors:               %d", stats["errors"])
    logger.info("With alt sources:     %d", stats["with_sources"])
    logger.info("With drop rate:       %d", stats["with_drop_rate"])
    logger.info("With profession:      %d", stats["with_profession"])
    logger.info("With vendor costs:    %d", stats["with_vendor_cost"])
    logger.info("With patch added:     %d", stats["with_patch"])
    logger.info("")
    logger.info("Output: %s", OUTPUT_FILE)

    # Stamp cache metadata (non-fatal if it fails)
    try:
        from pipeline_metadata import stamp_after_scrape
        wh_files = len([f for f in CACHE_DIR.iterdir() if f.suffix == ".json" and f.name != "_metadata.json"])
        stamp_after_scrape(CACHE_DIR, source="wowhead", total_files=wh_files)
    except Exception as exc:
        logger.warning("Failed to update cache metadata: %s", exc)


if __name__ == "__main__":
    main()
