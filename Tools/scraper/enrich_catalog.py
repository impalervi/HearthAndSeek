"""
enrich_catalog.py - Cross-reference catalog_dump.json with Wowhead to fill data gaps.

Resolves:
  1. Quest name -> questID  (via Wowhead search page, parsing embedded JSON data)
  2. Vendor name -> npcID + coordinates  (via search page + NPC tooltip API)
  3. Item name -> itemID verification  (for any entries with missing itemID)

Special handling:
  - "Decor Treasure Hunt" quests: 100+ variants with the same name but different
    item rewards. Matched by cross-referencing itemID from catalog_dump with the
    quest's itemrewards array on Wowhead.
  - Generic vendor names (e.g. "World Vendors") are skipped.

Uses existing local data from wowhead_vanilla.json and merged_decor.json as a
starting point, then queries Wowhead only for what's still missing.

All Wowhead responses are cached in data/wowhead_cache/ so re-runs skip
previously-fetched queries.

Output: data/enriched_catalog.json
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
from urllib.parse import quote

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
INPUT_FILE = DATA_DIR / "catalog_dump.json"
OUTPUT_FILE = DATA_DIR / "enriched_catalog.json"
OVERRIDES_FILE = DATA_DIR / "overrides.json"

# Existing data files to seed from
WOWHEAD_VANILLA_FILE = DATA_DIR / "wowhead_vanilla.json"
MERGED_DECOR_FILE = DATA_DIR / "merged_decor.json"

# Rate limiting: pause between Wowhead requests (in seconds)
REQUEST_DELAY = 2.0

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

# Generic / placeholder vendor names that should not be looked up on Wowhead
SKIP_VENDORS = {
    "Draenor World Vendors",
    "Eastern Kingdoms World Vendors",
    "World Vendors",
}

# NPC ID overrides for vendors where Wowhead search returns the wrong NPC.
# This happens when an NPC was recreated with a new ID in a later expansion
# (e.g., TBC Quel'Thalas NPCs replaced by Midnight versions) and Wowhead
# returns the old NPC first.
# Format: vendor_name → { npc_id, coords (optional), whZoneID (optional) }
NPC_OVERRIDES: dict[str, dict] = {
    # Midnight Quel'Thalas vendors (old TBC NPCs replaced by new Midnight NPCs)
    "Sathren Azuredawn": {
        "npc_id": 259864,       # was 16191 (TBC), now 259864 (Midnight)
        "coords": {"x": 43.2, "y": 47.4},
        "whZoneID": 15968,      # Midnight Eversong Woods
    },
    "Irodalmin": {
        "npc_id": 256026,       # was 16630 (TBC), now 256026 (Midnight)
        "coords": {"x": 48.2, "y": 51.6},
        "whZoneID": 16215,      # Midnight Silvermoon City
    },
}

# Wowhead zone ID → in-game zone name corrections.
# The in-game catalog dump often reports a broad zone (e.g., "Zuldazar") when the
# vendor is actually in a specific sub-city (e.g., "Dazar'alor"). Wowhead's NPC
# tooltip provides a more granular zone ID. This table maps those IDs to the
# correct zone name so the addon displays accurate locations.
WOWHEAD_ZONE_CORRECTIONS: dict[int, str] = {
    8670: "Dazar'alor",     # Dazar'alor (main city area)
    9598: "Dazar'alor",     # Dazar'alor — Terrace of Crafters / Great Seal area
}

# Wowhead zone ID → zone name mapping.
# Built from enrichment data to enable zone-aware NPC disambiguation and
# coordinate mismatch detection.  Multiple WH zone IDs can map to the same
# zone (sub-zones, phases, revisions).  For IDs that appear with multiple zone
# names, the majority-vote winner is used.
WH_ZONE_ID_TO_NAME: dict[int, str] = {
    # Eastern Kingdoms
    4: "Blasted Lands",
    10: "Duskwood",
    27: "Dun Morogh",
    33: "Northern Stranglethorn",
    38: "Loch Modan",
    46: "Burning Steppes",
    51: "Searing Gorge",
    56: "Wetlands",
    84: "Stormwind City",
    87: "Dun Morogh",
    89: "Teldrassil",
    95: "Ghostlands",
    130: "Silverpine Forest",
    210: "Eastern Plaguelands",
    217: "Ruins of Gilneas",
    1519: "Stormwind City",
    1584: "Blackrock Depths",
    2257: "Deeprun Tram",
    4298: "Acherus: The Ebon Hold",
    4922: "Twilight Highlands",
    # Kalimdor
    361: "Felwood",
    1637: "Orgrimmar",
    # Northrend
    394: "Grizzly Hills",
    440: "Dalaran",
    3537: "Borean Tundra",
    3711: "Sholazar Basin",
    # Pandaria
    5785: "The Jade Forest",
    5805: "Valley of the Four Winds",
    5840: "Vale of Eternal Blossoms",
    5841: "Kun-Lai Summit",
    # Draenor
    6662: "Talador",
    6719: "Shadowmoon Valley",
    6722: "Spires of Arak",
    7004: "Frostwall",
    7078: "Lunarfall",
    7332: "Stormshield",
    7333: "Stormshield",
    # Legion
    7334: "Mardum, the Shattered Abyss",
    7502: "Dalaran Sewers",
    7503: "Highmountain",
    7558: "Val'sharah",
    7637: "Suramar",
    7731: "Highmountain",
    7745: "The Maelstrom",
    7813: "Skyhold",
    7834: "Netherlight Temple",
    7846: "The Dreamgrove",
    7875: "Dreadscar Rift",
    7877: "Trueshot Lodge",
    7879: "Hall of the Guardian",
    7902: "The Wandering Isle",
    8899: "Antoran Wastes",
    # Battle for Azeroth
    8500: "Nazmir",
    8567: "Tiragarde Sound",
    8568: "Tiragarde Sound",
    8670: "Dazar'alor",
    9042: "Stormsong Valley",
    9598: "Dazar'alor",
    # Shadowlands
    10986: "Revendreth",
    11400: "The Maw",
    # Amirdrassil / misc
    1657: "Amirdrassil",
    # Dragonflight
    1537: "Dun Morogh",
    4755: "Thaldraszus",
    13644: "The Waking Shores",
    13645: "Silvermoon City",
    13844: "Thaldraszus",
    13862: "Valdrakken",
    14433: "The Forbidden Reach",
    # The War Within
    14717: "Isle of Dorn",
    14753: "City of Threads",
    14771: "Dornogal",
    14795: "The Ringing Deeps",
    14838: "Hallowfall",
    # Midnight
    15347: "Undermine",
    15355: "Harandar",
    15458: "Voidstorm",
    15781: "K'aresh",
    15947: "Zul'Aman",
    15958: "Masters' Perch",
    15968: "Eversong Woods",
    15969: "Silvermoon City",
    16105: "Founder's Point",
    16215: "Silvermoon City",
    # Housing
    10290: "Founder's Point",
    14022: "Founder's Point",
    15524: "Razorwind Shores",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_catalog")


# ---------------------------------------------------------------------------
# Faction helpers
# ---------------------------------------------------------------------------

# Wowhead zone IDs where NPC react data is stale or misleading — all NPCs
# in these zones should be treated as cross-faction (neutral).
CROSS_FACTION_ZONES = {
    # Midnight-revamped Quel'Thalas (was Horde-only, now both factions)
    3487,    # Eversong Woods / Silvermoon (old Wowhead zone ID)
    15968,   # Eversong Woods (Midnight Wowhead zone ID)
    15969,   # Ghostlands (Midnight Wowhead zone ID)
    16215,   # Silvermoon City (Midnight Wowhead zone ID)
    # Dalaran (neutral city, accessible to both factions)
    504,     # Dalaran (WotLK)
    7502,    # Dalaran (Legion)
    # Legion Class Halls (accessible to both factions for that class)
    7875,    # Dreadscar Rift (Warlock)
    7879,    # Hall of the Guardian (Mage)
}

# Specific NPC IDs that should always be treated as neutral, even when their
# Wowhead react data or zone suggests otherwise.
CROSS_FACTION_NPCS = {
    38535,   # Kelsey Steelspark — Rogue class hall vendor (in Dalaran zone)
    86037,   # Ravenspeaker Skeega — Arakkoa Outcasts rep (neutral faction)
}


def _react_to_faction(react) -> Optional[str]:
    """
    Convert Wowhead NPC 'react' field to a faction string.

    Wowhead react format: [alliance_reaction, horde_reaction]
      1  = friendly
      -1 = hostile
      0  = neutral

    Returns: "alliance", "horde", "neutral", or None if undetermined.
    """
    if not react or not isinstance(react, list) or len(react) < 2:
        return None
    a_react, h_react = react[0], react[1]
    if a_react == 1 and h_react == 1:
        return "neutral"
    elif a_react == 1 and h_react != 1:
        return "alliance"
    elif h_react == 1 and a_react != 1:
        return "horde"
    return "neutral"  # default to neutral if both are 0 or unknown


def _side_to_faction(side) -> Optional[str]:
    """
    Convert Wowhead quest 'side' field to a faction string.

    Wowhead side: 1 = Alliance, 2 = Horde, 3 = Both
    """
    if side == 1:
        return "alliance"
    elif side == 2:
        return "horde"
    elif side == 3:
        return "neutral"
    return None


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_key(prefix: str, name: str) -> str:
    """Generate a safe filename for a cache entry."""
    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{prefix}_{readable}_{safe}.json"


def cache_get(prefix: str, name: str) -> Optional[Any]:
    """Read a cached response, or return None if not cached."""
    path = CACHE_DIR / _cache_key(prefix, name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_put(prefix: str, name: str, data: Any) -> None:
    """Write a response to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(prefix, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def cache_delete(prefix: str, name: str) -> bool:
    """Delete a cached response. Returns True if the file existed."""
    path = CACHE_DIR / _cache_key(prefix, name)
    if path.exists():
        path.unlink()
        return True
    return False


def invalidate_null_coord_caches() -> int:
    """
    Remove cached NPC tooltip/page/full entries that have null coordinates,
    so the next enrichment run will re-fetch them from Wowhead.

    Returns the number of cache entries invalidated.
    """
    invalidated = 0
    if not CACHE_DIR.exists():
        return 0

    for path in CACHE_DIR.iterdir():
        if not path.name.endswith(".json"):
            continue
        prefix = path.name.split("_")[0]
        # Only invalidate NPC coordinate caches
        if prefix not in ("npc", ):
            # Check for npc_tooltip_, npc_page_coords_, npc_full_ prefixes
            if not any(path.name.startswith(p) for p in
                       ("npc_tooltip_", "npc_page_coords_", "npc_full_")):
                continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Invalidate if coords are null/missing
            if isinstance(data, dict):
                coords = data.get("coords")
                if coords is None:
                    path.unlink()
                    invalidated += 1
        except (json.JSONDecodeError, OSError):
            continue

    return invalidated


# ---------------------------------------------------------------------------
# Overrides: manual corrections for items that the pipeline gets wrong
# ---------------------------------------------------------------------------

def load_overrides() -> dict:
    """
    Load manual overrides from data/overrides.json.

    Format: { "<decorID>": { "field": "value", ... }, ... }
    Supported fields: sourceType, achievementName, npcX, npcY, zone,
                      vendorName, questID, faction, npcID
    """
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        with open(OVERRIDES_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            logger.warning("overrides.json must be a JSON object, got %s", type(raw).__name__)
            return {}
        logger.info("Loaded %d item overrides from overrides.json", len(raw))
        return raw
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not load overrides.json: %s", exc)
        return {}


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get(url: str, expect_json: bool = False) -> Optional[Any]:
    """
    Fetch a URL with rate limiting and error handling.
    Returns parsed JSON if expect_json=True, else raw response text.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        _last_request_time = time.time()

        # Exponential backoff on rate limit (403/429)
        if resp.status_code in (429, 403):
            for attempt, delay in enumerate((30, 60, 120), 1):
                logger.warning("Rate limited (%d). Retry %d/3 in %ds...",
                               resp.status_code, attempt, delay)
                time.sleep(delay)
                resp = requests.get(url, headers=HEADERS, timeout=20)
                _last_request_time = time.time()
                if resp.status_code not in (429, 403):
                    break

        if resp.status_code in (429, 403):
            # Still blocked after 3 retries — return sentinel so caller can
            # distinguish "blocked" from "not found" and avoid caching.
            logger.warning("Still blocked (%d) after 3 retries for %s",
                           resp.status_code, url)
            return "__BLOCKED__"

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        if expect_json:
            return resp.json()
        return resp.text

    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None
    except json.JSONDecodeError:
        logger.warning("Invalid JSON from %s", url)
        return None


# ---------------------------------------------------------------------------
# Wowhead search page parser
# ---------------------------------------------------------------------------

def _extract_json_arrays_from_html(html: str) -> list[list[dict]]:
    """
    Extract JSON arrays embedded in <script> tags on a Wowhead page.

    Wowhead embeds search result data as raw JSON arrays in script blocks.
    Each array contains objects with 'id' and 'name' fields (among others).
    Quest results have 'category' fields; NPC results have 'type' fields;
    item results have 'classs' fields.
    """
    arrays = []
    script_blocks = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    for block in script_blocks:
        stripped = block.strip()
        if stripped.startswith('[{') and '"id"' in stripped and '"name"' in stripped:
            try:
                data = json.loads(stripped)
                if data and isinstance(data, list) and isinstance(data[0], dict):
                    arrays.append(data)
            except json.JSONDecodeError:
                pass
    return arrays


def _search_wowhead(query: str):
    """
    Run a search on Wowhead and return all embedded JSON data arrays.
    Uses the main search page: https://www.wowhead.com/search?q=QUERY
    Returns list of arrays on success, "__BLOCKED__" on rate limit, [] on error.
    """
    url = f"https://www.wowhead.com/search?q={quote(query)}"
    html = _rate_limited_get(url, expect_json=False)
    if html == "__BLOCKED__":
        return "__BLOCKED__"
    if not html:
        return []
    return _extract_json_arrays_from_html(html)


# ---------------------------------------------------------------------------
# Seed data from existing local files
# ---------------------------------------------------------------------------

def load_seed_data() -> tuple[dict[str, int], dict[str, dict]]:
    """
    Load quest and NPC mappings from existing wowhead_vanilla.json and
    merged_decor.json files.

    Returns:
        quest_map:  {quest_name: questID}  (for non-duplicate quest names)
        npc_map:    {npc_name: {npc_id, coords, mapID}}
    """
    quest_map: dict[str, int] = {}
    npc_map: dict[str, dict] = {}

    # From wowhead_vanilla.json
    if WOWHEAD_VANILLA_FILE.exists():
        try:
            with open(WOWHEAD_VANILLA_FILE, "r", encoding="utf-8") as f:
                wh = json.load(f)
            for q in wh.get("quests", []):
                if q.get("quest_name") and q.get("quest_id"):
                    quest_map[q["quest_name"]] = q["quest_id"]
            for n in wh.get("npcs", []):
                if n.get("npc_name") and n.get("npc_id"):
                    npc_map[n["npc_name"]] = {
                        "npc_id": n["npc_id"],
                        "coords": n.get("coords"),
                        "mapID": n.get("mapID"),
                    }
            logger.info(
                "Seeded from wowhead_vanilla.json: %d quests, %d NPCs",
                len(quest_map), len(npc_map),
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load wowhead_vanilla.json: %s", exc)

    # From merged_decor.json
    if MERGED_DECOR_FILE.exists():
        try:
            with open(MERGED_DECOR_FILE, "r", encoding="utf-8") as f:
                merged = json.load(f)
            for item in merged:
                if item.get("vendor_npc") and item.get("npc_id"):
                    name = item["vendor_npc"]
                    if name not in npc_map:
                        npc_map[name] = {
                            "npc_id": item["npc_id"],
                            "coords": item.get("coords"),
                            "mapID": item.get("mapID"),
                        }
            logger.info(
                "After merged_decor.json seed: %d quests, %d NPCs total",
                len(quest_map), len(npc_map),
            )
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not load merged_decor.json: %s", exc)

    return quest_map, npc_map


# ---------------------------------------------------------------------------
# Quest lookup
# ---------------------------------------------------------------------------

def _is_quest_entry(entry: dict) -> bool:
    """Check if a search result entry is a quest (has category, no classs/type)."""
    return "category" in entry and "classs" not in entry and "type" not in entry


def lookup_quest_by_name(quest_name: str) -> Optional[int]:
    """
    Look up a quest ID by exact name match via Wowhead search.
    Returns the questID if a single unique match is found, or None.

    NOTE: Does NOT handle "Decor Treasure Hunt" or other duplicate-name quests.
    Those require matching by itemID and use a separate function.

    Also caches quest 'side' (faction) data for later use.
    """
    cached = cache_get("quest", quest_name)
    if cached is not None:
        # Invalidate if missing side data (old cache format) and had a result
        if cached.get("id") and "side" not in cached:
            pass  # fall through to re-fetch
        else:
            return cached.get("id")

    arrays = _search_wowhead(quest_name)
    if arrays == "__BLOCKED__":
        logger.warning("Wowhead blocked lookup for quest '%s' — skipping (not cached)", quest_name)
        return None
    if not arrays:
        cache_put("quest", quest_name, {"id": None})
        return None

    quest_lower = quest_name.lower().strip()
    matches = []

    for arr in arrays:
        for entry in arr:
            if _is_quest_entry(entry):
                entry_name = entry.get("name", "")
                if entry_name.lower().strip() == quest_lower:
                    matches.append(entry)

    if len(matches) == 1:
        quest_id = matches[0]["id"]
        side = matches[0].get("side")
        cache_put("quest", quest_name, {
            "id": quest_id,
            "name": matches[0]["name"],
            "side": side,
            "faction": _side_to_faction(side),
        })
        return quest_id
    elif len(matches) > 1:
        # Check for cross-faction pair: exactly 2 matches, one Alliance + one Horde
        sides = {m.get("side") for m in matches}
        if len(matches) == 2 and sides == {1, 2}:
            # Alliance+Horde pair — same quest, different factions. Pick first, mark neutral.
            quest_id = matches[0]["id"]
            logger.info("Cross-faction quest pair for '%s': IDs %s, marking neutral",
                        quest_name, [m["id"] for m in matches])
            cache_put("quest", quest_name, {
                "id": quest_id,
                "name": matches[0]["name"],
                "side": 3,  # treat as both factions
                "faction": "neutral",
            })
            return quest_id
        logger.debug("Multiple quest matches for '%s': %d results", quest_name, len(matches))
        cache_put("quest", quest_name, {"id": None, "ambiguous": True, "count": len(matches)})
        return None
    else:
        def _normalize(s):
            return re.sub(r'[^a-z0-9]', '', s.lower())
        target = _normalize(quest_name)
        for arr in arrays:
            for entry in arr:
                if _is_quest_entry(entry) and _normalize(entry.get("name", "")) == target:
                    quest_id = entry["id"]
                    side = entry.get("side")
                    cache_put("quest", quest_name, {
                        "id": quest_id,
                        "name": entry["name"],
                        "side": side,
                        "faction": _side_to_faction(side),
                    })
                    return quest_id

        cache_put("quest", quest_name, {"id": None})
        return None


def lookup_quest_by_item_reward(quest_name: str, item_id: int) -> Optional[int]:
    """
    Look up a quest ID by name + item reward cross-reference.

    This handles quests like "Decor Treasure Hunt" where many quests share the
    same name but reward different items. We search Wowhead for the quest name,
    then find the specific quest whose itemrewards contain our item_id.

    The full search results (all quests with the name) are cached together to
    avoid re-fetching for each item.
    """
    cache_key = f"quest_results_{quest_name}"
    cached_results = cache_get("quest_results", quest_name)

    if cached_results is None:
        # Fetch from Wowhead and cache the raw quest results
        arrays = _search_wowhead(quest_name)
        quest_lower = quest_name.lower().strip()
        all_matching_quests = []
        for arr in arrays:
            for entry in arr:
                if _is_quest_entry(entry):
                    entry_name = entry.get("name", "")
                    if entry_name.lower().strip() == quest_lower:
                        all_matching_quests.append(entry)
        cache_put("quest_results", quest_name, all_matching_quests)
        cached_results = all_matching_quests

    # Now search through results for one whose itemrewards contains our item_id
    for quest in cached_results:
        rewards = quest.get("itemrewards", [])
        # Each reward is [itemID, count]
        for reward in rewards:
            if isinstance(reward, list) and len(reward) >= 1:
                if reward[0] == item_id:
                    quest_id = quest.get("id")
                    # Also cache the per-item quest faction for later use
                    side = quest.get("side")
                    per_item_faction_key = f"{quest_name}__itemID_{item_id}__faction"
                    cache_put("quest_faction", per_item_faction_key, {
                        "side": side,
                        "faction": _side_to_faction(side),
                    })
                    return quest_id

    return None


# ---------------------------------------------------------------------------
# NPC / Vendor lookup
# ---------------------------------------------------------------------------

def _is_npc_entry(entry: dict) -> bool:
    """Check if a search result entry is an NPC."""
    return "type" in entry and "classs" not in entry and "category" not in entry


def lookup_npc_id(vendor_name: str) -> Optional[dict]:
    """
    Look up an NPC by name via Wowhead search page.
    Returns {npc_id, npc_name, react, wh_location} or None.
    """
    # Check NPC_OVERRIDES first (bypasses cache and Wowhead search)
    override = NPC_OVERRIDES.get(vendor_name)
    if override:
        result = {
            "npc_id": override["npc_id"],
            "npc_name": vendor_name,
            "wh_location": [override["whZoneID"]] if override.get("whZoneID") else None,
            "react": [1, 1],  # neutral (overridden vendors are cross-faction)
        }
        logger.info("  -> NPC_OVERRIDES: %s → npcID %d", vendor_name, override["npc_id"])
        cache_put("npc_search", vendor_name, result)
        return result

    cached = cache_get("npc_search", vendor_name)
    if cached is not None:
        if cached.get("npc_id"):
            # Invalidate cache if react data is missing (old cache format)
            if "react" not in cached:
                pass  # fall through to re-fetch
            else:
                return cached
        elif cached.get("npc_id") is None:
            return None

    arrays = _search_wowhead(vendor_name)
    if not arrays:
        cache_put("npc_search", vendor_name, {"npc_id": None})
        return None

    vendor_lower = vendor_name.lower().strip()

    def _build_result(entry):
        return {
            "npc_id": entry["id"],
            "npc_name": entry["name"],
            "wh_location": entry.get("location"),
            "react": entry.get("react"),  # [A_react, H_react]: 1=friendly, -1=hostile
        }

    # Exact match first
    for arr in arrays:
        for entry in arr:
            if _is_npc_entry(entry):
                if entry.get("name", "").lower().strip() == vendor_lower:
                    result = _build_result(entry)
                    cache_put("npc_search", vendor_name, result)
                    return result

    # Normalized fallback
    def _normalize(s):
        return re.sub(r'[^a-z0-9]', '', s.lower())
    target = _normalize(vendor_name)
    for arr in arrays:
        for entry in arr:
            if _is_npc_entry(entry) and _normalize(entry.get("name", "")) == target:
                result = _build_result(entry)
                cache_put("npc_search", vendor_name, result)
                return result

    cache_put("npc_search", vendor_name, {"npc_id": None})
    return None


def lookup_npc_id_all(vendor_name: str) -> list[dict]:
    """
    Return ALL exact NPC name matches from Wowhead search (not just the first).
    Used for zone-aware disambiguation when multiple NPCs share the same name.
    """
    # Check NPC_OVERRIDES first — if overridden, return as the sole result
    override = NPC_OVERRIDES.get(vendor_name)
    if override:
        return [{
            "npc_id": override["npc_id"],
            "npc_name": vendor_name,
            "wh_location": [override["whZoneID"]] if override.get("whZoneID") else None,
            "react": [1, 1],
        }]

    # Check cache for previous all-results lookup
    cached = cache_get("npc_search_all", vendor_name)
    if cached is not None:
        return cached

    arrays = _search_wowhead(vendor_name)
    if not arrays:
        cache_put("npc_search_all", vendor_name, [])
        return []

    vendor_lower = vendor_name.lower().strip()
    results = []
    seen_ids: set[int] = set()

    # Exact match
    for arr in arrays:
        for entry in arr:
            if _is_npc_entry(entry):
                if entry.get("name", "").lower().strip() == vendor_lower:
                    npc_id = entry["id"]
                    if npc_id not in seen_ids:
                        seen_ids.add(npc_id)
                        results.append({
                            "npc_id": npc_id,
                            "npc_name": entry["name"],
                            "wh_location": entry.get("location"),
                            "react": entry.get("react"),
                        })

    # Normalized fallback if no exact matches
    if not results:
        def _normalize(s):
            return re.sub(r'[^a-z0-9]', '', s.lower())
        target = _normalize(vendor_name)
        for arr in arrays:
            for entry in arr:
                if _is_npc_entry(entry) and _normalize(entry.get("name", "")) == target:
                    npc_id = entry["id"]
                    if npc_id not in seen_ids:
                        seen_ids.add(npc_id)
                        results.append({
                            "npc_id": npc_id,
                            "npc_name": entry["name"],
                            "wh_location": entry.get("location"),
                            "react": entry.get("react"),
                        })

    cache_put("npc_search_all", vendor_name, results)
    return results


def _pick_npc_by_zone(
    candidates: list[dict],
    hint_zones: set[str],
) -> Optional[dict]:
    """
    Disambiguate NPC candidates by checking which one's location matches
    the item's zone.  Uses WH_ZONE_ID_TO_NAME to map Wowhead location IDs
    to zone names, then picks the candidate whose location intersects hint_zones.

    Falls back to tooltip-based matching if search-result locations don't resolve.
    """
    # Pass 1: check search-result location field (no API calls needed)
    for candidate in candidates:
        wh_locs = candidate.get("wh_location") or []
        for wh_id in wh_locs:
            zone_name = WH_ZONE_ID_TO_NAME.get(wh_id)
            if zone_name and zone_name in hint_zones:
                logger.info("  Zone-aware pick (search): npcID=%d, whZone=%d -> '%s'",
                            candidate["npc_id"], wh_id, zone_name)
                return candidate

    # Pass 2: fetch tooltip for each candidate and check whZoneID
    for candidate in candidates:
        npc_id = candidate["npc_id"]
        tooltip = fetch_npc_coords(npc_id)
        if tooltip:
            wh_zone = tooltip.get("whZoneID")
            if wh_zone:
                zone_name = WH_ZONE_ID_TO_NAME.get(wh_zone)
                if zone_name and zone_name in hint_zones:
                    logger.info("  Zone-aware pick (tooltip): npcID=%d, whZone=%d -> '%s'",
                                npc_id, wh_zone, zone_name)
                    return candidate

    logger.debug("  Zone-aware pick: no candidate matched hint_zones=%s", hint_zones)
    return None


def fetch_npc_coords(npc_id: int) -> Optional[dict]:
    """
    Fetch NPC coordinates from the Wowhead tooltip API.

    API: https://nether.wowhead.com/tooltip/npc/NPCID
    Returns JSON with:
      - name: NPC name
      - map: {zone: WOWHEAD_ZONE_ID, coords: {"0": [[x1,y1], [x2,y2], ...]}}

    We take the first coordinate pair and the zone ID.
    Note: zone IDs here are Wowhead's internal IDs, not in-game mapIDs.
    """
    cached = cache_get("npc_tooltip", str(npc_id))
    if cached is not None:
        return cached if cached.get("coords") else None

    url = f"https://nether.wowhead.com/tooltip/npc/{npc_id}"
    data = _rate_limited_get(url, expect_json=True)

    if not data or not isinstance(data, dict):
        cache_put("npc_tooltip", str(npc_id), {"coords": None, "whZoneID": None})
        return None

    map_data = data.get("map")
    if not map_data or not isinstance(map_data, dict):
        cache_put("npc_tooltip", str(npc_id), {"coords": None, "whZoneID": None})
        return None

    zone_id = map_data.get("zone")
    coords_data = map_data.get("coords", {})

    # coords_data format: {"0": [[x1,y1], [x2,y2], ...]}
    # Take the first coordinate pair from phase "0"
    first_coords = None
    for phase_key, coord_pairs in coords_data.items():
        if isinstance(coord_pairs, list) and coord_pairs:
            pair = coord_pairs[0]
            if isinstance(pair, list) and len(pair) >= 2:
                first_coords = {"x": pair[0], "y": pair[1]}
                break

    result = {
        "coords": first_coords,
        "whZoneID": zone_id,
    }
    cache_put("npc_tooltip", str(npc_id), result)

    if first_coords:
        return result
    return None


def fetch_npc_coords_from_page(npc_id: int) -> Optional[dict]:
    """
    Fallback coordinate scraper: fetch the full Wowhead NPC page and extract
    coordinates from embedded JavaScript map data.

    The NPC page (https://www.wowhead.com/npc=NPCID) often has map data even
    when the tooltip API doesn't. The map data is embedded in:
      - WH.setPageData('mapper-data-...',{...}) calls
      - Or inline JSON with "coords" keys

    Returns {coords: {x, y}, whZoneID} or None.
    """
    cached = cache_get("npc_page_coords", str(npc_id))
    if cached is not None:
        return cached if cached.get("coords") else None

    url = f"https://www.wowhead.com/npc={npc_id}"
    html = _rate_limited_get(url, expect_json=False)

    if not html:
        cache_put("npc_page_coords", str(npc_id), {"coords": None, "whZoneID": None})
        return None

    # Strategy 1: Look for WH.setPageData('mapper-data-...' patterns
    # These contain mapper JSON with coordinate arrays
    mapper_matches = re.findall(
        r"WH\.setPageData\('mapper-data-[^']*',\s*(\{.*?\})\);",
        html, re.DOTALL,
    )
    for match in mapper_matches:
        try:
            mapper = json.loads(match)
            # Mapper format varies, but coords are typically in:
            # mapper.coords or mapper[zone_id].coords
            coords = _extract_coords_from_mapper(mapper)
            if coords:
                zone_id = mapper.get("zone") or mapper.get("areaId")
                result = {"coords": coords, "whZoneID": zone_id}
                cache_put("npc_page_coords", str(npc_id), result)
                return result
        except (json.JSONDecodeError, TypeError):
            continue

    # Strategy 2: Look for "coords":{"0":[[x,y]]} patterns in any script block
    coord_pattern = re.findall(
        r'"coords"\s*:\s*\{[^}]*\[\s*\[\s*([\d.]+)\s*,\s*([\d.]+)',
        html,
    )
    if coord_pattern:
        x, y = float(coord_pattern[0][0]), float(coord_pattern[0][1])
        # Also try to find zone ID nearby
        zone_match = re.search(r'"zone"\s*:\s*(\d+)', html)
        zone_id = int(zone_match.group(1)) if zone_match else None
        result = {"coords": {"x": x, "y": y}, "whZoneID": zone_id}
        cache_put("npc_page_coords", str(npc_id), result)
        return result

    # Strategy 3: Look for pin/marker patterns like data-pin="x,y"
    pin_pattern = re.findall(
        r'data-(?:pin|coords)\s*=\s*"([\d.]+)\s*,\s*([\d.]+)"',
        html,
    )
    if pin_pattern:
        x, y = float(pin_pattern[0][0]), float(pin_pattern[0][1])
        result = {"coords": {"x": x, "y": y}, "whZoneID": None}
        cache_put("npc_page_coords", str(npc_id), result)
        return result

    cache_put("npc_page_coords", str(npc_id), {"coords": None, "whZoneID": None})
    return None


def _extract_coords_from_mapper(mapper: dict) -> Optional[dict]:
    """Extract first coordinate pair from a Wowhead mapper data structure."""
    # Direct coords field
    coords_data = mapper.get("coords")
    if isinstance(coords_data, dict):
        for phase_key, coord_pairs in coords_data.items():
            if isinstance(coord_pairs, list) and coord_pairs:
                pair = coord_pairs[0]
                if isinstance(pair, list) and len(pair) >= 2:
                    return {"x": pair[0], "y": pair[1]}

    # Nested under zone ID keys
    for key, value in mapper.items():
        if isinstance(value, dict) and "coords" in value:
            coords_data = value["coords"]
            if isinstance(coords_data, dict):
                for phase_key, coord_pairs in coords_data.items():
                    if isinstance(coord_pairs, list) and coord_pairs:
                        pair = coord_pairs[0]
                        if isinstance(pair, list) and len(pair) >= 2:
                            return {"x": pair[0], "y": pair[1]}

    return None


# ---------------------------------------------------------------------------
# Wowhead item "sold-by" scraper
# ---------------------------------------------------------------------------

# Wowhead zone IDs for faction classification of Treasure Hunt vendors
WH_ZONE_ALLIANCE = {16105}  # Founder's Point
WH_ZONE_HORDE = {15524}     # Razorwind Shores


def fetch_item_quest_rewards(item_id: int) -> list[dict]:
    """
    Fetch quest reward data from a Wowhead item page.

    Wowhead embeds Listview data for quests rewarding this item:
        new Listview({template: 'quest', id: 'reward-from-q', data: [...]});

    Each quest entry has: id, name, level, reqlevel, category, etc.

    Returns a list of dicts: [{id, name, ...}, ...]
    """
    cache_key = str(item_id)
    cached = cache_get("item_questreward", cache_key)
    if cached is not None:
        return cached

    url = f"https://www.wowhead.com/item={item_id}"
    html = _rate_limited_get(url, expect_json=False)

    if html == "__BLOCKED__":
        logger.warning("Wowhead blocked item %d quest rewards — skipping (not cached)", item_id)
        return []
    if not html:
        cache_put("item_questreward", cache_key, [])
        return []

    quests = _extract_listview_data(html, "reward-from-q")

    cache_put("item_questreward", cache_key, quests)
    return quests


def fetch_item_sold_by(item_id: int) -> list[dict]:
    """
    Fetch the "sold-by" NPC list from a Wowhead item page.

    Wowhead embeds Listview data in JavaScript on item pages:
        new Listview({template: 'npc', id: 'sold-by', data: [...]});

    Each NPC entry has: id, name, location (list of whZoneIDs), react, etc.

    Returns a list of dicts: [{id, name, location, react}, ...]
    """
    cache_key = str(item_id)
    cached = cache_get("item_soldby", cache_key)
    if cached is not None:
        return cached

    url = f"https://www.wowhead.com/item={item_id}/sold-by"
    html = _rate_limited_get(url, expect_json=False)

    if html == "__BLOCKED__":
        logger.warning("Wowhead blocked item %d sold-by — skipping (not cached)", item_id)
        return []
    if not html:
        cache_put("item_soldby", cache_key, [])
        return []

    # Extract Listview blocks with id: 'sold-by' or id: "sold-by"
    # Pattern: new Listview({...id: 'sold-by'...data: [...]...});
    # We look for the data array within the sold-by Listview block.
    vendors = _extract_listview_data(html, "sold-by")

    cache_put("item_soldby", cache_key, vendors)
    return vendors


def _extract_listview_data(html: str, listview_id: str) -> list[dict]:
    """
    Extract data array from a Wowhead Listview block with the given id.

    Matches patterns like:
        new Listview({template: 'npc', id: 'sold-by', ..., data: [{...}, ...]});

    The Listview block contains nested objects/arrays, so we can't use [^}]*
    to skip between fields. Instead, we find the id marker first, then locate
    the data array by searching for "data:" followed by a JSON array, using
    bracket-depth counting to extract the full array.
    """
    # Step 1: Find the Listview block that contains the target id
    id_pattern = (
        r"new\s+Listview\(\s*\{.*?"
        r"""id:\s*['"]""" + re.escape(listview_id) + r"""['"]"""
    )
    id_match = re.search(id_pattern, html, re.DOTALL)
    if not id_match:
        return []

    # Step 2: From the id match position, find "data:" and extract the JSON array
    rest = html[id_match.end():]
    data_match = re.search(r'data:\s*\[', rest)
    if not data_match:
        return []

    # Step 3: Extract the full array using bracket-depth counting
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


def classify_vendors_by_faction(
    vendors: list[dict],
) -> dict[str, dict]:
    """
    Given a list of Wowhead sold-by NPC entries, classify them into
    Alliance and Horde vendors based on their zone location.

    Returns: {"Alliance": {id, name}, "Horde": {id, name}} or partial/empty.
    """
    result: dict[str, dict] = {}

    for npc in vendors:
        npc_id = npc.get("id")
        npc_name = npc.get("name", "")
        locations = npc.get("location")
        if not npc_id or not locations:
            continue

        for zone_id in locations:
            if zone_id in WH_ZONE_ALLIANCE and "Alliance" not in result:
                result["Alliance"] = {"npcID": npc_id, "name": npc_name}
            elif zone_id in WH_ZONE_HORDE and "Horde" not in result:
                result["Horde"] = {"npcID": npc_id, "name": npc_name}

        if "Alliance" in result and "Horde" in result:
            break

    return result


def lookup_npc_full(
    vendor_name: str,
    hint_zones: set[str] | None = None,
) -> Optional[dict]:
    """
    Full NPC lookup: search for NPC by name, then fetch tooltip for coordinates.
    Returns {npc_id, npc_name, coords: {x, y}, whZoneID, react, faction} or None.

    When hint_zones is provided and multiple NPCs share the name, the candidate
    whose Wowhead location best matches one of the hint zones is preferred.
    """
    # Check NPC_OVERRIDES first (bypasses all caching)
    override = NPC_OVERRIDES.get(vendor_name)
    if override and override.get("coords"):
        result = {
            "npc_id": override["npc_id"],
            "npc_name": vendor_name,
            "coords": override["coords"],
            "whZoneID": override.get("whZoneID"),
            "react": [1, 1],
            "faction": "neutral",
        }
        cache_put("npc_full", vendor_name, result)
        return result

    # When hint_zones is provided, bypass npc_full cache to allow
    # zone-aware disambiguation (the cache may hold a stale first-match result).
    if not hint_zones:
        cached = cache_get("npc_full", vendor_name)
        if cached is not None:
            if cached.get("npc_id"):
                if "faction" not in cached:
                    pass  # fall through to re-build (old cache format)
                else:
                    return cached
            elif cached.get("npc_id") is None:
                return None

    # Step 1: Find NPC ID(s) — use zone-aware disambiguation when possible
    if hint_zones:
        all_candidates = lookup_npc_id_all(vendor_name)
        if not all_candidates:
            cache_put("npc_full", vendor_name, {"npc_id": None})
            return None
        if len(all_candidates) > 1:
            logger.info("  -> %d NPC matches for '%s', disambiguating by zone...",
                        len(all_candidates), vendor_name)
            best = _pick_npc_by_zone(all_candidates, hint_zones)
            npc_info = best or all_candidates[0]
        else:
            npc_info = all_candidates[0]
    else:
        npc_info = lookup_npc_id(vendor_name)
        if not npc_info or not npc_info.get("npc_id"):
            cache_put("npc_full", vendor_name, {"npc_id": None})
            return None

    npc_id = npc_info["npc_id"]

    # Step 2: Fetch coordinates from tooltip (with page fallback)
    tooltip_data = fetch_npc_coords(npc_id)
    if not tooltip_data or not tooltip_data.get("coords"):
        page_data = fetch_npc_coords_from_page(npc_id)
        if page_data and page_data.get("coords"):
            logger.info("  -> Page fallback found coords for npcID=%d", npc_id)
            tooltip_data = page_data

    # Step 3: Determine faction from react data
    react = npc_info.get("react")
    faction = _react_to_faction(react)

    result = {
        "npc_id": npc_id,
        "npc_name": npc_info.get("npc_name", vendor_name),
        "coords": tooltip_data.get("coords") if tooltip_data else None,
        "whZoneID": tooltip_data.get("whZoneID") if tooltip_data else None,
        "react": react,
        "faction": faction,
    }
    cache_put("npc_full", vendor_name, result)
    return result


# ---------------------------------------------------------------------------
# Item lookup (for missing itemIDs)
# ---------------------------------------------------------------------------

def lookup_item_id(item_name: str) -> Optional[int]:
    """
    Look up an item ID by exact name match via Wowhead search.
    Used only when itemID is 0 or missing in the catalog.
    """
    cached = cache_get("item", item_name)
    if cached is not None:
        return cached.get("id")

    arrays = _search_wowhead(item_name)
    if not arrays:
        cache_put("item", item_name, {"id": None})
        return None

    item_lower = item_name.lower().strip()

    for arr in arrays:
        for entry in arr:
            # Item entries have 'classs' key
            if "classs" in entry:
                if entry.get("name", "").lower().strip() == item_lower:
                    item_id = entry.get("id")
                    cache_put("item", item_name, {"id": item_id, "name": entry["name"]})
                    return item_id

    cache_put("item", item_name, {"id": None})
    return None


# ---------------------------------------------------------------------------
# Main enrichment logic
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Enrich catalog_dump.json with Wowhead data (quests, NPCs, coords).",
    )
    parser.add_argument(
        "--refresh-null",
        action="store_true",
        help="Invalidate cached NPC entries with null coordinates and re-fetch them. "
             "Use this when Wowhead may have added coords for previously-unmapped NPCs.",
    )
    return parser.parse_args()


def main() -> None:
    """Main entry point: enrich catalog_dump.json and write enriched_catalog.json."""
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Invalidate stale null-coord caches if requested
    if args.refresh_null:
        count = invalidate_null_coord_caches()
        logger.info("Invalidated %d cached NPC entries with null coordinates", count)

    # Load overrides
    overrides = load_overrides()

    # Load the catalog
    if not INPUT_FILE.exists():
        logger.error("Input file not found: %s", INPUT_FILE)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    logger.info("Loaded %d entries from catalog_dump.json", len(catalog))

    # Seed known data from existing files
    quest_map, npc_map = load_seed_data()

    # -----------------------------------------------------------------------
    # Analyze what needs to be looked up
    # -----------------------------------------------------------------------

    # Quest analysis: identify unique quest names AND which are duplicates
    # (like "Decor Treasure Hunt" which has 100+ variants)
    from collections import Counter
    quest_name_counts = Counter(
        item["quest"] for item in catalog if item.get("quest")
    )

    # Simple quests: appear with only one unique name, can be resolved by name alone
    # Duplicate quests: same name for different items, need itemID cross-reference
    simple_quest_names = set()
    duplicate_quest_names = set()
    for name, count in quest_name_counts.items():
        if name not in quest_map:
            # Check if multiple catalog items share this quest name
            # We need to see if Wowhead has multiple quests with this name
            # For efficiency, we'll try name-only first and handle ambiguity
            simple_quest_names.add(name)

    unique_vendors = set()
    vendors_need_coords = set()
    # Map each vendor name to the set of zones its items appear in
    # (used for zone-aware NPC disambiguation)
    vendor_zones: dict[str, set[str]] = {}
    for item in catalog:
        if item.get("vendor"):
            vendor = item["vendor"]
            if vendor in SKIP_VENDORS:
                continue
            zone = item.get("zone")
            if zone:
                vendor_zones.setdefault(vendor, set()).add(zone)
            if vendor not in npc_map:
                unique_vendors.add(vendor)
            elif npc_map[vendor].get("npc_id") and not npc_map[vendor].get("coords"):
                # Seeded with NPC ID but missing coordinates — re-query
                vendors_need_coords.add(vendor)

    items_need_id = [item for item in catalog if not item.get("itemID") or item["itemID"] == 0]

    logger.info("Quests to look up:  %d (already known: %d)", len(simple_quest_names), len(quest_map))
    logger.info("Vendors to look up: %d new + %d re-query coords (already known: %d)",
                 len(unique_vendors), len(vendors_need_coords), len(npc_map))
    logger.info("Items needing ID:   %d", len(items_need_id))

    # -----------------------------------------------------------------------
    # Phase 1: Resolve quest names -> questIDs
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 1: Quest ID resolution")
    logger.info("=" * 60)

    quest_resolved = 0
    quest_ambiguous = 0
    quest_failed = 0
    ambiguous_quest_names = set()  # Quests needing itemID cross-reference

    for i, quest_name in enumerate(sorted(simple_quest_names), 1):
        logger.info("[%d/%d] Looking up quest: %s", i, len(simple_quest_names), quest_name)
        quest_id = lookup_quest_by_name(quest_name)
        if quest_id:
            quest_map[quest_name] = quest_id
            quest_resolved += 1
            logger.info("  -> questID=%d", quest_id)
        else:
            # Check if it was ambiguous (multiple results)
            cached = cache_get("quest", quest_name)
            if cached and cached.get("ambiguous"):
                quest_ambiguous += 1
                ambiguous_quest_names.add(quest_name)
                logger.info("  -> AMBIGUOUS (%d results) - will resolve by itemID", cached["count"])
            else:
                quest_failed += 1
                logger.warning("  -> NOT FOUND on Wowhead")

    logger.info(
        "Quest name resolution: %d resolved, %d ambiguous, %d not found",
        quest_resolved, quest_ambiguous, quest_failed,
    )

    # -----------------------------------------------------------------------
    # Phase 1b: Resolve ambiguous quests by itemID cross-reference
    # -----------------------------------------------------------------------
    if ambiguous_quest_names:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Phase 1b: Resolving %d ambiguous quest(s) by item reward", len(ambiguous_quest_names))
        logger.info("=" * 60)

        # Build a map: quest_name -> [(item, itemID)] from catalog
        # so we can batch-resolve quests that share names
        items_by_quest: dict[str, list[dict]] = {}
        for item in catalog:
            qname = item.get("quest")
            if qname in ambiguous_quest_names:
                items_by_quest.setdefault(qname, []).append(item)

        per_item_resolved = 0
        per_item_failed = 0

        for quest_name in sorted(ambiguous_quest_names):
            items = items_by_quest.get(quest_name, [])
            logger.info("Resolving '%s' for %d catalog items...", quest_name, len(items))

            for item in items:
                item_id = item.get("itemID")
                if not item_id:
                    per_item_failed += 1
                    continue

                quest_id = lookup_quest_by_item_reward(quest_name, item_id)
                if quest_id:
                    # Store per-item quest ID (keyed by decorID)
                    per_item_key = f"{quest_name}__itemID_{item_id}"
                    quest_map[per_item_key] = quest_id
                    per_item_resolved += 1
                    logger.info("  decorID=%d (itemID=%d) -> questID=%d", item["decorID"], item_id, quest_id)
                else:
                    per_item_failed += 1
                    logger.warning("  decorID=%d (itemID=%d) -> no matching quest reward", item["decorID"], item_id)

        logger.info(
            "Per-item resolution: %d resolved, %d failed",
            per_item_resolved, per_item_failed,
        )

    # -----------------------------------------------------------------------
    # Phase 2: Resolve vendor names -> npcID + coordinates
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 2: Vendor/NPC resolution")
    logger.info("=" * 60)

    npc_resolved = 0
    npc_with_coords = 0
    npc_failed = 0

    for i, vendor_name in enumerate(sorted(unique_vendors), 1):
        zones = vendor_zones.get(vendor_name)
        logger.info("[%d/%d] Looking up NPC: %s (zones: %s)", i, len(unique_vendors),
                    vendor_name, zones or "?")
        npc_data = lookup_npc_full(vendor_name, hint_zones=zones)
        if npc_data and npc_data.get("npc_id"):
            npc_map[vendor_name] = npc_data
            npc_resolved += 1
            coords_str = ""
            if npc_data.get("coords"):
                npc_with_coords += 1
                c = npc_data["coords"]
                coords_str = " @ (%.1f, %.1f) whZone=%s" % (c["x"], c["y"], npc_data.get("whZoneID"))
            logger.info("  -> npcID=%d%s", npc_data["npc_id"], coords_str)
        else:
            npc_failed += 1
            logger.warning("  -> NOT FOUND on Wowhead")

    # Re-query seeded NPCs that have npc_id but missing coordinates
    coords_updated = 0
    for i, vendor_name in enumerate(sorted(vendors_need_coords), 1):
        old_info = npc_map[vendor_name]
        old_npc_id = old_info["npc_id"]
        logger.info("[%d/%d] Re-querying coords for NPC %d (%s)",
                    i, len(vendors_need_coords), old_npc_id, vendor_name)
        npc_data = lookup_npc_full(vendor_name, hint_zones=vendor_zones.get(vendor_name))
        if npc_data and npc_data.get("coords"):
            # Merge new coords into existing entry, keep npc_id from seed
            old_info["coords"] = npc_data["coords"]
            old_info["whZoneID"] = npc_data.get("whZoneID") or old_info.get("whZoneID")
            if npc_data.get("faction"):
                old_info["faction"] = npc_data["faction"]
            c = npc_data["coords"]
            logger.info("  -> coords found: (%.1f, %.1f) whZone=%s",
                        c["x"], c["y"], npc_data.get("whZoneID"))
            coords_updated += 1
        else:
            logger.warning("  -> still no coords on Wowhead")

    logger.info(
        "NPC resolution: %d resolved (%d with coords), %d not found, "
        "%d coords re-queried (%d updated), %d total known",
        npc_resolved, npc_with_coords, npc_failed,
        len(vendors_need_coords), coords_updated, len(npc_map),
    )

    # -----------------------------------------------------------------------
    # Phase 3: Verify/resolve missing itemIDs
    # -----------------------------------------------------------------------
    if items_need_id:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Phase 3: Item ID verification (%d items)", len(items_need_id))
        logger.info("=" * 60)

        item_resolved = 0
        for i, item in enumerate(items_need_id, 1):
            logger.info("[%d/%d] Looking up item: %s", i, len(items_need_id), item["name"])
            found_id = lookup_item_id(item["name"])
            if found_id:
                item_resolved += 1
                logger.info("  -> itemID=%d", found_id)
            else:
                logger.warning("  -> NOT FOUND on Wowhead")
        logger.info("Item resolution: %d resolved", item_resolved)
    else:
        logger.info("")
        logger.info("Phase 3: Item ID verification -- SKIPPED (all items have IDs)")

    # -----------------------------------------------------------------------
    # Phase 4: Build enriched catalog
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 4: Building enriched catalog")
    logger.info("=" * 60)

    enriched = []
    stats = {
        "total": len(catalog),
        "with_quest_id": 0,
        "with_npc_id": 0,
        "with_coords": 0,
        "with_faction": 0,
        "quest_unresolved": 0,
        "vendor_unresolved": 0,
    }

    for item in catalog:
        enriched_item = dict(item)  # shallow copy
        item_faction = None  # track faction from any source

        # --- Quest enrichment ---
        quest_name = item.get("quest")
        quest_id = None
        quest_faction = None
        if quest_name:
            # Try direct quest_map lookup (for unique quest names)
            quest_id = quest_map.get(quest_name)

            # Get quest faction from cache
            quest_cache = cache_get("quest", quest_name)
            if quest_cache:
                quest_faction = quest_cache.get("faction")

            # If not found, try per-item lookup (for ambiguous names like "Decor Treasure Hunt")
            if quest_id is None and item.get("itemID"):
                per_item_key = f"{quest_name}__itemID_{item['itemID']}"
                quest_id = quest_map.get(per_item_key)

                # Get per-item quest faction
                per_item_faction_key = f"{quest_name}__itemID_{item['itemID']}__faction"
                faction_cache = cache_get("quest_faction", per_item_faction_key)
                if faction_cache:
                    quest_faction = faction_cache.get("faction")

        enriched_item["questID"] = quest_id
        enriched_item["questFaction"] = quest_faction
        if quest_faction:
            item_faction = quest_faction
        if quest_name:
            if quest_id:
                stats["with_quest_id"] += 1
            else:
                stats["quest_unresolved"] += 1

        # --- Vendor/NPC enrichment ---
        vendor_name = item.get("vendor")
        npc_faction = None
        if vendor_name and vendor_name not in SKIP_VENDORS:
            npc_info = npc_map.get(vendor_name)
            if npc_info and npc_info.get("npc_id"):
                enriched_item["npcID"] = npc_info["npc_id"]
                coords = npc_info.get("coords")
                enriched_item["npcX"] = coords["x"] if coords else None
                enriched_item["npcY"] = coords["y"] if coords else None
                enriched_item["npcMapID"] = npc_info.get("whZoneID") or npc_info.get("mapID")
                npc_faction = npc_info.get("faction")
                # Override stale react data for cross-faction zones/NPCs
                _wh_zone = npc_info.get("whZoneID")
                _npc_id = npc_info.get("npc_id")
                if _wh_zone in CROSS_FACTION_ZONES or _npc_id in CROSS_FACTION_NPCS:
                    if npc_faction and npc_faction != "neutral":
                        logger.debug("Override: %s npcFaction '%s' -> 'neutral' "
                                     "(cross-faction zone/NPC)", vendor_name, npc_faction)
                        npc_faction = "neutral"
                stats["with_npc_id"] += 1
                if coords:
                    stats["with_coords"] += 1
            else:
                enriched_item["npcID"] = None
                enriched_item["npcX"] = None
                enriched_item["npcY"] = None
                enriched_item["npcMapID"] = None
                stats["vendor_unresolved"] += 1
        else:
            enriched_item["npcID"] = None
            enriched_item["npcX"] = None
            enriched_item["npcY"] = None
            enriched_item["npcMapID"] = None

        # --- Zone correction from Wowhead zone ID ---
        # The in-game dump sometimes uses a broad zone name (e.g. "Zuldazar") when
        # the NPC is in a specific city (e.g. "Dazar'alor"). Use Wowhead's zone ID
        # to auto-correct these, unless a manual override already applies.
        _npc_map_id = enriched_item.get("npcMapID")
        _decor_id_str = str(item.get("decorID", ""))
        if _npc_map_id and _npc_map_id in WOWHEAD_ZONE_CORRECTIONS:
            corrected_zone = WOWHEAD_ZONE_CORRECTIONS[_npc_map_id]
            current_zone = enriched_item.get("zone") or ""
            # Only correct if current zone differs and no manual override exists
            if current_zone != corrected_zone and _decor_id_str not in overrides:
                logger.debug("  Zone correction: decorID=%s '%s' -> '%s' (npcMapID=%d)",
                             _decor_id_str, current_zone, corrected_zone, _npc_map_id)
                enriched_item["zone"] = corrected_zone
                stats["zone_corrections"] = stats.get("zone_corrections", 0) + 1

        # --- Coordinate/map safety guard ---
        # Detect when NPC coordinates are in a different zone's coordinate space
        # than the item's display zone.  Better to show NO pin than a WRONG pin.
        _npc_map_id = enriched_item.get("npcMapID")
        if _npc_map_id and enriched_item.get("npcX") is not None:
            npc_zone_name = WH_ZONE_ID_TO_NAME.get(_npc_map_id)
            item_zone = enriched_item.get("zone") or ""
            if npc_zone_name and item_zone and npc_zone_name != item_zone:
                # NPC coords are in a different zone's coordinate space
                logger.info("  COORD MISMATCH: decorID=%s '%s' — coords from '%s' "
                            "(whZone=%d) but item zone='%s'. Clearing coords.",
                            enriched_item.get("decorID"), enriched_item.get("name"),
                            npc_zone_name, _npc_map_id, item_zone)
                enriched_item["coordsMismatch"] = True
                enriched_item["coordsMismatchDetail"] = (
                    f"coords from '{npc_zone_name}' (whZone={_npc_map_id}), "
                    f"item zone='{item_zone}'"
                )
                stats["coord_mismatches"] = stats.get("coord_mismatches", 0) + 1

        enriched_item["npcFaction"] = npc_faction
        # Use NPC faction as fallback when quest faction is unknown.
        # If the NPC only reacts to one faction, the quest is almost certainly
        # faction-locked too (cross-faction quests don't use faction-locked vendors).
        if not item_faction and npc_faction:
            item_faction = npc_faction

        # --- Resolved faction (quest > NPC) ---
        enriched_item["faction"] = item_faction
        if item_faction:
            stats["with_faction"] += 1

        # --- ItemID verification ---
        if not item.get("itemID") or item["itemID"] == 0:
            found_id = lookup_item_id(item["name"])
            if found_id:
                enriched_item["itemID"] = found_id
                enriched_item["itemID_source"] = "wowhead_lookup"

        # --- Apply manual overrides ---
        decor_id = str(item.get("decorID", ""))
        if decor_id in overrides:
            override = overrides[decor_id]
            for key, value in override.items():
                if key.startswith("_"):
                    continue  # skip comment fields like "_note"
                enriched_item[key] = value
            stats["overrides_applied"] = stats.get("overrides_applied", 0) + 1
            logger.debug("  Applied override for decorID=%s: %s", decor_id, list(override.keys()))

        enriched.append(enriched_item)

    # -----------------------------------------------------------------------
    # Phase 4b: Vendor coordinate recovery via item "Sold by" pages
    # -----------------------------------------------------------------------
    # For items that have a vendor but no NPC coordinates (tooltip + page
    # scraper both returned nothing), try the Wowhead ITEM page's "Sold by"
    # section.  This lists alternative NPC IDs that sell the item, often with
    # coordinates the NPC page didn't have.
    # Also used to fix zone mismatches: if current NPC is in the wrong zone,
    # the sold-by list may contain the correct NPC in the right zone.
    # -----------------------------------------------------------------------
    items_need_vendor_coords = [
        item for item in enriched
        if item.get("itemID")
        and item.get("npcID")
        and (item.get("npcX") is None or item.get("coordsMismatch"))
    ]

    if items_need_vendor_coords:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Phase 4b: Vendor coord recovery via item sold-by pages (%d items)",
                    len(items_need_vendor_coords))
        logger.info("=" * 60)

        p4b_stats = {"scraped": 0, "recovered": 0, "still_missing": 0,
                     "mismatch_fixed": 0}

        for item in items_need_vendor_coords:
            item_id = item["itemID"]
            item_zone = item.get("zone") or ""
            decor_id = item.get("decorID")

            vendors = fetch_item_sold_by(item_id)
            p4b_stats["scraped"] += 1

            if not vendors:
                p4b_stats["still_missing"] += 1
                continue

            # Try to find a vendor with coordinates, preferring one in the
            # item's zone.
            best_vendor = None
            best_coords = None
            best_wh_zone = None
            any_coords = None  # fallback: any vendor with coords

            for v in vendors:
                v_npc_id = v.get("id")
                if not v_npc_id:
                    continue

                # Check if this vendor's location matches item zone
                v_locations = v.get("location") or []
                zone_match = False
                for wh_id in v_locations:
                    z = WH_ZONE_ID_TO_NAME.get(wh_id)
                    if z and z == item_zone:
                        zone_match = True
                        break

                # Fetch coordinates for this vendor
                coord_data = fetch_npc_coords(v_npc_id)
                if not coord_data or not coord_data.get("coords"):
                    coord_data = fetch_npc_coords_from_page(v_npc_id)
                if not coord_data or not coord_data.get("coords"):
                    continue

                if any_coords is None:
                    any_coords = (v_npc_id, coord_data, v.get("name", ""))

                if zone_match:
                    best_vendor = v_npc_id
                    best_coords = coord_data
                    best_wh_zone = coord_data.get("whZoneID")
                    logger.info("  decorID=%s: zone-matched vendor npcID=%d in '%s'",
                                decor_id, v_npc_id, item_zone)
                    break

                # Also accept if tooltip zone maps to item zone
                tooltip_zone = WH_ZONE_ID_TO_NAME.get(coord_data.get("whZoneID"))
                if tooltip_zone and tooltip_zone == item_zone:
                    best_vendor = v_npc_id
                    best_coords = coord_data
                    best_wh_zone = coord_data.get("whZoneID")
                    logger.info("  decorID=%s: tooltip-zone-matched vendor npcID=%d in '%s'",
                                decor_id, v_npc_id, item_zone)
                    break

            # Apply the best match
            if best_vendor and best_coords:
                c = best_coords["coords"]
                was_mismatch = item.get("coordsMismatch")
                item["npcID"] = best_vendor
                item["npcX"] = c["x"]
                item["npcY"] = c["y"]
                item["npcMapID"] = best_wh_zone
                item.pop("coordsMismatch", None)
                item.pop("coordsMismatchDetail", None)
                if was_mismatch:
                    p4b_stats["mismatch_fixed"] += 1
                else:
                    p4b_stats["recovered"] += 1
                logger.info("  decorID=%s '%s': recovered coords from sold-by "
                            "npcID=%d @ (%.1f, %.1f)",
                            decor_id, item.get("name"), best_vendor, c["x"], c["y"])
            elif not item.get("coordsMismatch") and any_coords:
                # Use any vendor with coords (might not match zone perfectly)
                v_npc_id, coord_data, v_name = any_coords
                c = coord_data["coords"]
                # Validate zone match before applying
                tooltip_zone = WH_ZONE_ID_TO_NAME.get(coord_data.get("whZoneID"))
                if tooltip_zone is None or tooltip_zone == item_zone:
                    # Unknown WH zone or matches — safe to apply
                    item["npcID"] = v_npc_id
                    item["npcX"] = c["x"]
                    item["npcY"] = c["y"]
                    item["npcMapID"] = coord_data.get("whZoneID")
                    p4b_stats["recovered"] += 1
                    logger.info("  decorID=%s '%s': fallback coords from sold-by "
                                "npcID=%d '%s' @ (%.1f, %.1f)",
                                decor_id, item.get("name"), v_npc_id, v_name,
                                c["x"], c["y"])
                else:
                    p4b_stats["still_missing"] += 1
                    logger.debug("  decorID=%s: fallback vendor in '%s' != item zone '%s'",
                                 decor_id, tooltip_zone, item_zone)
            else:
                p4b_stats["still_missing"] += 1

        logger.info("Vendor coord recovery complete:")
        logger.info("  Items scraped:      %d", p4b_stats["scraped"])
        logger.info("  Coords recovered:   %d", p4b_stats["recovered"])
        logger.info("  Mismatches fixed:   %d", p4b_stats["mismatch_fixed"])
        logger.info("  Still missing:      %d", p4b_stats["still_missing"])

    # -----------------------------------------------------------------------
    # Phase 5: Faction-specific vendors for housing zone items
    # -----------------------------------------------------------------------
    # Items in Founder's Point (Alliance) / Razorwind Shores (Horde) have
    # faction-specific vendors. We scrape Wowhead sold-by data and attach
    # factionVendors sub-tables for items with different vendors per faction.
    # Treasure Hunt items also get skipQuestChain=True.
    # -----------------------------------------------------------------------
    HOUSING_ZONES = {"Founder's Point", "Razorwind Shores"}
    housing_zone_items = [
        item for item in enriched
        if (item.get("zone") or "") in HOUSING_ZONES
        or item.get("quest") == "Decor Treasure Hunt"
    ]

    if housing_zone_items:
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 5: Housing zone items — faction vendor scraping")
        logger.info("=" * 60)

        th_count = sum(1 for i in housing_zone_items
                       if i.get("quest") == "Decor Treasure Hunt")
        non_th_count = len(housing_zone_items) - th_count
        logger.info("Found %d housing zone items (%d Treasure Hunt, %d regular vendors)",
                    len(housing_zone_items), th_count, non_th_count)

        p5_stats = {
            "scraped": 0, "with_vendors": 0, "same_npc_skipped": 0,
            "no_both_factions": 0, "npc_lookups": 0, "with_coords": 0,
        }

        for item in housing_zone_items:
            item_id = item.get("itemID")
            if not item_id:
                logger.warning("  Item '%s' (decorID=%s) has no itemID, skipping",
                               item.get("name"), item.get("decorID"))
                continue

            # Scrape sold-by NPCs from Wowhead item page
            vendors = fetch_item_sold_by(item_id)
            p5_stats["scraped"] += 1

            if not vendors:
                logger.warning("  No sold-by data for item %d '%s'",
                               item_id, item.get("name"))
                continue

            # Classify vendors into Alliance/Horde by zone
            faction_vendors = classify_vendors_by_faction(vendors)

            if len(faction_vendors) < 2:
                p5_stats["no_both_factions"] += 1
                logger.debug("  Only %d faction(s) found for item %d '%s'",
                             len(faction_vendors), item_id, item.get("name"))
                continue

            # Skip if same NPC in both factions (no faction-specific data needed)
            a_npc = faction_vendors.get("Alliance", {}).get("npcID")
            h_npc = faction_vendors.get("Horde", {}).get("npcID")
            if a_npc and h_npc and a_npc == h_npc:
                p5_stats["same_npc_skipped"] += 1
                logger.debug("  Same NPC for both factions: item %d '%s' (npcID=%d)",
                             item_id, item.get("name"), a_npc)
                continue

            # Fetch NPC coordinates for each faction vendor
            for faction, vendor_info in faction_vendors.items():
                npc_id = vendor_info["npcID"]
                coords_data = fetch_npc_coords(npc_id)
                p5_stats["npc_lookups"] += 1

                if coords_data and coords_data.get("coords"):
                    vendor_info["x"] = coords_data["coords"]["x"]
                    vendor_info["y"] = coords_data["coords"]["y"]
                    vendor_info["whZoneID"] = coords_data.get("whZoneID")
                    p5_stats["with_coords"] += 1
                else:
                    # Try page fallback
                    page_data = fetch_npc_coords_from_page(npc_id)
                    p5_stats["npc_lookups"] += 1
                    if page_data and page_data.get("coords"):
                        vendor_info["x"] = page_data["coords"]["x"]
                        vendor_info["y"] = page_data["coords"]["y"]
                        vendor_info["whZoneID"] = page_data.get("whZoneID")
                        p5_stats["with_coords"] += 1
                    else:
                        logger.warning("  No coords for %s vendor NPC %d '%s'",
                                       faction, npc_id, vendor_info["name"])

            # Resolve zone names from whZoneID
            for faction, vendor_info in faction_vendors.items():
                wh_zone = vendor_info.pop("whZoneID", None)
                if wh_zone in WH_ZONE_ALLIANCE:
                    vendor_info["zone"] = "Founder's Point"
                elif wh_zone in WH_ZONE_HORDE:
                    vendor_info["zone"] = "Razorwind Shores"
                elif wh_zone:
                    logger.warning("  Unexpected whZoneID %d for %s vendor NPC %d",
                                   wh_zone, faction, vendor_info.get("npcID", 0))
                    vendor_info["zone"] = ""
                else:
                    # Default zone based on faction
                    vendor_info["zone"] = (
                        "Founder's Point" if faction == "Alliance"
                        else "Razorwind Shores"
                    )

            # Attach to item
            item["factionVendors"] = faction_vendors
            p5_stats["with_vendors"] += 1

            # Treasure Hunt items get skipQuestChain
            if item.get("quest") == "Decor Treasure Hunt":
                item["skipQuestChain"] = True

            logger.debug("  %s (decorID=%s): A=%s, H=%s",
                         item.get("name"), item.get("decorID"),
                         faction_vendors.get("Alliance", {}).get("name", "?"),
                         faction_vendors.get("Horde", {}).get("name", "?"))

        logger.info("Housing zone scraping complete:")
        logger.info("  Items scraped:        %d", p5_stats["scraped"])
        logger.info("  With faction vendors: %d", p5_stats["with_vendors"])
        logger.info("  Same NPC (skipped):   %d", p5_stats["same_npc_skipped"])
        logger.info("  Single-faction only:  %d", p5_stats["no_both_factions"])
        logger.info("  NPC coord lookups:    %d", p5_stats["npc_lookups"])
        logger.info("  With coordinates:     %d", p5_stats["with_coords"])

    # -----------------------------------------------------------------------
    # Phase 6: Discover quest rewards from Wowhead item pages
    # -----------------------------------------------------------------------
    # Items with vendor data but no quest data may still have quest rewards
    # discoverable via Wowhead. This is common for Midnight expansion items
    # where the in-game catalog reports only vendor sources.
    # -----------------------------------------------------------------------
    QUEST_DISCOVERY_ZONES = {
        # Quel'Thalas (revamped)
        "Eversong Woods", "Silvermoon City", "Isle of Quel'Danas",
        "Ghostlands", "Zul'Aman",
        # Midnight expansion zones
        "Voidstorm", "The Voidstorm", "Harandar",
        "Founder's Point", "Razorwind Shores", "K'aresh",
        # Liberation of Undermine
        "Undermine", "Liberation of Undermine",
    }
    quest_discovery_items = [
        item for item in enriched
        if not item.get("questID")
        and item.get("itemID")
        and (item.get("zone") or "") in QUEST_DISCOVERY_ZONES
    ]

    if quest_discovery_items:
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 6: Quest reward discovery from Wowhead item pages")
        logger.info("=" * 60)
        logger.info("Found %d items in Midnight zones with no quest data",
                    len(quest_discovery_items))

        p6_stats = {"scraped": 0, "found": 0, "no_quest": 0}

        for item in quest_discovery_items:
            item_id = item["itemID"]
            quests = fetch_item_quest_rewards(item_id)
            p6_stats["scraped"] += 1

            if quests:
                # Take the first quest (usually there's only one reward-from quest)
                quest = quests[0]
                quest_id = quest.get("id")
                quest_name = quest.get("name")

                if quest_id and quest_name:
                    item["questID"] = quest_id
                    item["quest"] = quest_name
                    # Add Quest source so determine_source_type() picks it up
                    sources = item.setdefault("sources", [])
                    if not any(s.get("type") == "Quest" for s in sources):
                        sources.insert(0, {"type": "Quest", "value": quest_name})
                    p6_stats["found"] += 1
                    logger.debug("  %s (decorID=%s, itemID=%d): quest='%s' (ID=%d)",
                                 item.get("name"), item.get("decorID"),
                                 item_id, quest_name, quest_id)
                else:
                    p6_stats["no_quest"] += 1
            else:
                p6_stats["no_quest"] += 1

        logger.info("Quest discovery complete:")
        logger.info("  Items scraped:    %d", p6_stats["scraped"])
        logger.info("  Quests found:     %d", p6_stats["found"])
        logger.info("  No quest reward:  %d", p6_stats["no_quest"])

    # -----------------------------------------------------------------------
    # Phase 6b: Vendor unlock quest resolution
    # -----------------------------------------------------------------------
    # Items whose vendor requires completing a quest (vendorUnlockQuest in
    # vendor_requirements.json) should be promoted to Quest + Vendor source
    # type.  This phase loads the requirements file, resolves quest names to
    # quest IDs via Wowhead, and adds a Quest source to the enriched data so
    # output_catalog_lua.py classifies them correctly.
    # -----------------------------------------------------------------------
    vr_json = DATA_DIR / "vendor_requirements.json"
    if vr_json.exists():
        with open(vr_json, "r", encoding="utf-8") as f:
            vr_data = json.load(f)
        vr_reqs = vr_data.get("requirements", {})

        # Build decorID → item index for fast lookup
        decor_to_item = {item.get("decorID"): item for item in enriched if item.get("decorID")}

        # Collect items that have an unlock quest name but no questID yet
        p6b_candidates = []
        for decor_id_str, req in vr_reqs.items():
            quest_name = req.get("unlockQuest")
            if not quest_name:
                continue
            try:
                decor_id = int(decor_id_str)
            except (ValueError, TypeError):
                continue
            item = decor_to_item.get(decor_id)
            if item and not item.get("questID"):
                p6b_candidates.append((item, quest_name))

        # Quests that Wowhead search fails to find (name mismatch, indexing
        # gaps, or ambiguous results where items aren't listed as rewards).
        # Manually verified quest IDs from Wowhead quest pages.
        VENDOR_QUEST_FALLBACK: dict[str, int] = {
            "Tears of Elune":           40890,  # "The Tears of Elune"
            "Weight of Duty":           82895,  # "The Weight of Duty"
            "Bringer of the Light":     44004,  # exact match, search doesn't index
            "A Binding Contract":        7604,  # exact match, search doesn't index
            "To Kill a Queen":          82141,  # exact match, search doesn't index
            "10,000 Years of Roasting": 67063,  # comma in name breaks search
            "Return to Zuldazar":       51985,  # ambiguous (3 versions), Horde war campaign
            "The Bargain is Struck":    47432,  # ambiguous (2 versions), Zuldazar quest
        }

        if p6b_candidates:
            logger.info("")
            logger.info("=" * 60)
            logger.info("PHASE 6b: Vendor unlock quest resolution")
            logger.info("=" * 60)
            logger.info("Found %d items with vendor unlock quest but no questID",
                        len(p6b_candidates))

            p6b_stats = {"resolved": 0, "failed": 0}

            for item, quest_name in p6b_candidates:
                quest_id = VENDOR_QUEST_FALLBACK.get(quest_name) or lookup_quest_by_name(quest_name)
                if quest_id:
                    item["questID"] = quest_id
                    item["quest"] = quest_name
                    # Add Quest source so determine_source_type() picks it up
                    sources = item.setdefault("sources", [])
                    if not any(s.get("type") == "Quest" for s in sources):
                        sources.insert(0, {"type": "Quest", "value": quest_name})
                    p6b_stats["resolved"] += 1
                    logger.debug("  %s (decorID=%s): quest='%s' (ID=%d)",
                                 item.get("name"), item.get("decorID"),
                                 quest_name, quest_id)
                else:
                    p6b_stats["failed"] += 1
                    logger.warning("  %s (decorID=%s): could not resolve quest '%s'",
                                   item.get("name"), item.get("decorID"), quest_name)

            logger.info("Vendor unlock quest resolution complete:")
            logger.info("  Resolved:   %d", p6b_stats["resolved"])
            logger.info("  Unresolved: %d", p6b_stats["failed"])

    # -----------------------------------------------------------------------
    # Phase 7: Multi-vendor discovery
    # -----------------------------------------------------------------------
    # Items sourced from Vendors may be sold by NPCs in multiple zones
    # (e.g. Bolt Chair is sold in Mechagon, Stormwind, Orgrimmar, Dornogal).
    # We scrape Wowhead sold-by data and attach _allVendors lists so the
    # output stage can pick the best primary vendor and list the rest.
    # Items that already have factionVendors are skipped (handled by Phase 5).
    # -----------------------------------------------------------------------
    multi_vendor_items = [
        item for item in enriched
        if item.get("itemID")
        and any(s.get("type") == "Vendor" for s in (item.get("sources") or []))
        and not item.get("factionVendors")
    ]

    if multi_vendor_items:
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 7: Multi-vendor discovery (items sold in multiple zones)")
        logger.info("=" * 60)
        logger.info("Checking %d vendor-sourced items for multi-vendor data",
                    len(multi_vendor_items))

        p7_stats = {
            "scraped": 0, "multi_vendor": 0, "single_vendor": 0,
            "no_data": 0, "total_additional": 0,
        }
        p7_updated_items = []  # for statistics output

        for item in multi_vendor_items:
            item_id = item["itemID"]
            vendors = fetch_item_sold_by(item_id)
            p7_stats["scraped"] += 1

            if not vendors:
                p7_stats["no_data"] += 1
                continue

            if len(vendors) < 2:
                p7_stats["single_vendor"] += 1
                continue

            # Build vendor list with zone + faction info.
            # A single NPC can exist in multiple locations (e.g., Second Chair
            # Pawdo in Orgrimmar, Stormwind City, and Dornogal). We create
            # separate entries for each resolvable zone.
            all_vendors = []
            seen_zones = set()  # deduplicate same zone from different NPCs
            for v in vendors:
                npc_id = v.get("id")
                npc_name = v.get("name", "")
                locations = v.get("location") or []
                react = v.get("react") or []

                # Determine faction from react field
                # [1, -1] = Alliance only, [-1, 1] = Horde only, else neutral
                faction = None
                if react == [1, -1]:
                    faction = "Alliance"
                elif react == [-1, 1]:
                    faction = "Horde"

                # Expand each location into a separate vendor entry
                for wh_id in locations:
                    zone_name = WH_ZONE_ID_TO_NAME.get(wh_id)
                    if not zone_name:
                        continue
                    # Deduplicate: same NPC in same zone (different sub-zone IDs)
                    dedup_key = (npc_id, zone_name)
                    if dedup_key in seen_zones:
                        continue
                    seen_zones.add(dedup_key)

                    all_vendors.append({
                        "npcID": npc_id,
                        "name": npc_name,
                        "zone": zone_name,
                        "whZoneID": wh_id,
                        "faction": faction,
                    })

            if len(all_vendors) >= 2:
                item["_allVendors"] = all_vendors
                p7_stats["multi_vendor"] += 1
                additional_count = len(all_vendors) - 1
                p7_stats["total_additional"] += additional_count
                p7_updated_items.append((item.get("name"), len(all_vendors)))
                logger.debug("  %s (decorID=%s): %d vendors across zones",
                             item.get("name"), item.get("decorID"),
                             len(all_vendors))
            else:
                p7_stats["single_vendor"] += 1

        logger.info("Multi-vendor discovery complete:")
        logger.info("  Items scraped:          %d", p7_stats["scraped"])
        logger.info("  With multiple vendors:  %d", p7_stats["multi_vendor"])
        logger.info("  Single vendor only:     %d", p7_stats["single_vendor"])
        logger.info("  No sold-by data:        %d", p7_stats["no_data"])
        logger.info("  Total additional vendors: %d", p7_stats["total_additional"])

        if p7_updated_items:
            logger.info("")
            logger.info("Items with multiple vendors:")
            for name, count in sorted(p7_updated_items):
                logger.info("  %-50s %d vendors", name, count)

    # -----------------------------------------------------------------------
    # Phase 7b: Vendor discovery for quest-sourced items
    # -----------------------------------------------------------------------
    # Many quest-reward items can ALSO be purchased from vendors. Populate
    # vendor data so the UI can show "Purchase from X (available after
    # completing the quest)" for these items.
    # -----------------------------------------------------------------------
    quest_no_vendor = [
        item for item in enriched
        if item.get("itemID")
        and any(s.get("type") == "Quest" for s in (item.get("sources") or []))
        and not any(s.get("type") == "Vendor" for s in (item.get("sources") or []))
    ]

    if quest_no_vendor:
        logger.info("")
        logger.info("=" * 60)
        logger.info("PHASE 7b: Vendor discovery for quest-sourced items")
        logger.info("=" * 60)
        logger.info("Checking %d quest items for vendor sold-by data",
                    len(quest_no_vendor))

        p7b_found = 0
        p7b_multi = 0
        for item in quest_no_vendor:
            item_id = item["itemID"]
            vendors = fetch_item_sold_by(item_id)

            if not vendors:
                continue

            # Pick the first vendor with a resolvable zone
            best_vendor = None
            all_vendors_resolved = []
            for v in vendors:
                npc_id = v.get("id")
                npc_name = v.get("name", "")
                react = v.get("react") or []
                faction = None
                if react == [1, -1]:
                    faction = "Alliance"
                elif react == [-1, 1]:
                    faction = "Horde"

                locations = v.get("location") or []
                for wh_id in locations:
                    zone_name = WH_ZONE_ID_TO_NAME.get(wh_id)
                    if zone_name:
                        entry = {
                            "npcID": npc_id,
                            "name": npc_name,
                            "zone": zone_name,
                            "whZoneID": wh_id,
                            "faction": faction,
                        }
                        all_vendors_resolved.append(entry)
                        if not best_vendor:
                            best_vendor = entry

            if not best_vendor:
                continue

            # Add Vendor source to the item
            item["sources"].append({"type": "Vendor", "value": best_vendor["name"]})
            item["vendor"] = best_vendor["name"]
            item["npcID"] = best_vendor["npcID"]
            p7b_found += 1

            # If multiple vendors, attach _allVendors for multi-vendor display
            if len(all_vendors_resolved) >= 2:
                item["_allVendors"] = all_vendors_resolved
                p7b_multi += 1

            logger.debug("  %s (decorID=%s): vendor=%s in %s",
                         item.get("name"), item.get("decorID"),
                         best_vendor["name"], best_vendor["zone"])

        logger.info("Quest-item vendor discovery complete:")
        logger.info("  Items with vendors found: %d / %d", p7b_found, len(quest_no_vendor))
        logger.info("  Items with multi-vendor:  %d", p7b_multi)

    # -----------------------------------------------------------------------
    # Write output
    # -----------------------------------------------------------------------
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(enriched, f, indent=2, ensure_ascii=False)

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    logger.info("")
    logger.info("=" * 60)
    logger.info("ENRICHMENT SUMMARY")
    logger.info("=" * 60)
    logger.info("Total catalog entries:     %d", stats["total"])
    logger.info("Entries with questID:      %d", stats["with_quest_id"])
    logger.info("Entries with npcID:        %d", stats["with_npc_id"])
    logger.info("Entries with NPC coords:   %d", stats["with_coords"])
    logger.info("Entries with faction:      %d", stats["with_faction"])
    logger.info("Quests unresolved:         %d", stats["quest_unresolved"])
    logger.info("Vendors unresolved:        %d", stats["vendor_unresolved"])
    logger.info("Manual overrides applied:  %d", stats.get("overrides_applied", 0))
    logger.info("Zone corrections applied: %d", stats.get("zone_corrections", 0))
    logger.info("Coord mismatches (cleared):%d", stats.get("coord_mismatches", 0))
    logger.info("Total known quest map:     %d entries", len(quest_map))
    logger.info("Total known NPC map:       %d entries", len(npc_map))

    # Report vendors with no coordinates
    vendors_no_coords = {}
    for item in enriched:
        vendor = item.get("vendor") or item.get("vendorName") or ""
        if vendor and vendor not in SKIP_VENDORS:
            if item.get("npcX") is None and item.get("npcID"):
                vendors_no_coords.setdefault(vendor, []).append(item.get("decorID"))
    if vendors_no_coords:
        logger.info("")
        logger.info("VENDORS MISSING COORDINATES (%d unique):", len(vendors_no_coords))
        for vendor, decor_ids in sorted(vendors_no_coords.items(),
                                         key=lambda x: -len(x[1])):
            logger.info("  %-35s %d items (npcID: %s)",
                        vendor, len(decor_ids),
                        next((str(i.get("npcID")) for i in enriched
                              if (i.get("vendor") or i.get("vendorName")) == vendor
                              and i.get("npcID")), "?"))
    logger.info("")
    logger.info("Output: %s", OUTPUT_FILE)

    # Save the resolved lookup tables for reference / reuse by other scripts
    lookup_tables = {
        "quest_name_to_id": {
            k: v for k, v in quest_map.items()
            if "__itemID_" not in k  # Only simple name mappings
        },
        "quest_item_to_id": {
            k: v for k, v in quest_map.items()
            if "__itemID_" in k  # Per-item mappings
        },
        "npc_name_to_data": {
            name: {
                "npc_id": info.get("npc_id"),
                "coords": info.get("coords"),
                "whZoneID": info.get("whZoneID") or info.get("mapID"),
            }
            for name, info in npc_map.items()
            if info.get("npc_id")
        },
    }
    lookup_file = DATA_DIR / "enrichment_lookups.json"
    with open(lookup_file, "w", encoding="utf-8") as f:
        json.dump(lookup_tables, f, indent=2, ensure_ascii=False)
    logger.info("Lookup tables saved to: %s", lookup_file)


if __name__ == "__main__":
    main()
