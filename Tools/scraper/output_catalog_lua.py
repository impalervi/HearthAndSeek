"""
output_catalog_lua.py - Convert enriched_catalog.json into a Lua data file
for the HearthAndSeek WoW addon.

Reads from data/enriched_catalog.json and outputs to ../../Data/CatalogData.lua
(relative to this script's location, which resolves to HearthAndSeek/Data/CatalogData.lua).

Output: HearthAndSeek/Data/CatalogData.lua
"""

import json
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from output_lua import lua_string, lua_number, lua_value

# Wowhead internal zone IDs → our zone names. Populated in enrich_catalog
# and reused here so the NPC-fallback promotion (Wowhead sourcemore lookup
# for items with empty in-game sourceText) can fill in zone + mapID.
from enrich_catalog import WH_ZONE_ID_TO_NAME

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"
EXTRA_JSON = SCRIPT_DIR / "data" / "enriched_catalog_extra.json"
BOSS_DUMP_JSON = SCRIPT_DIR / "data" / "boss_dump.json"
FACTION_QUEST_OVERRIDES_JSON = SCRIPT_DIR / "data" / "faction_quest_overrides.json"
VENDOR_REQUIREMENTS_JSON = SCRIPT_DIR / "data" / "vendor_requirements.json"
SILVERMOON_VENDORS_JSON = SCRIPT_DIR / "data" / "silvermoon_vendors.json"
THEMES_JSON = SCRIPT_DIR / "data" / "item_themes.json"
KEYWORDS_JSON = SCRIPT_DIR / "data" / "item_keywords.json"
VERSIONS_JSON = SCRIPT_DIR / "data" / "item_versions.json"
LUA_OUTPUT = SCRIPT_DIR.parent.parent / "Data" / "CatalogData.lua"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("output_catalog_lua")

# ---------------------------------------------------------------------------
# Source type / detail derivation
# ---------------------------------------------------------------------------

SOURCE_PRIORITY = ["Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Vendor", "Shop"]

# Known base profession names for parsing sourceDetail strings like
# "Midnight Tailoring (50)" → "Tailoring"
PROFESSION_NAMES = [
    "Alchemy", "Blacksmithing", "Cooking", "Enchanting", "Engineering",
    "Inscription", "Jewelcrafting", "Leatherworking", "Tailoring",
    "Mining", "Herbalism", "Skinning",
]


def _has_source_type(item: dict[str, Any], stype: str) -> bool:
    for s in (item.get("sources") or []):
        if s.get("type") == stype:
            return True
    return False


def _has_category(item: dict[str, Any], value: str) -> bool:
    for s in (item.get("sources") or []):
        if s.get("type") == "Category" and (s.get("value") or "").lower() == value.lower():
            return True
    return False


def get_primary_source_type(item: dict[str, Any]) -> str:
    """Determine the primary source type using smarter priority rules.

    Key changes from the original:
    - Achievement > Vendor: when an item requires an achievement to purchase from
      a vendor, the achievement is the real gate; the vendor is just redemption.
    - Prey: items with a Category source of "Prey" get their own type.
    - Faction remapped to Vendor.
    """
    source_types = set()
    for s in (item.get("sources") or []):
        t = s.get("type")
        if t:
            source_types.add(t)

    # Prey detection: Category source with value "Prey"
    if _has_category(item, "Prey"):
        return "Prey"

    # Achievement takes priority over Vendor (vendor is just the redemption)
    if "Achievement" in source_types and "Vendor" in source_types:
        return "Achievement"

    for priority in SOURCE_PRIORITY:
        if priority in source_types:
            return priority
    if "Faction" in source_types:
        return "Vendor"
    # Check sourceTextRaw for Shop items not caught by parser
    raw = (item.get("sourceTextRaw") or "")
    if "Shop|r" in raw or "In-Game Shop|r" in raw:
        return "Shop"
    return "Other"


def get_source_detail(item: dict[str, Any], source_type: str) -> str:
    """Return the detail string for the determined source type."""
    if source_type == "Quest" and item.get("quest"):
        return item["quest"]
    if source_type == "Achievement" and item.get("achievement"):
        return item["achievement"]
    if source_type == "Prey" and item.get("achievement"):
        return item["achievement"]
    if source_type == "Vendor" and item.get("vendor"):
        return item["vendor"]
    if source_type == "Profession" and item.get("profession"):
        return item["profession"]
    # Fallback: search sources array for Drop/Treasure/etc.
    for s in (item.get("sources") or []):
        if s.get("type") == source_type and s.get("value"):
            return s["value"]
    # Last resort: return the first non-empty detail we can find
    for field in ("quest", "vendor", "achievement", "profession"):
        if item.get(field):
            return item[field]
    for s in (item.get("sources") or []):
        if s.get("type") in ("Drop", "Treasure") and s.get("value"):
            return s["value"]
    return ""


def parse_profession_name(detail: str) -> str:
    """Extract base profession name from strings like 'Midnight Tailoring (50)'."""
    if not detail:
        return ""
    for prof in PROFESSION_NAMES:
        if prof.lower() in detail.lower():
            return prof
    # Special case: Junkyard Tinkering → Engineering
    if "tinkering" in detail.lower():
        return "Engineering"
    return ""


def get_achievement_name(item: dict[str, Any]) -> str:
    """Get achievement name regardless of primary source type."""
    if item.get("achievement"):
        return item["achievement"]
    for s in (item.get("sources") or []):
        if s.get("type") == "Achievement" and s.get("value"):
            return s["value"]
    return ""


def get_vendor_name(item: dict[str, Any]) -> str:
    """Get vendor name regardless of primary source type."""
    if item.get("vendor"):
        return item["vendor"]
    for s in (item.get("sources") or []):
        if s.get("type") == "Vendor" and s.get("value"):
            return s["value"]
    return ""


# ---------------------------------------------------------------------------
# Vendor cost extraction from sourceTextRaw
# ---------------------------------------------------------------------------

# Regex patterns for parsing cost data from WoW escape sequences in sourceTextRaw.
# Currency: amount|Hcurrency:ID|h|TiconPath:0|t|h
# Gold:     amount|TINTERFACE\MONEYFRAME\UI-GOLDICON.BLP:0|t
_RE_CURRENCY = re.compile(
    r'(\d+)\|Hcurrency:(\d+)\|h\|T([^:]+):0\|t\|h', re.IGNORECASE
)
_RE_GOLD = re.compile(
    r'(\d+)\|TINTERFACE.MONEYFRAME.UI-GOLDICON', re.IGNORECASE
)


def extract_costs(source_text_raw: str) -> list[dict[str, Any]]:
    """
    Parse vendor costs from a sourceTextRaw string.

    Returns a list of cost entries:
      [{"amount": 100, "currencyID": 824, "iconPath": "interface\\ICONS\\..."}, ...]
    Gold uses currencyID=0, iconPath="INTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP".
    """
    if not source_text_raw:
        return []

    costs: list[dict[str, Any]] = []
    seen_currencies: set[int] = set()

    # Extract currency costs
    for m in _RE_CURRENCY.finditer(source_text_raw):
        amount = int(m.group(1))
        currency_id = int(m.group(2))
        icon_path = m.group(3)
        if currency_id not in seen_currencies:
            seen_currencies.add(currency_id)
            costs.append({
                "amount": amount,
                "currencyID": currency_id,
                "iconPath": icon_path,
            })

    # Extract gold cost
    for m in _RE_GOLD.finditer(source_text_raw):
        amount = int(m.group(1))
        if 0 not in seen_currencies:
            seen_currencies.add(0)
            costs.append({
                "amount": amount,
                "currencyID": 0,
                "iconPath": "INTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP",
            })

    return costs


# ---------------------------------------------------------------------------
# Zone-to-expansion mapping
# ---------------------------------------------------------------------------

ZONE_TO_EXPANSION: dict[str, str] = {
    # Classic
    "Stormwind City": "Classic",
    "Hillsbrad Foothills": "Classic",
    "Ironforge": "Classic",
    "Dun Morogh": "Classic",
    "Elwynn Forest": "Classic",
    "Duskwood": "Classic",
    "Loch Modan": "Classic",
    "Searing Gorge": "Classic",
    "Burning Steppes": "Classic",
    "Eastern Plaguelands": "Classic",
    "Mulgore": "Classic",
    "Northern Stranglethorn": "Classic",
    "Silverpine Forest": "Classic",
    "Blasted Lands": "Classic",
    "Felwood": "Classic",
    "Wetlands": "Classic",
    "Teldrassil": "Classic",
    "Deeprun Tram": "Classic",
    "Blackrock Depths": "Classic",
    "Deadmines": "Classic",
    "Darkshore": "Classic",
    "Shadowfang Keep": "Classic",

    # The Burning Crusade
    # Most Quel'Thalas zones moved to Midnight, but Ghostlands remains TBC-only
    # (requires Zidormi timeline switch to access the old TBC version)
    "Ghostlands": "The Burning Crusade",

    # Wrath of the Lich King
    "Borean Tundra": "Wrath of the Lich King",
    "Grizzly Hills": "Wrath of the Lich King",
    "Sholazar Basin": "Wrath of the Lich King",
    "Acherus: The Ebon Hold": "Wrath of the Lich King",
    "Pit of Saron": "Wrath of the Lich King",
    "Rescue Koltira": "Wrath of the Lich King",

    # Cataclysm
    "Twilight Highlands": "Cataclysm",
    "Ruins of Gilneas": "Cataclysm",

    # Mists of Pandaria
    "Kun-Lai Summit": "Mists of Pandaria",
    "Vale of Eternal Blossoms": "Mists of Pandaria",
    "Valley of the Four Winds": "Mists of Pandaria",
    "The Jade Forest": "Mists of Pandaria",
    "The Wandering Isle": "Mists of Pandaria",
    "Temple of the Jade Serpent": "Mists of Pandaria",
    "Siege of Orgrimmar": "Mists of Pandaria",

    # Warlords of Draenor
    "Spires of Arak": "Warlords of Draenor",
    "Lunarfall": "Warlords of Draenor",
    "Frostwall": "Warlords of Draenor",
    "Stormshield": "Warlords of Draenor",
    "Talador": "Warlords of Draenor",
    "Warspear": "Warlords of Draenor",
    "Iron Docks": "Warlords of Draenor",
    "Skyreach": "Warlords of Draenor",
    "Shadowmoon Valley": "Warlords of Draenor",

    # Legion
    "Highmountain": "Legion",
    "Val'sharah": "Legion",
    "Suramar": "Legion",
    "Azsuna": "Legion",
    "Dalaran": "Legion",
    "Dalaran Sewers": "Legion",
    "Dreadscar Rift": "Legion",
    "Hall of the Guardian": "Legion",
    "Trueshot Lodge": "Legion",
    "The Dreamgrove": "Legion",
    "The Maelstrom": "Legion",
    "Mardum, the Shattered Abyss": "Legion",
    "Skyhold": "Legion",
    "Netherlight Temple": "Legion",
    "Slayer's Rise": "Legion",
    "Court of Stars": "Legion",
    "Darkheart Thicket": "Legion",
    "Neltharion's Lair": "Legion",
    "The Nighthold": "Legion",
    "Karazhan": "Legion",
    "Antoran Wastes": "Legion",
    "The Seat of the Triumvirate": "Legion",

    # Battle for Azeroth
    "Zuldazar": "Battle for Azeroth",
    "Dazar'alor": "Battle for Azeroth",
    "Nazmir": "Battle for Azeroth",
    "Stormsong Valley": "Battle for Azeroth",
    "Tiragarde Sound": "Battle for Azeroth",
    "Freehold": "Battle for Azeroth",
    "Mechagon": "Battle for Azeroth",
    "Orgrimmar": "Battle for Azeroth",
    "Shrine of the Storm": "Battle for Azeroth",
    "Crucible of Storms": "Battle for Azeroth",
    "Chamber of Heart": "Battle for Azeroth",

    # Shadowlands
    "Revendreth": "Shadowlands",
    "Sinfall": "Shadowlands",
    "The Maw": "Shadowlands",

    # Dragonflight
    "The Waking Shores": "Dragonflight",
    "Thaldraszus": "Dragonflight",
    "Valdrakken": "Dragonflight",
    "The Forbidden Reach": "Dragonflight",
    "Neltharus": "Dragonflight",
    "Algeth'ar Academy": "Dragonflight",
    "Amirdrassil": "Dragonflight",

    # The War Within
    "Dornogal": "The War Within",
    "Hallowfall": "The War Within",
    "The Ringing Deeps": "The War Within",
    "Isle of Dorn": "The War Within",
    "City of Threads": "The War Within",
    "Priory of the Sacred Flame": "The War Within",
    "Cinderbrew Meadery": "The War Within",

    # Neighborhoods (player housing zones)
    "Founder's Point": "Neighborhoods",
    "Razorwind Shores": "Neighborhoods",

    # Midnight
    "Harandar": "Midnight",
    "Voidstorm": "Midnight",
    "The Voidspire": "Midnight",
    "The Dreamrift": "Midnight",
    "Windrunner Spire": "Midnight",
    "Zul'Aman": "Midnight",
    "Eversong Woods": "Midnight",
    "Silvermoon City": "Midnight",
    "Isle of Quel'Danas": "Midnight",
    "Murder Row": "Midnight",
    "Magisters' Terrace": "Midnight",
    "The Blinding Vale": "Midnight",
    "Voidscar Arena": "Midnight",
    "March on Quel'Danas": "Midnight",
    "Arcantina": "Midnight",
    "Maisara Caverns": "Midnight",
    "Masters' Perch": "Midnight",
    "Nexus-Point Xenas": "Midnight",
    "Midnight Delves": "Midnight",  # catch-all for delve / world drops

    # The War Within (patch content released during TWW)
    "K'aresh": "The War Within",
    "Undermine": "The War Within",
    "Liberation of Undermine": "The War Within",
}

# ---------------------------------------------------------------------------
# Zone name → uiMapID mapping.
# Pipeline-emitted so the addon doesn't need HereBeDragons at runtime.
# Midnight zones use the new Midnight-era mapIDs (not the old TBC ones).
# Values collected via /hs debug zones in-game dump.
# ---------------------------------------------------------------------------
ZONE_TO_MAPID: dict[str, int] = {
    # Eastern Kingdoms
    "Blasted Lands": 17,
    "Burning Steppes": 36,
    "Darkshore": 62,
    "Dun Morogh": 27,
    "Duskwood": 47,
    "Eastern Plaguelands": 23,
    "Elwynn Forest": 37,
    "Loch Modan": 48,
    "Northern Stranglethorn": 50,
    "Ruins of Gilneas": 217,
    "Searing Gorge": 32,
    "Silverpine Forest": 21,
    "Hillsbrad Foothills": 25,
    "Ironforge": 87,
    "Stormwind City": 84,
    "The Cape of Stranglethorn": 210,
    "Twilight Highlands": 241,
    "Westfall": 52,
    "Wetlands": 56,
    # Kalimdor
    "Darkshore": 62,
    "Dustwallow Marsh": 70,
    "Felwood": 77,
    "Mulgore": 7,
    "Orgrimmar": 85,
    "Silithus": 81,
    "Teldrassil": 57,
    "Thunder Bluff": 88,
    "Winterspring": 83,
    # Outland / TBC
    "Ghostlands": 95,         # stays TBC (Zidormi timeline for Provisioner Vredigar)
    "Nagrand": 107,
    "Shadowmoon Valley": 104,
    # Northrend
    "Borean Tundra": 114,
    "Crystalsong Forest": 127,
    "Dalaran": 41,
    "Dalaran Sewers": 628,    # The Underbelly (Legion Dalaran sub-zone)
    "Grizzly Hills": 116,
    "Icecrown": 118,
    "Sholazar Basin": 119,
    # Cataclysm / phased
    "Deeprun Tram": 499,
    "Gilneas": 179,
    "Gilneas City": 202,
    "Northshire": 425,
    # Pandaria
    "Kun-Lai Summit": 379,
    "The Jade Forest": 371,
    "The Wandering Isle": 378,
    "Vale of Eternal Blossoms": 390,
    "Valley of the Four Winds": 376,
    # Draenor
    "Frostfire Ridge": 525,
    "Frostwall": 590,
    "Gorgrond": 543,
    "Lunarfall": 582,
    "Spires of Arak": 542,
    "Stormshield": 622,
    "Talador": 535,
    "Warspear": 624,
    # Legion
    "Antoran Wastes": 885,
    "Azsuna": 630,
    "Dreadscar Rift": 717,
    "Hall of the Guardian": 734,
    "Highmountain": 650,
    "Mac'Aree": 882,
    "Mardum, the Shattered Abyss": 672,
    "Netherlight Temple": 702,
    "Skyhold": 695,
    "Suramar": 680,
    "The Dreamgrove": 747,
    "The Maelstrom": 276,
    "Trueshot Lodge": 739,
    "Val'sharah": 641,
    # Battle for Azeroth
    "Boralus": 1161,
    "Dazar'alor": 1163,
    "Drustvar": 896,
    "Mechagon": 1462,
    "Nazmir": 863,
    "Stormsong Valley": 942,
    "Tiragarde Sound": 895,
    "Vol'dun": 864,
    "Zuldazar": 862,
    # Shadowlands
    "Revendreth": 1525,
    "Sinfall": 1699,
    "Oribos": 1670,
    "The Maw": 1543,
    # Dragonflight
    "Thaldraszus": 2025,
    "The Azure Span": 2024,
    "The Forbidden Reach": 2107,
    "The Waking Shores": 2022,
    "Valdrakken": 2112,
    # The War Within
    "Dornogal": 2339,
    "Hallowfall": 2215,
    "Isle of Dorn": 2248,
    "The Ringing Deeps": 2214,
    "Undermine": 2346,
    "City of Threads": 2213,
    "Liberation of Undermine": 2406,  # raid instance map
    # Midnight (Quel'Thalas) — use Midnight-era mapIDs, NOT old TBC ones
    "Eversong Woods": 2395,
    "Silvermoon City": 2393,
    "Isle of Quel'Danas": 2424,
    "Harandar": 2413,
    "K'aresh": 2472,             # Tazavesh / Veiled Market
    "Masters' Perch": 2444,
    "Murder Row": 2433,
    "Slayer's Rise": 2397,
    "The Voidstorm": 2405,
    "Voidstorm": 2405,            # alias without "The"
    "Arcantina": 2541,
    # Midnight dungeons/raids (zone maps)
    "Cinderbrew Meadery": 2335,
    "Magisters' Terrace": 348,
    "Maisara Caverns": 2501,
    "Nexus-Point Xenas": 2405,   # delve in The Voidstorm
    "Voidscar Arena": 2572,
    "Windrunner Spire": 2492,
    "The Blinding Vale": 2500,
    "The Dreamrift": 2531,
    "The Voidspire": 2529,
    "March on Quel'Danas": 2533,
    "Zul'Aman": 2437,
    # Midnight other
    "Chamber of Heart": 1021,
    "Amirdrassil": 2239,          # Bel'ameth (outdoor tree)
    "Midnight Delves": 2537,       # Quel'Thalas continent map
    # Neighborhoods (player housing)
    "Founder's Point": 2352,
    "Razorwind Shores": 2351,
    # Instances used as zones (non-outdoor)
    "Blackrock Depths": 242,
    "Court of Stars": 761,
    "Darkheart Thicket": 733,
    "Deadmines": 55,
    "Deadwind Pass": 42,
    "Freehold": 936,
    "Iron Docks": 595,
    "Karazhan": 350,
    "Neltharion's Lair": 731,
    "Neltharus": 2080,
    "Pit of Saron": 184,
    "Priory of the Sacred Flame": 2308,
    "Rescue Koltira": 118,       # DK quest scenario in Icecrown
    "Shadowfang Keep": 310,
    "Shrine of the Storm": 1039,
    "Siege of Orgrimmar": 556,
    "Skyreach": 601,
    "Temple of the Jade Serpent": 429,
    "The Nighthold": 764,
    "The Seat of the Triumvirate": 903,
    "Algeth'ar Academy": 2097,
    "Crucible of Storms": 1345,
    # Class halls / special
    "Acherus: The Ebon Hold": 647,
    "Undercity": 90,
}

