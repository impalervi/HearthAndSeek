"""
enrich_faction_quests.py - Discover cross-faction quest chains for decor items.

Some decor items (e.g. Gnomish Sprocket Table) have different quest chains
depending on the player's faction. This script discovers both faction's quests
by scraping Wowhead decor pages and parsing the structured "sources" data.

The Wowhead decor page contains a JSON sources array:
    "sources": [
        {"sourceType":4, "entityType":5, "entityId":54992, "name":"...", "starter":{...}},
        {"sourceType":4, "entityType":5, "entityId":55651, "name":"...", "starter":{...}}
    ]

sourceType=4 means Quest, entityType=5 means Quest entity.

For items currently marked as "horde" or "alliance" with a questID, if the
Wowhead page has quest sources for BOTH factions, the item should be "neutral"
with factionQuestChains.

Input:  data/enriched_catalog.json
Output: data/faction_quest_overrides.json

Usage:
    python enrich_faction_quests.py [--force]
"""

import argparse
import json
import hashlib
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
INPUT_FILE = DATA_DIR / "enriched_catalog.json"
OUTPUT_FILE = DATA_DIR / "faction_quest_overrides.json"

REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_faction_quests")


# ---------------------------------------------------------------------------
# Manual overrides for items where Wowhead page parsing doesn't work
# ---------------------------------------------------------------------------

# decorID -> { "Alliance": { "questID": int, "questName": str },
#              "Horde":    { "questID": int, "questName": str } }
MANUAL_OVERRIDES: dict[int, dict] = {
    # Add entries here if automatic discovery fails for specific items
}


# ---------------------------------------------------------------------------
# Known Horde/Alliance zone associations for faction inference
# ---------------------------------------------------------------------------

HORDE_ZONES = {
    "zuldazar", "dazar'alor", "orgrimmar", "thunder bluff", "undercity",
    "silvermoon city", "nazmir", "vol'dun", "frostwall", "frostfire ridge",
}

ALLIANCE_ZONES = {
    "stormwind city", "ironforge", "darnassus", "the exodar", "boralus",
    "tiragarde sound", "stormsong valley", "drustvar", "lunarfall",
    "shadowmoon valley",
}


def _infer_faction_from_zone(zone_name: str) -> Optional[str]:
    """Infer faction from a zone name."""
    if not zone_name:
        return None
    lower = zone_name.lower()
    if lower in HORDE_ZONES:
        return "Horde"
    if lower in ALLIANCE_ZONES:
        return "Alliance"
    return None


# ---------------------------------------------------------------------------
# Cache helpers
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
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            _last_request_time = time.time()

            if resp.status_code in (429, 403):
                backoff = 30 * (attempt + 1)
                if attempt < max_retries - 1:
                    logger.warning(
                        "HTTP %d for %s. Waiting %ds...",
                        resp.status_code, url, backoff,
                    )
                    time.sleep(backoff)
                    continue
                return None

            if resp.status_code in (404, 302):
                logger.debug("Not found: %s", url)
                return None

            if resp.status_code != 200:
                logger.warning("HTTP %d for %s", resp.status_code, url)
                return None

            return resp.text

        except requests.RequestException as exc:
            logger.error("Request failed: %s: %s", url, exc)
            return None

    return None


# ---------------------------------------------------------------------------
# Wowhead decor page parsing
# ---------------------------------------------------------------------------

def _name_to_slug(name: str) -> str:
    """Convert an item name to a Wowhead URL slug."""
    slug = name.lower()
    slug = re.sub(r"['\"]", "", slug)
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug


def _extract_sources(html: str) -> Optional[list]:
    """
    Extract the sources array from a Wowhead decor page.

    The page contains JavaScript like:
        "sources":[{...},{...}]
    within a larger data object.
    """
    # Try to find the sources array in the page data
    match = re.search(r'"sources"\s*:\s*(\[.*?\])\s*[,}]', html, re.DOTALL)
    if not match:
        return None

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        pass

    # Try a more lenient extraction: find balanced brackets
    start = match.start(1)
    depth = 0
    end = start
    for i in range(start, min(start + 10000, len(html))):
        if html[i] == '[':
            depth += 1
        elif html[i] == ']':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    if end > start:
        try:
            return json.loads(html[start:end])
        except json.JSONDecodeError:
            pass

    return None


def _get_quest_sources(sources: list) -> list[dict]:
    """Filter sources to quest entries only (sourceType=4, entityType=5)."""
    quests = []
    for src in sources:
        if src.get("sourceType") == 4 and src.get("entityType") == 5:
            quest = {
                "questID": src.get("entityId"),
                "questName": src.get("name"),
            }
            # Try to infer faction from starter zone
            starter = src.get("starter", {})
            area = starter.get("area", {})
            zone_name = area.get("name", "")
            faction = _infer_faction_from_zone(zone_name)
            if faction:
                quest["faction"] = faction
                quest["starterZone"] = zone_name
            quests.append(quest)
    return quests


