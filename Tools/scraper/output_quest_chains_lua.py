"""
output_quest_chains_lua.py - Convert quest_chains.json into a Lua data file
for the HearthAndSeek WoW addon.

Reads from data/quest_chains.json and outputs to ../../Data/QuestChains.lua
(relative to this script's location, which resolves to HearthAndSeek/Data/QuestChains.lua).

The output Lua table is structured so the addon can:
  1. Look up the full prerequisite chain for any decor reward quest
  2. Walk the chain backwards to find the first incomplete quest
  3. Display the chain visually in the UI

Output: HearthAndSeek/Data/QuestChains.lua
"""

import json
import logging
import sys
from pathlib import Path

from output_lua import lua_string, lua_number

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
QUEST_CHAINS_JSON = SCRIPT_DIR / "data" / "quest_chains.json"
QUEST_GIVERS_JSON = SCRIPT_DIR / "data" / "quest_givers.json"
ENRICHED_CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"
FACTION_QUEST_OVERRIDES_JSON = SCRIPT_DIR / "data" / "faction_quest_overrides.json"
LUA_OUTPUT = SCRIPT_DIR.parent.parent / "Data" / "QuestChains.lua"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("output_quest_chains_lua")


# ---------------------------------------------------------------------------
# Lua serialization helpers
# ---------------------------------------------------------------------------

def lua_int_list(ids: list[int]) -> str:
    """Serialize a list of integers as a Lua inline table."""
    if not ids:
        return "{}"
    parts = [str(i) for i in ids]
    return f"{{ {', '.join(parts)} }}"