# Midnight zone mapIDs that are real navigable zones despite being nested
# under a parent Zone (not directly under a Continent). Emitted as
# NS.CatalogData.TrustedZoneIDs so the runtime can skip parent-walk for these.
TRUSTED_ZONE_IDS: dict[int, bool] = {
    2393: True,  # Silvermoon City (parent=Eversong Woods)
    2395: True,  # Eversong Woods
    2405: True,  # The Voidstorm
    2413: True,  # Harandar
    2424: True,  # Isle of Quel'Danas
    2437: True,  # Zul'Aman
    2444: True,  # Masters' Perch (sub-zone of Voidstorm)
}

# ---------------------------------------------------------------------------
# Vendor coordinate overrides — curated reference data.
# Applied in serialize_item() to fix NPC IDs, coordinates, and zones.
# Coords are slightly offset (~+0.1) from the reference source.
# Keys are vendor names (matching vendorName field in catalog items).
#
# Some vendor names are shared between different NPCs in different zones
# (e.g. "Rae'ana" exists in both The Waking Shores and Silvermoon City).
# VENDOR_COORDS_BY_ZONE provides zone-specific entries for these; the
# resolver in resolve_vendor_override() checks it first, falling back to
# the name-only VENDOR_COORDS entry.
# ---------------------------------------------------------------------------
VENDOR_COORDS: dict[str, dict] = {
    # === Classic — Eastern Kingdoms ===
    "Captain Lancy Revshon":   {"npcID": 49877,  "x": 67.9, "y": 73.2, "mapID": 84,   "zone": "Stormwind City"},
    "Riica":                   {"npcID": 254603, "x": 77.9, "y": 65.9, "mapID": 84,   "zone": "Stormwind City"},
    "Solelo":                  {"npcID": 256071, "x": 49.1, "y": 80.2, "mapID": 84,   "zone": "Stormwind City"},
    "Tuuran":                  {"npcID": 261231, "x": 48.7, "y": 69.0, "mapID": 84,   "zone": "Stormwind City"},
    "Captain Stonehelm":       {"npcID": 50309,  "x": 55.7, "y": 48.4, "mapID": 87,   "zone": "Ironforge"},
    "Dedric Sleetshaper":      {"npcID": 253235, "x": 24.8, "y": 44.1, "mapID": 87,   "zone": "Ironforge"},
    "Inge Brightview":         {"npcID": 253232, "x": 75.9, "y": 9.6,  "mapID": 87,   "zone": "Ironforge"},
    "Stuart Fleming":          {"npcID": 3178,   "x": 6.4,  "y": 57.6, "mapID": 56,   "zone": "Wetlands"},
    "Fiona":                   {"npcID": 45417,  "x": 73.9, "y": 52.4, "mapID": 23,   "zone": "Eastern Plaguelands"},
    "Hoddruc Bladebender":     {"npcID": 115805, "x": 46.9, "y": 44.8, "mapID": 36,   "zone": "Burning Steppes"},
    "Maurice Essman":          {"npcID": 44337,  "x": 45.9, "y": 88.8, "mapID": 17,   "zone": "Blasted Lands"},
    "Wilkinson":               {"npcID": 44114,  "x": 20.4, "y": 58.5, "mapID": 47,   "zone": "Duskwood"},
    "Drac Roughcut":           {"npcID": 1465,   "x": 35.7, "y": 49.2, "mapID": 48,   "zone": "Loch Modan"},
    "Edwin Harly":             {"npcID": 2140,   "x": 44.2, "y": 39.8, "mapID": 21,   "zone": "Silverpine Forest"},
    "Jacquilina Dramet":       {"npcID": 2483,   "x": 43.9, "y": 23.4, "mapID": 50,   "zone": "Northern Stranglethorn"},
    "Master Smith Burninate":  {"npcID": 14624,  "x": 38.7, "y": 28.9, "mapID": 32,   "zone": "Searing Gorge"},
    "Thanthaldis Snowgleam":   {"npcID": 13217,  "x": 44.9, "y": 46.6, "mapID": 25,   "zone": "Hillsbrad Foothills"},
    # === Classic — Kalimdor ===
    "Brave Tuho":              {"npcID": 50483,  "x": 46.3, "y": 50.8, "mapID": 88,   "zone": "Thunder Bluff"},
    "Stone Guard Nargol":      {"npcID": 50488,  "x": 50.3, "y": 58.6, "mapID": 85,   "zone": "Orgrimmar"},
    "Joruh":                   {"npcID": 254606, "x": 38.9, "y": 72.1, "mapID": 85,   "zone": "Orgrimmar"},
    "Lonalo":                  {"npcID": 256119, "x": 58.5, "y": 50.8, "mapID": 85,   "zone": "Orgrimmar"},
    "Gabbi":                   {"npcID": 261262, "x": 48.5, "y": 81.2, "mapID": 85,   "zone": "Orgrimmar"},
    "Axle":                    {"npcID": 23995,  "x": 42.0, "y": 74.1, "mapID": 70,   "zone": "Dustwallow Marsh"},
    "Plugger Spazzring":       {"npcID": 144129, "x": 49.9, "y": 32.4, "mapID": 1186, "zone": "Blackrock Depths"},
    "Innkeeper Belm":          {"npcID": 1247,   "x": 54.5, "y": 51.0, "mapID": 27,   "zone": "Dun Morogh"},
    # === Cataclysm ===
    "Marie Allen":             {"npcID": 211065, "x": 60.5, "y": 92.6, "mapID": 217,  "zone": "Ruins of Gilneas"},
    "Lord Candren":            {"npcID": 50307,  "x": 57.0, "y": 56.1, "mapID": 217,  "zone": "Ruins of Gilneas"},
    "Samantha Buckley":        {"npcID": 216888, "x": 65.3, "y": 47.4, "mapID": 217,  "zone": "Ruins of Gilneas"},
    "Breana Bitterbrand":      {"npcID": 253227, "x": 49.7, "y": 29.8, "mapID": 241,  "zone": "Twilight Highlands"},
    "Craw MacGraw":            {"npcID": 49386,  "x": 48.7, "y": 30.8, "mapID": 241,  "zone": "Twilight Highlands"},
    "Materialist Ophinell":    {"npcID": 249196, "x": 49.8, "y": 81.2, "mapID": 241,  "zone": "Twilight Highlands"},
    # === The Burning Crusade ===
    "Provisioner Vredigar":    {"npcID": 16528,  "x": 47.7, "y": 32.6, "mapID": 95,   "zone": "Ghostlands"},
    # === Wrath of the Lich King ===
    "Purser Boulian":          {"npcID": 28038,  "x": 26.9, "y": 59.4, "mapID": 119,  "zone": "Sholazar Basin"},
    "Woodsman Drake":          {"npcID": 27391,  "x": 32.5, "y": 60.0, "mapID": 116,  "zone": "Grizzly Hills"},
    "Ahlurglgr":               {"npcID": 25206,  "x": 43.1, "y": 13.9, "mapID": 114,  "zone": "Borean Tundra"},
    # === Mists of Pandaria ===
    "San Redscale":            {"npcID": 58414,  "x": 56.9, "y": 44.6, "mapID": 371,  "zone": "The Jade Forest"},
    "Frederick the Fabulous":  {"npcID": 253602, "x": 57.8, "y": 15.8, "mapID": 371,  "zone": "The Jade Forest"},
    "Brother Furtrim":         {"npcID": 59698,  "x": 57.3, "y": 61.1, "mapID": 379,  "zone": "Kun-Lai Summit"},
    "Gina Mudclaw":            {"npcID": 58706,  "x": 53.3, "y": 52.0, "mapID": 376,  "zone": "Valley of the Four Winds"},
    "Sage Lotusbloom":         {"npcID": 64001,  "x": 62.9, "y": 23.4, "mapID": 390,  "zone": "Vale of Eternal Blossoms"},
    "Sage Whiteheart":         {"npcID": 64032,  "x": 85.3, "y": 61.8, "mapID": 1530, "zone": "Vale of Eternal Blossoms"},
    "Tan Shin Tiao":           {"npcID": 64605,  "x": 82.3, "y": 29.5, "mapID": 390,  "zone": "Vale of Eternal Blossoms"},
    "Lali the Assistant":      {"npcID": 62088,  "x": 82.9, "y": 31.0, "mapID": 390,  "zone": "Vale of Eternal Blossoms"},
    # === Warlords of Draenor ===
    "Moz'def":                 {"npcID": 79812,  "x": 48.1, "y": 66.2, "mapID": 525,  "zone": "Frostfire Ridge"},
    "Supplymaster Eri":        {"npcID": 76872,  "x": 48.1, "y": 66.2, "mapID": 525,  "zone": "Frostfire Ridge"},
    "Sergeant Grimjaw":        {"npcID": 79774,  "x": 43.9, "y": 47.6, "mapID": 590,  "zone": "Frostwall"},
    "Kil'rip":                 {"npcID": 87015,  "x": 48.1, "y": 66.2, "mapID": 525,  "zone": "Frostfire Ridge"},
    "Vora Strongarm":          {"npcID": 87312,  "x": 48.1, "y": 66.2, "mapID": 525,  "zone": "Frostfire Ridge"},
    "Sergeant Crowler":        {"npcID": 78564,  "x": 38.6, "y": 31.6, "mapID": 582,  "zone": "Lunarfall"},
    "Maaria":                  {"npcID": 85427,  "x": 31.1, "y": 15.2, "mapID": 539,  "zone": "Shadowmoon Valley"},
    "Peter":                   {"npcID": 88220,  "x": 31.1, "y": 15.2, "mapID": 539,  "zone": "Shadowmoon Valley"},
    "Artificer Kallaes":       {"npcID": 81133,  "x": 46.3, "y": 39.5, "mapID": 539,  "zone": "Shadowmoon Valley"},
    "Ruuan the Seer":          {"npcID": 87775,  "x": 46.7, "y": 45.2, "mapID": 542,  "zone": "Spires of Arak"},
    "Trader Caerel":           {"npcID": 85950,  "x": 41.5, "y": 60.0, "mapID": 622,  "zone": "Stormshield"},
    "Vindicator Nuurem":       {"npcID": 85932,  "x": 46.5, "y": 74.8, "mapID": 622,  "zone": "Stormshield"},
    "Shadow-Sage Brakoss":     {"npcID": 85946,  "x": 46.6, "y": 75.2, "mapID": 622,  "zone": "Stormshield"},
    "Ravenspeaker Skeega":     {"npcID": 86037,  "x": 53.4, "y": 60.1, "mapID": 624,  "zone": "Warspear"},
    "Duskcaller Erthix":       {"npcID": 256946, "x": 70.5, "y": 57.8, "mapID": 535,  "zone": "Talador"},
    # === Legion ===
    "Toraan the Revered":      {"npcID": 127151, "x": 68.3, "y": 57.1, "mapID": 831,  "zone": "Antoran Wastes"},
    "Berazus":                 {"npcID": 89939,  "x": 47.9, "y": 23.8, "mapID": 630,  "zone": "Azsuna"},
    "Rasil Fireborne":         {"npcID": 112716, "x": 43.5, "y": 49.6, "mapID": 627,  "zone": "Dalaran"},
    "Halenthos Brightstride":  {"npcID": 252043, "x": 67.6, "y": 34.0, "mapID": 627,  "zone": "Dalaran"},
    "Val'zuun":                {"npcID": 105333, "x": 67.5, "y": 63.4, "mapID": 628,  "zone": "Dalaran Sewers"},
    "Ransa Greyfeather":       {"npcID": 106902, "x": 38.2, "y": 46.2, "mapID": 750,  "zone": "Highmountain"},
    "Torv Dubstomp":           {"npcID": 108017, "x": 54.9, "y": 78.2, "mapID": 652,  "zone": "Highmountain"},
    "Crafty Palu":             {"npcID": 108537, "x": 41.7, "y": 10.6, "mapID": 650,  "zone": "Highmountain"},
    "First Arcanist Thalyssra": {"npcID": 115736, "x": 36.6, "y": 46.0, "mapID": 680, "zone": "Suramar"},
    "Leyweaver Inondra":       {"npcID": 93971,  "x": 40.4, "y": 69.9, "mapID": 680,  "zone": "Suramar"},
    "Jocenna":                 {"npcID": 252969, "x": 49.7, "y": 63.0, "mapID": 680,  "zone": "Suramar"},
    "Mynde":                   {"npcID": 255101, "x": 45.7, "y": 69.3, "mapID": 680,  "zone": "Suramar"},
    "Sileas Duskvine":         {"npcID": 253434, "x": 80.0, "y": 74.0, "mapID": 641,  "zone": "Val'sharah"},
    "Sundries Merchant":       {"npcID": 248594, "x": 51.0, "y": 77.9, "mapID": 680,  "zone": "Suramar"},
    "Selfira Ambergrove":      {"npcID": 253387, "x": 54.4, "y": 72.5, "mapID": 641,  "zone": "Val'sharah"},
    "Sylvia Hartshorn":        {"npcID": 106901, "x": 54.8, "y": 73.4, "mapID": 641,  "zone": "Val'sharah"},
    "Corbin Branbell":         {"npcID": 252498, "x": 42.2, "y": 59.5, "mapID": 641,  "zone": "Val'sharah"},
    "Hilseth Travelstride":    {"npcID": 112634, "x": 57.2, "y": 72.1, "mapID": 641,  "zone": "Val'sharah"},
    "Myria Glenbrook":         {"npcID": 109306, "x": 60.3, "y": 85.0, "mapID": 641,  "zone": "Val'sharah"},
    "Mrgrgrl":                 {"npcID": 256826, "x": 68.8, "y": 95.3, "mapID": 641,  "zone": "Val'sharah"},
    # Legion class halls
    "Falara Nightsong":        {"npcID": 112407, "x": 61.1, "y": 56.9, "mapID": 720,  "zone": "Mardum, the Shattered Abyss"},
    "Eadric the Pure":         {"npcID": 100196, "x": 75.7, "y": 49.2, "mapID": 23,   "zone": "Eastern Plaguelands"},
    "Outfitter Reynolds":      {"npcID": 103693, "x": 44.7, "y": 49.0, "mapID": 739,  "zone": "Trueshot Lodge"},
    "Amurra Thistledew":       {"npcID": 112323, "x": 40.1, "y": 17.9, "mapID": 747,  "zone": "The Dreamgrove"},
    "Kelsey Steelspark":       {"npcID": 105986, "x": 27.0, "y": 37.0, "mapID": 626,  "zone": "Dalaran"},
    "Caydori Brightstar":      {"npcID": 112338, "x": 50.5, "y": 59.2, "mapID": 709,  "zone": "Kun-Lai Summit"},
    "Quartermaster Ozorg":     {"npcID": 93550,  "x": 44.0, "y": 37.3, "mapID": 647,  "zone": "Acherus: The Ebon Hold"},
    "Gigi Gigavoid":           {"npcID": 112434, "x": 58.9, "y": 32.8, "mapID": 717,  "zone": "Dreadscar Rift"},
    "Jackson Watkins":         {"npcID": 112440, "x": 44.9, "y": 58.0, "mapID": 735,  "zone": "Hall of the Guardian"},
    "Flamesmith Lanying":      {"npcID": 112318, "x": 30.4, "y": 60.8, "mapID": 726,  "zone": "The Maelstrom"},
    "Quartermaster Durnolf":   {"npcID": 112392, "x": 55.6, "y": 26.1, "mapID": 695,  "zone": "Skyhold"},
    "Meridelle Lightspark":    {"npcID": 112401, "x": 38.7, "y": 23.9, "mapID": 702,  "zone": "Netherlight Temple"},
    # === Battle for Azeroth ===
    "MOTHER":                  {"npcID": 152194, "x": 48.4, "y": 72.3, "mapID": 1473, "zone": "Chamber of Heart"},
    "Caspian":                 {"npcID": 252313, "x": 59.7, "y": 69.8, "mapID": 942,  "zone": "Stormsong Valley"},
    "Stolen Royal Vendorbot":  {"npcID": 150716, "x": 73.8, "y": 37.1, "mapID": 1462, "zone": "Mechagon"},
    "Provisioner Fray":        {"npcID": 135808, "x": 67.7, "y": 22.0, "mapID": 1161, "zone": "Boralus"},
    "Pearl Barlow":            {"npcID": 252345, "x": 70.8, "y": 15.8, "mapID": 1161, "zone": "Boralus"},
    "Janey Forrest":           {"npcID": 246721, "x": 56.4, "y": 46.0, "mapID": 1161, "zone": "Boralus"},
    "Delphine":                {"npcID": 252316, "x": 53.5, "y": 31.4, "mapID": 895,  "zone": "Tiragarde Sound"},
    "Provisioner Lija":        {"npcID": 135459, "x": 39.2, "y": 79.6, "mapID": 863,  "zone": "Nazmir"},
    "Provisioner Mukra":       {"npcID": 148924, "x": 51.3, "y": 95.2, "mapID": 1165, "zone": "Dazar'alor"},
    "Captain Zen'taga":        {"npcID": 148923, "x": 44.7, "y": 94.6, "mapID": 1165, "zone": "Dazar'alor"},
    "Arcanist Peroleth":       {"npcID": 251921, "x": 58.1, "y": 62.8, "mapID": 862,  "zone": "Zuldazar"},
    "T'lama":                  {"npcID": 252326, "x": 37.0, "y": 59.3, "mapID": 1164, "zone": "Dazar'alor"},
    "Captain Donald Adams":    {"npcID": 50304,  "x": 63.3, "y": 49.2, "mapID": 90,   "zone": "Undercity"},
    # === Shadowlands ===
    "Chachi the Artiste":      {"npcID": 174710, "x": 54.1, "y": 25.0, "mapID": 1699, "zone": "Sinfall"},
    "Ve'nari":                 {"npcID": 162804, "x": 46.9, "y": 41.8, "mapID": 1543, "zone": "The Maw"},
    # === Dragonflight ===
    "Tethalash":               {"npcID": 196637, "x": 25.6, "y": 33.8, "mapID": 2112, "zone": "Valdrakken"},
    "Unatos":                  {"npcID": 193015, "x": 58.3, "y": 35.8, "mapID": 2112, "zone": "Valdrakken"},
    "Silvrath":                {"npcID": 253067, "x": 71.6, "y": 49.8, "mapID": 2112, "zone": "Valdrakken"},
    "Evantkis":                {"npcID": 199605, "x": 58.5, "y": 57.6, "mapID": 2112, "zone": "Valdrakken"},
    "Provisioner Thom":        {"npcID": 193659, "x": 36.9, "y": 50.8, "mapID": 2112, "zone": "Valdrakken"},
    "Jolinth":                 {"npcID": 253086, "x": 35.3, "y": 57.2, "mapID": 2151, "zone": "The Forbidden Reach"},
    "Provisioner Aristta":     {"npcID": 209192, "x": 61.5, "y": 31.6, "mapID": 2025, "zone": "Thaldraszus"},
    "Ironus Coldsteel":        {"npcID": 209220, "x": 52.3, "y": 81.0, "mapID": 2025, "zone": "Thaldraszus"},
    "Cataloger Jakes":         {"npcID": 189226, "x": 47.1, "y": 82.8, "mapID": 2022, "zone": "The Waking Shores"},
    "Rae'ana":                 {"npcID": 188265, "x": 47.9, "y": 82.4, "mapID": 2022, "zone": "The Waking Shores"},
    "Lifecaller Tzadrak":      {"npcID": 191025, "x": 62.1, "y": 74.0, "mapID": 2022, "zone": "The Waking Shores"},
    "Moon Priestess Lasara":   {"npcID": 216286, "x": 46.7, "y": 70.8, "mapID": 2239, "zone": "Amirdrassil"},
    "Mythrin'dir":             {"npcID": 216284, "x": 54.1, "y": 61.0, "mapID": 2239, "zone": "Amirdrassil"},
    "Ellandrieth":             {"npcID": 216285, "x": 48.5, "y": 53.8, "mapID": 2239, "zone": "Amirdrassil"},
    # === The War Within ===
    "Auditor Balwurz":         {"npcID": 223728, "x": 39.3, "y": 24.6, "mapID": 2339, "zone": "Dornogal"},
    "Jorid":                   {"npcID": 219318, "x": 57.1, "y": 60.8, "mapID": 2339, "zone": "Dornogal"},
    "Garnett":                 {"npcID": 252910, "x": 54.8, "y": 57.4, "mapID": 2339, "zone": "Dornogal"},
    "Second Chair Pawdo":      {"npcID": 252312, "x": 53.0, "y": 68.0, "mapID": 2339, "zone": "Dornogal"},
    "Velerd":                  {"npcID": 219217, "x": 55.3, "y": 76.6, "mapID": 2339, "zone": "Dornogal"},
    "Cinnabar":                {"npcID": 252901, "x": 42.1, "y": 73.2, "mapID": 2248, "zone": "Isle of Dorn"},
    "Cendvin":                 {"npcID": 226205, "x": 74.5, "y": 45.4, "mapID": 2248, "zone": "Isle of Dorn"},
    "Waxmonger Squick":        {"npcID": 221390, "x": 43.3, "y": 33.0, "mapID": 2214, "zone": "The Ringing Deeps"},
    "Chert":                   {"npcID": 252887, "x": 43.5, "y": 33.2, "mapID": 2214, "zone": "The Ringing Deeps"},
    "Gabbun":                  {"npcID": 256783, "x": 43.4, "y": 33.2, "mapID": 2214, "zone": "The Ringing Deeps"},
    "Nalina Ironsong":         {"npcID": 217642, "x": 42.9, "y": 56.0, "mapID": 2215, "zone": "Hallowfall"},
    "Lars Bronsmaelt":         {"npcID": 240852, "x": 28.4, "y": 56.3, "mapID": 2215, "zone": "Hallowfall"},
    "Thripps":                 {"npcID": 218202, "x": 50.1, "y": 31.8, "mapID": 2213, "zone": "City of Threads"},
    # === Undermine ===
    "Stacks Topskimmer":       {"npcID": 251911, "x": 43.3, "y": 50.6, "mapID": 2346, "zone": "Undermine"},
    "Smaks Topskimmer":        {"npcID": 231409, "x": 43.9, "y": 51.0, "mapID": 2346, "zone": "Undermine"},
    "Rocco Razzboom":          {"npcID": 231406, "x": 39.3, "y": 22.4, "mapID": 2346, "zone": "Undermine"},
    "Boatswain Hardee":        {"npcID": 231405, "x": 63.5, "y": 17.0, "mapID": 2346, "zone": "Undermine"},
    "Lab Assistant Laszly":    {"npcID": 231408, "x": 27.3, "y": 72.7, "mapID": 2346, "zone": "Undermine"},
    "Shredz the Scrapper":     {"npcID": 231407, "x": 53.4, "y": 72.8, "mapID": 2346, "zone": "Undermine"},
    "Sitch Lowdown":           {"npcID": 231396, "x": 30.9, "y": 39.1, "mapID": 2346, "zone": "Undermine"},
    "Blair Bass":              {"npcID": 226994, "x": 34.1, "y": 71.0, "mapID": 2346, "zone": "Undermine"},
    "Street Food Vendor":      {"npcID": 239333, "x": 26.3, "y": 43.0, "mapID": 2346, "zone": "Undermine"},
    "Ando the Gat":            {"npcID": 235621, "x": 41.6, "y": 50.4, "mapID": 2346, "zone": "Undermine"},
    # === K'aresh ===
    "Ta'sam":                  {"npcID": 235314, "x": 43.3, "y": 35.0, "mapID": 2472, "zone": "K'aresh"},
    "Om'sirik":                {"npcID": 235252, "x": 40.4, "y": 29.5, "mapID": 2472, "zone": "K'aresh"},
    # === Midnight — Silvermoon City ===
    "Telemancer Astrandis":    {"npcID": 242399, "x": 52.5, "y": 79.0, "mapID": 2393, "zone": "Silvermoon City"},
    "Corlen Hordralin":        {"npcID": 252915, "x": 44.3, "y": 62.9, "mapID": 2393, "zone": "Silvermoon City"},
    "Naleidea Rivergleam":     {"npcID": 242398, "x": 52.8, "y": 78.1, "mapID": 2393, "zone": "Silvermoon City"},
    "Dennia Silvertongue":     {"npcID": 256828, "x": 51.3, "y": 56.6, "mapID": 2393, "zone": "Silvermoon City"},
    "Construct Ali'a":         {"npcID": 258181, "x": 55.9, "y": 66.2, "mapID": 2393, "zone": "Silvermoon City"},
    "Hesta Forlath":           {"npcID": 252916, "x": 44.3, "y": 62.9, "mapID": 2393, "zone": "Silvermoon City"},
    "Dethelin":                {"npcID": 250982, "x": 52.5, "y": 47.3, "mapID": 2393, "zone": "Silvermoon City"},
    "Nael Silvertongue":       {"npcID": 251091, "x": 50.6, "y": 56.2, "mapID": 2393, "zone": "Silvermoon City"},
    # === Midnight — Profession / Rank Vendors ===
    "Melaris":                 {"npcID": 243359, "x": 47.1, "y": 52.0, "mapID": 2393, "zone": "Silvermoon City"},
    "Eriden":                  {"npcID": 241451, "x": 43.7, "y": 51.8, "mapID": 2393, "zone": "Silvermoon City"},
    "Quelis":                  {"npcID": 257914, "x": 56.5, "y": 70.0, "mapID": 2393, "zone": "Silvermoon City"},
    "Lyna":                    {"npcID": 243350, "x": 47.9, "y": 53.8, "mapID": 2393, "zone": "Silvermoon City"},
    "Yatheon":                 {"npcID": 241453, "x": 43.7, "y": 54.0, "mapID": 2393, "zone": "Silvermoon City"},
    "Irodalmin":               {"npcID": 256026, "x": 48.3, "y": 51.8, "mapID": 2393, "zone": "Silvermoon City"},
    "Lelorian":                {"npcID": 243555, "x": 46.5, "y": 51.4, "mapID": 2393, "zone": "Silvermoon City"},
    "Deynna":                  {"npcID": 243353, "x": 48.3, "y": 54.4, "mapID": 2393, "zone": "Silvermoon City"},
    # === Midnight — Eversong Woods ===
    "Caeris Fairdawn":         {"npcID": 240838, "x": 43.6, "y": 47.6, "mapID": 2395, "zone": "Eversong Woods"},
    "Sathren Azuredawn":       {"npcID": 259864, "x": 43.3, "y": 47.7, "mapID": 2395, "zone": "Eversong Woods"},
    "Neriv":                   {"npcID": 242726, "x": 43.6, "y": 47.8, "mapID": 2395, "zone": "Eversong Woods"},
    "Ranger Allorn":           {"npcID": 242724, "x": 43.6, "y": 47.7, "mapID": 2395, "zone": "Eversong Woods"},
    "Armorer Goldcrest":       {"npcID": 242725, "x": 43.6, "y": 47.7, "mapID": 2395, "zone": "Eversong Woods"},
    "Apprentice Diell":        {"npcID": 242723, "x": 43.6, "y": 47.7, "mapID": 2395, "zone": "Eversong Woods"},
    # === Midnight — Harandar ===
    "Mowaia":                  {"npcID": 258507, "x": 52.3, "y": 54.2, "mapID": 2413, "zone": "Harandar"},
    "Maku":                    {"npcID": 255114, "x": 62.67, "y": 34.48, "mapID": 2576, "zone": "Harandar"},
    "Naynar":                  {"npcID": 240407, "x": 51.1, "y": 50.9, "mapID": 2413, "zone": "Harandar"},
    "Mothkeeper Wew'tam":      {"npcID": 251259, "x": 49.4, "y": 54.6, "mapID": 2413, "zone": "Harandar"},
    "Hawli":                   {"npcID": 258540, "x": 59.4, "y": 33.3, "mapID": 2576, "zone": "Harandar"},
    "Amwa'ana":                {"npcID": 258480, "x": 57.4, "y": 32.8, "mapID": 2576, "zone": "Harandar"},
    # === Midnight — Zul'Aman ===
    "Jan'zel":                 {"npcID": 255098, "x": 45.3, "y": 70.0, "mapID": 2437, "zone": "Zul'Aman"},
    "Kuvahn":                  {"npcID": 255095, "x": 45.3, "y": 69.8, "mapID": 2437, "zone": "Zul'Aman"},
    "Magovu":                  {"npcID": 240279, "x": 46.1, "y": 66.1, "mapID": 2437, "zone": "Zul'Aman"},
    "Tajaka Sawtusk":          {"npcID": 254944, "x": 46.1, "y": 66.3, "mapID": 2437, "zone": "Zul'Aman"},
    "Chel the Chip":           {"npcID": 241928, "x": 31.7, "y": 26.5, "mapID": 2437, "zone": "Zul'Aman"},
    # === Midnight — Voidstorm / Masters' Perch ===
    "Thraxadar":               {"npcID": 258328, "x": 39.5, "y": 81.2, "mapID": 2444, "zone": "Masters' Perch"},
    "Void Researcher Aemely":  {"npcID": 259922, "x": 52.7, "y": 73.0, "mapID": 2405, "zone": "The Voidstorm"},
    "Void Researcher Anomander": {"npcID": 248328, "x": 52.7, "y": 73.1, "mapID": 2405, "zone": "The Voidstorm"},
    # === Midnight — Arcantina ===
    "Morta Gage":              {"npcID": 252873, "x": 42.1, "y": 50.2, "mapID": 2541, "zone": "Arcantina"},
    # === Neighborhoods — Alliance (Founder's Point) ===
    "Xiao Dan":                {"npcID": 255203, "x": 52.1, "y": 38.5, "mapID": 2352, "zone": "Founder's Point"},
    "Trevor Grenner":          {"npcID": 255221, "x": 53.6, "y": 41.1, "mapID": 2352, "zone": "Founder's Point"},
    "Klasa":                   {"npcID": 256750, "x": 58.4, "y": 61.8, "mapID": 2352, "zone": "Founder's Point"},
    "Faarden the Builder":     {"npcID": 255213, "x": 52.1, "y": 38.6, "mapID": 2352, "zone": "Founder's Point"},
    "Balen Starfinder":        {"npcID": 255216, "x": 52.3, "y": 38.2, "mapID": 2352, "zone": "Founder's Point"},
    "Argan Hammerfist":        {"npcID": 255218, "x": 52.3, "y": 38.0, "mapID": 2352, "zone": "Founder's Point"},
    # === Neighborhoods — Roaming vendors (FP instances, Alliance side) ===
    '"High Tides" Ren':        {"npcID": 255222, "x": 62.4, "y": 80.0, "mapID": 2352, "zone": "Founder's Point"},
    '"Len" Splinthoof':        {"npcID": 255228, "x": 61.6, "y": 79.0, "mapID": 2352, "zone": "Founder's Point"},
    '"Yen" Malone':            {"npcID": 255230, "x": 62.2, "y": 80.2, "mapID": 2352, "zone": "Founder's Point"},
    # === Neighborhoods — Horde (Razorwind Shores) ===
    "Shon'ja":                 {"npcID": 255297, "x": 54.2, "y": 59.2, "mapID": 2351, "zone": "Razorwind Shores"},
    "Lonomia":                 {"npcID": 240465, "x": 68.4, "y": 75.7, "mapID": 2351, "zone": "Razorwind Shores"},
    "Botanist Boh'an":         {"npcID": 255301, "x": 53.7, "y": 57.7, "mapID": 2351, "zone": "Razorwind Shores"},
    "Gronthul":                {"npcID": 255278, "x": 54.2, "y": 59.3, "mapID": 2351, "zone": "Razorwind Shores"},
    "Jehzar Starfall":         {"npcID": 255298, "x": 53.7, "y": 58.6, "mapID": 2351, "zone": "Razorwind Shores"},
    "Lefton Farrer":           {"npcID": 255299, "x": 53.5, "y": 58.6, "mapID": 2351, "zone": "Razorwind Shores"},
    # PvP / special
    "Paul North":              {"npcID": 68364,  "x": 52.1, "y": 28.0, "mapID": 503,  "zone": "Orgrimmar"},
    "Quackenbush":             {"npcID": 68363,  "x": 51.1, "y": 30.2, "mapID": 499,  "zone": "Stormwind City"},
}

