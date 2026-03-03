"""
scrape_wowhead.py - Scrape Wowhead housing decor farming guides.

This module targets the Wowhead "Decor Farming" guide pages, which list housing
decoration items grouped by source type (Quest, Vendor, Achievement, Drop) and
organized by zone within each expansion.

Primary URL (Vanilla/Cata PoC):
  https://www.wowhead.com/guide/player-housing/decor-farming-vanilla-cataclysm-quests-drops-achievements-vendors

Strategy:
  Wowhead guide pages render content via JavaScript, but embed structured data in
  WH.Gatherer.addData() calls within <script> tags. These contain:
    - Type 201: Decor items (decorID -> name)
    - Type 1:   NPCs (npcID -> name)
    - Type 5:   Quests (questID -> name, faction side)
    - Type 10:  Achievements (achievementID -> name, icon)
    - Type 7:   Dungeons/Zones (zoneID -> name)
    - Type 17:  Currencies (currencyID -> name)

  Additionally, the raw HTML contains /way commands with coordinates and NPC names
  in surrounding markup context.

  We parse both data sources and cross-reference them.

Output: data/wowhead_vanilla.json
"""

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GUIDE_URLS: dict[str, str] = {
    "vanilla": (
        "https://www.wowhead.com/guide/player-housing/"
        "decor-farming-vanilla-cataclysm-quests-drops-achievements-vendors"
    ),
    # Future expansions can be added here:
    # "tbc": "https://www.wowhead.com/guide/player-housing/decor-farming-...",
}

OUTPUT_DIR = Path(__file__).resolve().parent / "data"

