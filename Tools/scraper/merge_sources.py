"""
merge_sources.py - Merge Wowhead and WoWDB scraped data into unified records.

The Wowhead scraper now outputs structured data with separate arrays:
  - decor_items: decorID + name (73 for Vanilla)
  - quests: questID + name + faction
  - npcs: npcID + name + coordinates (from /way commands)
  - achievements: achievementID + name
  - dungeons: zoneID + name

The WoWDB scraper outputs a flat list of 317 quest decor items with:
  - decor_name, decor_id, category, subcategory, quest_source, quest_zone,
    vendor_npc, budget_cost, currency_cost, interior_exterior, tags

Matching strategy:
  - Match WoWDB items to Wowhead decor_items by name (exact, then fuzzy).
  - Enrich with quest/NPC data from Wowhead reference tables.
  - Cross-reference NPC names to get coordinates from /way commands.

Output: data/merged_decor.json
"""

import json
import logging
from pathlib import Path
from typing import Any

try:
    from thefuzz import fuzz
    HAS_FUZZY = True
except ImportError:
    HAS_FUZZY = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DATA_DIR = Path(__file__).resolve().parent / "data"

WOWHEAD_FILES = [
    DATA_DIR / "wowhead_vanilla.json",
    # Future: DATA_DIR / "wowhead_tbc.json", etc.
]

WOWDB_FILE = DATA_DIR / "wowdb_quests.json"
OUTPUT_FILE = DATA_DIR / "merged_decor.json"

FUZZY_THRESHOLD = 85

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("merge_sources")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    """Load a JSON file, returning None if not found."""
    if not path.exists():
        logger.warning("File not found: %s", path)
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load %s: %s", path, exc)
        return None


def _normalize_name(name: str | None) -> str:
    """Normalize a name for matching: lowercase, strip, collapse whitespace."""
    if not name:
        return ""
    return " ".join(name.lower().strip().split())


# ---------------------------------------------------------------------------
# Wowhead data indexing
# ---------------------------------------------------------------------------

class WowheadData:
    """Holds and indexes all Wowhead reference data across expansions."""

    def __init__(self):
        self.decor_by_name: dict[str, dict] = {}  # normalized name -> decor item
        self.quests_by_id: dict[int, dict] = {}    # questID -> quest info
        self.npcs_by_id: dict[int, dict] = {}      # npcID -> npc info
        self.npcs_by_name: dict[str, dict] = {}    # normalized name -> npc info
        self.achievements_by_id: dict[int, dict] = {}
        self.dungeons_by_id: dict[int, dict] = {}

    def add_expansion(self, data: dict[str, Any]) -> None:
        """Add data from one expansion's Wowhead scrape."""
        for item in data.get("decor_items", []):
            key = _normalize_name(item.get("decor_name"))
            if key:
                self.decor_by_name[key] = item

        for quest in data.get("quests", []):
            qid = quest.get("quest_id")
            if qid:
                self.quests_by_id[qid] = quest

        for npc in data.get("npcs", []):
            nid = npc.get("npc_id")
            if nid:
                self.npcs_by_id[nid] = npc
            name_key = _normalize_name(npc.get("npc_name"))
            if name_key:
                self.npcs_by_name[name_key] = npc

        for ach in data.get("achievements", []):
            aid = ach.get("achievement_id")
            if aid:
                self.achievements_by_id[aid] = ach

        for dg in data.get("dungeons", []):
            did = dg.get("zone_id")
            if did:
                self.dungeons_by_id[did] = dg

    def find_decor(self, name: str) -> dict | None:
        """Find a Wowhead decor item by name (exact, then fuzzy)."""
        normalized = _normalize_name(name)
        if not normalized:
            return None

        # Exact match
        if normalized in self.decor_by_name:
            return self.decor_by_name[normalized]

        # Fuzzy match
        if HAS_FUZZY:
            best_score = 0
            best_match = None
            for key, item in self.decor_by_name.items():
                score = fuzz.ratio(normalized, key)
                if score > best_score:
                    best_score = score
                    best_match = item
            if best_score >= FUZZY_THRESHOLD and best_match:
                logger.debug("Fuzzy: '%s' -> '%s' (score=%d)", name, best_match.get("decor_name"), best_score)
                return best_match

        return None

    def find_npc_by_name(self, name: str) -> dict | None:
        """Find NPC info (with coordinates) by name."""
        key = _normalize_name(name)
        return self.npcs_by_name.get(key)


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def merge_record(
    wowdb_item: dict[str, Any],
    wh_decor: dict[str, Any] | None,
    wh_data: WowheadData,
) -> dict[str, Any]:
    """Merge a WoWDB record with Wowhead data into a unified record."""

    # Try to find NPC coordinates from Wowhead /way data
    npc_name = wowdb_item.get("vendor_npc")
    npc_info = wh_data.find_npc_by_name(npc_name) if npc_name else None

    coords = None
    map_id = None
    npc_id = None

    if npc_info:
        coords = npc_info.get("coords")
        map_id = npc_info.get("mapID")
        npc_id = npc_info.get("npc_id")

    # Get decor ID from Wowhead if we matched
    wh_decor_id = wh_decor.get("decor_id") if wh_decor else None

    # Determine source type
    source_type = "Quest"  # WoWDB is filtered by quest source
    if wowdb_item.get("vendor_npc"):
        source_type = "Vendor"

    # Determine faction from quest data if available
    faction = None
    # (Quest faction data is in Wowhead quests reference — we'd need the questID
    # to look it up, which requires the full linkage. For now, leave as None.)

    merged = {
        # Identity
        "decor_name": wowdb_item.get("decor_name", "Unknown"),
        "decor_id": wowdb_item.get("decor_id") or wh_decor_id,
        "wh_decor_id": wh_decor_id,

        # Source
        "source_type": source_type,
        "quest_source": wowdb_item.get("quest_source"),
        "quest_zone": wowdb_item.get("quest_zone"),
        "vendor_npc": npc_name,

        # IDs (from Wowhead reference data)
        "npc_id": npc_id,

        # Location
        "zone": wowdb_item.get("quest_zone") or "Unknown",
        "mapID": map_id,
        "coords": coords,

        # WoWDB metadata
        "category": wowdb_item.get("category"),
        "subcategory": wowdb_item.get("subcategory"),
        "budget_cost": wowdb_item.get("budget_cost"),
        "currency_cost": wowdb_item.get("currency_cost"),
        "interior_exterior": wowdb_item.get("interior_exterior"),
        "tags": wowdb_item.get("tags", {}),

        # Context
        "expansion": wh_decor.get("expansion") if wh_decor else None,
        "faction": faction,

        # Merge metadata
        "has_wowhead_match": wh_decor is not None,
        "has_coordinates": coords is not None,
    }

    return merged