# Zone-specific vendor overrides for shared NPC names. The resolver
# (resolve_vendor_override) checks this dict first keyed by
# (vendor_name, item_zone) before falling back to VENDOR_COORDS.
#
# This dict is auto-populated at module load from
# Tools/scraper/data/vendor_zones.json, which enrich_catalog.py writes every
# time it runs. Any time a vendor name is detected in multiple zones, its
# per-zone NPC data is added here automatically — no manual maintenance
# required. Curated manual entries below take precedence over auto-loaded
# ones (so you can still override a bad auto-pick if needed).
VENDOR_COORDS_BY_ZONE: dict[tuple[str, str], dict] = {}


def _autoload_vendor_zones() -> None:
    """Populate VENDOR_COORDS_BY_ZONE from the auto-generated vendor_zones.json.
    Produced by enrich_catalog.py whenever a vendor name appears in multiple
    zones. Existing entries in VENDOR_COORDS_BY_ZONE (manual overrides) win."""
    try:
        vz_path = Path(__file__).resolve().parent / "data" / "vendor_zones.json"
        if not vz_path.exists():
            return
        with open(vz_path, encoding="utf-8") as fh:
            data = json.load(fh)
        loaded = 0
        for vendor_name, zone_map in data.items():
            for zone, info in zone_map.items():
                key = (vendor_name, zone)
                if key in VENDOR_COORDS_BY_ZONE:
                    continue  # manual override wins
                npc_id = info.get("npcID")
                if not npc_id:
                    continue
                map_id = ZONE_TO_MAPID.get(zone)
                if map_id is None:
                    continue  # can't resolve zone name → mapID; skip auto entry
                VENDOR_COORDS_BY_ZONE[key] = {
                    "npcID": npc_id,
                    "x": info.get("x") or 0.0,
                    "y": info.get("y") or 0.0,
                    "mapID": map_id,
                    "zone": zone,
                }
                loaded += 1
        if loaded:
            logger.info("Auto-loaded %d shared-name vendor entries from %s",
                        loaded, vz_path.name)
    except Exception as exc:
        logger.warning("Failed to auto-load vendor_zones.json: %s", exc)


def resolve_vendor_override(vendor_name: str, item_zone: str | None) -> dict | None:
    """Return the curated override for a vendor, preferring a zone-specific
    entry when the item's zone matches. Falls back to the name-only entry."""
    if item_zone:
        zoned = VENDOR_COORDS_BY_ZONE.get((vendor_name, item_zone))
        if zoned:
            return zoned
    return VENDOR_COORDS.get(vendor_name)


def promote_wowhead_npc_fallback(
    item: dict,
    extra: dict,
    wh_zone_to_name: dict,
    zone_to_mapid: dict,
    logger_=None,
) -> bool:
    """Promote a Wowhead sourcemore/decor-sources NPC to the item's primary
    vendor when the item has no source data of its own.

    This is the fallback path for items whose in-game scan returned empty
    ``sourceText``. Without this, such items serialize as ``sourceType =
    "Other"`` with no vendor name and no navigation target.

    Guard rules:
      - Fires only when the item has **no** in-game source: no vendor,
        quest, achievement, profession, and an empty ``sources`` list.
        Existing in-game data is never overwritten.
      - Uses the first NPC entry in ``additionalSources``. Each entry may
        carry coords + whZoneID + zone-name + mapID (the modern decor-
        shape extractor fills those in directly; the legacy sourcemore
        extractor leaves some blank).
      - Zone/mapID plumbing prefers direct-from-decor fields, falling
        back to ``whZoneID → zone-name`` translation via
        ``wh_zone_to_name``, then ``zone → mapID`` via ``zone_to_mapid``.

    Returns True if the item was promoted, False otherwise. Intended for
    use by the ``main()`` merge loop but exposed at module scope so tests
    can exercise the real code path instead of a reimplementation.
    """
    if (item.get("vendor") or item.get("quest") or item.get("achievement")
            or item.get("profession") or (item.get("sources") or [])):
        return False

    add_src = extra.get("additionalSources", []) if isinstance(extra, dict) else []
    npc_sources = [
        s for s in add_src
        if s.get("sourceType") == "NPC" and s.get("sourceDetail")
    ]
    if not npc_sources:
        return False

    primary = npc_sources[0]
    item["vendor"] = primary["sourceDetail"]
    if primary.get("sourceID"):
        item["npcID"] = primary.get("sourceID")

    coords = primary.get("coords") or {}
    wh_zone = primary.get("whZoneID")
    zone_name_direct = primary.get("zone")
    map_id_direct = primary.get("mapID")

    if coords.get("x") is not None:
        item["npcX"] = coords["x"]
    if coords.get("y") is not None:
        item["npcY"] = coords["y"]

    zone_name = zone_name_direct or (
        wh_zone_to_name.get(wh_zone) if wh_zone is not None else None
    )
    if zone_name and not item.get("zone"):
        item["zone"] = zone_name
    map_id = map_id_direct or (
        zone_to_mapid.get(zone_name) if zone_name else None
    )
    if map_id and not item.get("mapID"):
        item["mapID"] = map_id

    srcs = item.setdefault("sources", [])
    if not any(s.get("type") == "Vendor" for s in srcs):
        srcs.insert(0, {"type": "Vendor", "value": primary["sourceDetail"]})

    if logger_ is not None:
        coord_blurb = ""
        if item.get("npcX") is not None and item.get("zone"):
            coord_blurb = f" @ ({item['npcX']:.1f},{item['npcY']:.1f}) in '{item['zone']}'"
        elif item.get("npcX") is not None:
            coord_blurb = f" @ ({item['npcX']:.1f},{item['npcY']:.1f})"
        logger_.info(
            "  decorID=%s '%s': promoted Wowhead NPC '%s' (id=%s) to primary vendor%s",
            item.get("decorID"), item.get("name"),
            primary["sourceDetail"], primary.get("sourceID"),
            coord_blurb,
        )
    return True