HEADERS = {
    "User-Agent": (
        "HearthAndSeek-Scraper/0.1 "
        "(+https://github.com/ImpalerV/HearthAndSeek; educational WoW addon project)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 2.0  # seconds between requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scrape_wowhead")


# ---------------------------------------------------------------------------
# Known zone-to-mapID mapping (Vanilla/Cata zones)
# ---------------------------------------------------------------------------
ZONE_MAP_IDS: dict[str, int] = {
    "Elwynn Forest": 37,
    "Westfall": 52,
    "Redridge Mountains": 49,
    "Duskwood": 47,
    "Stranglethorn Vale": 50,
    "Northern Stranglethorn": 50,
    "Cape of Stranglethorn": 210,
    "Swamp of Sorrows": 51,
    "Blasted Lands": 17,
    "Burning Steppes": 36,
    "Searing Gorge": 32,
    "Badlands": 15,
    "Loch Modan": 48,
    "Wetlands": 56,
    "Dun Morogh": 27,
    "Ironforge": 87,
    "Stormwind City": 84,
    "Stormwind": 84,
    "Tirisfal Glades": 18,
    "Silverpine Forest": 21,
    "Hillsbrad Foothills": 25,
    "Alterac Mountains": 25,
    "Arathi Highlands": 14,
    "The Hinterlands": 26,
    "Western Plaguelands": 22,
    "Eastern Plaguelands": 23,
    "Ghostlands": 95,
    "Eversong Woods": 94,
    "Undercity": 90,
    "Durotar": 1,
    "Mulgore": 7,
    "The Barrens": 10,
    "Northern Barrens": 10,
    "Southern Barrens": 199,
    "Stonetalon Mountains": 65,
    "Ashenvale": 63,
    "Darkshore": 62,
    "Teldrassil": 57,
    "Felwood": 77,
    "Winterspring": 83,
    "Moonglade": 80,
    "Silithus": 81,
    "Un'Goro Crater": 78,
    "Tanaris": 71,
    "Thousand Needles": 64,
    "Desolace": 66,
    "Feralas": 69,
    "Dustwallow Marsh": 70,
    "Azshara": 76,
    "Orgrimmar": 85,
    "Thunder Bluff": 88,
    "Darnassus": 89,
    "Exodar": 103,
    "Bloodmyst Isle": 106,
    "Azuremyst Isle": 97,
    "Twilight Highlands": 241,
    "Uldum": 249,
    "Mount Hyjal": 198,
    "Vashj'ir": 203,
    "Deepholm": 207,
    "Tol Barad": 244,
    "Gilneas": 217,
    "Kharanos": 27,
    "Tranquillien": 95,
    "Silvermoon City": 110,
    "Blackrock Depths": 242,
    "Shadowfang Keep": 310,
    "The Deadmines": 291,
}

# Wowhead quest _side values
FACTION_SIDES = {
    1: "Alliance",
    2: "Horde",
    3: "Both",
}


# ---------------------------------------------------------------------------
# Gatherer data types
# ---------------------------------------------------------------------------
GATHERER_TYPE_NPC = "1"
GATHERER_TYPE_QUEST = "5"
GATHERER_TYPE_DUNGEON = "7"
GATHERER_TYPE_ACHIEVEMENT = "10"
GATHERER_TYPE_CURRENCY = "17"
GATHERER_TYPE_GUIDE = "100"
GATHERER_TYPE_DECOR = "201"


# ---------------------------------------------------------------------------
# Parsing: WH.Gatherer.addData() extraction
# ---------------------------------------------------------------------------

def _parse_gatherer_data(html: str) -> dict[str, dict[str, Any]]:
    """
    Extract all WH.Gatherer.addData() blocks from the HTML.

    Returns a dict keyed by type string (e.g., "1", "5", "201") where each
    value is a merged dict of all entries for that type.
    """
    result: dict[str, dict[str, Any]] = {}
    pattern = re.compile(r'WH\.Gatherer\.addData\((\d+),\s*\d+,\s*(\{.*?\})\)')

    for match in pattern.finditer(html):
        gtype = match.group(1)
        try:
            data = json.loads(match.group(2))
            if gtype not in result:
                result[gtype] = {}
            result[gtype].update(data)
        except json.JSONDecodeError as exc:
            logger.debug("Failed to parse Gatherer block type %s: %s", gtype, exc)

    return result


def _parse_way_commands(html: str) -> list[dict[str, Any]]:
    """
    Extract /way commands from the raw HTML text, including surrounding context
    for NPC name and location identification.

    Returns a list of dicts with mapID, x, y, npc_name, and location_desc.
    """
    results = []
    # Pattern matches /way #mapID x y NPC_Name with surrounding context
    # The context is in Wowhead's custom markup format
    pattern = re.compile(
        r'(?:([^\]]{0,120}))'        # preceding context
        r'/way\s+#?(\d+)\s+'         # /way #mapID
        r'([\d.]+)\s+([\d.]+)\s*'    # x y
        r'([^\r\n\[\\]{0,80})',      # trailing text (NPC name etc.)
        re.MULTILINE
    )

    for match in pattern.finditer(html):
        before = match.group(1) or ""
        map_id = int(match.group(2))
        x = float(match.group(3))
        y = float(match.group(4))
        after = (match.group(5) or "").strip()

        # Clean Wowhead markup from context
        before_clean = re.sub(r'\[/?[^\]]*\]', '', before).strip()
        after_clean = re.sub(r'\[/?[^\]]*\]', '', after).strip()

        results.append({
            "mapID": map_id,
            "x": x,
            "y": y,
            "npc_name": after_clean if after_clean else None,
            "location_desc": before_clean[-80:] if before_clean else None,
        })

    return results


# ---------------------------------------------------------------------------
# Main extraction: combine Gatherer data + /way commands
# ---------------------------------------------------------------------------

def _build_items_from_gatherer(
    gatherer: dict[str, dict[str, Any]],
    way_commands: list[dict[str, Any]],
    expansion: str,
) -> list[dict[str, Any]]:
    """
    Build structured item records by combining:
    - Type 201 (decor items) for item names and IDs
    - Type 5 (quests) for quest names and faction
    - Type 1 (NPCs) for NPC names
    - Type 10 (achievements) for achievement names
    - Type 7 (dungeons) for dungeon/zone names
    - /way commands for coordinates

    Note: The Gatherer data doesn't directly link decor items to quests/NPCs.
    That linkage comes from the WoWDB merge step. Here we output all the
    reference data we have so the merge step can cross-reference.
    """
    items: list[dict[str, Any]] = []

    decor_data = gatherer.get(GATHERER_TYPE_DECOR, {})
    quest_data = gatherer.get(GATHERER_TYPE_QUEST, {})
    npc_data = gatherer.get(GATHERER_TYPE_NPC, {})
    achievement_data = gatherer.get(GATHERER_TYPE_ACHIEVEMENT, {})
    dungeon_data = gatherer.get(GATHERER_TYPE_DUNGEON, {})

    # Build an NPC name -> waypoint lookup for coordinate enrichment
    npc_waypoints: dict[str, dict[str, Any]] = {}
    for wp in way_commands:
        if wp["npc_name"]:
            # Normalize NPC name for matching
            name_key = wp["npc_name"].strip().lower()
            npc_waypoints[name_key] = wp

    # Output decor items with their IDs and names
    for decor_id, decor_info in decor_data.items():
        items.append({
            "decor_id": int(decor_id),
            "decor_name": decor_info.get("name_enus", "Unknown"),
            "source_type": None,  # Will be resolved in merge step
            "expansion": expansion,
            "quest_id": None,
            "quest_name": None,
            "faction": None,
            "npc_name": None,
            "npc_id": None,
            "coords": None,
            "mapID": None,
            "zone": None,
            "dungeon": None,
            "achievement_id": None,
            "achievement_name": None,
        })

    # Output quest reference data
    quest_refs = []
    for quest_id, quest_info in quest_data.items():
        side_num = quest_info.get("_side", 3)
        quest_refs.append({
            "quest_id": int(quest_id),
            "quest_name": quest_info.get("name_enus", "Unknown"),
            "faction": FACTION_SIDES.get(side_num, "Both"),
            "reqrace": quest_info.get("reqrace", 0),
        })

    # Output NPC reference data with coordinates from /way commands
    npc_refs = []
    for npc_id, npc_info in npc_data.items():
        npc_name = npc_info.get("name_enus", "Unknown")
        name_key = npc_name.strip().lower()

        ref: dict[str, Any] = {
            "npc_id": int(npc_id),
            "npc_name": npc_name,
            "coords": None,
            "mapID": None,
        }

        # Try to find coordinates from /way commands
        if name_key in npc_waypoints:
            wp = npc_waypoints[name_key]
            ref["coords"] = {"x": wp["x"], "y": wp["y"]}
            ref["mapID"] = wp["mapID"]

        npc_refs.append(ref)

    # Output achievement reference data
    achievement_refs = []
    for ach_id, ach_info in achievement_data.items():
        achievement_refs.append({
            "achievement_id": int(ach_id),
            "achievement_name": ach_info.get("name_enus", "Unknown"),
        })

    # Output dungeon reference data
    dungeon_refs = []
    for zone_id, zone_info in dungeon_data.items():
        dungeon_refs.append({
            "zone_id": int(zone_id),
            "zone_name": zone_info.get("name_enus", "Unknown"),
        })

    return {
        "decor_items": items,
        "quests": quest_refs,
        "npcs": npc_refs,
        "achievements": achievement_refs,
        "dungeons": dungeon_refs,
        "way_commands": way_commands,
    }


# ---------------------------------------------------------------------------
# Fetching and orchestration
# ---------------------------------------------------------------------------

def fetch_guide(url: str) -> str | None:
    """Fetch a Wowhead guide page with polite request headers and error handling."""
    logger.info("Fetching: %s", url)
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        logger.info("Received %d bytes", len(response.text))
        return response.text
    except requests.RequestException as exc:
        logger.error("Failed to fetch %s: %s", url, exc)
        return None


def scrape_expansion(expansion: str, url: str) -> dict[str, Any]:
    """Scrape a single expansion guide page and return structured data."""
    html = fetch_guide(url)
    if html is None:
        return {"decor_items": [], "quests": [], "npcs": [], "achievements": [], "dungeons": [], "way_commands": []}

    # Parse WH.Gatherer.addData() blocks
    gatherer = _parse_gatherer_data(html)
    logger.info(
        "Parsed Gatherer data: %s",
        {k: len(v) for k, v in gatherer.items()}
    )

    # Parse /way commands
    way_commands = _parse_way_commands(html)
    logger.info("Parsed %d /way commands", len(way_commands))

    # Build structured output
    result = _build_items_from_gatherer(gatherer, way_commands, expansion)

    decor_count = len(result["decor_items"])
    quest_count = len(result["quests"])
    npc_count = len(result["npcs"])
    logger.info(
        "Extracted from %s: %d decor items, %d quests, %d NPCs, %d achievements, %d dungeons",
        expansion, decor_count, quest_count, npc_count,
        len(result["achievements"]), len(result["dungeons"])
    )

    return result


def main() -> None:
    """Main entry point: scrape all configured guides and save to JSON."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    for expansion, url in GUIDE_URLS.items():
        logger.info("=== Scraping %s guide ===", expansion)
        result = scrape_expansion(expansion, url)

        output_file = OUTPUT_DIR / f"wowhead_{expansion}.json"
        with open(output_file, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)

        decor_count = len(result["decor_items"])
        logger.info("Saved %d decor items (+ reference data) to %s", decor_count, output_file)

        if decor_count == 0:
            logger.warning(
                "No decor items extracted for %s. The WH.Gatherer data may "
                "not include Type 201 entries on this page.",
                expansion,
            )

        time.sleep(REQUEST_DELAY)

    logger.info("Wowhead scraping complete.")


if __name__ == "__main__":
    main()