def fetch_decor_sources(decor_id: int, name: str, force: bool = False) -> Optional[list]:
    """
    Fetch quest sources for a decor item from Wowhead.

    Returns list of quest source dicts, or None.
    """
    cache_name = str(decor_id)

    if not force:
        cached = cache_get("decor_sources", cache_name)
        if cached is not None:
            return cached

    slug = _name_to_slug(name)
    url = f"https://www.wowhead.com/decor/{slug}-{decor_id}"
    html = _rate_limited_get(url)

    if not html:
        cache_put("decor_sources", cache_name, [])
        return []

    sources = _extract_sources(html)
    if not sources:
        logger.debug("No sources found for decor %d (%s)", decor_id, name)
        cache_put("decor_sources", cache_name, [])
        return []

    quest_sources = _get_quest_sources(sources)
    cache_put("decor_sources", cache_name, quest_sources)
    return quest_sources


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Discover cross-faction quest chains for HearthAndSeek items.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch all decor pages, ignoring cache",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_FILE.exists():
        logger.error("Input not found: %s", INPUT_FILE)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Find items with faction-specific quests
    candidates = []
    for item in catalog:
        faction = item.get("faction")
        quest_id = item.get("questID")
        if faction in ("horde", "alliance") and quest_id:
            candidates.append(item)

    logger.info("Found %d items with faction-specific quests", len(candidates))

    # Process each candidate
    overrides: dict[str, dict] = {}
    fetched = 0
    cross_faction = 0
    single_faction = 0
    manual = 0

    for item in candidates:
        decor_id = item["decorID"]
        name = item["name"]
        known_quest_id = item["questID"]
        known_faction = item["faction"].capitalize()  # "horde" → "Horde"
        other_faction = "Alliance" if known_faction == "Horde" else "Horde"

        # Check manual overrides first
        if decor_id in MANUAL_OVERRIDES:
            overrides[str(decor_id)] = {
                "decorID": decor_id,
                "name": name,
                **MANUAL_OVERRIDES[decor_id],
            }
            manual += 1
            logger.info("  [MANUAL] %s (decorID %d)", name, decor_id)
            continue

        # Fetch decor page
        quest_sources = fetch_decor_sources(decor_id, name, force=args.force)
        fetched += 1

        if not quest_sources or len(quest_sources) < 2:
            single_faction += 1
            logger.info("  [SINGLE] %s (decorID %d) — %d quest source(s)",
                         name, decor_id, len(quest_sources) if quest_sources else 0)
            continue

        # Multiple quest sources — find cross-faction quest
        other_quest = None
        for qs in quest_sources:
            if qs["questID"] != known_quest_id:
                # Verify this is actually the other faction
                if qs.get("faction") == other_faction:
                    other_quest = qs
                    break
                elif not qs.get("faction"):
                    # Can't determine faction from zone — assume it's the other one
                    other_quest = qs
                    break

        if other_quest:
            cross_faction += 1
            override_entry = {
                "decorID": decor_id,
                "name": name,
                known_faction: {
                    "questID": known_quest_id,
                    "questName": item.get("quest", ""),
                },
                other_faction: {
                    "questID": other_quest["questID"],
                    "questName": other_quest.get("questName", ""),
                },
            }
            overrides[str(decor_id)] = override_entry
            logger.info(
                "  [CROSS]  %s (decorID %d): %s quest %d, %s quest %d",
                name, decor_id,
                known_faction, known_quest_id,
                other_faction, other_quest["questID"],
            )
        else:
            single_faction += 1
            logger.info("  [SINGLE] %s (decorID %d) — no cross-faction quest found",
                         name, decor_id)

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("FACTION QUEST DISCOVERY SUMMARY")
    logger.info("=" * 60)
    logger.info("Candidates:         %d", len(candidates))
    logger.info("  Cross-faction:    %d", cross_faction)
    logger.info("  Single-faction:   %d", single_faction)
    logger.info("  Manual overrides: %d", manual)
    logger.info("  Pages fetched:    %d", fetched)

    # Write output
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(overrides, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("Output: %s (%d entries)", OUTPUT_FILE, len(overrides))

    if overrides:
        logger.info("")
        logger.info("Cross-faction items found:")
        for entry in overrides.values():
            logger.info("  %s (decorID %d):", entry["name"], entry["decorID"])
            for faction in ("Alliance", "Horde"):
                if faction in entry:
                    fq = entry[faction]
                    logger.info("    %s: quest %d (%s)",
                                faction, fq["questID"], fq.get("questName", ""))


if __name__ == "__main__":
    main()