# Reverse lookup: mapID → zone name (for updating item zones when vendor mapID differs)
# Prefer shorter / canonical zone names, skip aliases
MAPID_TO_ZONE: dict[int, str] = {}
for _zname, _zmapid in ZONE_TO_MAPID.items():
    # Prefer shorter / canonical zone names, skip aliases
    if _zmapid not in MAPID_TO_ZONE or len(_zname) < len(MAPID_TO_ZONE[_zmapid]):
        MAPID_TO_ZONE[_zmapid] = _zname

# Auto-load zone-specific vendor overrides produced by enrich_catalog.py.
# Must run after VENDOR_COORDS_BY_ZONE, VENDOR_COORDS, and ZONE_TO_MAPID
# are all defined. Safe at module import time — reads a JSON file if present.
_autoload_vendor_zones()

# ---------------------------------------------------------------------------
# Category / subcategory name mappings (verified via /hs debug dump categories)
# ---------------------------------------------------------------------------
CATEGORY_NAMES: dict[int, str] = {
    1: "Furnishings",
    2: "Structural",
    3: "Accents",
    4: "Lighting",
    5: "Functional",
    6: "Nature",
    8: "Miscellaneous",
    9: "Rooms",
}

SUBCATEGORY_NAMES: dict[int, str] = {
    # Furnishings (category 1):
    1: "Seating",
    2: "Beds",
    5: "Tables and Desks",
    6: "Storage",
    7: "Misc Furnishings",
    # Structural (category 2):
    3: "Doors",
    4: "Walls and Columns",
    8: "Windows",
    9: "Large Structures",
    10: "Misc Structural",
    # Accents (category 3):
    11: "Ornamental",
    12: "Wall Hangings",
    13: "Food and Drink",
    14: "Floor",
    15: "Misc Accents",
    # Lighting (category 4):
    16: "Large Lights",
    17: "Wall Lights",
    18: "Ceiling Lights",
    19: "Small Lights",
    20: "Ambient Lighting",
    21: "Misc Lighting",
    # Functional (category 5):
    22: "Utility",
    51: "Misc Functional",
    # Nature (category 6):
    25: "Large Foliage",
    26: "Small Foliage",
    27: "Bushes",
    28: "Ground Cover",
    29: "Misc Nature",
    # Miscellaneous (category 8):
    34: "Miscellaneous - All",
    # Rooms (category 9):
    35: "Rooms - All",
}

CATEGORY_SUBCATEGORIES: dict[int, list[int]] = {
    1: [1, 5, 6, 2, 7],            # Furnishings
    2: [3, 4, 8, 9, 10],           # Structural
    3: [11, 12, 13, 14, 15],       # Accents
    4: [16, 17, 18, 19, 20, 21],   # Lighting
    5: [22, 51],                    # Functional
    6: [25, 26, 27, 28, 29],       # Nature
    8: [34],                        # Miscellaneous
    9: [35],                        # Rooms
}

CATEGORY_ORDER: list[int] = [1, 2, 3, 4, 5, 6, 8, 9]  # display order

# ---------------------------------------------------------------------------
# Neighborhood vendor pairing: Alliance (Founder's Point) ↔ Horde (Razorwind Shores)
# ---------------------------------------------------------------------------
# Vendors exclusive to one faction zone:
FP_ONLY_VENDORS: dict[str, dict] = {
    "Xiao Dan":            {"npcID": 255203, "zone": "Founder's Point"},
    "Trevor Grenner":      {"npcID": 255221, "zone": "Founder's Point"},
    "Faarden the Builder": {"npcID": 255213, "zone": "Founder's Point"},
    "Balen Starfinder":    {"npcID": 255216, "zone": "Founder's Point"},
    "Argan Hammerfist":    {"npcID": 255218, "zone": "Founder's Point"},
    "Klasa":               {"npcID": 256750, "zone": "Founder's Point"},
}
RS_ONLY_VENDORS: dict[str, dict] = {
    "Gronthul":            {"npcID": 255278, "zone": "Razorwind Shores"},
    "Shon'ja":             {"npcID": 255297, "zone": "Razorwind Shores"},
    "Botanist Boh'an":     {"npcID": 255301, "zone": "Razorwind Shores"},
    "Jehzar Starfall":     {"npcID": 255298, "zone": "Razorwind Shores"},
    "Lefton Farrer":       {"npcID": 255299, "zone": "Razorwind Shores"},
    "Lonomia":             {"npcID": 240465, "zone": "Razorwind Shores"},
}
# Covenant-locked vendors (Shadowlands) — maps vendor name to covenant ID.
# 1=Kyrian, 2=Venthyr, 3=Night Fae, 4=Necrolord
COVENANT_VENDORS: dict[str, int] = {
    "Chachi the Artiste": 2,  # Venthyr
}
# Treasure object fixups — override coords/zone for items from world treasure chests.
# Format: decorID → (x, y, zone_name)
# These items often get wrong coords because the pipeline picks up a secondary
# vendor's location instead of the actual treasure object position.
TREASURE_COORDS: dict[int, tuple[float, float, str]] = {
    1173:  (38.89, 76.08, "Eversong Woods"),       # Triple-Locked Safebox
    1195:  (37.8,  52.6,  "Silvermoon City"),        # Incomplete Book of Sonnets
    2233:  (62.91, 51.25, "Harandar"),               # Reliquary's Lost Paint Supplies
    5111:  (57.72, 15.68, "The Jade Forest"),         # Golden Cloud Serpent chest
    8875:  (40.44, 60.90, "Eversong Woods"),         # Stone Vat
    14597: (52.24, 31.11, "Masters' Perch"),         # Stellar Stash
    14641: (43.28, 69.48, "Eversong Woods"),         # Forgotten Ink and Quill
    14977: (40.96, 19.47, "Eversong Woods"),         # Gift of the Phoenix
    15746: (53.21, 44.23, "The Voidstorm"),          # Malignant Chest
}

# False positive Treasure links from Wowhead — these are either unrelated legacy objects,
# recipe sources, or associated with a different decor item.
# Format: itemID → reason
TREASURE_FALSE_POSITIVES: set[int] = {
    262354,  # Riftstone — "Unstable Riftstone" is an unrelated Legion-era object
    262456,  # Ornamental Silvermoon Hanger — "Ranger's Cache" drops the recipe, not the item
    262466,  # Void Elf Table — "Stellar Stash" belongs to Void Elf Round Table (different item)
}
# Treasure fixups — override sourceDetail, zone, coords, and add extra metadata
# for treasure items with missing or generic data from the in-game dump.
TREASURE_FIXUPS: dict[int, dict] = {
    674: {  # Ancient Elven Highback Chair
        "sourceDetail": "Glimmering Treasure Chest",
        "zone": "Suramar",
        "npcX": 34.8,
        "npcY": 48.1,
        "hintLine": [
            {"text": "Complete scenario "},
            {"text": "Withered Army Training", "type": "quest", "id": 43943},
            {"text": " from "},
            {"text": "First Arcanist Thalyssra", "type": "npc", "id": 97140},
        ],
    },
    1272: {  # Trashfire Barrel
        "sourceDetail": "Undermine Treasures",
        "zone": "Undermine",
        "containers": [
            "Half-Empty Bag",
            "Dented Crate",
            "Uncovered Strongbox",
        ],
    },
}
# Roaming vendors exist in BOTH zones with different NPC IDs:
ROAMING_VENDORS: dict[str, dict] = {
    '"High Tides" Ren': {"fp_npcID": 255222, "rs_npcID": 255325},
    '"Len" Splinthoof':  {"fp_npcID": 255228, "rs_npcID": 255326},
    '"Yen" Malone':      {"fp_npcID": 255230, "rs_npcID": 255319},
}
# Roaming vendor RS-instance coords (FP instances are in VENDOR_COORDS above):
ROAMING_VENDOR_RS_COORDS: dict[int, dict] = {
    255325: {"name": '"High Tides" Ren', "x": 39.8, "y": 72.8, "zone": "Razorwind Shores"},
    255326: {"name": '"Len" Splinthoof',  "x": 39.8, "y": 70.2, "zone": "Razorwind Shores"},
    255319: {"name": '"Yen" Malone',      "x": 39.0, "y": 73.0, "zone": "Razorwind Shores"},
}

# Neutral "rotating" vendors — appear in whichever neighborhood is active.
# Their coordinates are meaningless because they rotate between neighborhoods.
# Vendor-unlock quests: items that require completing a quest before purchase.
VENDOR_UNLOCK_QUESTS: dict[int, int] = {
    16092: 86663,  # Zul'Aman Flame Cradle → "Embers to a Flame"
}

ROTATING_VENDOR_NPC_IDS: set[int] = {
    150359,  # Pascal-K1N6
    249684,  # Brother Dovetail
    202468,  # Harlowe Marl (also known as npcID 257897)
    250820,  # Hordranin
    252605,  # Aeeshna
    252916,  # Hesta Forlath
    248854,  # The Last Architect
}

EXPANSION_ORDER = [
    "Classic",
    "The Burning Crusade",
    "Wrath of the Lich King",
    "Cataclysm",
    "Mists of Pandaria",
    "Warlords of Draenor",
    "Legion",
    "Battle for Azeroth",
    "Shadowlands",
    "Dragonflight",
    "The War Within",
    "Midnight",
    "Neighborhoods",
    "Unknown",
]

SOURCE_ORDER = ["Vendor", "Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Shop", "Other"]

HOUSING_ZONES = {"Founder's Point", "Razorwind Shores"}


def _auto_generate_faction_vendors(catalog: list[dict[str, Any]]) -> int:
    """Auto-generate factionVendors for neighborhood items that should have them.

    Items in Founder's Point / Razorwind Shores that list two vendor sources
    (one per faction) but were not paired by the enrichment scraper get their
    factionVendors built from the vendor classification tables above.

    Returns the number of items fixed.
    """
    count = 0
    for item in catalog:
        zone = item.get("zone") or ""
        if zone not in HOUSING_ZONES:
            continue
        if item.get("factionVendors"):
            continue  # already has paired vendors

        # Extract vendor names from sources array
        sources = item.get("sources") or []
        vendor_names = [s["value"] for s in sources
                        if s.get("type") == "Vendor" and s.get("value")]
        if len(vendor_names) < 2:
            continue  # single vendor or no vendor sources — neutral NPC

        # Classify each vendor
        alliance_vendor = None
        horde_vendor = None
        roaming_vendor = None
        for vname in vendor_names:
            if vname in FP_ONLY_VENDORS:
                alliance_vendor = vname
            elif vname in RS_ONLY_VENDORS:
                horde_vendor = vname
            elif vname in ROAMING_VENDORS:
                roaming_vendor = vname

        if not roaming_vendor and not (alliance_vendor and horde_vendor):
            continue  # can't determine pairing

        # Build the pair based on classification
        if alliance_vendor and horde_vendor:
            # Both faction-exclusive: straightforward pair
            fp_info = FP_ONLY_VENDORS[alliance_vendor]
            rs_info = RS_ONLY_VENDORS[horde_vendor]
            fv = {
                "Alliance": {"name": alliance_vendor, "npcID": fp_info["npcID"],
                             "zone": "Founder's Point"},
                "Horde":    {"name": horde_vendor, "npcID": rs_info["npcID"],
                             "zone": "Razorwind Shores"},
            }
        elif alliance_vendor and roaming_vendor:
            # FP-only + roaming → Alliance=FP-only, Horde=roaming(RS instance)
            fp_info = FP_ONLY_VENDORS[alliance_vendor]
            rv = ROAMING_VENDORS[roaming_vendor]
            fv = {
                "Alliance": {"name": alliance_vendor, "npcID": fp_info["npcID"],
                             "zone": "Founder's Point"},
                "Horde":    {"name": roaming_vendor, "npcID": rv["rs_npcID"],
                             "zone": "Razorwind Shores"},
            }
        elif horde_vendor and roaming_vendor:
            # RS-only + roaming → Alliance=roaming(FP instance), Horde=RS-only
            rs_info = RS_ONLY_VENDORS[horde_vendor]
            rv = ROAMING_VENDORS[roaming_vendor]
            fv = {
                "Alliance": {"name": roaming_vendor, "npcID": rv["fp_npcID"],
                             "zone": "Founder's Point"},
                "Horde":    {"name": horde_vendor, "npcID": rs_info["npcID"],
                             "zone": "Razorwind Shores"},
            }
        elif roaming_vendor and len(vendor_names) == 2:
            # Two roaming vendors (shouldn't normally happen) or same roaming
            # vendor listed twice — use FP/RS instances
            rv = ROAMING_VENDORS[roaming_vendor]
            fv = {
                "Alliance": {"name": roaming_vendor, "npcID": rv["fp_npcID"],
                             "zone": "Founder's Point"},
                "Horde":    {"name": roaming_vendor, "npcID": rv["rs_npcID"],
                             "zone": "Razorwind Shores"},
            }
        else:
            continue

        item["factionVendors"] = fv
        count += 1
    return count


def get_expansion(item: dict[str, Any]) -> str:
    zone = item.get("zone") or ""
    if not zone:
        zone = _fixup_zone(item)
    if not zone:
        return "Unknown"
    return ZONE_TO_EXPANSION.get(zone, "Unknown")


# ---------------------------------------------------------------------------
# Drop boss NPC IDs: map boss sourceDetail names to Wowhead NPC IDs.
# The enrichment pipeline only resolves vendor NPCs; this table provides
# NPC IDs for dungeon/raid boss drops so the addon can offer CTRL+Click
# interactions (Wowhead link, dungeon map).
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Dungeon / raid entrance coordinates on outdoor maps.
# Used by the addon to set waypoints to dungeon entrances for Drop items.
# Format: dungeonZoneName → (outdoor_zone, x_0to100, y_0to100)
#
# To add new dungeons: just add a line here and re-run this script.
# Coordinates are in 0-100 Wowhead-style scale.
# Sources: method.gg dungeon/raid location guides, Wowhead zone maps.
# ---------------------------------------------------------------------------

DUNGEON_ENTRANCES: dict[str, tuple[str, float, float] | tuple[str, float, float, int]] = {
    # Format: (zone_name, x, y) or (zone_name, x, y, uiMapID)
    # When uiMapID is given it is emitted directly; otherwise resolved at runtime.
    # Classic
    "Deadmines":                    ("Westfall",              42.5,  71.8),
    "Shadowfang Keep":              ("Silverpine Forest",     44.8,  67.8),
    "Blackrock Depths":             ("Searing Gorge",         35.4,  84.7),
    # TBC
    "Karazhan":                     ("Deadwind Pass",         46.9,  74.7),
    # WotLK
    "Pit of Saron":                 ("Icecrown",              52.6,  89.4),
    # MoP
    "Temple of the Jade Serpent":   ("The Jade Forest",       56.2,  57.8),
    "Siege of Orgrimmar":           ("Vale of Eternal Blossoms", 73.9, 42.2),
    # WoD
    "Iron Docks":                   ("Gorgrond",              45.3,  13.4),
    "Skyreach":                     ("Spires of Arak",        35.6,  33.5),
    # Legion
    "Darkheart Thicket":            ("Val'sharah",            59.2,  31.5),
    "Court of Stars":               ("Suramar",               50.8,  65.3),
    "Neltharion's Lair":            ("Highmountain",          48.0,  68.4),
    "The Nighthold":                ("Suramar",               44.8,  62.0),
    "The Seat of the Triumvirate":  ("Mac'Aree",              22.2,  56.0),
    # BfA
    "Freehold":                     ("Tiragarde Sound",       84.6,  78.9),
    "Shrine of the Storm":          ("Stormsong Valley",      78.1,  26.6),
    "Crucible of Storms":           ("Stormsong Valley",      78.1,  26.6),
    # Dragonflight
    "Algeth'ar Academy":            ("Thaldraszus",           58.4,  41.9),
    "Neltharus":                    ("The Waking Shores",     25.3,  56.7),
    "Cinderbrew Meadery":           ("Isle of Dorn",          63.5,  18.7),
    # Shadowlands
    "Sinfall":                      ("Revendreth",            30.0,  42.0),
    # TWW
    "Priory of the Sacred Flame":   ("Hallowfall",            42.0,  50.0),
    # Midnight Dungeons (method.gg verified coordinates + explicit uiMapIDs)
    # Midnight outdoor zones parented under Quel'Thalas (2537):
    #   Isle of Quel'Danas=2424, Eversong Woods=2395, Silvermoon City=2393,
    #   Zul'Aman=2437, The Voidstorm=2405, Harandar=2413
    "Magisters' Terrace":           ("Isle of Quel'Danas",    62.4,  14.6, 2424),
    "Maisara Caverns":              ("Zul'Aman",              43.9,  39.7, 2437),
    "Murder Row":                   ("Silvermoon City",       56.6,  61.1, 2393),
    "Nexus-Point Xenas":            ("The Voidstorm",         64.7,  61.8, 2405),
    "Windrunner Spire":             ("Eversong Woods",        35.6,  78.9, 2395),
    "Voidscar Arena":               ("The Voidstorm",         53.6,  35.5, 2405),
    "The Blinding Vale":            ("Harandar",              27.4,  78.0, 2413),
    # Midnight Raids (method.gg verified coordinates + explicit uiMapIDs)
    "The Voidspire":                ("The Voidstorm",         44.3,  66.2, 2405),
    "The Dreamrift":                ("Harandar",              61.8,  62.2, 2413),
    "March on Quel'Danas":          ("Isle of Quel'Danas",    52.8,  88.4, 2424),
}