def serialize_quest_entry(
    quest_id: int,
    quest_data: dict,
    giver_data: dict | None = None,
    decor_id: int | None = None,
    indent: str = "    ",
) -> str:
    """Serialize a single quest chain entry to a Lua table literal."""
    name = quest_data.get("name") or f"Quest #{quest_id}"
    prereqs = quest_data.get("prereqs", [])
    is_decor = quest_data.get("is_decor_quest", False)

    lines = [f"{indent}[{quest_id}] = {{"]
    lines.append(f"{indent}    name = {lua_string(name)},")
    lines.append(f"{indent}    prereqs = {lua_int_list(prereqs)},")

    # Only mark decor quests explicitly (saves space: most entries are prereqs)
    if is_decor:
        lines.append(f"{indent}    isDecorQuest = true,")
        if decor_id is not None:
            lines.append(f"{indent}    decorID = {decor_id},")

    # Include storyline name if available (useful for UI grouping)
    storyline = quest_data.get("storyline_name")
    if storyline:
        lines.append(f"{indent}    storyline = {lua_string(storyline)},")

    # Series chain: short alternative prereq path (series vs full storyline)
    series_chain = quest_data.get("series_chain")
    if series_chain:
        lines.append(f"{indent}    seriesChain = {lua_int_list(series_chain)},")

    # Quest-giver NPC data (from enrich_quest_givers.py)
    if giver_data and (giver_data.get("npcId") or giver_data.get("x") is not None):
        lines.append(f"{indent}    giverName = {lua_string(giver_data.get('npcName'))},")
        if giver_data.get("npcId"):
            lines.append(f"{indent}    giverID = {lua_number(giver_data.get('npcId'))},")
        if giver_data.get("x") is not None and giver_data.get("y") is not None:
            lines.append(f"{indent}    giverX = {lua_number(giver_data['x'])},")
            lines.append(f"{indent}    giverY = {lua_number(giver_data['y'])},")
        zone_name = giver_data.get("zoneName")
        if zone_name:
            lines.append(f"{indent}    giverZone = {lua_string(zone_name)},")

    lines.append(f"{indent}}},")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def main() -> None:
    if not QUEST_CHAINS_JSON.exists():
        logger.error("Quest chains file not found: %s", QUEST_CHAINS_JSON)
        logger.error("Run enrich_quest_chains.py first.")
        sys.exit(1)

    with open(QUEST_CHAINS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    quests = data.get("quests", {})
    metadata = data.get("metadata", {})

    # Load quest-giver data (optional — may not exist yet)
    quest_givers: dict[str, dict] = {}
    if QUEST_GIVERS_JSON.exists():
        with open(QUEST_GIVERS_JSON, "r", encoding="utf-8") as f:
            givers_data = json.load(f)
        quest_givers = givers_data.get("quest_givers", {})
        giver_meta = givers_data.get("metadata", {})
        logger.info(
            "Loaded quest-giver data: %d with coords, %d NPC-only",
            giver_meta.get("with_coordinates", 0),
            giver_meta.get("with_npc_only", 0),
        )
    else:
        logger.warning("Quest givers file not found: %s (skipping giver data)", QUEST_GIVERS_JSON)

    logger.info(
        "Loaded %d quests (%d decor, %d prereqs)",
        metadata.get("total_quests", len(quests)),
        metadata.get("decor_quests", 0),
        metadata.get("prereq_quests", 0),
    )

    # Build questID → decorID reverse lookup from the enriched catalog
    quest_to_decor: dict[int, int] = {}
    if ENRICHED_CATALOG_JSON.exists():
        with open(ENRICHED_CATALOG_JSON, "r", encoding="utf-8") as f:
            catalog = json.load(f)
        for item in catalog:
            qid = item.get("questID")
            did = item.get("decorID")
            if qid and did:
                quest_to_decor[qid] = did
        logger.info("Built questID -> decorID lookup: %d entries", len(quest_to_decor))
    else:
        logger.warning("Enriched catalog not found: %s (skipping decorID lookup)", ENRICHED_CATALOG_JSON)

    # Add faction quest override entries to quest_to_decor
    if FACTION_QUEST_OVERRIDES_JSON.exists():
        with open(FACTION_QUEST_OVERRIDES_JSON, "r", encoding="utf-8") as f:
            faction_overrides = json.load(f)
        added = 0
        for entry in faction_overrides.values():
            did = entry.get("decorID")
            if not did:
                continue
            for faction in ("Alliance", "Horde"):
                if faction in entry and entry[faction].get("questID"):
                    qid = entry[faction]["questID"]
                    if qid not in quest_to_decor:
                        quest_to_decor[qid] = did
                        added += 1
        if added:
            logger.info("Added %d faction quest override entries to questID -> decorID lookup", added)

    # Build Lua output
    lines: list[str] = []

    # Header
    lines.append("-- Auto-generated by output_quest_chains_lua.py. DO NOT EDIT MANUALLY.")
    lines.append(f"-- Quest chain data: {len(quests)} quests")
    lines.append(f"-- Decor reward quests: {metadata.get('decor_quests', '?')}")
    lines.append(f"-- Prerequisite quests: {metadata.get('prereq_quests', '?')}")
    lines.append(f"-- Quests with giver coords: {len([g for g in quest_givers.values() if g.get('x') is not None])}")
    lines.append("local _, NS = ...")
    lines.append("")
    lines.append("NS.QuestChains = {")

    # Quest names to skip (random dailies with no useful chain info)
    SKIP_QUEST_NAMES = {"Decor Treasure Hunt"}
    skipped_count = 0

    # Sort by quest ID for deterministic output
    givers_used = 0
    for quest_id_str in sorted(quests.keys(), key=lambda x: int(x)):
        quest_data = quests[quest_id_str]
        quest_id = int(quest_id_str)
        if quest_data.get("name") in SKIP_QUEST_NAMES:
            skipped_count += 1
            continue
        giver = quest_givers.get(quest_id_str)
        if giver and (giver.get("npcId") or giver.get("x") is not None):
            givers_used += 1
        decor_id = quest_to_decor.get(quest_id)
        lines.append(serialize_quest_entry(quest_id, quest_data, giver, decor_id))

    lines.append("}")
    lines.append("")

    # Also generate a reverse lookup: quest_id -> list of quest IDs that require it.
    # This lets the addon quickly answer "what quests does completing X unlock?"
    successors: dict[int, list[int]] = {}
    for quest_id_str, quest_data in quests.items():
        if quest_data.get("name") in SKIP_QUEST_NAMES:
            continue
        quest_id = int(quest_id_str)
        for prereq_id in quest_data.get("prereqs", []):
            if prereq_id not in successors:
                successors[prereq_id] = []
            successors[prereq_id].append(quest_id)

    if successors:
        lines.append("-- Reverse lookup: questID -> quests that require it as a prerequisite")
        lines.append("NS.QuestSuccessors = {")
        for prereq_id in sorted(successors.keys()):
            succ_list = sorted(successors[prereq_id])
            lines.append(f"    [{prereq_id}] = {lua_int_list(succ_list)},")
        lines.append("}")
        lines.append("")

    # Generate the list of decor quest IDs for quick lookup
    decor_quest_ids = sorted(
        int(qid) for qid, qdata in quests.items()
        if qdata.get("is_decor_quest")
        and qdata.get("name") not in SKIP_QUEST_NAMES
    )
    if decor_quest_ids:
        lines.append("-- Decor reward quest IDs (for quick membership test)")
        lines.append("NS.DecorQuestIDs = {")
        # Output as a set-like table: [questID] = true
        for qid in decor_quest_ids:
            lines.append(f"    [{qid}] = true,")
        lines.append("}")
        lines.append("")

    lua_content = "\n".join(lines)

    # Write output
    LUA_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(LUA_OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(lua_content)

    logger.info("Generated: %s", LUA_OUTPUT)
    logger.info("  Total quest entries:  %d", len(quests))
    logger.info("  Skipped (Treasure Hunt): %d", skipped_count)
    logger.info("  With giver data:      %d", givers_used)
    logger.info("  Successor mappings:   %d", len(successors))
    logger.info("  Decor quest IDs:      %d", len(decor_quest_ids))


if __name__ == "__main__":
    main()
