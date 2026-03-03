"""
generate_routes.py - Generate optimized regional route packs from merged decor data.

Takes the merged data from merge_sources.py and produces regional route packs:
  1. Group items by continent/region.
  2. Within each region, group by zone.
  3. Within each zone, sort by proximity (nearest-neighbor heuristic).
  4. Insert TRAVEL steps between zones.
  5. Output one JSON file per regional pack to data/routes/.

Route optimization uses a simple nearest-neighbor heuristic:
  - Start from a capital city (Stormwind for Alliance EK, Orgrimmar for Horde Kalimdor).
  - Visit the nearest unvisited zone first.
  - Within a zone, visit the nearest unvisited NPC/quest giver first.
  - When all items in a zone are done, move to the next nearest zone.

Output: data/routes/<pack_id>.json
"""

import json
import logging
import math
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"
MERGED_FILE = DATA_DIR / "merged_decor.json"
ROUTES_DIR = DATA_DIR / "routes"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_routes")


# ---------------------------------------------------------------------------
# Region / continent definitions
# ---------------------------------------------------------------------------

# Map zone names to their continent/region.
# This is used to group items into regional packs.
ZONE_TO_REGION: dict[str, str] = {
    # === Eastern Kingdoms (Vanilla / Cata revamp) ===
    "Elwynn Forest": "eastern_kingdoms",
    "Westfall": "eastern_kingdoms",
    "Redridge Mountains": "eastern_kingdoms",
    "Duskwood": "eastern_kingdoms",
    "Stranglethorn Vale": "eastern_kingdoms",
    "Northern Stranglethorn": "eastern_kingdoms",
    "Cape of Stranglethorn": "eastern_kingdoms",
    "Swamp of Sorrows": "eastern_kingdoms",
    "Blasted Lands": "eastern_kingdoms",
    "Burning Steppes": "eastern_kingdoms",
    "Searing Gorge": "eastern_kingdoms",
    "Badlands": "eastern_kingdoms",
    "Loch Modan": "eastern_kingdoms",
    "Wetlands": "eastern_kingdoms",
    "Dun Morogh": "eastern_kingdoms",
    "Ironforge": "eastern_kingdoms",
    "Stormwind City": "eastern_kingdoms",
    "Tirisfal Glades": "eastern_kingdoms",
    "Silverpine Forest": "eastern_kingdoms",
    "Hillsbrad Foothills": "eastern_kingdoms",
    "Arathi Highlands": "eastern_kingdoms",
    "The Hinterlands": "eastern_kingdoms",
    "Western Plaguelands": "eastern_kingdoms",
    "Eastern Plaguelands": "eastern_kingdoms",
    "Ghostlands": "eastern_kingdoms",
    "Eversong Woods": "eastern_kingdoms",
    "Undercity": "eastern_kingdoms",
    "Twilight Highlands": "eastern_kingdoms",
    "Tol Barad": "eastern_kingdoms",
    "Tol Barad Peninsula": "eastern_kingdoms",
    "Isle of Quel'Danas": "eastern_kingdoms",
    "Silvermoon City": "eastern_kingdoms",

    # === Kalimdor (Vanilla / Cata revamp) ===
    "Durotar": "kalimdor",
    "Mulgore": "kalimdor",
    "The Barrens": "kalimdor",
    "Northern Barrens": "kalimdor",
    "Southern Barrens": "kalimdor",
    "Stonetalon Mountains": "kalimdor",
    "Ashenvale": "kalimdor",
    "Darkshore": "kalimdor",
    "Teldrassil": "kalimdor",
    "Felwood": "kalimdor",
    "Winterspring": "kalimdor",
    "Moonglade": "kalimdor",
    "Silithus": "kalimdor",
    "Un'Goro Crater": "kalimdor",
    "Tanaris": "kalimdor",
    "Thousand Needles": "kalimdor",
    "Desolace": "kalimdor",
    "Feralas": "kalimdor",
    "Dustwallow Marsh": "kalimdor",
    "Azshara": "kalimdor",
    "Orgrimmar": "kalimdor",
    "Orgrimmar": "kalimdor",
    "Thunder Bluff": "kalimdor",
    "Darnassus": "kalimdor",
    "Exodar": "kalimdor",
    "Bloodmyst Isle": "kalimdor",
    "Azuremyst Isle": "kalimdor",
    "Mount Hyjal": "kalimdor",
    "Uldum": "kalimdor",
    "Vashj'ir": "kalimdor",
    "Kelp'thar Forest": "kalimdor",
    "Shimmering Expanse": "kalimdor",
    "Abyssal Depths": "kalimdor",
    "Deepholm": "kalimdor",
    "Molten Front": "kalimdor",

    # === Outland (TBC) ===
    "Hellfire Peninsula": "outland",
    "Zangarmarsh": "outland",
    "Terokkar Forest": "outland",
    "Nagrand": "outland",
    "Blade's Edge Mountains": "outland",
    "Netherstorm": "outland",
    "Shadowmoon Valley": "outland",
    "Shattrath City": "outland",

    # === Northrend (WotLK) ===
    "Borean Tundra": "northrend",
    "Howling Fjord": "northrend",
    "Dragonblight": "northrend",
    "Grizzly Hills": "northrend",
    "Zul'Drak": "northrend",
    "Sholazar Basin": "northrend",
    "The Storm Peaks": "northrend",
    "Storm Peaks": "northrend",
    "Icecrown": "northrend",
    "Crystalsong Forest": "northrend",
    "Wintergrasp": "northrend",
    "Dalaran": "northrend",

    # === Pandaria (MoP) ===
    "The Jade Forest": "pandaria",
    "Jade Forest": "pandaria",
    "Valley of the Four Winds": "pandaria",
    "Krasarang Wilds": "pandaria",
    "Kun-Lai Summit": "pandaria",
    "Townlong Steppes": "pandaria",
    "Dread Wastes": "pandaria",
    "Vale of Eternal Blossoms": "pandaria",
    "Isle of Thunder": "pandaria",
    "Timeless Isle": "pandaria",
    "Shrine of Two Moons": "pandaria",
    "Shrine of Seven Stars": "pandaria",

    # === Draenor (WoD) ===
    "Frostfire Ridge": "draenor",
    "Shadowmoon Valley (Draenor)": "draenor",
    "Gorgrond": "draenor",
    "Talador": "draenor",
    "Spires of Arak": "draenor",
    "Nagrand (Draenor)": "draenor",
    "Tanaan Jungle": "draenor",
    "Ashran": "draenor",
    "Lunarfall": "draenor",
    "Frostwall": "draenor",
    "Warspear": "draenor",
    "Stormshield": "draenor",

    # === Broken Isles (Legion) ===
    "Azsuna": "broken_isles",
    "Val'sharah": "broken_isles",
    "Highmountain": "broken_isles",
    "Stormheim": "broken_isles",
    "Suramar": "broken_isles",
    "Broken Shore": "broken_isles",
    "Argus": "broken_isles",
    "Krokuun": "broken_isles",
    "Mac'Aree": "broken_isles",
    "Antoran Wastes": "broken_isles",
    "Dalaran (Broken Isles)": "broken_isles",

    # === Kul Tiras & Zandalar (BfA) ===
    "Tiragarde Sound": "bfa",
    "Drustvar": "bfa",
    "Stormsong Valley": "bfa",
    "Boralus": "bfa",
    "Boralus Harbor": "bfa",
    "Zuldazar": "bfa",
    "Nazmir": "bfa",
    "Vol'dun": "bfa",
    "Dazar'alor": "bfa",
    "Mechagon Island": "bfa",
    "Mechagon": "bfa",
    "Nazjatar": "bfa",

    # === Shadowlands ===
    "Bastion": "shadowlands",
    "Maldraxxus": "shadowlands",
    "Ardenweald": "shadowlands",
    "Revendreth": "shadowlands",
    "The Maw": "shadowlands",
    "Oribos": "shadowlands",
    "Korthia": "shadowlands",
    "Zereth Mortis": "shadowlands",

    # === Dragon Isles (Dragonflight) ===
    "The Waking Shores": "dragon_isles",
    "Waking Shores": "dragon_isles",
    "Ohn'ahran Plains": "dragon_isles",
    "The Azure Span": "dragon_isles",
    "Azure Span": "dragon_isles",
    "Thaldraszus": "dragon_isles",
    "Valdrakken": "dragon_isles",
    "Zaralek Cavern": "dragon_isles",
    "Emerald Dream": "dragon_isles",
    "The Forbidden Reach": "dragon_isles",
    "Forbidden Reach": "dragon_isles",

    # === Khaz Algar (The War Within) ===
    "Isle of Dorn": "khaz_algar",
    "The Ringing Deeps": "khaz_algar",
    "Ringing Deeps": "khaz_algar",
    "Hallowfall": "khaz_algar",
    "Azj-Kahet": "khaz_algar",
    "Dornogal": "khaz_algar",

    # === Undermine (Midnight) ===
    "Undermine": "undermine",
    "The Undermine": "undermine",
}