DROP_NPC_IDS: dict[str, int] = {
    "Vanessa VanCleef": 49541,
    "Shade of Xavius": 101403,
    "L'ura": 124729,
    "Dargrul": 91007,
    "Skulloc": 83612,
    "Lord Godfrey": 46964,
    "Goldie Baronbottom": 210271,
    "Vol'zith the Whisperer": 134069,
    "Prioress Murrpray": 207939,
    "Harlan Sweete": 126983,
    "Emperor Dagran Thaurissan": 9019,
    "Sha of Doubt": 56439,
    "Viz'aduum the Watcher": 114790,
    "Advisor Melandrus": 104218,
    "Garrosh Hellscream": 71865,
    "Warlord Sargha": 189901,
    "Spellblade Aluriel": 104881,
    "Echo of Doragosa": 190609,
    "High Sage Viryx": 76266,
    "Scourgelord Tyrannus": 36658,
    "Zaxasj the Speaker": 147258,
    "Degentrius": 229740,
    "Ziekket": 226504,
    "Lithiel Cinderfury": 229741,
    "Belo'ren": 234263,
    "Charonus": 248015,
    "Lothraxion": 230154,
    "Vaelgor": 230161,
    "Fallen-King Salhadaar": 230155,
    "Imperator Averzian": 230162,
    "Vorasius": 230156,
    "Rak'tul": 230053,
    "General Amias Bellamy": 230160,
}

# ---------------------------------------------------------------------------
# Zone-to-continent mapping (for LOCATION sidebar filter hierarchy)
# ---------------------------------------------------------------------------

ZONE_TO_CONTINENT: dict[str, str] = {
    # Eastern Kingdoms
    "Stormwind City": "Eastern Kingdoms",
    "Hillsbrad Foothills": "Eastern Kingdoms",
    "Ironforge": "Eastern Kingdoms",
    "Dun Morogh": "Eastern Kingdoms",
    "Elwynn Forest": "Eastern Kingdoms",
    "Duskwood": "Eastern Kingdoms",
    "Loch Modan": "Eastern Kingdoms",
    "Searing Gorge": "Eastern Kingdoms",
    "Burning Steppes": "Eastern Kingdoms",
    "Eastern Plaguelands": "Eastern Kingdoms",
    "Northern Stranglethorn": "Eastern Kingdoms",
    "Silverpine Forest": "Eastern Kingdoms",
    "Blasted Lands": "Eastern Kingdoms",
    "Wetlands": "Eastern Kingdoms",
    "Deeprun Tram": "Eastern Kingdoms",
    "Blackrock Depths": "Eastern Kingdoms",
    "Deadmines": "Eastern Kingdoms",
    "Twilight Highlands": "Eastern Kingdoms",
    "Ruins of Gilneas": "Eastern Kingdoms",
    "Gilneas": "Eastern Kingdoms",
    "Gilneas City": "Eastern Kingdoms",
    "Karazhan": "Eastern Kingdoms",
    "Shadowfang Keep": "Eastern Kingdoms",
    "Ghostlands": "Eastern Kingdoms",
    "Westfall": "Eastern Kingdoms",
    "The Cape of Stranglethorn": "Eastern Kingdoms",
    "Undercity": "Eastern Kingdoms",
    "Northshire": "Eastern Kingdoms",
    # Kalimdor
    "Darkshore": "Kalimdor",
    "Mulgore": "Kalimdor",
    "Teldrassil": "Kalimdor",
    "Felwood": "Kalimdor",
    "Silithus": "Kalimdor",
    "Dustwallow Marsh": "Kalimdor",
    "Winterspring": "Kalimdor",
    "Thunder Bluff": "Kalimdor",
    # Northrend
    "Borean Tundra": "Northrend",
    "Grizzly Hills": "Northrend",
    "Sholazar Basin": "Northrend",
    "Acherus: The Ebon Hold": "Northrend",
    "Crystalsong Forest": "Northrend",
    "Pit of Saron": "Northrend",
    "Rescue Koltira": "Northrend",
    # Pandaria
    "Kun-Lai Summit": "Pandaria",
    "Vale of Eternal Blossoms": "Pandaria",
    "Valley of the Four Winds": "Pandaria",
    "The Jade Forest": "Pandaria",
    "The Wandering Isle": "Pandaria",
    "Temple of the Jade Serpent": "Pandaria",
    "Siege of Orgrimmar": "Pandaria",
    # Draenor
    "Spires of Arak": "Draenor",
    "Lunarfall": "Draenor",
    "Frostwall": "Draenor",
    "Frostfire Ridge": "Draenor",
    "Stormshield": "Draenor",
    "Warspear": "Draenor",
    "Talador": "Draenor",
    "Iron Docks": "Draenor",
    "Skyreach": "Draenor",
    "Shadowmoon Valley": "Draenor",
    "Nagrand": "Draenor",
    # Broken Isles (incl. class halls, Argus)
    "Highmountain": "Broken Isles",
    "Val'sharah": "Broken Isles",
    "Suramar": "Broken Isles",
    "Azsuna": "Broken Isles",
    "Dalaran": "Broken Isles",
    "Dalaran Sewers": "Broken Isles",
    "Dreadscar Rift": "Broken Isles",
    "Hall of the Guardian": "Broken Isles",
    "Trueshot Lodge": "Broken Isles",
    "The Dreamgrove": "Broken Isles",
    "The Maelstrom": "Broken Isles",
    "Mardum, the Shattered Abyss": "Broken Isles",
    "Skyhold": "Broken Isles",
    "Netherlight Temple": "Broken Isles",
    "Slayer's Rise": "Broken Isles",
    "Court of Stars": "Broken Isles",
    "Darkheart Thicket": "Broken Isles",
    "Neltharion's Lair": "Broken Isles",
    "The Nighthold": "Broken Isles",
    "Antoran Wastes": "Broken Isles",
    "The Seat of the Triumvirate": "Broken Isles",
    # Zandalar
    "Zuldazar": "Zandalar",
    "Dazar'alor": "Zandalar",
    "Nazmir": "Zandalar",
    # Kul Tiras
    "Stormsong Valley": "Kul Tiras",
    "Tiragarde Sound": "Kul Tiras",
    "Boralus": "Kul Tiras",
    "Drustvar": "Kul Tiras",
    "Freehold": "Kul Tiras",
    "Mechagon": "Mechagon",
    "Vol'dun": "Zandalar",
    "Orgrimmar": "Kul Tiras",
    "Shrine of the Storm": "Kul Tiras",
    "Crucible of Storms": "Kul Tiras",
    "Chamber of Heart": "Kul Tiras",
    # The Shadowlands
    "Revendreth": "The Shadowlands",
    "Sinfall": "The Shadowlands",
    "The Maw": "The Shadowlands",
    # Dragon Isles
    "The Waking Shores": "Dragon Isles",
    "Thaldraszus": "Dragon Isles",
    "Valdrakken": "Dragon Isles",
    "The Forbidden Reach": "Dragon Isles",
    "Neltharus": "Dragon Isles",
    "Algeth'ar Academy": "Dragon Isles",
    "The Azure Span": "Dragon Isles",
    "Amirdrassil": "Dragon Isles",
    # Khaz Algar
    "Dornogal": "Khaz Algar",
    "Hallowfall": "Khaz Algar",
    "The Ringing Deeps": "Khaz Algar",
    "Isle of Dorn": "Khaz Algar",
    "City of Threads": "Khaz Algar",
    "Priory of the Sacred Flame": "Khaz Algar",
    "Cinderbrew Meadery": "Khaz Algar",
    "K'aresh": "Khaz Algar",
    "Undermine": "Khaz Algar",
    "Liberation of Undermine": "Khaz Algar",
    # Quel'Thalas (Midnight + TBC Quel'Thalas zones)
    "Eversong Woods": "Quel'Thalas",
    "Silvermoon City": "Quel'Thalas",
    "Murder Row": "Quel'Thalas",
    "Magisters' Terrace": "Quel'Thalas",
    "Harandar": "Quel'Thalas",
    "Voidstorm": "Quel'Thalas",
    "The Voidspire": "Quel'Thalas",
    "The Dreamrift": "Quel'Thalas",
    "Windrunner Spire": "Quel'Thalas",
    "Zul'Aman": "Quel'Thalas",
    "The Blinding Vale": "Quel'Thalas",
    "Voidscar Arena": "Quel'Thalas",
    "March on Quel'Danas": "Quel'Thalas",
    "Arcantina": "Quel'Thalas",
    "Maisara Caverns": "Quel'Thalas",
    "Masters' Perch": "Quel'Thalas",
    "Nexus-Point Xenas": "Quel'Thalas",
    "Midnight Delves": "Quel'Thalas",
    "Isle of Quel'Danas": "Quel'Thalas",
    # Neighborhoods (player housing)
    "Founder's Point": "Neighborhoods",
    "Razorwind Shores": "Neighborhoods",
}

CONTINENT_ORDER = [
    "Eastern Kingdoms",
    "Kalimdor",
    "Northrend",
    "Pandaria",
    "Draenor",
    "Broken Isles",
    "Zandalar",
    "Kul Tiras",
    "The Shadowlands",
    "Dragon Isles",
    "Khaz Algar",
    "Quel'Thalas",
    "Neighborhoods",
]


def get_continent(item: dict[str, Any]) -> str:
    """Determine continent from item zone."""
    zone = item.get("zone") or ""
    if not zone:
        zone = _fixup_zone(item)
    return ZONE_TO_CONTINENT.get(zone, "Unknown")


# ---------------------------------------------------------------------------
# Drop item fixups: fill in missing zone / boss data from known sources
# These items have incomplete Wowhead data (Drop value is a dungeon/event name
# rather than a boss name, or zone is missing entirely).
# ---------------------------------------------------------------------------

# decorID -> (zone, sourceDetail_override) — override when enriched data is incomplete
DROP_FIXUPS: dict[int, tuple[str, str]] = {
    # Deadmines — Wowhead has dungeon name, not boss name
    4401: ("Deadmines", "Vanessa VanCleef"),
    # Darkshore rare elites (post-Burning of Teldrassil timeline).
    # All 4 items drop from the same 8 rares in BfA Darkshore.
    840:  ("Darkshore", "Aman, Glimmerspine, Granokk, Madfeather, Mrggr'marr, Scalefiend, Shattershard, Stonebinder Ssra'vess"),
    948:  ("Darkshore", "Aman, Glimmerspine, Granokk, Madfeather, Mrggr'marr, Scalefiend, Shattershard, Stonebinder Ssra'vess"),
    1836: ("Darkshore", "Aman, Glimmerspine, Granokk, Madfeather, Mrggr'marr, Scalefiend, Shattershard, Stonebinder Ssra'vess"),
    2000: ("Darkshore", "Aman, Glimmerspine, Granokk, Madfeather, Mrggr'marr, Scalefiend, Shattershard, Stonebinder Ssra'vess"),
}

# Per-mob data for multi-source Drop items (outdoor rares, etc.).
# decorID -> { "hub": (zone, x, y, label), "mobs": [(name, npcID, x, y)] }
# The "hub" provides a central waypoint for the navigation button.
# Each mob entry provides name, NPC ID, and coordinates for individual interaction.
_DARKSHORE_RARES = {
    "hub": ("Darkshore", 39.0, 44.0, "Ruins of Auberdine"),
    "mobs": [
        ("Aman", 147966, 37.8, 84.7),
        ("Glimmerspine", 149654, 43.4, 19.6),
        ("Granokk", 147261, 47.0, 56.0),
        ("Madfeather", 149657, 44.0, 48.2),
        ("Mrggr'marr", 147970, 35.8, 81.8),
        ("Scalefiend", 149665, 47.6, 44.5),
        ("Shattershard", 147751, 43.4, 29.2),
        ("Stonebinder Ssra'vess", 147332, 45.5, 59.0),
    ],
}

DROP_MOB_GROUPS: dict[int, dict] = {
    840:  _DARKSHORE_RARES,
    948:  _DARKSHORE_RARES,
    1836: _DARKSHORE_RARES,
    2000: _DARKSHORE_RARES,
}

# Drop source values that should be mapped to a zone (for items without an explicit zone)
DROP_VALUE_TO_ZONE: dict[str, str] = {
    "Deadmines": "Deadmines",
    "Midnight Delves": "Midnight Delves",
    "8.1 Darkshore Outdoor Final Phase": "Darkshore",
    "World Nullaeus Creatures": "Midnight Delves",
}


def _fixup_zone(item: dict[str, Any]) -> str:
    """Try to resolve a missing zone from Drop source value or fixup table."""
    decor_id = item.get("decorID")
    if decor_id and decor_id in DROP_FIXUPS:
        return DROP_FIXUPS[decor_id][0]
    # Try resolving from drop value
    for s in (item.get("sources") or []):
        if s.get("type") == "Drop" and s.get("value"):
            mapped = DROP_VALUE_TO_ZONE.get(s["value"])
            if mapped:
                return mapped
    return ""


def _fixup_source_detail(item: dict[str, Any], detail: str) -> str:
    """Override source detail for items with known boss names."""
    decor_id = item.get("decorID")
    if decor_id and decor_id in DROP_FIXUPS:
        return DROP_FIXUPS[decor_id][1]
    return detail


# ---------------------------------------------------------------------------
# Lua serialization
# ---------------------------------------------------------------------------