def main() -> None:
    """Main entry point: load both sources, merge, and save."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not HAS_FUZZY:
        logger.warning("thefuzz not installed. Fuzzy matching disabled.")

    # Load Wowhead structured data
    wh_data = WowheadData()
    for wh_file in WOWHEAD_FILES:
        data = load_json(wh_file)
        if data and isinstance(data, dict):
            wh_data.add_expansion(data)
            logger.info(
                "Loaded Wowhead %s: %d decor, %d quests, %d NPCs",
                wh_file.stem,
                len(data.get("decor_items", [])),
                len(data.get("quests", [])),
                len(data.get("npcs", [])),
            )
        elif data and isinstance(data, list):
            # Legacy flat format fallback
            logger.info("Loaded Wowhead %s (legacy flat format): %d items", wh_file.stem, len(data))

    logger.info(
        "Wowhead index: %d decor names, %d NPCs with coords",
        len(wh_data.decor_by_name),
        sum(1 for n in wh_data.npcs_by_id.values() if n.get("coords")),
    )

    # Load WoWDB data
    wowdb_raw = load_json(WOWDB_FILE)
    wowdb_items = wowdb_raw if isinstance(wowdb_raw, list) else []
    logger.info("Total WoWDB items: %d", len(wowdb_items))

    if not wowdb_items:
        logger.warning("No WoWDB items. Run scrape_wowdb_quests.py first.")

    # Merge: WoWDB is the authoritative catalog
    merged: list[dict[str, Any]] = []
    matched = 0
    unmatched = 0
    with_coords = 0

    for wowdb_item in wowdb_items:
        decor_name = wowdb_item.get("decor_name", "")
        wh_decor = wh_data.find_decor(decor_name)

        if wh_decor:
            matched += 1
        else:
            unmatched += 1

        record = merge_record(wowdb_item, wh_decor, wh_data)
        if record["has_coordinates"]:
            with_coords += 1
        merged.append(record)

    # Add Wowhead-only decor items (in Wowhead but not WoWDB)
    wowdb_names = {_normalize_name(item.get("decor_name")) for item in wowdb_items}
    wh_only = 0
    for key, wh_item in wh_data.decor_by_name.items():
        if key not in wowdb_names:
            record = {
                "decor_name": wh_item.get("decor_name", "Unknown"),
                "decor_id": wh_item.get("decor_id"),
                "wh_decor_id": wh_item.get("decor_id"),
                "source_type": "Unknown",
                "quest_source": None,
                "quest_zone": None,
                "vendor_npc": None,
                "npc_id": None,
                "zone": "Unknown",
                "mapID": None,
                "coords": None,
                "category": None,
                "subcategory": None,
                "budget_cost": None,
                "currency_cost": None,
                "interior_exterior": None,
                "tags": {},
                "expansion": wh_item.get("expansion"),
                "faction": None,
                "has_wowhead_match": True,
                "has_coordinates": False,
            }
            merged.append(record)
            wh_only += 1

    # Summary
    logger.info("=== Merge Summary ===")
    logger.info("WoWDB items:            %d", len(wowdb_items))
    logger.info("Wowhead decor items:    %d", len(wh_data.decor_by_name))
    logger.info("Matched (enriched):     %d", matched)
    logger.info("Unmatched (WoWDB-only): %d", unmatched)
    logger.info("Wowhead-only:           %d", wh_only)
    logger.info("With coordinates:       %d", with_coords)
    logger.info("Total merged:           %d", len(merged))

    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, ensure_ascii=False)

    logger.info("Saved to %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