# Region metadata for pack generation
REGION_META: dict[str, dict[str, Any]] = {
    "eastern_kingdoms": {
        "title": "Eastern Kingdoms Collection Tour",
        "description": "Collect all quest and vendor decor across Eastern Kingdoms zones.",
        "expansion": "vanilla",
        "start_city": "Stormwind City",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "kalimdor": {
        "title": "Kalimdor Collection Tour",
        "description": "Collect all quest and vendor decor across Kalimdor zones.",
        "expansion": "vanilla",
        "start_city": "Orgrimmar",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "outland": {
        "title": "Outland Collection Tour",
        "description": "Collect all quest and vendor decor across Outland.",
        "expansion": "tbc",
        "start_city": "Shattrath City",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "northrend": {
        "title": "Northrend Collection Tour",
        "description": "Collect all quest and vendor decor across Northrend.",
        "expansion": "wotlk",
        "start_city": "Dalaran",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "pandaria": {
        "title": "Pandaria Collection Tour",
        "description": "Collect all quest and vendor decor across Pandaria.",
        "expansion": "mop",
        "start_city": "Vale of Eternal Blossoms",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "draenor": {
        "title": "Draenor Collection Tour",
        "description": "Collect all quest and vendor decor across Draenor.",
        "expansion": "wod",
        "start_city": "Ashran",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "broken_isles": {
        "title": "Broken Isles Collection Tour",
        "description": "Collect all quest and vendor decor across the Broken Isles.",
        "expansion": "legion",
        "start_city": "Dalaran (Broken Isles)",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "bfa": {
        "title": "Kul Tiras & Zandalar Collection Tour",
        "description": "Collect all quest and vendor decor from BfA zones.",
        "expansion": "bfa",
        "start_city": "Boralus",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "shadowlands": {
        "title": "Shadowlands Collection Tour",
        "description": "Collect all quest and vendor decor across the Shadowlands.",
        "expansion": "shadowlands",
        "start_city": "Oribos",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "dragon_isles": {
        "title": "Dragon Isles Collection Tour",
        "description": "Collect all quest and vendor decor across the Dragon Isles.",
        "expansion": "dragonflight",
        "start_city": "Valdrakken",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "khaz_algar": {
        "title": "Khaz Algar Collection Tour",
        "description": "Collect all quest and vendor decor in Khaz Algar.",
        "expansion": "tww",
        "start_city": "Dornogal",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
    "undermine": {
        "title": "Undermine Collection Tour",
        "description": "Collect all quest and vendor decor in Undermine.",
        "expansion": "midnight",
        "start_city": "Undermine",
        "start_coords": {"x": 50.0, "y": 50.0},
    },
}

# Approximate zone center coordinates (used for inter-zone distance calculation).
# These are rough map-percentage coordinates (0-100 scale) and are used only
# for relative ordering within a continent, not for precise navigation.
ZONE_CENTERS: dict[str, dict[str, float]] = {
    "Stormwind City": {"x": 50.0, "y": 50.0},
    "Elwynn Forest": {"x": 50.0, "y": 60.0},
    "Westfall": {"x": 40.0, "y": 70.0},
    "Redridge Mountains": {"x": 60.0, "y": 65.0},
    "Duskwood": {"x": 50.0, "y": 75.0},
    "Northern Stranglethorn": {"x": 40.0, "y": 85.0},
    "Cape of Stranglethorn": {"x": 40.0, "y": 90.0},
    "Swamp of Sorrows": {"x": 65.0, "y": 75.0},
    "Blasted Lands": {"x": 65.0, "y": 85.0},
    "Burning Steppes": {"x": 55.0, "y": 55.0},
    "Searing Gorge": {"x": 50.0, "y": 50.0},
    "Badlands": {"x": 60.0, "y": 50.0},
    "Loch Modan": {"x": 55.0, "y": 45.0},
    "Wetlands": {"x": 50.0, "y": 40.0},
    "Dun Morogh": {"x": 45.0, "y": 45.0},
    "Ironforge": {"x": 45.0, "y": 42.0},
    "Tirisfal Glades": {"x": 40.0, "y": 20.0},
    "Undercity": {"x": 40.0, "y": 22.0},
    "Silverpine Forest": {"x": 35.0, "y": 25.0},
    "Hillsbrad Foothills": {"x": 45.0, "y": 28.0},
    "Arathi Highlands": {"x": 55.0, "y": 30.0},
    "The Hinterlands": {"x": 55.0, "y": 25.0},
    "Western Plaguelands": {"x": 50.0, "y": 18.0},
    "Eastern Plaguelands": {"x": 60.0, "y": 15.0},
    "Ghostlands": {"x": 65.0, "y": 12.0},
    "Eversong Woods": {"x": 65.0, "y": 8.0},
    "Twilight Highlands": {"x": 70.0, "y": 35.0},
    "Tol Barad": {"x": 75.0, "y": 40.0},

    # Kalimdor
    "Orgrimmar": {"x": 50.0, "y": 30.0},
    "Durotar": {"x": 52.0, "y": 32.0},
    "Northern Barrens": {"x": 45.0, "y": 38.0},
    "Southern Barrens": {"x": 43.0, "y": 48.0},
    "Mulgore": {"x": 35.0, "y": 42.0},
    "Thunder Bluff": {"x": 35.0, "y": 40.0},
    "Stonetalon Mountains": {"x": 35.0, "y": 35.0},
    "Ashenvale": {"x": 40.0, "y": 28.0},
    "Darkshore": {"x": 30.0, "y": 20.0},
    "Teldrassil": {"x": 25.0, "y": 12.0},
    "Darnassus": {"x": 25.0, "y": 10.0},
    "Felwood": {"x": 38.0, "y": 22.0},
    "Winterspring": {"x": 48.0, "y": 18.0},
    "Moonglade": {"x": 42.0, "y": 18.0},
    "Azshara": {"x": 55.0, "y": 25.0},
    "Dustwallow Marsh": {"x": 48.0, "y": 52.0},
    "Thousand Needles": {"x": 40.0, "y": 58.0},
    "Desolace": {"x": 28.0, "y": 48.0},
    "Feralas": {"x": 25.0, "y": 58.0},
    "Tanaris": {"x": 48.0, "y": 68.0},
    "Un'Goro Crater": {"x": 40.0, "y": 65.0},
    "Silithus": {"x": 32.0, "y": 72.0},
    "Mount Hyjal": {"x": 42.0, "y": 15.0},
    "Uldum": {"x": 50.0, "y": 78.0},
    "Deepholm": {"x": 50.0, "y": 50.0},
    "Exodar": {"x": 15.0, "y": 15.0},
    "Bloodmyst Isle": {"x": 18.0, "y": 12.0},
    "Azuremyst Isle": {"x": 15.0, "y": 18.0},
}


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def distance(a: dict[str, float], b: dict[str, float]) -> float:
    """Euclidean distance between two coordinate dicts with 'x' and 'y' keys."""
    return math.sqrt((a["x"] - b["x"]) ** 2 + (a["y"] - b["y"]) ** 2)


def nearest_neighbor_sort(
    items: list[dict[str, Any]],
    start: dict[str, float],
) -> list[dict[str, Any]]:
    """
    Sort items by nearest-neighbor heuristic starting from 'start' coordinates.

    Items without coordinates are appended at the end in their original order.
    """
    with_coords = []
    without_coords = []

    for item in items:
        coords = item.get("coords")
        if coords and isinstance(coords, dict) and "x" in coords and "y" in coords:
            with_coords.append(item)
        else:
            without_coords.append(item)

    if not with_coords:
        return items  # nothing to sort

    sorted_items: list[dict[str, Any]] = []
    remaining = list(with_coords)
    current_pos = start

    while remaining:
        # Find nearest item to current position
        best_idx = 0
        best_dist = float("inf")
        for i, item in enumerate(remaining):
            c = item["coords"]
            d = distance(current_pos, c)
            if d < best_dist:
                best_dist = d
                best_idx = i

        nearest = remaining.pop(best_idx)
        sorted_items.append(nearest)
        current_pos = nearest["coords"]

    sorted_items.extend(without_coords)
    return sorted_items


def nearest_zone_order(
    zones: list[str],
    start_zone: str,
) -> list[str]:
    """
    Order zones by nearest-neighbor starting from start_zone.

    Uses ZONE_CENTERS for inter-zone distances. Zones not in ZONE_CENTERS
    are appended at the end.
    """
    known = [z for z in zones if z in ZONE_CENTERS]
    unknown = [z for z in zones if z not in ZONE_CENTERS]

    if not known:
        return zones

    start_coords = ZONE_CENTERS.get(start_zone, {"x": 50.0, "y": 50.0})
    sorted_zones: list[str] = []
    remaining = list(known)
    current_pos = start_coords

    while remaining:
        best_idx = 0
        best_dist = float("inf")
        for i, zone in enumerate(remaining):
            d = distance(current_pos, ZONE_CENTERS[zone])
            if d < best_dist:
                best_dist = d
                best_idx = i

        nearest_zone = remaining.pop(best_idx)
        sorted_zones.append(nearest_zone)
        current_pos = ZONE_CENTERS[nearest_zone]

    sorted_zones.extend(unknown)
    return sorted_zones


# ---------------------------------------------------------------------------
# Route generation
# ---------------------------------------------------------------------------

def group_by_zone(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Group items by their zone field."""
    zones: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        zone = item.get("zone", "Unknown")
        zones.setdefault(zone, []).append(item)
    return zones


def generate_steps(
    zone_items: dict[str, list[dict[str, Any]]],
    zone_order: list[str],
) -> list[dict[str, Any]]:
    """
    Generate an ordered list of steps (QUEST/VENDOR/TRAVEL) from zone-grouped items.

    Inserts TRAVEL steps between zones. Within each zone, sorts items by
    nearest-neighbor proximity.
    """
    steps: list[dict[str, Any]] = []
    step_index = 1
    prev_zone: str | None = None

    for zone in zone_order:
        items = zone_items.get(zone, [])
        if not items:
            continue

        # Insert a TRAVEL step when changing zones
        if prev_zone is not None and zone != prev_zone:
            travel_step = {
                "stepIndex": step_index,
                "type": "TRAVEL",
                "label": f"Travel to {zone}",
                "decorName": None,
                "decorID": None,
                "questID": None,
                "npc": None,
                "npcID": None,
                "mapID": _get_map_id(zone),
                "coords": _get_zone_entry_coords(zone),
                "zone": zone,
                "note": f"Go to {zone}",
            }
            steps.append(travel_step)
            step_index += 1

        # Sort items within zone by proximity
        start = ZONE_CENTERS.get(zone, {"x": 50.0, "y": 50.0})
        sorted_items = nearest_neighbor_sort(items, start)

        for item in sorted_items:
            step_type = _map_source_to_step_type(item.get("source_type", "Quest"))
            decor_name = item.get("decor_name", "Unknown")

            if step_type == "VENDOR":
                label = f"Buy {decor_name}"
            elif step_type == "QUEST":
                label = f"Complete '{item.get('quest_source', decor_name)}'"
            else:
                label = f"Collect {decor_name}"

            coords = item.get("coords")
            coords_list = None
            if coords and isinstance(coords, dict):
                coords_list = [coords.get("x"), coords.get("y")]
            elif coords and isinstance(coords, list):
                coords_list = coords

            step = {
                "stepIndex": step_index,
                "type": step_type,
                "label": label,
                "decorName": decor_name,
                "decorID": item.get("decor_id"),
                "questID": item.get("quest_id"),
                "npc": item.get("npc_name"),
                "npcID": item.get("npc_id"),
                "mapID": item.get("mapID") or _get_map_id(zone),
                "coords": coords_list,
                "zone": zone,
                "note": None,
            }
            steps.append(step)
            step_index += 1

        prev_zone = zone

    return steps


def _map_source_to_step_type(source_type: str) -> str:
    """Map a source type string to a step type enum."""
    mapping = {
        "Quest": "QUEST",
        "Vendor": "VENDOR",
        "Achievement": "QUEST",  # treated like quests for routing purposes
        "Drop": "DUNGEON",
    }
    return mapping.get(source_type, "QUEST")


def _get_map_id(zone: str) -> int | None:
    """Look up the mapID for a zone name."""
    # Import from scrape_wowhead's constant
    from scrape_wowhead import ZONE_MAP_IDS
    return ZONE_MAP_IDS.get(zone)


def _get_zone_entry_coords(zone: str) -> list[float] | None:
    """Get approximate entry coordinates for a zone (used for TRAVEL steps)."""
    center = ZONE_CENTERS.get(zone)
    if center:
        return [center["x"], center["y"]]
    return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point: load merged data, generate routes, save per-region JSON."""
    ROUTES_DIR.mkdir(parents=True, exist_ok=True)

    # Load merged data
    if not MERGED_FILE.exists():
        logger.error("Merged data file not found: %s", MERGED_FILE)
        logger.error("Run merge_sources.py first.")
        sys.exit(1)

    with open(MERGED_FILE, "r", encoding="utf-8") as fh:
        all_items = json.load(fh)
    logger.info("Loaded %d merged items", len(all_items))

    # Filter to only QUEST and VENDOR items (v1 step types)
    quest_vendor_items = [
        item for item in all_items
        if item.get("source_type") in ("Quest", "Vendor")
    ]
    logger.info("Filtered to %d Quest/Vendor items", len(quest_vendor_items))

    # Group items by region.
    # WoWDB zones can be comma-separated (e.g. "Stormwind City, Boralus Harbor").
    # We try each sub-zone and use the first one that matches a known region.
    region_items: dict[str, list[dict[str, Any]]] = {}
    unassigned: list[dict[str, Any]] = []

    for item in quest_vendor_items:
        raw_zone = item.get("zone", "Unknown")
        resolved_zone = None
        resolved_region = None

        # Try each comma-separated sub-zone
        for sub_zone in raw_zone.split(","):
            sub_zone = sub_zone.strip()
            if not sub_zone:
                continue
            region = ZONE_TO_REGION.get(sub_zone)
            if region:
                resolved_zone = sub_zone
                resolved_region = region
                break

        if resolved_region and resolved_zone:
            # Update the item's zone to the resolved single zone for routing
            item["zone"] = resolved_zone
            region_items.setdefault(resolved_region, []).append(item)
        else:
            unassigned.append(item)
            logger.debug("Zone '%s' not mapped to any region", raw_zone)

    if unassigned:
        logger.warning(
            "%d items have zones not mapped to a region. These will be placed "
            "in an 'unassigned' pack.",
            len(unassigned),
        )
        region_items["unassigned"] = unassigned

    # Generate route packs per region
    for region_key, items in region_items.items():
        logger.info("=== Generating route: %s (%d items) ===", region_key, len(items))

        meta = REGION_META.get(region_key, {
            "title": f"{region_key.replace('_', ' ').title()} Collection Tour",
            "description": f"Collect all quest and vendor decor in {region_key.replace('_', ' ').title()}.",
            "start_city": "Unknown",
            "start_coords": {"x": 50.0, "y": 50.0},
        })

        # Group by zone within region
        zone_groups = group_by_zone(items)
        zones = list(zone_groups.keys())
        logger.info("Zones in %s: %s", region_key, zones)

        # Order zones by nearest-neighbor from start city
        start_city = meta.get("start_city", "Unknown")
        ordered_zones = nearest_zone_order(zones, start_city)
        logger.info("Zone visit order: %s", ordered_zones)

        # Generate ordered steps
        steps = generate_steps(zone_groups, ordered_zones)
        logger.info("Generated %d steps for %s", len(steps), region_key)

        # Build pack structure
        expansion = meta.get("expansion", region_key)
        pack_id = f"{expansion}_{region_key}"
        pack = {
            "packID": pack_id,
            "title": meta["title"],
            "description": meta["description"],
            "expansion": expansion,
            "region": region_key,
            "totalSteps": len(steps),
            "steps": steps,
        }

        # Save
        output_file = ROUTES_DIR / f"{pack_id}.json"
        with open(output_file, "w", encoding="utf-8") as fh:
            json.dump(pack, fh, indent=2, ensure_ascii=False)
        logger.info("Saved route to %s", output_file)

    logger.info("Route generation complete. %d packs generated.", len(region_items))


if __name__ == "__main__":
    main()