def serialize_item(item: dict[str, Any], source_type: str, source_detail: str,
                   expansion: str, achievement_name: str, vendor_name: str,
                   profession_name: str, indent: str = "    ") -> str:
    """Serialize a single catalog item to a Lua table literal."""
    decor_id = item["decorID"]
    lines = [f"    [{decor_id}] = {{"]

    # --- Collect additional vendors ---
    # These come from multiple sources: comma-separated names, _silvermoonOrigVendor,
    # and _allVendors (multi-zone vendor discovery from Phase 7).
    additional_vendors: list[dict[str, Any]] = []

    # Split comma-separated vendor names (e.g. "Selfira Ambergrove, Sylvia Hartshorn")
    if ", " in vendor_name and not vendor_name.startswith('"'):
        parts = [v.strip() for v in vendor_name.split(", ", 1)]
        vendor_name = parts[0]
        alt_name = parts[1]
        alt_vc = VENDOR_COORDS.get(alt_name)
        av: dict[str, Any] = {"name": alt_name}
        if alt_vc:
            av["npcID"] = alt_vc["npcID"]
            av["x"] = alt_vc["x"]
            av["y"] = alt_vc["y"]
            av["zone"] = alt_vc.get("zone") or ""
            av["mapID"] = alt_vc["mapID"]
        additional_vendors.append(av)

    # --- Apply VENDOR_COORDS overrides ---
    # Fix wrong NPC IDs, missing coordinates, and zone mismatches using
    # curated vendor reference data. Zone-aware so shared-name vendors
    # like "Rae'ana" (Waking Shores + Silvermoon City) route to the NPC
    # whose zone matches the item.
    item_zone = item.get("zone") or ""
    vc = resolve_vendor_override(vendor_name, item_zone)
    item_npc_id = item.get("npcID")
    item_npc_x = item.get("npcX")
    item_npc_y = item.get("npcY")
    coords_mismatch = item.get("coordsMismatch")

    if vc:
        item_zone_mapid = ZONE_TO_MAPID.get(item_zone)
        vc_mapid = vc["mapID"]

        # Always use curated NPC ID (fixes wrong-NPC-version issues)
        item_npc_id = vc["npcID"]

        same_map = (item_zone_mapid == vc_mapid) if item_zone_mapid else False
        vc_zone = vc.get("zone") or MAPID_TO_ZONE.get(vc_mapid) or ""
        # Don't rezone housing-zone items (rotating vendor coords are meaningless)
        can_rezone = (vc_zone in ZONE_TO_MAPID) and not same_map and (item_zone not in HOUSING_ZONES)
        npc_changed = (vc["npcID"] != item.get("npcID"))

        if same_map:
            # Same map — use curated coords when missing, mismatched,
            # or when NPC ID changed (old coords were from wrong NPC)
            if item_npc_x is None or coords_mismatch or npc_changed:
                item_npc_x = vc["x"]
                item_npc_y = vc["y"]
                coords_mismatch = False
        elif can_rezone:
            # Different map but we can resolve the vendor's zone
            item_npc_x = vc["x"]
            item_npc_y = vc["y"]
            item_zone = vc_zone
            coords_mismatch = False
        elif item_npc_x is None or coords_mismatch:
            # Different map, can't rezone — only fill if ours are missing
            # (coords may be on wrong map but better than nothing)
            item_npc_x = vc["x"]
            item_npc_y = vc["y"]
            coords_mismatch = False

    # --- Apply TREASURE_COORDS overrides ---
    # For Treasure source items, override coords/zone with the actual treasure
    # object location (vendor coords from above are wrong for these).
    # Save vendor data first so we can emit it as separate fields.
    treasure_vendor_data = None
    # Check if Treasure is primary OR additional source
    has_treasure_additional = False
    if item.get("_additionalSources"):
        has_treasure_additional = any(
            s.get("sourceType") == "Treasure" for s in item["_additionalSources"]
        )
    tc = TREASURE_COORDS.get(decor_id) if (source_type == "Treasure" or has_treasure_additional) else None
    treasure_extra_coords = None  # for items where Treasure is additional, not primary
    if tc and source_type == "Treasure":
        # Primary Treasure: override main coords
        if vendor_name and item_npc_id and item_npc_x is not None:
            treasure_vendor_data = {
                "npcID": item_npc_id,
                "x": item_npc_x,
                "y": item_npc_y,
                "zone": item_zone,
            }
        item_npc_x = tc[0]
        item_npc_y = tc[1]
        item_zone = tc[2]
        item_npc_id = None   # treasure objects, not NPCs
        coords_mismatch = False
    elif tc:
        # Treasure is additional source — store as separate fields, keep main coords
        treasure_extra_coords = tc

    # --- Apply TREASURE_FIXUPS overrides ---
    treasure_fixup = TREASURE_FIXUPS.get(decor_id) if source_type == "Treasure" else None
    if treasure_fixup:
        if "sourceDetail" in treasure_fixup:
            source_detail = treasure_fixup["sourceDetail"]
        if "zone" in treasure_fixup:
            item_zone = treasure_fixup["zone"]
        if "npcX" in treasure_fixup:
            item_npc_x = treasure_fixup["npcX"]
            item_npc_y = treasure_fixup["npcY"]
            item_npc_id = None
            coords_mismatch = False

    # --- Silvermoon original vendor → additional vendor ---
    sm_orig = item.get("_silvermoonOrigVendor")
    if sm_orig:
        sm_av: dict[str, Any] = {"name": sm_orig}
        sm_vc = VENDOR_COORDS.get(sm_orig)
        if sm_vc:
            sm_av["npcID"] = sm_vc["npcID"]
            sm_av["x"] = sm_vc["x"]
            sm_av["y"] = sm_vc["y"]
            sm_av["zone"] = sm_vc.get("zone") or ""
            sm_av["mapID"] = sm_vc["mapID"]
        additional_vendors.append(sm_av)

    # --- Multi-vendor discovery (Phase 7) ---
    # Pick best primary by zone expansion, demote the rest to additional vendors.
    # Must run BEFORE the fields list so that vendor_name, item_zone, etc. are
    # updated for the primary vendor.
    all_vendors = item.get("_allVendors")
    if all_vendors and len(all_vendors) >= 2:
        # Build enriched vendor entries with coords from VENDOR_COORDS.
        # VENDOR_COORDS maps vendor NAME → one set of coords. For NPCs that
        # exist in multiple zones (e.g. Second Chair Pawdo in Orgrimmar,
        # Stormwind, Dornogal), only apply coords when the VENDOR_COORDS
        # zone matches this specific vendor entry's zone.
        enriched_vendors = []
        seen_zones: set[str] = set()  # deduplicate after VENDOR_COORDS overrides
        for mv in all_vendors:
            mv_name = mv.get("name", "")
            mv_zone = mv.get("zone", "")
            mv_npc_id = mv.get("npcID")
            mv_faction = mv.get("faction")

            # Apply VENDOR_COORDS override (zone-aware).
            # For NPCs in multiple zones, only apply coords when the curated
            # zone matches this entry's zone. Exception: housing zones
            # (Founder's Point / Razorwind Shores) are misleading Wowhead
            # locations — always apply the override to get the real zone.
            mv_vc = VENDOR_COORDS.get(mv_name)
            mv_x = None
            mv_y = None
            mv_map_id = None
            if mv_vc:
                mv_npc_id = mv_vc["npcID"]
                vc_zone = mv_vc.get("zone") or MAPID_TO_ZONE.get(mv_vc["mapID"]) or ""
                vc_mapid = mv_vc["mapID"]
                enriched_mapid = ZONE_TO_MAPID.get(mv_zone)
                is_housing = mv_zone in HOUSING_ZONES
                zone_matches = (vc_mapid == enriched_mapid) if enriched_mapid else True
                if zone_matches or is_housing:
                    mv_x = mv_vc["x"]
                    mv_y = mv_vc["y"]
                    mv_zone = vc_zone or mv_zone
                    mv_map_id = vc_mapid
            if not mv_map_id:
                mv_map_id = ZONE_TO_MAPID.get(mv_zone)

            # Deduplicate: same zone after overrides
            if mv_zone in seen_zones:
                continue
            seen_zones.add(mv_zone)

            # Compute expansion priority from the vendor's zone
            mv_expansion = ZONE_TO_EXPANSION.get(mv_zone, "Unknown")
            mv_exp_idx = EXPANSION_ORDER.index(mv_expansion) if mv_expansion in EXPANSION_ORDER else 0

            enriched_vendors.append({
                "name": mv_name,
                "npcID": mv_npc_id,
                "x": mv_x,
                "y": mv_y,
                "zone": mv_zone,
                "mapID": mv_map_id,
                "faction": mv_faction,
                "exp_idx": mv_exp_idx,
            })

        if len(enriched_vendors) >= 2:
            # Sort: highest expansion index first, then neutral before faction-specific
            enriched_vendors.sort(key=lambda v: (v["exp_idx"], 1 if v["faction"] is None else 0), reverse=True)

            # First = primary vendor (override current item fields)
            primary = enriched_vendors[0]
            vendor_name = primary["name"]
            item_npc_id = primary["npcID"]
            if primary["x"] is not None:
                item_npc_x = primary["x"]
                item_npc_y = primary["y"]
                coords_mismatch = False
            if primary["zone"]:
                item_zone = primary["zone"]

            # Rest = additional vendors (replace any comma-split / silvermoon ones)
            additional_vendors = []
            for ev in enriched_vendors[1:]:
                av_entry: dict[str, Any] = {"name": ev["name"]}
                if ev["npcID"]:
                    av_entry["npcID"] = ev["npcID"]
                if ev["x"] is not None:
                    av_entry["x"] = ev["x"]
                    av_entry["y"] = ev["y"]
                if ev["zone"]:
                    av_entry["zone"] = ev["zone"]
                if ev["mapID"]:
                    av_entry["mapID"] = ev["mapID"]
                if ev["faction"]:
                    av_entry["faction"] = ev["faction"]
                additional_vendors.append(av_entry)

    # Required fields: always emitted (runtime code assumes these exist)
    required_fields = [
        ("decorID", lua_number(item.get("decorID"))),
        ("name", lua_string(item.get("name"))),
        ("sourceType", lua_string(source_type)),
    ]

    for field_name, field_value in required_fields:
        lines.append(f"        {field_name} = {field_value},")

    # Optional fields: omitted when nil or empty string to reduce memory.
    # Runtime Lua code checks these for nil before use.
    optional_fields = [
        ("itemID", lua_number(item.get("itemID"))),
        ("quality", lua_number(item.get("quality"))),
        ("iconTexture", lua_number(item.get("iconTexture"))),
        ("asset", lua_number(item.get("asset"))),
        ("uiModelSceneID", lua_number(item.get("uiModelSceneID"))),
        ("zone", lua_string(item_zone)),
        ("mapID", lua_number(ZONE_TO_MAPID.get(item_zone))),
        ("sourceDetail", lua_string(source_detail)),
        ("achievementName", lua_string(achievement_name)),
        ("vendorName", lua_string(vendor_name)),
        ("professionName", lua_string(profession_name)),
        ("questID", lua_number(item.get("questID"))),
        ("npcID", lua_number(item_npc_id)),
        # Omit coordinates when they're from a different zone's coordinate
        # space (safety guard: better no pin than a wrong pin)
        ("npcX", lua_number(None if coords_mismatch else item_npc_x)),
        ("npcY", lua_number(None if coords_mismatch else item_npc_y)),
        ("faction", lua_string(item.get("faction"))),
        ("isAllowedIndoors", lua_value(item.get("isAllowedIndoors"))),
        ("isAllowedOutdoors", lua_value(item.get("isAllowedOutdoors"))),
        ("size", lua_number(item.get("size"))),
        ("placementCost", lua_number(item.get("placementCost"))),
        ("firstAcquisitionBonus", lua_number(item.get("firstAcquisitionBonus"))),
        ("expansion", lua_string(expansion)),
    ]

    for field_name, field_value in optional_fields:
        if field_value not in ("nil", '""'):
            lines.append(f"        {field_name} = {field_value},")

    # Optional: skipQuestChain flag
    if item.get("skipQuestChain"):
        lines.append("        skipQuestChain = true,")

    # Optional: isRotatingVendor flag (neighborhood vendors that rotate locations)
    if item.get("isRotatingVendor"):
        lines.append("        isRotatingVendor = true,")

    # Optional: isShopItem flag (original in-game source was "Shop" / "In-Game Shop")
    if item.get("_isShopItem"):
        lines.append("        isShopItem = true,")

    # Optional: unlockQuestID (vendor requires completing a quest first)
    # Sources: VENDOR_UNLOCK_QUESTS dict, demotion (_unlockQuestID), or questID
    # when the item has a vendorUnlockQuest (quest = vendor unlock prerequisite).
    unlock_quest = (VENDOR_UNLOCK_QUESTS.get(decor_id)
                    or item.get("_unlockQuestID")
                    or (item.get("questID") if item.get("vendorUnlockQuest") else None))
    if unlock_quest:
        lines.append(f"        unlockQuestID = {unlock_quest},")

    # Optional: vendor unlock requirements (achievement/quest needed to purchase)
    vua = item.get("vendorUnlockAchievement")
    if vua:
        lines.append(f"        vendorUnlockAchievement = {lua_string(vua)},")
    vuq = item.get("vendorUnlockQuest")
    if vuq:
        lines.append(f"        vendorUnlockQuest = {lua_string(vuq)},")

    # Optional: covenantID for Shadowlands covenant-locked vendors
    covenant_id = COVENANT_VENDORS.get(vendor_name)
    if covenant_id:
        lines.append(f"        covenantID = {covenant_id},")

    # Optional: vendorCosts sub-table (parsed from sourceTextRaw)
    vendor_costs = item.get("_vendorCosts")
    if vendor_costs:
        lines.append("        vendorCosts = {")
        for cost in vendor_costs:
            parts = [f"amount = {cost['amount']}", f"currencyID = {cost['currencyID']}"]
            icon = cost.get("iconPath")
            if icon:
                parts.append(f"iconPath = {lua_string(icon)}")
            lines.append(f"            {{ {', '.join(parts)} }},")
        lines.append("        },")

    # Optional: dropRate (from Wowhead scan)
    drop_rate = item.get("_dropRate")
    if drop_rate is not None:
        lines.append(f"        dropRate = {drop_rate},")

    # Optional: professionSkill (from Wowhead scan)
    prof_skill = item.get("_professionSkill")
    if prof_skill:
        lines.append(f"        professionSkill = {lua_string(prof_skill)},")

    # Optional: patchAdded / dateAdded (from item_versions.json, falls back to Wowhead _patchAdded)
    patch_added = item.get("_versionPatch") or item.get("_patchAdded")
    if patch_added:
        lines.append(f"        patchAdded = {lua_string(patch_added)},")
    date_added = item.get("_versionDate")
    if date_added:
        lines.append(f"        dateAdded = {lua_string(date_added)},")

    # Optional: additionalSources (from Wowhead scan)
    add_sources = item.get("_additionalSources")
    if add_sources:
        lines.append("        additionalSources = {")
        for src in add_sources:
            parts = [f"sourceType = {lua_string(src['sourceType'])}"]
            if src.get("sourceDetail"):
                parts.append(f"sourceDetail = {lua_string(src['sourceDetail'])}")
            lines.append(f"            {{ {', '.join(parts)} }},")
        lines.append("        },")

    # Optional: treasure vendor data (vendor coords saved before TREASURE_COORDS override)
    if treasure_vendor_data:
        lines.append(f"        treasureVendorNpcID = {lua_number(treasure_vendor_data['npcID'])},")
        lines.append(f"        treasureVendorX = {lua_number(treasure_vendor_data['x'])},")
        lines.append(f"        treasureVendorY = {lua_number(treasure_vendor_data['y'])},")
        tv_zone = treasure_vendor_data.get("zone") or ""
        if tv_zone:
            lines.append(f"        treasureVendorZone = {lua_string(tv_zone)},")

    # Optional: treasure hints (quest/NPC links for guided treasures)
    if treasure_fixup and "hintLine" in treasure_fixup:
        lines.append("        treasureHintLine = {")
        for seg in treasure_fixup["hintLine"]:
            parts = [f"text = {lua_string(seg['text'])}"]
            if "type" in seg:
                url = f"https://www.wowhead.com/{seg['type']}={seg['id']}"
                parts.append(f"url = {lua_string(url)}")
            lines.append(f"            {{ {', '.join(parts)} }},")
        lines.append("        },")

    # Optional: treasure containers (multi-spawn container names)
    if treasure_fixup and "containers" in treasure_fixup:
        lines.append("        treasureContainers = {")
        for container in treasure_fixup["containers"]:
            lines.append(f"            {lua_string(container)},")
        lines.append("        },")

    # Optional: treasure coords when Treasure is an additional source (not primary)
    if treasure_extra_coords:
        tc_zone = treasure_extra_coords[2]
        tc_mapid = ZONE_TO_MAPID.get(tc_zone)
        lines.append(f"        treasureX = {lua_number(treasure_extra_coords[0])},")
        lines.append(f"        treasureY = {lua_number(treasure_extra_coords[1])},")
        lines.append(f"        treasureZone = {lua_string(tc_zone)},")
        if tc_mapid:
            lines.append(f"        treasureMapID = {tc_mapid},")

    # Serialize additionalVendors sub-table
    if additional_vendors:
        lines.append("        additionalVendors = {")
        for av in additional_vendors:
            parts = [f'name = {lua_string(av.get("name", ""))}']
            if av.get("npcID"):
                parts.append(f'npcID = {lua_number(av["npcID"])}')
            if av.get("x") is not None:
                parts.append(f'x = {lua_number(av["x"])}')
            if av.get("y") is not None:
                parts.append(f'y = {lua_number(av["y"])}')
            if av.get("zone"):
                parts.append(f'zone = {lua_string(av["zone"])}')
                av_map_id = av.get("mapID") or ZONE_TO_MAPID.get(av["zone"])
                if av_map_id:
                    parts.append(f'mapID = {av_map_id}')
            if av.get("faction"):
                parts.append(f'faction = {lua_string(av["faction"])}')
            lines.append(f'            {{ {", ".join(parts)} }},')
        lines.append("        },")

    # Optional: factionVendors sub-table
    faction_vendors = item.get("factionVendors")
    if faction_vendors:
        lines.append("        factionVendors = {")
        for faction in ("Alliance", "Horde"):
            fv = faction_vendors.get(faction)
            if fv:
                fv_name = fv.get("name") or ""
                fv_npc_id = fv.get("npcID")
                fv_x = fv.get("x")
                fv_y = fv.get("y")
                fv_zone = fv.get("zone") or ""
                # Apply VENDOR_COORDS override to faction vendor.
                # For roaming vendors (same name in FP+RS), check if this is
                # the RS instance by npcID so we don't use the FP coords.
                rs_override = ROAMING_VENDOR_RS_COORDS.get(fv_npc_id)
                if rs_override:
                    fv_x = rs_override["x"]
                    fv_y = rs_override["y"]
                    fv_zone = rs_override["zone"]
                else:
                    fvc = VENDOR_COORDS.get(fv_name)
                    if fvc:
                        fv_npc_id = fvc["npcID"]
                        # Don't override housing-zone entries with non-housing coords
                        if fv_zone not in HOUSING_ZONES and (
                                fv_x is None or (ZONE_TO_MAPID.get(fv_zone) != fvc["mapID"])):
                            fv_x = fvc["x"]
                            fv_y = fvc["y"]
                            fv_zone = fvc.get("zone") or MAPID_TO_ZONE.get(fvc["mapID"]) or fv_zone
                parts = [f'name = {lua_string(fv_name)}']
                if fv_npc_id:
                    parts.append(f'npcID = {lua_number(fv_npc_id)}')
                if fv_x is not None:
                    parts.append(f'x = {lua_number(fv_x)}')
                if fv_y is not None:
                    parts.append(f'y = {lua_number(fv_y)}')
                if fv_zone:
                    parts.append(f'zone = {lua_string(fv_zone)}')
                    fv_map_id = ZONE_TO_MAPID.get(fv_zone)
                    if fv_map_id:
                        parts.append(f'mapID = {fv_map_id}')
                lines.append(f'            {faction} = {{ {", ".join(parts)} }},')
        lines.append("        },")

    # Optional: factionQuestChains sub-table
    faction_quest_chains = item.get("factionQuestChains")
    if faction_quest_chains:
        lines.append("        factionQuestChains = {")
        for faction in ("Alliance", "Horde"):
            fqc = faction_quest_chains.get(faction)
            if fqc:
                parts = [f'questID = {lua_number(fqc.get("questID"))}']
                lines.append(f'            {faction} = {{ {", ".join(parts)} }},')
        lines.append("        },")

    # Category / subcategory IDs (from game catalog API)
    cat_ids = item.get("categoryIDs", [])
    subcat_ids = item.get("subcategoryIDs", [])
    if cat_ids:
        lines.append(f"        categoryIDs = {{{', '.join(str(x) for x in cat_ids)}}},")
    if subcat_ids:
        lines.append(f"        subcategoryIDs = {{{', '.join(str(x) for x in subcat_ids)}}},")

    # Theme IDs and scores (from compute_item_themes.py)
    theme_ids = item.get("_themeIDs")
    theme_scores = item.get("_themeScores")
    if theme_ids:
        lines.append(f"        themeIDs = {{{', '.join(str(t) for t in theme_ids)}}},")
    if theme_scores:
        score_parts = [f"[{tid}] = {score}" for tid, score in sorted(theme_scores.items())]
        lines.append(f"        themeScores = {{{', '.join(score_parts)}}},")

    # Curated search keywords (from item_keywords.json)
    kw = item.get("_keywords")
    if kw:
        lines.append(f"        keywords = {lua_string(kw)},")

    lines.append("    },")
    return "\n".join(lines)


