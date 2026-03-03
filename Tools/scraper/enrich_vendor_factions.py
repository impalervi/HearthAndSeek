#!/usr/bin/env python3
"""
enrich_vendor_factions.py — Cross-reference Wowhead "Sold By" for all vendor items.

For each decor item with a vendor, fetches Wowhead "Sold By" data and checks
if multiple faction-specific vendors exist. When an Alliance vendor and a Horde
vendor are found for the same item, creates a factionVendors sub-table.

This script:
  1. Reads enriched_catalog.json
  2. For items with a vendor + itemID but no factionVendors, scrapes Wowhead
  3. Classifies vendors by faction using NPC react data
  4. Creates factionVendors entries for faction-specific vendor pairs
  5. Fetches NPC coordinates for new vendors
  6. Fixes faction annotation (neutral when both factions have a vendor)
  7. Saves updated enriched_catalog.json

Usage:
    python enrich_vendor_factions.py              # Full run
    python enrich_vendor_factions.py --dry-run    # Preview without saving
    python enrich_vendor_factions.py --stats      # Stats only (cached data)
"""

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

# Import shared infrastructure from the main enrichment script
from enrich_catalog import (
    _react_to_faction,
    cache_get,
    cache_put,
    fetch_item_sold_by,
    fetch_npc_coords,
    fetch_npc_coords_from_page,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("enrich_vendor_factions")


# ---------------------------------------------------------------------------
# Wowhead zone ID → in-game zone name mapping
# Maps Wowhead's internal zone IDs (from NPC tooltip API) to zone names
# that match our ZONE_TO_MAPID table in output_catalog_lua.py.
# ---------------------------------------------------------------------------
WHZONE_TO_ZONE: dict[int, str] = {
    # Eastern Kingdoms
    4: "Dun Morogh",
    10: "Duskwood",
    33: "Stormwind City",
    36: "Westfall",
    38: "Loch Modan",
    40: "Darkshore",
    44: "Elwynn Forest",
    46: "Burning Steppes",
    51: "Searing Gorge",
    130: "Silverpine Forest",
    148: "Deadwind Pass",
    159: "Eastern Plaguelands",
    210: "Icecrown",  # Argent Tournament sub-zone
    1519: "Ironforge",
    1537: "Ironforge",  # Old Ironforge
    2257: "Deeprun Tram",
    5287: "Burning Steppes",
    5297: "Blasted Lands",
    5300: "Loch Modan",
    5304: "Hillsbrad Foothills",
    5311: "Searing Gorge",
    5315: "Duskwood",
    5316: "Northern Stranglethorn",
    5317: "Wetlands",
    5320: "Twilight Highlands",
    5339: "The Cape of Stranglethorn",
    4714: "Gilneas",
    4706: "Ruins of Gilneas",
    # Kalimdor
    14: "Dustwallow Marsh",
    222: "Orgrimmar",
    321: "Orgrimmar",  # Valley of Strength
    361: "Felwood",
    1497: "Orgrimmar",  # sub-zone (Valley of Honor etc.)
    1584: "Winterspring",
    1637: "Orgrimmar",  # main city zone
    1638: "Thunder Bluff",
    5025: "Orgrimmar",  # Cleft of Shadow / etc
    331: "Teldrassil",
    362: "Thunder Bluff",
    405: "Mulgore",
    440: "Winterspring",
    1657: "Darnassus",
    2597: "Silithus",
    5326: "Felwood",
    # Outland
    3430: "Eversong Woods",
    3433: "Ghostlands",
    3487: "Silvermoon City",
    3518: "Nagrand",
    3520: "Shadowmoon Valley",
    # Northrend
    3537: "Borean Tundra",
    65: "Crystalsong Forest",
    394: "Grizzly Hills",
    67: "Icecrown",
    3711: "Sholazar Basin",
    4395: "Dalaran",
    4560: "Dalaran",  # Broken Isles Dalaran
    # Cataclysm
    696: "Deeprun Tram",
    # Pandaria
    5841: "Valley of the Four Winds",
    5840: "Vale of Eternal Blossoms",
    6134: "The Wandering Isle",
    # Draenor
    4298: "The Jade Forest",  # Wowhead alias
    4755: "Kun-Lai Summit",   # Wowhead alias
    4922: "Dornogal",         # TWW sub-zone
    5785: "The Jade Forest",
    5805: "Kun-Lai Summit",
    6662: "Stormshield",      # Ashran Alliance hub
    6719: "Frostfire Ridge",
    6720: "Frostfire Ridge",
    6722: "Gorgrond",
    6849: "Frostwall",
    7004: "Gorgrond",
    7078: "Stormshield",
    6940: "Lunarfall",
    6941: "Spires of Arak",
    6937: "Talador",
    7332: "Warspear",
    7333: "Warspear",
    # Legion
    7334: "Azsuna",
    7503: "Highmountain",
    7541: "Val'sharah",
    7558: "Dalaran",    # Legion Dalaran sub-zone
    7637: "Suramar",
    7502: "The Maelstrom",
    7745: "Dalaran Sewers",
    7813: "Trueshot Lodge",
    7834: "Dreadscar Rift",
    7846: "The Dreamgrove",
    7867: "Trueshot Lodge",
    7875: "Skyhold",
    7877: "Hall of the Guardian",
    7879: "Antoran Wastes",
    7882: "Mac'Aree",
    7902: "Netherlight Temple",
    7731: "Dreadscar Rift",
    7598: "Hall of the Guardian",
    7705: "Netherlight Temple",
    7648: "Skyhold",
    # Battle for Azeroth
    8567: "Boralus",
    8568: "Boralus",    # sub-zone
    8500: "Zuldazar",
    8499: "Dazar'alor",
    8501: "Nazmir",
    8496: "Tiragarde Sound",
    8497: "Drustvar",
    8670: "Zuldazar",   # sub-zone
    8701: "Dornogal",   # Wowhead alias
    8717: "Stormsong Valley",
    8721: "Mechagon",
    8899: "Mechagon",   # sub-zone
    8502: "Vol'dun",
    9042: "Dornogal",   # sub-zone
    9389: "Nazmir",     # sub-zone
    9598: "Dornogal",   # sub-zone
    9667: "Mechagon",   # sub-zone
    # Shadowlands
    10058: "Revendreth",
    10225: "Revendreth", # sub-zone
    10290: "Revendreth", # sub-zone
    10522: "The Maw",
    10986: "Revendreth", # Sinfall area
    11400: "Revendreth", # sub-zone
    11462: "Revendreth",
    # Dragonflight
    12905: "Valdrakken",     # sub-zone
    13577: "The Waking Shores",
    13644: "The Waking Shores",
    13645: "The Azure Span",
    13646: "Thaldraszus",
    13647: "Valdrakken",
    13844: "Valdrakken",     # sub-zone
    13862: "The Waking Shores",
    14022: "Valdrakken",     # sub-zone
    14433: "The Forbidden Reach",
    # The War Within
    14529: "Dornogal",
    14717: "Hallowfall",
    14752: "Isle of Dorn",
    14753: "Dornogal",
    14771: "Dornogal",       # sub-zone
    14795: "The Ringing Deeps",
    14838: "The Ringing Deeps",
    14881: "City of Threads",
    14969: "City of Threads",
    15093: "Undermine",
    15105: "Undermine",
    # Midnight
    15311: "Eversong Woods",
    15312: "Silvermoon City",
    15347: "Silvermoon City",   # sub-zone
    15355: "Eversong Woods",    # sub-zone
    15458: "Silvermoon City",   # sub-zone
    15476: "The Voidstorm",
    15517: "Harandar",
    15522: "Harandar",          # sub-zone
    15781: "Silvermoon City",   # sub-zone
    15947: "Silvermoon City",   # sub-zone
    15958: "Silvermoon City",   # sub-zone
    15968: "Silvermoon City",   # sub-zone
    15969: "Silvermoon City",   # sub-zone
    # Neighborhoods
    16105: "Founder's Point",
    15524: "Razorwind Shores",
    # Special / Instances
    209: "Shadowfang Keep",
    4131: "Magisters' Terrace",
    6298: "Orgrimmar",           # Brawl'gar Arena (Horde Brawler's Guild)
    6618: "Deeprun Tram",        # Bizmo's Brawlpub (Alliance Brawler's Guild)
}


def classify_vendors_by_react(
    vendors: list[dict],
) -> dict[str, dict]:
    """
    Given a list of Wowhead sold-by NPC entries, classify them into
    Alliance and Horde vendors based on NPC react data.

    React format: [alliance_reaction, horde_reaction]
      1 = friendly, -1 = hostile, 0 = neutral

    Only picks up vendors where exactly one faction is friendly.
    Returns: {"Alliance": {npcID, name, react}, "Horde": {...}} or partial/empty.
    """
    result: dict[str, dict] = {}

    for npc in vendors:
        npc_id = npc.get("id")
        npc_name = npc.get("name", "")
        react = npc.get("react")

        if not npc_id:
            continue

        faction = _react_to_faction(react)
        if faction == "alliance" and "Alliance" not in result:
            result["Alliance"] = {
                "npcID": npc_id,
                "name": npc_name,
                "react": react,
                "location": npc.get("location", []),
            }
        elif faction == "horde" and "Horde" not in result:
            result["Horde"] = {
                "npcID": npc_id,
                "name": npc_name,
                "react": react,
                "location": npc.get("location", []),
            }

        if "Alliance" in result and "Horde" in result:
            break

    return result


def resolve_zone_from_wh(wh_zone_ids: list[int]) -> str:
    """Resolve a Wowhead zone ID list to an in-game zone name."""
    for wh_id in wh_zone_ids:
        if wh_id in WHZONE_TO_ZONE:
            return WHZONE_TO_ZONE[wh_id]
    return ""


def fetch_vendor_coords(npc_id: int) -> Optional[dict]:
    """Fetch NPC coordinates, trying tooltip API first, then page scrape."""
    coords = fetch_npc_coords(npc_id)
    if coords and coords.get("coords"):
        return coords
    # Fallback to page scraping
    coords = fetch_npc_coords_from_page(npc_id)
    if coords and coords.get("coords"):
        return coords
    return None


def enrich_vendor_factions(
    catalog: list[dict],
    dry_run: bool = False,
    stats_only: bool = False,
) -> dict[str, Any]:
    """
    Main enrichment logic. Processes all vendor items and adds factionVendors
    where Wowhead shows faction-specific vendor pairs.

    Returns statistics dict.
    """
    stats = {
        "total_vendor_items": 0,
        "already_has_faction_vendors": 0,
        "no_item_id": 0,
        "scraped": 0,
        "cache_hits": 0,
        "no_sold_by_data": 0,
        "single_neutral_vendor": 0,
        "same_vendor_both_factions": 0,
        "only_one_faction": 0,
        "faction_pair_found": 0,
        "coords_fetched": 0,
        "faction_fixed": 0,
        "new_vendors_added": 0,  # vendors not in original item data
        "examples": [],
    }

    for item in catalog:
        vendor = item.get("vendor") or ""
        if not vendor:
            continue

        stats["total_vendor_items"] += 1

        # Skip items that already have factionVendors
        if item.get("factionVendors"):
            stats["already_has_faction_vendors"] += 1
            continue

        item_id = item.get("itemID")
        if not item_id:
            stats["no_item_id"] += 1
            continue

        decor_id = item.get("decorID", 0)
        name = item.get("name", "")

        # Check if we have cached sold-by data (for stats_only mode)
        cached = cache_get("item_soldby", str(item_id))
        if stats_only and cached is None:
            continue

        # Fetch Wowhead "Sold By" data
        if cached is not None:
            stats["cache_hits"] += 1
            sold_by = cached
        else:
            sold_by = fetch_item_sold_by(item_id)
            stats["scraped"] += 1

        if not sold_by:
            stats["no_sold_by_data"] += 1
            continue

        # Classify vendors by faction using react data
        faction_vendors = classify_vendors_by_react(sold_by)

        if not faction_vendors:
            stats["single_neutral_vendor"] += 1
            continue

        if len(faction_vendors) < 2:
            stats["only_one_faction"] += 1
            continue

        # Check for same NPC in both factions (skip)
        a_npc = faction_vendors.get("Alliance", {}).get("npcID")
        h_npc = faction_vendors.get("Horde", {}).get("npcID")
        if a_npc == h_npc:
            stats["same_vendor_both_factions"] += 1
            continue

        # Found a faction-specific vendor pair!
        stats["faction_pair_found"] += 1

        # Check if one of the vendors is NEW (not in original item data)
        original_npc = item.get("npcID")
        a_is_new = a_npc != original_npc
        h_is_new = h_npc != original_npc
        if a_is_new or h_is_new:
            stats["new_vendors_added"] += 1

        # Fetch coordinates for both vendors
        for faction_key in ("Alliance", "Horde"):
            fv = faction_vendors[faction_key]
            npc_id = fv["npcID"]

            coords_data = fetch_vendor_coords(npc_id)
            if coords_data and coords_data.get("coords"):
                fv["x"] = round(coords_data["coords"]["x"], 1)
                fv["y"] = round(coords_data["coords"]["y"], 1)
                fv["whZoneID"] = coords_data.get("whZoneID")
                stats["coords_fetched"] += 1
            else:
                fv["x"] = None
                fv["y"] = None
                fv["whZoneID"] = None

            # Resolve zone name
            wh_locs = fv.pop("location", [])
            wh_zone = fv.pop("whZoneID", None)
            # Try tooltip zone first, then sold-by location
            zone = ""
            if wh_zone:
                zone = WHZONE_TO_ZONE.get(wh_zone, "")
            if not zone and wh_locs:
                zone = resolve_zone_from_wh(wh_locs)
            fv["zone"] = zone

            # Clean up temporary fields
            fv.pop("react", None)

        # Build example entry
        a_fv = faction_vendors["Alliance"]
        h_fv = faction_vendors["Horde"]
        example = {
            "decorID": decor_id,
            "name": name,
            "alliance_vendor": f"{a_fv['name']} (NPC {a_fv['npcID']}) in {a_fv['zone']}",
            "horde_vendor": f"{h_fv['name']} (NPC {h_fv['npcID']}) in {h_fv['zone']}",
            "old_faction": item.get("faction", ""),
            "old_vendor": vendor,
            "vendor_was_new": a_is_new or h_is_new,
        }
        stats["examples"].append(example)

        if dry_run:
            logger.info(
                "  [DRY RUN] %s (decorID=%d): A=%s in %s, H=%s in %s",
                name, decor_id,
                a_fv["name"], a_fv["zone"],
                h_fv["name"], h_fv["zone"],
            )
            continue

        # Apply changes to item
        item["factionVendors"] = faction_vendors

        # Fix faction: if both factions have a vendor, it's neutral
        old_faction = item.get("faction", "")
        if old_faction != "neutral":
            item["faction"] = "neutral"
            stats["faction_fixed"] += 1

        logger.info(
            "  %s (decorID=%d): A=%s in %s, H=%s in %s  [was: %s]",
            name, decor_id,
            a_fv["name"], a_fv["zone"],
            h_fv["name"], h_fv["zone"],
            old_faction,
        )

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Enrich vendor items with faction-specific vendor pairs from Wowhead.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview changes without saving.",
    )
    parser.add_argument(
        "--stats", action="store_true",
        help="Show stats from cached data only (no new scraping).",
    )
    args = parser.parse_args()

    # Load catalog
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    logger.info("Loaded %d items from %s", len(catalog), CATALOG_JSON)

    # Run enrichment
    stats = enrich_vendor_factions(
        catalog,
        dry_run=args.dry_run,
        stats_only=args.stats,
    )

    # Print statistics
    print("\n" + "=" * 60)
    print("VENDOR FACTION ENRICHMENT RESULTS")
    print("=" * 60)
    print(f"  Total vendor items:            {stats['total_vendor_items']}")
    print(f"  Already has factionVendors:     {stats['already_has_faction_vendors']}")
    print(f"  No itemID (skipped):            {stats['no_item_id']}")
    print(f"  Cache hits:                     {stats['cache_hits']}")
    print(f"  Scraped from Wowhead:           {stats['scraped']}")
    print(f"  No sold-by data:                {stats['no_sold_by_data']}")
    print(f"  Single/neutral vendor only:     {stats['single_neutral_vendor']}")
    print(f"  Only one faction vendor:        {stats['only_one_faction']}")
    print(f"  Same NPC both factions:         {stats['same_vendor_both_factions']}")
    print(f"  FACTION PAIRS FOUND:            {stats['faction_pair_found']}")
    print(f"  New vendors added from Wowhead: {stats['new_vendors_added']}")
    print(f"  Coords fetched:                 {stats['coords_fetched']}")
    print(f"  Faction annotations fixed:      {stats['faction_fixed']}")

    if stats["examples"]:
        print(f"\n  Examples ({len(stats['examples'])} total):")
        for ex in stats["examples"][:20]:
            new_tag = " [NEW VENDOR]" if ex["vendor_was_new"] else ""
            print(f"    decorID={ex['decorID']:5d}: {ex['name']}")
            print(f"      Alliance: {ex['alliance_vendor']}")
            print(f"      Horde:    {ex['horde_vendor']}")
            print(f"      Was: faction={ex['old_faction']}, vendor={ex['old_vendor']}{new_tag}")

    # Save if not dry-run
    if not args.dry_run and not args.stats and stats["faction_pair_found"] > 0:
        with open(CATALOG_JSON, "w", encoding="utf-8") as f:
            json.dump(catalog, f, indent=2, ensure_ascii=False)
        logger.info("Saved %d items to %s", len(catalog), CATALOG_JSON)
    elif args.dry_run:
        logger.info("[DRY RUN] No changes saved.")
    elif args.stats:
        logger.info("[STATS ONLY] No changes saved.")


if __name__ == "__main__":
    main()