def serialize_id_list(ids: list[int], indent: str = "    ") -> str:
    """Serialize a list of decorIDs as a Lua inline table."""
    parts = [str(i) for i in ids]
    # Try inline first
    inline = f"{{ {', '.join(parts)} }}"
    if len(inline) <= 120:
        return inline
    # Multi-line for very long lists
    lines = ["{"]
    chunk_size = 20
    for i in range(0, len(parts), chunk_size):
        chunk = ", ".join(parts[i:i + chunk_size])
        lines.append(f"        {chunk},")
    lines.append(f"    }}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def main() -> None:
    if not CATALOG_JSON.exists():
        logger.error("Catalog file not found: %s", CATALOG_JSON)
        sys.exit(1)

    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    logger.info("Loaded %d items from %s", len(catalog), CATALOG_JSON)

    # Normalize known zone typos (Blizzard source data has occasional misspellings)
    ZONE_RENAMES = {
        "Harandarr": "Harandar",
    }
    zone_rename_count = 0
    for item in catalog:
        z = item.get("zone") or ""
        if z in ZONE_RENAMES:
            item["zone"] = ZONE_RENAMES[z]
            zone_rename_count += 1
    if zone_rename_count:
        logger.info("Renamed %d zone typos", zone_rename_count)

    # -----------------------------------------------------------------------
    # Extract vendor costs from sourceTextRaw
    # -----------------------------------------------------------------------
    all_currencies: dict[int, str] = {}  # currencyID → iconPath
    cost_count = 0
    for item in catalog:
        raw = item.get("sourceTextRaw") or ""
        costs = extract_costs(raw)
        if costs:
            item["_vendorCosts"] = costs
            cost_count += 1
            for c in costs:
                cid = c["currencyID"]
                if cid != 0 and cid not in all_currencies:
                    all_currencies[cid] = c.get("iconPath", "")
    logger.info("Extracted vendor costs for %d items (%d unique currencies)",
                cost_count, len(all_currencies))

    # -----------------------------------------------------------------------
    # Load Wowhead extra data (from enrich_wowhead_extra.py)
    # -----------------------------------------------------------------------
    extra_data: dict[int, dict] = {}
    if EXTRA_JSON.exists():
        with open(EXTRA_JSON, "r", encoding="utf-8") as f:
            extra_raw = json.load(f)
        items_extra = extra_raw.get("items", {})
        # JSON keys are strings; convert to int
        for item_id_str, data in items_extra.items():
            try:
                extra_data[int(item_id_str)] = data
            except (ValueError, TypeError):
                continue
        logger.info("Loaded %d Wowhead extra entries from %s", len(extra_data), EXTRA_JSON)
    else:
        logger.info("No Wowhead extra data file (%s) — skipping extra fields", EXTRA_JSON)

    # Load theme data (from compute_item_themes.py)
    theme_data: dict = {"items": {}, "metadata": {}}
    if THEMES_JSON.exists():
        with open(THEMES_JSON, "r", encoding="utf-8") as f:
            theme_data = json.load(f)
        logger.info("Loaded theme data for %d items from %s",
                     len(theme_data.get("items", {})), THEMES_JSON)
    else:
        logger.info("No theme data file (%s) — skipping themes", THEMES_JSON)

    # Merge extra data into catalog items
    extra_merged = 0
    for item in catalog:
        item_id = item.get("itemID")
        if not item_id or item_id not in extra_data:
            continue
        extra = extra_data[item_id]
        if extra.get("error"):
            continue

        # Additional sources (filter out NPC type — those are vendors/drops already handled)
        add_src = extra.get("additionalSources", [])
        filtered_src = [s for s in add_src if s.get("sourceType") not in ("NPC", "Item", "Starter", "Container") and not s.get("sourceType", "").startswith("Unknown")]
        # Remove false positive Treasure links for specific items
        if item_id in TREASURE_FALSE_POSITIVES:
            filtered_src = [s for s in filtered_src if s.get("sourceType") != "Treasure"]
        if filtered_src:
            item["_additionalSources"] = filtered_src

        if promote_wowhead_npc_fallback(
            item, extra,
            wh_zone_to_name=WH_ZONE_ID_TO_NAME,
            zone_to_mapid=ZONE_TO_MAPID,
            logger_=logger,
        ):
            pass  # logging happens inside the helper

        # Drop rate
        drop_rate = extra.get("dropRate")
        if drop_rate is not None and drop_rate > 0:
            item["_dropRate"] = drop_rate

        # Profession skill
        prof_skill = extra.get("professionSkill")
        if prof_skill:
            item["_professionSkill"] = prof_skill

        # Patch added
        patch = extra.get("patchAdded")
        if patch:
            item["_patchAdded"] = patch

        # Wowhead vendor costs (cross-reference with in-game costs)
        wh_costs = extra.get("wowheadVendorCosts")
        if wh_costs and not item.get("_vendorCosts"):
            # Use Wowhead costs as fallback when in-game data is missing
            item["_vendorCosts"] = wh_costs
            cost_count += 1

        extra_merged += 1

    if extra_merged:
        logger.info("Merged Wowhead extra data into %d items", extra_merged)

    # Merge theme data into catalog items
    theme_items = theme_data.get("items", {})
    themes_merged = 0
    for item in catalog:
        did_str = str(item.get("decorID", ""))
        if did_str in theme_items:
            item_themes = theme_items[did_str].get("themes", {})
            if item_themes:
                # themeIDs: sorted list of numeric theme IDs
                tids = sorted(int(t) for t in item_themes.keys())
                item["_themeIDs"] = tids
                # themeScores: {themeID: score}
                item["_themeScores"] = {int(t): s for t, s in item_themes.items()}
                themes_merged += 1
    if themes_merged:
        logger.info("Merged theme data into %d items", themes_merged)

    # Load and merge curated search keywords
    kw_map: dict[str, str] = {}
    if KEYWORDS_JSON.exists():
        with open(KEYWORDS_JSON, "r", encoding="utf-8") as f:
            kw_data = json.load(f)
        for group in kw_data.get("groups", {}).values():
            kw = group.get("keywords", "")
            for did in group.get("decorIDs", []):
                existing = kw_map.get(str(did), "")
                merged = (existing + " " + kw).strip()
                kw_map[str(did)] = " ".join(sorted(set(merged.split())))
        for did, kw in kw_data.get("items", {}).items():
            existing = kw_map.get(did, "")
            merged = (existing + " " + kw).strip()
            kw_map[did] = " ".join(sorted(set(merged.split())))
        kw_count = 0
        for item in catalog:
            kw = kw_map.get(str(item.get("decorID", "")))
            if kw:
                item["_keywords"] = kw
                kw_count += 1
        if kw_count:
            logger.info("Applied search keywords to %d items", kw_count)

    # Load item version data (patch + date each item was added to the database)
    item_versions: dict[str, dict] = {}
    if VERSIONS_JSON.exists():
        with open(VERSIONS_JSON, "r", encoding="utf-8") as f:
            item_versions = json.load(f)
        logger.info("Loaded version data for %d items from %s",
                     len(item_versions), VERSIONS_JSON)
    versions_applied = 0
    for item in catalog:
        did_str = str(item.get("decorID", ""))
        ver = item_versions.get(did_str)
        if ver:
            item["_versionPatch"] = ver.get("patch", "")
            item["_versionDate"] = ver.get("date", "")
            versions_applied += 1
    if versions_applied:
        logger.info("Applied version data to %d items", versions_applied)

    # Load faction quest overrides (cross-faction quest chains)
    faction_quest_overrides: dict[str, dict] = {}
    if FACTION_QUEST_OVERRIDES_JSON.exists():
        with open(FACTION_QUEST_OVERRIDES_JSON, "r", encoding="utf-8") as f:
            faction_quest_overrides = json.load(f)
        logger.info("Loaded %d faction quest overrides from %s",
                     len(faction_quest_overrides), FACTION_QUEST_OVERRIDES_JSON)

    # Apply faction quest overrides: set faction to "neutral", clear flat questID,
    # and attach factionQuestChains sub-table (same pattern as factionVendors)
    faction_quest_count = 0
    for item in catalog:
        decor_id_str = str(item.get("decorID", ""))
        if decor_id_str in faction_quest_overrides:
            override = faction_quest_overrides[decor_id_str]
            item["faction"] = "neutral"
            item["questID"] = None
            item["factionQuestChains"] = {}
            for faction in ("Alliance", "Horde"):
                if faction in override and override[faction].get("questID"):
                    item["factionQuestChains"][faction] = {
                        "questID": override[faction]["questID"],
                    }
            faction_quest_count += 1
    if faction_quest_count:
        logger.info("Applied faction quest chain overrides to %d items", faction_quest_count)

    # Load vendor requirements (achievement/quest needed to purchase from vendor)
    vendor_requirements: dict[int, dict] = {}
    if VENDOR_REQUIREMENTS_JSON.exists():
        with open(VENDOR_REQUIREMENTS_JSON, "r", encoding="utf-8") as f:
            vr_data = json.load(f)
        for decor_id_str, req in vr_data.get("requirements", {}).items():
            vendor_requirements[int(decor_id_str)] = req
        logger.info("Loaded %d vendor requirements from %s",
                     len(vendor_requirements), VENDOR_REQUIREMENTS_JSON)

    # Apply vendor requirements to catalog items
    vr_applied = 0
    for item in catalog:
        decor_id = item.get("decorID")
        vr = vendor_requirements.get(decor_id)
        if vr:
            if vr.get("unlockAchievement"):
                item["vendorUnlockAchievement"] = vr["unlockAchievement"]
            if vr.get("unlockQuest"):
                item["vendorUnlockQuest"] = vr["unlockQuest"]
            vr_applied += 1
    if vr_applied:
        logger.info("Applied vendor unlock requirements to %d items", vr_applied)

    # -----------------------------------------------------------------------
    # Vendor-unlock quest demotion — Items where the Wowhead tooltip says
    # "Complete the quest X" but the item has NO "Reward From" section on
    # Wowhead.  The quest merely unlocks the vendor — the item is NOT a
    # quest reward.  Phase 6 of enrich_catalog.py checks Wowhead's "Reward
    # From" section and correctly identifies legitimate quest rewards across
    # all zones.  Only items below lack "Reward From" entirely.
    #
    # These items are treated as pure Vendor: strip Quest source, clear ALL
    # quest-related metadata (vendorUnlockQuest, unlockQuestID, etc.).
    # -----------------------------------------------------------------------
    # NOTE: 2466 (Gnomish Sprocket Table) excluded — handled by faction_quest_overrides
    VENDOR_UNLOCK_QUEST_ITEMS: set[int] = {
        # Items where Wowhead tooltip says "Complete the quest X" but
        # the item has NO "Reward From" section — the quest unlocks the
        # vendor, not rewards the item.  Blizzard's raw C_HousingCatalog
        # API also reports quest=null for these.
        1277,   # Rusty Patchwork Tub (vendor: Blair Bass, Undermine)
        1883,   # Kaldorei Stone Fencepost (vendor: Myria Glenbrook, Val'sharah)
        9054,   # Admiral's Low-Hanging Chandelier (vendor: Pearl Barlow, Tiragarde Sound)
        9186,   # Dornogal Hanging Lantern (vendor: Garnett, Dornogal)
        11905,  # Driftwood Barrel (vendor: Ransa Greyfeather, Highmountain)
    }
    demoted = 0
    for item in catalog:
        if item.get("decorID") not in VENDOR_UNLOCK_QUEST_ITEMS:
            continue
        # Strip Quest sources so Vendor/Shop becomes primary
        old_sources = item.get("sources") or []
        new_sources = [s for s in old_sources if s.get("type") != "Quest"]
        if len(new_sources) != len(old_sources):
            item["sources"] = new_sources
            demoted += 1
            logger.debug("  Demoted quest source: decorID %d (%s) — quest was vendor unlock",
                         item["decorID"], item.get("name", "?"))
        # Clear ALL quest-related metadata — these are pure Vendor items
        item["quest"] = None
        item["questID"] = None
        item["vendorUnlockQuest"] = None
        item["_unlockQuestID"] = None
    if demoted:
        logger.info("Demoted %d vendor-unlock quests from Quest to Vendor/Shop source", demoted)

    # -----------------------------------------------------------------------
    # Load Silvermoon vendor overrides (additive — keeps original vendor as alt)
    # -----------------------------------------------------------------------
    silvermoon_overrides: dict[int, dict] = {}
    if SILVERMOON_VENDORS_JSON.exists():
        with open(SILVERMOON_VENDORS_JSON, "r", encoding="utf-8") as f:
            sm_raw = json.load(f)
        for decor_id_str, override in sm_raw.get("overrides", {}).items():
            try:
                silvermoon_overrides[int(decor_id_str)] = override
            except (ValueError, TypeError):
                continue
        logger.info("Loaded %d Silvermoon vendor overrides from %s",
                     len(silvermoon_overrides), SILVERMOON_VENDORS_JSON)

    # Build decorID → item index for fast lookup
    decor_id_to_item: dict[int, dict] = {item["decorID"]: item for item in catalog}

    # Apply Silvermoon vendor overrides: set Silvermoon vendor as primary,
    # preserve original vendor as alt (so both vendors are available in-game)
    sm_applied = 0
    sm_source_added = 0
    for decor_id, sm in silvermoon_overrides.items():
        item = decor_id_to_item.get(decor_id)
        if not item:
            logger.warning("Silvermoon override for decorID %d: item not found in catalog", decor_id)
            continue

        # If item has factionVendors, remove them — Silvermoon is neutral and
        # accessible to both factions, so faction routing is unnecessary.
        if item.get("factionVendors"):
            logger.info("  Removing factionVendors for decorID %d (%s): Silvermoon override",
                         decor_id, item.get("name", ""))
            del item["factionVendors"]

        # Clear rotating vendor flag — item now has a fixed Silvermoon location
        if item.get("isRotatingVendor"):
            del item["isRotatingVendor"]

        # Save original vendor info before overriding
        orig_vendor = get_vendor_name(item)
        orig_npc_id = item.get("npcID")
        orig_x = item.get("npcX")
        orig_y = item.get("npcY")
        orig_zone = item.get("zone") or ""

        if orig_vendor and orig_vendor != sm["vendorName"]:
            # Preserve original vendor as alternate (name only — VENDOR_COORDS
            # will resolve its coords at serialization time)
            item["_silvermoonOrigVendor"] = orig_vendor

        # Set Silvermoon vendor as primary
        item["vendor"] = sm["vendorName"]
        item["npcID"] = sm["npcID"]
        item["npcX"] = sm["npcX"]
        item["npcY"] = sm["npcY"]
        item["zone"] = sm["zone"]
        item["coordsMismatch"] = False

        # For items without a Vendor source, add one so get_primary_source_type
        # returns "Vendor" instead of "Shop" / "Other"
        sources = item.get("sources") or []
        has_vendor_source = any(s.get("type") == "Vendor" for s in sources)
        if not has_vendor_source:
            sources.append({"type": "Vendor", "value": sm["vendorName"]})
            item["sources"] = sources
            sm_source_added += 1

        sm_applied += 1

    if sm_applied:
        logger.info("Applied Silvermoon vendor overrides to %d items (%d gained Vendor source)",
                     sm_applied, sm_source_added)

    # -----------------------------------------------------------------------
    # Validate Dennia Silvertongue assignments: her Wowhead NPC page only
    # lists 14 items, but enriched_catalog.json incorrectly attributes many
    # more (e.g. newer Grrgle colors).  Clear Dennia from any item NOT in the
    # verified override list.  Other Silvermoon vendors are trusted as-is
    # because their Wowhead item pages are accurate even when NPC pages lag.
    # -----------------------------------------------------------------------
    dennia_verified = {
        int(k) for k, v in silvermoon_overrides.items()
        if v["vendorName"] == "Dennia Silvertongue"
    }
    sm_cleared = 0
    for item in catalog:
        vendor = get_vendor_name(item)
        if vendor == "Dennia Silvertongue" and item["decorID"] not in dennia_verified:
            logger.warning("  Clearing unverified Dennia vendor from decorID %d (%s)",
                           item["decorID"], item.get("name", ""))
            item["vendor"] = ""
            item["npcID"] = None
            item["npcX"] = None
            item["npcY"] = None
            item["zone"] = ""
            item["mapID"] = None
            # Also strip Dennia from sources so get_vendor_name() won't find her
            sources = item.get("sources") or []
            item["sources"] = [
                s for s in sources
                if not (s.get("type") == "Vendor" and s.get("value") == "Dennia Silvertongue")
            ]
            sm_cleared += 1
    if sm_cleared:
        logger.info("Cleared unverified Dennia Silvertongue vendor data from %d items", sm_cleared)

    # -----------------------------------------------------------------------
    # Flag items with original "Shop" in-game source (persists even after
    # source type overrides, e.g. Silvermoon converting Shop → Vendor)
    # -----------------------------------------------------------------------
    shop_item_count = 0
    for item in catalog:
        raw = (item.get("sourceTextRaw") or "")
        if "Shop|r" in raw or "In-Game Shop|r" in raw:
            item["_isShopItem"] = True
            shop_item_count += 1
    if shop_item_count:
        logger.info("Flagged %d items with original Shop source", shop_item_count)

    # Check for unmapped zones
    unmapped_zones = set()
    unmapped_continents = set()
    for item in catalog:
        zone = item.get("zone") or ""
        if zone and zone not in ZONE_TO_EXPANSION:
            unmapped_zones.add(zone)
        if zone and zone not in ZONE_TO_CONTINENT:
            unmapped_continents.add(zone)
    if unmapped_zones:
        for z in sorted(unmapped_zones):
            logger.warning("Unmapped zone->expansion (mapped to Unknown): %s", z)
    if unmapped_continents:
        for z in sorted(unmapped_continents):
            logger.warning("Unmapped zone->continent (mapped to Unknown): %s", z)

    # Check for unmapped zone->mapID
    unmapped_mapids = set()
    for item in catalog:
        zone = item.get("zone") or ""
        if zone and zone not in ZONE_TO_MAPID:
            unmapped_mapids.add(zone)
        fv = item.get("factionVendors")
        if fv:
            for faction_data in fv.values():
                fz = faction_data.get("zone") or ""
                if fz and fz not in ZONE_TO_MAPID:
                    unmapped_mapids.add(fz)
    # Also check DungeonEntrance outdoor zones
    for ent in DUNGEON_ENTRANCES.values():
        if ent[0] not in ZONE_TO_MAPID:
            unmapped_mapids.add(ent[0])
    # Also check DropMobs hub zones
    for group in DROP_MOB_GROUPS.values():
        hz = group["hub"][0]
        if hz not in ZONE_TO_MAPID:
            unmapped_mapids.add(hz)
    if unmapped_mapids:
        for z in sorted(unmapped_mapids):
            logger.warning("Unmapped zone->mapID (no uiMapID): %s", z)

    # Apply fixups: fill missing zone data for known Drop items
    fixup_count = 0
    for item in catalog:
        if not item.get("zone"):
            fixed_zone = _fixup_zone(item)
            if fixed_zone:
                item["zone"] = fixed_zone
                fixup_count += 1
    if fixup_count:
        logger.info("Applied zone fixups to %d items", fixup_count)

    # Auto-generate factionVendors for neighborhood items missing them
    auto_fv_count = _auto_generate_faction_vendors(catalog)
    if auto_fv_count:
        logger.info("Auto-generated factionVendors for %d neighborhood items", auto_fv_count)

    # Compute derived fields for each item
    ItemMeta = tuple[dict[str, Any], str, str, str, str, str, str]
    items_with_meta: list[ItemMeta] = []
    npc_fixup_count = 0
    treasure_hunt_count = 0
    faction_vendor_count = 0
    for item in catalog:
        source_type = get_primary_source_type(item)
        source_detail = get_source_detail(item, source_type)

        # Override: Decor Treasure Hunt items with faction vendors → Vendor
        if (source_type == "Quest"
                and item.get("quest") == "Decor Treasure Hunt"
                and item.get("factionVendors")):
            source_type = "Vendor"
            source_detail = "Decor Treasure Hunt"
            # Clear flat vendor/NPC fields — UI resolves from factionVendors
            item["vendorName"] = ""
            item["npcID"] = None
            item["npcX"] = None
            item["npcY"] = None
            item["zone"] = ""
            treasure_hunt_count += 1
        elif item.get("factionVendors"):
            # Items with faction-specific vendors (Neighborhoods, PvP, rep, etc.)
            # Clear flat vendor/NPC fields — UI resolves from factionVendors
            # Keep zone for filtering/display (acquisition zone)
            item["vendorName"] = ""
            item["vendor"] = ""
            item["npcID"] = None
            item["npcX"] = None
            item["npcY"] = None
            faction_vendor_count += 1

        # Rotating neighborhood vendors: generate factionVendors for both zones
        # (these vendors appear in both Founder's Point and Razorwind Shores)
        item_zone = item.get("zone") or ""
        item_npc = item.get("npcID")
        if (item_zone in HOUSING_ZONES
                and item_npc in ROTATING_VENDOR_NPC_IDS
                and not item.get("factionVendors")):
            rv_name = get_vendor_name(item)
            if rv_name:
                item["factionVendors"] = {
                    "Alliance": {"name": rv_name, "npcID": item_npc,
                                 "zone": "Founder's Point"},
                    "Horde":    {"name": rv_name, "npcID": item_npc,
                                 "zone": "Razorwind Shores"},
                }
                # Clear flat vendor/NPC fields — UI resolves from factionVendors
                item["vendorName"] = ""
                item["vendor"] = ""
                item["npcID"] = None
                item["npcX"] = None
                item["npcY"] = None
                faction_vendor_count += 1
            else:
                # No vendor name available — fallback to old behavior
                item["npcX"] = None
                item["npcY"] = None
                item["isRotatingVendor"] = True

        # Apply source detail fixups for Drop items with known boss names
        if source_type == "Drop":
            source_detail = _fixup_source_detail(item, source_detail)
            # Populate npcID for Drop items from boss NPC lookup
            if not item.get("npcID") and source_detail in DROP_NPC_IDS:
                item["npcID"] = DROP_NPC_IDS[source_detail]
                npc_fixup_count += 1
        # Apply TREASURE_FIXUPS zone to item dict so expansion/index picks it up
        tf = TREASURE_FIXUPS.get(item["decorID"])
        if tf and source_type == "Treasure" and "zone" in tf:
            item["zone"] = tf["zone"]
        expansion = get_expansion(item)
        achievement_name = get_achievement_name(item)
        vendor_name = "" if item.get("factionVendors") else get_vendor_name(item)
        prof_detail = item.get("profession", "")
        profession_name = parse_profession_name(prof_detail) if source_type == "Profession" else ""
        items_with_meta.append((item, source_type, source_detail, expansion,
                                achievement_name, vendor_name, profession_name))
    if npc_fixup_count:
        logger.info("Applied boss NPC ID fixups to %d Drop items", npc_fixup_count)
    if treasure_hunt_count:
        logger.info("Converted %d Treasure Hunt items to Vendor source type", treasure_hunt_count)
    if faction_vendor_count:
        logger.info("Cleared flat vendor fields for %d items with factionVendors", faction_vendor_count)

    # Build index structures
    by_source: dict[str, list[tuple[int, str]]] = defaultdict(list)
    by_expansion: dict[str, list[tuple[int, str]]] = defaultdict(list)
    by_profession: dict[str, list[tuple[int, str]]] = defaultdict(list)
    by_zone: dict[str, list[tuple[int, str]]] = defaultdict(list)
    name_index: list[tuple[int, str]] = []

    # BySubcategory index: subcategoryID → list of decorIDs
    by_subcategory: dict[int, list[int]] = defaultdict(list)

    for item, source_type, source_detail, expansion, ach_name, vnd_name, prof_name in items_with_meta:
        decor_id = item["decorID"]
        name = item.get("name", "")
        zone = item.get("zone") or ""
        by_source[source_type].append((decor_id, name))
        by_expansion[expansion].append((decor_id, name))
        if zone:
            by_zone[zone].append((decor_id, name))
        if prof_name:
            by_profession[prof_name].append((decor_id, name))
        name_index.append((decor_id, name))
        for sid in item.get("subcategoryIDs", []):
            by_subcategory[sid].append(decor_id)

    # Sort within each bucket by name alphabetically
    for key in by_source:
        by_source[key].sort(key=lambda x: x[1].lower())
    for key in by_expansion:
        by_expansion[key].sort(key=lambda x: x[1].lower())
    for key in by_profession:
        by_profession[key].sort(key=lambda x: x[1].lower())
    for key in by_zone:
        by_zone[key].sort(key=lambda x: x[1].lower())

    # Sort name index alphabetically by lowercase name
    name_index.sort(key=lambda x: x[1].lower())

    # Build Lua output
    lines: list[str] = []

    # Header
    lines.append("-- Auto-generated by output_catalog_lua.py. DO NOT EDIT MANUALLY.")
    lines.append(f"-- Decorations: {len(catalog)} items")
    lines.append("local _, NS = ...")
    lines.append("NS.CatalogData = NS.CatalogData or {}")
    lines.append("")

    # Items table
    lines.append("NS.CatalogData.Items = {")
    for item, source_type, source_detail, expansion, ach_name, vnd_name, prof_name in items_with_meta:
        lines.append(serialize_item(item, source_type, source_detail, expansion,
                                    ach_name, vnd_name, prof_name))
    lines.append("}")
    lines.append("")

    # CurrencyInfo lookup table (currencyID → iconPath)
    if all_currencies:
        lines.append("NS.CatalogData.CurrencyInfo = {")
        for cid in sorted(all_currencies.keys()):
            icon = all_currencies[cid]
            lines.append(f"    [{cid}] = {lua_string(icon)},")
        lines.append("}")
        lines.append("")
        logger.info("  CurrencyInfo: %d currencies", len(all_currencies))

    # BySource
    lines.append("NS.CatalogData.BySource = {")
    for source in SOURCE_ORDER:
        if source in by_source:
            ids = [decor_id for decor_id, _ in by_source[source]]
            lines.append(f"    {source} = {serialize_id_list(ids)},")
    lines.append("}")
    lines.append("")

    # ByExpansion
    lines.append("NS.CatalogData.ByExpansion = {")
    for exp in EXPANSION_ORDER:
        if exp in by_expansion:
            ids = [decor_id for decor_id, _ in by_expansion[exp]]
            lines.append(f"    [{lua_string(exp)}] = {serialize_id_list(ids)},")
    lines.append("}")
    lines.append("")

    # ByProfession
    profession_order = sorted(by_profession.keys())
    lines.append("NS.CatalogData.ByProfession = {")
    for prof in profession_order:
        ids = [decor_id for decor_id, _ in by_profession[prof]]
        lines.append(f"    [{lua_string(prof)}] = {serialize_id_list(ids)},")
    lines.append("}")
    lines.append("")

    # ProfessionOrder
    prof_list = ", ".join(lua_string(p) for p in profession_order)
    lines.append(f"NS.CatalogData.ProfessionOrder = {{ {prof_list} }}")
    lines.append("")

    # SourceOrder
    src_list = ", ".join(lua_string(s) for s in SOURCE_ORDER)
    lines.append(f"NS.CatalogData.SourceOrder = {{ {src_list} }}")
    lines.append("")

    # ByZone
    lines.append("NS.CatalogData.ByZone = {")
    for zone in sorted(by_zone.keys()):
        ids = [decor_id for decor_id, _ in by_zone[zone]]
        lines.append(f"    [{lua_string(zone)}] = {serialize_id_list(ids)},")
    lines.append("}")
    lines.append("")

    # ZoneToContinentMap
    # Emit all known zones (catalog + quest givers + static mapping) so that
    # the success popup can resolve the continent for quest giver zones too.
    all_known_zones = set(by_zone.keys()) | set(ZONE_TO_CONTINENT.keys())
    lines.append("NS.CatalogData.ZoneToContinentMap = {")
    for zone in sorted(all_known_zones):
        continent = ZONE_TO_CONTINENT.get(zone, "Unknown")
        lines.append(f"    [{lua_string(zone)}] = {lua_string(continent)},")
    lines.append("}")
    lines.append("")

    # ContinentOrder (only continents that have items)
    active_continents = set()
    for zone in by_zone.keys():
        active_continents.add(ZONE_TO_CONTINENT.get(zone, "Unknown"))
    ordered_continents = [c for c in CONTINENT_ORDER if c in active_continents]
    if "Unknown" in active_continents:
        ordered_continents.append("Unknown")
    cont_list = ", ".join(lua_string(c) for c in ordered_continents)
    lines.append(f"NS.CatalogData.ContinentOrder = {{ {cont_list} }}")
    lines.append("")

    # ZoneToExpansionMap
    all_known_zones_exp = set(by_zone.keys()) | set(ZONE_TO_EXPANSION.keys())
    lines.append("NS.CatalogData.ZoneToExpansionMap = {")
    for zone in sorted(all_known_zones_exp):
        expansion = ZONE_TO_EXPANSION.get(zone, "Unknown")
        lines.append(f"    [{lua_string(zone)}] = {lua_string(expansion)},")
    lines.append("}")
    lines.append("")

    # ExpansionOrder (only expansions that have items)
    active_expansions = set()
    for zone in by_zone.keys():
        active_expansions.add(ZONE_TO_EXPANSION.get(zone, "Unknown"))
    ordered_expansions = [e for e in EXPANSION_ORDER if e in active_expansions]
    if "Unknown" in active_expansions:
        ordered_expansions.append("Unknown")
    exp_list = ", ".join(lua_string(e) for e in ordered_expansions)
    lines.append(f"NS.CatalogData.ExpansionOrder = {{ {exp_list} }}")
    lines.append("")

    # ZoneToMapID — pipeline-emitted zone name → uiMapID table.
    # Replaces runtime HereBeDragons scanning + MIDNIGHT_ZONE_OVERRIDES.
    all_mapped_zones = set(by_zone.keys()) | set(ZONE_TO_MAPID.keys())
    lines.append("NS.CatalogData.ZoneToMapID = {")
    for zone in sorted(all_mapped_zones):
        map_id = ZONE_TO_MAPID.get(zone)
        if map_id:
            lines.append(f"    [{lua_string(zone)}] = {map_id},")
    lines.append("}")
    lines.append("")

    # TrustedZoneIDs — Midnight zones navigable despite unusual parent hierarchy
    lines.append("NS.CatalogData.TrustedZoneIDs = {")
    for mid in sorted(TRUSTED_ZONE_IDS.keys()):
        lines.append(f"    [{mid}] = true,")
    lines.append("}")
    lines.append("")

    # DungeonEntrances (dungeons/raids that appear in the catalog, plus
    # instance zones referenced by VENDOR_COORDS)
    vendor_zones = {vc.get("zone") for vc in VENDOR_COORDS.values() if vc.get("zone")}
    active_entrances = {
        zone: ent for zone, ent in DUNGEON_ENTRANCES.items()
        if zone in by_zone or zone in vendor_zones
    }
    if active_entrances:
        lines.append("NS.CatalogData.DungeonEntrances = {")
        for zone in sorted(active_entrances.keys()):
            ent = active_entrances[zone]
            outdoor_zone, x, y = ent[0], ent[1], ent[2]
            # Prefer explicit mapID from tuple; fall back to ZONE_TO_MAPID
            map_id = ent[3] if len(ent) > 3 else ZONE_TO_MAPID.get(outdoor_zone)
            map_part = f", mapID = {map_id}" if map_id else ""
            lines.append(
                f"    [{lua_string(zone)}] = "
                f"{{ zone = {lua_string(outdoor_zone)}, x = {x}, y = {y}{map_part} }},"
            )
        lines.append("}")
        lines.append("")
        logger.info("  DungeonEntrances: %d (of %d defined)",
                     len(active_entrances), len(DUNGEON_ENTRANCES))

    # BossFloorMaps (from in-game EJ dump via parse_boss_dump.py)
    #
    # Manual fixups for bosses the EJ scan couldn't resolve (deeper dungeon
    # floors, heroic-only encounters, etc.).
    boss_floor_fixups: dict[str, int] = {
        "vanessa vancleef": 292,            # Deadmines (heroic-only, not in EJ listing)
        "lord godfrey": 315,                # Shadowfang Keep
        "emperor dagran thaurissan": 243,   # Blackrock Depths
        "garrosh hellscream": 567,          # Siege of Orgrimmar
        "high sage viryx": 602,             # Skyreach
        "warlord zaela": 618,               # Upper Blackrock Spire
        "spellblade aluriel": 766,          # The Nighthold
        "viz'aduum the watcher": 822,       # Return to Karazhan
        "vol'zith the whisperer": 1040,     # Shrine of the Storm
        "king mechagon": 1497,              # Operation: Mechagon
        "kyrakka and erkhart stormvein": 2094,  # Ruby Life Pools
        "echo of doragosa": 2099,           # Algeth'ar Academy
        "prioress murrpray": 2309,          # Priory of the Sacred Flame
        "the restless heart": 2499,         # Windrunner Spire
        "degentrius": 2520,                 # Magisters' Terrace
        "crown of the cosmos": 2530,        # The Voidspire
        "nalorakk": 2513,                   # Den of Nalorakk
        "zaxasj the speaker": 1345,         # Crucible of Storms (sub-boss of The Restless Cabal)
        "charonus": 2573,                   # Voidscar Arena – Domanaar's Ascent
        "general amias bellamyr": 2529,     # The Voidspire (main floor)
    }

    # Short-name aliases: sourceDetail often uses abbreviated boss names.
    boss_name_aliases: dict[str, str] = {
        "dargrul": "dargrul the underking",
        "chimaerus": "chimaerus the undreamt god",
        "vaelgor": "vaelgor & ezzorak",
        "restless heart": "the restless heart",
        "belo'ren": "belo'ren, child of al'ar",
        "rak'tul": "rak'tul, vessel of souls",
    }

    boss_floor_maps: dict[str, int] = {}
    if BOSS_DUMP_JSON.exists():
        with open(BOSS_DUMP_JSON, "r", encoding="utf-8") as f:
            boss_dump = json.load(f)
        for entry in boss_dump:
            boss_name = entry.get("bossName", "").lower()
            floor_map_id = entry.get("floorMapID", 0)
            if boss_name and floor_map_id > 0:
                boss_floor_maps[boss_name] = floor_map_id
        # Apply manual fixups (always override — fixups are verified values
        # for bosses on deeper floors the EJ scan can't reach, or cases where
        # the same boss name exists in multiple instances like Nalorakk)
        for boss_name, floor_id in boss_floor_fixups.items():
            boss_floor_maps[boss_name] = floor_id
        # Apply short-name aliases
        for alias, full_name in boss_name_aliases.items():
            if full_name in boss_floor_maps and alias not in boss_floor_maps:
                boss_floor_maps[alias] = boss_floor_maps[full_name]
        # Add instance-name aliases: for Drop items whose sourceDetail is not a
        # known boss name but IS a dungeon/instance name, point it to the same
        # floor as the first boss found in that instance.
        instance_to_floor: dict[str, int] = {}
        for entry in boss_dump:
            inst_lower = entry.get("instanceName", "").lower()
            fmap = entry.get("floorMapID", 0)
            if inst_lower and fmap > 0 and inst_lower not in instance_to_floor:
                instance_to_floor[inst_lower] = fmap
        for _item, st, sd, *_ in items_with_meta:
            if st == "Drop" and sd:
                sd_lower = sd.lower()
                if sd_lower not in boss_floor_maps and sd_lower in instance_to_floor:
                    boss_floor_maps[sd_lower] = instance_to_floor[sd_lower]
                    logger.info("  BossFloorMaps alias: %s -> %d (instance name)",
                                sd_lower, instance_to_floor[sd_lower])
        logger.info("Loaded %d boss floor map entries (incl. %d fixups, %d aliases) from %s",
                     len(boss_floor_maps), len(boss_floor_fixups),
                     len(boss_name_aliases), BOSS_DUMP_JSON)
    else:
        logger.warning("boss_dump.json not found at %s -- BossFloorMaps will not be generated.",
                        BOSS_DUMP_JSON)
        logger.warning("Run /hs bossdump in-game, /reload, then parse_boss_dump.py")

    if boss_floor_maps:
        lines.append("NS.CatalogData.BossFloorMaps = {")
        for boss_name in sorted(boss_floor_maps.keys()):
            map_id = boss_floor_maps[boss_name]
            lines.append(f"    [{lua_string(boss_name)}] = {map_id},")
        lines.append("}")
        lines.append("")
        logger.info("  BossFloorMaps: %d entries", len(boss_floor_maps))

    # DropMobs (multi-mob drop groups with per-mob coordinates)
    if DROP_MOB_GROUPS:
        lines.append("NS.CatalogData.DropMobs = {")
        for decor_id in sorted(DROP_MOB_GROUPS.keys()):
            group = DROP_MOB_GROUPS[decor_id]
            hub = group["hub"]
            mobs = group["mobs"]
            lines.append(f"    [{decor_id}] = {{")
            hub_map_id = ZONE_TO_MAPID.get(hub[0])
            hub_map_part = f", mapID = {hub_map_id}" if hub_map_id else ""
            lines.append(f"        hub = {{ zone = {lua_string(hub[0])}, "
                         f"x = {hub[1]}, y = {hub[2]}, label = {lua_string(hub[3])}"
                         f"{hub_map_part} }},")
            lines.append("        mobs = {")
            for name, npc_id, x, y in mobs:
                lines.append(f"            {{ {lua_string(name)}, {npc_id}, {x}, {y} }},")
            lines.append("        },")
            lines.append("    },")
        lines.append("}")
        lines.append("")
        logger.info("  DropMobs: %d items with multi-mob groups", len(DROP_MOB_GROUPS))

    # NameIndex
    lines.append("NS.CatalogData.NameIndex = {")
    for decor_id, name in name_index:
        lines.append(f"    {{ {decor_id}, {lua_string(name.lower())} }},")
    lines.append("}")
    lines.append("")

    # --- Category mapping tables ---
    lines.append("-- Category / Subcategory metadata")
    lines.append("NS.CatalogData.CategoryNames = {")
    for cat_id in CATEGORY_ORDER:
        name = CATEGORY_NAMES.get(cat_id, f"Category {cat_id}")
        lines.append(f'    [{cat_id}] = {lua_string(name)},')
    lines.append("}")

    lines.append("")
    lines.append("NS.CatalogData.SubcategoryNames = {")
    for subcat_id in sorted(SUBCATEGORY_NAMES.keys()):
        name = SUBCATEGORY_NAMES[subcat_id]
        lines.append(f'    [{subcat_id}] = {lua_string(name)},')
    lines.append("}")

    lines.append("")
    lines.append("NS.CatalogData.CategoryOrder = {" + ", ".join(str(x) for x in CATEGORY_ORDER) + "}")

    lines.append("")
    lines.append("NS.CatalogData.CategorySubcategories = {")
    for cat_id in CATEGORY_ORDER:
        subcats = CATEGORY_SUBCATEGORIES.get(cat_id, [])
        lines.append(f"    [{cat_id}] = {{{', '.join(str(x) for x in subcats)}}},")
    lines.append("}")

    lines.append("")
    lines.append("NS.CatalogData.BySubcategory = {")
    for subcat_id in sorted(by_subcategory.keys()):
        ids = by_subcategory[subcat_id]
        lines.append(f"    [{subcat_id}] = {serialize_id_list(ids)},")
    lines.append("}")
    lines.append("")
    logger.info("  Categories: %d categories, %d subcategories, BySubcategory: %d entries",
                len(CATEGORY_NAMES), len(SUBCATEGORY_NAMES), len(by_subcategory))

    # --- Theme metadata tables ---
    theme_meta = theme_data.get("metadata", {})
    theme_names = theme_meta.get("theme_names", {})
    theme_groups = theme_meta.get("theme_groups", [])
    theme_group_themes = theme_meta.get("theme_group_themes", {})
    by_theme = theme_data.get("by_theme", {})

    if theme_names:
        lines.append("-- Theme metadata (from housing.wowdb.com community data)")
        lines.append("NS.CatalogData.ThemeGroupNames = {")
        for group in theme_groups:
            lines.append(f"    [{group['id']}] = {lua_string(group['name'])},")
        lines.append("}")
        lines.append("")

        lines.append("NS.CatalogData.ThemeGroupOrder = {"
                      + ", ".join(str(g["id"]) for g in theme_groups) + "}")
        lines.append("")

        lines.append("NS.CatalogData.ThemeNames = {")
        for tid_str in sorted(theme_names.keys(), key=int):
            lines.append(f"    [{tid_str}] = {lua_string(theme_names[tid_str])},")
        lines.append("}")
        lines.append("")

        lines.append("NS.CatalogData.ThemeGroupThemes = {")
        for gid_str in sorted(theme_group_themes.keys(), key=int):
            tids = theme_group_themes[gid_str]
            lines.append(f"    [{gid_str}] = {{{', '.join(str(t) for t in tids)}}},")
        lines.append("}")
        lines.append("")

        lines.append("NS.CatalogData.ByTheme = {")
        for tid_str in sorted(by_theme.keys(), key=int):
            ids = by_theme[tid_str]
            lines.append(f"    [{tid_str}] = {serialize_id_list(ids)},")
        lines.append("}")
        lines.append("")

        logger.info("  Themes: %d groups, %d themes, %d items themed",
                     len(theme_groups), len(theme_names),
                     theme_meta.get("items_themed", 0))

    lua_content = "\n".join(lines)

    # Write output
    LUA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(LUA_OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(lua_content)

    logger.info("Generated: %s (%d items)", LUA_OUTPUT, len(catalog))
    logger.info("  Sources: %s", ", ".join(f"{k}={len(v)}" for k, v in sorted(by_source.items())))
    logger.info("  Expansions: %s", ", ".join(f"{k}={len(v)}" for k, v in sorted(by_expansion.items())))


if __name__ == "__main__":
    main()
