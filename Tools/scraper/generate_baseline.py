#!/usr/bin/env python3
"""
Generate a data regression baseline snapshot from the current pipeline output.

Reads enriched_catalog.json and quest_chains.json, computes a compact fingerprint
for each item, and saves the result as tests/data_baseline.json.

Usage:
    python Tools/scraper/generate_baseline.py                    # Generate baseline
    python Tools/scraper/generate_baseline.py --output path.json # Custom output path
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path setup — import helpers from output_catalog_lua.py
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
TESTS_DIR = SCRIPT_DIR / "tests"
DEFAULT_OUTPUT = TESTS_DIR / "data_baseline.json"

sys.path.insert(0, str(SCRIPT_DIR))

from output_catalog_lua import (
    ZONE_TO_EXPANSION,
    get_primary_source_type,
    get_source_detail,
    parse_profession_name,
)
from pipeline_metadata import get_game_version


# ---------------------------------------------------------------------------
# Chain length computation
# ---------------------------------------------------------------------------

def compute_chain_length(quest_id: int, quests: dict[str, dict]) -> int:
    """Walk the prereq chain from root to the given quest and return the step count.

    The chain length is the number of quests from the root (inclusive) to the
    decor quest (inclusive).  A quest with no prereqs has chain length 1.
    """
    quest_id_str = str(quest_id)
    if quest_id_str not in quests:
        return 0

    # Walk backward to root, counting steps
    visited: set[str] = set()
    length = 0
    current = quest_id_str

    while current and current not in visited:
        visited.add(current)
        length += 1
        quest = quests.get(current)
        if not quest:
            break
        prereqs = quest.get("prereqs", [])
        if not prereqs:
            break
        # Follow the first prereq (chains are linear in practice)
        current = str(prereqs[0])

    return length


# ---------------------------------------------------------------------------
# Item fingerprint computation
# ---------------------------------------------------------------------------

def compute_item_fingerprint(item: dict[str, Any], quests: dict[str, dict]) -> dict[str, Any]:
    """Compute a compact fingerprint dict for a single catalog item.

    Only non-null / meaningful fields are included to keep it compact.
    """
    fp: dict[str, Any] = {}

    # Source type
    source_type = get_primary_source_type(item)
    fp["sourceType"] = source_type

    # Zone
    zone = item.get("zone")
    if zone:
        fp["zone"] = zone

    # Quest ID
    quest_id = item.get("questID")
    if quest_id is not None:
        fp["questID"] = quest_id

    # Vendor name
    vendor = item.get("vendor")
    if vendor:
        fp["vendorName"] = vendor

    # NPC ID
    npc_id = item.get("npcID")
    if npc_id is not None:
        fp["npcID"] = npc_id

    # Has coords
    npc_x = item.get("npcX")
    npc_y = item.get("npcY")
    if npc_x is not None and npc_y is not None:
        fp["hasCoords"] = True

    # Chain length (for quest items with a questID in quest_chains)
    if quest_id is not None and str(quest_id) in quests:
        chain_len = compute_chain_length(quest_id, quests)
        if chain_len > 0:
            fp["chainLength"] = chain_len

    # Expansion (derived from zone)
    if zone:
        expansion = ZONE_TO_EXPANSION.get(zone)
        if expansion:
            fp["expansion"] = expansion

    # Achievement name
    achievement = item.get("achievement")
    if achievement:
        fp["achievementName"] = achievement

    # Profession name (parsed from profession source detail)
    if source_type == "Profession":
        detail = get_source_detail(item, source_type)
        prof_name = parse_profession_name(detail)
        if prof_name:
            fp["professionName"] = prof_name

    # Faction
    faction = item.get("faction")
    if faction:
        fp["faction"] = faction

    return fp


# ---------------------------------------------------------------------------
# Severity classification and regression comparison
# ---------------------------------------------------------------------------

CRITICAL_FIELDS = {"sourceType", "questID", "chainLength"}
WARNING_FIELDS = {"zone", "expansion", "hasCoords", "vendorName", "npcID",
                  "achievementName", "professionName"}
INFO_FIELDS = {"faction"}


class ItemDiff:
    """Represents a single field change for a decor item."""

    __slots__ = ("decor_id", "name", "field", "old_value", "new_value", "severity")

    def __init__(self, decor_id: str, name: str, field: str,
                 old_value: Any, new_value: Any, severity: str):
        self.decor_id = decor_id
        self.name = name
        self.field = field
        self.old_value = old_value
        self.new_value = new_value
        self.severity = severity

    def __repr__(self) -> str:
        return (f"  decorID {self.decor_id} \"{self.name}\": "
                f"{self.field} {self.old_value!r} -> {self.new_value!r}")


def classify_severity(field: str) -> str:
    """Return the severity level for a changed field."""
    if field in CRITICAL_FIELDS:
        return "CRITICAL"
    if field in WARNING_FIELDS:
        return "WARNING"
    return "INFO"


def compare_item(decor_id: str, name: str,
                 baseline_fp: dict, current_fp: dict) -> list[ItemDiff]:
    """Compare baseline and current fingerprints for a single item."""
    diffs: list[ItemDiff] = []
    all_fields = set(baseline_fp.keys()) | set(current_fp.keys())

    for field in sorted(all_fields):
        old_val = baseline_fp.get(field)
        new_val = current_fp.get(field)
        if old_val != new_val:
            severity = classify_severity(field)
            diffs.append(ItemDiff(decor_id, name, field, old_val, new_val, severity))

    return diffs


def format_report(diffs: list[ItemDiff],
                  new_items: list[tuple[str, str]],
                  removed_items: list[tuple[str, str]]) -> str:
    """Format a human-readable regression report."""
    lines: list[str] = []

    total_changes = len(diffs) + len(new_items) + len(removed_items)
    if total_changes == 0:
        return "No regressions detected. Baseline matches current data."

    # Group diffs by category
    source_type_changes = [d for d in diffs if d.field == "sourceType"]
    chain_length_changes = [d for d in diffs if d.field == "chainLength"]
    coord_losses = [d for d in diffs if d.field == "hasCoords" and d.new_value is None]
    zone_changes = [d for d in diffs if d.field == "zone"]
    expansion_changes = [d for d in diffs if d.field == "expansion"]
    quest_id_changes = [d for d in diffs if d.field == "questID"]
    vendor_changes = [d for d in diffs if d.field == "vendorName"]
    npc_changes = [d for d in diffs if d.field == "npcID"]
    faction_changes = [d for d in diffs if d.field == "faction"]
    coord_gains = [d for d in diffs if d.field == "hasCoords" and d.new_value is not None]
    profession_changes = [d for d in diffs if d.field == "professionName"]
    achievement_changes = [d for d in diffs if d.field == "achievementName"]

    categorized_fields = {
        "sourceType", "chainLength", "hasCoords", "zone", "expansion",
        "questID", "vendorName", "npcID", "faction", "professionName",
        "achievementName",
    }
    other_changes = [d for d in diffs if d.field not in categorized_fields]

    crit_count = sum(1 for d in diffs if d.severity == "CRITICAL")
    warn_count = sum(1 for d in diffs if d.severity == "WARNING")
    info_count = sum(1 for d in diffs if d.severity == "INFO")

    lines.append(f"REGRESSION DETECTED: {len(diffs)} field changes, "
                 f"{len(new_items)} new items, {len(removed_items)} removed items")
    lines.append("")

    def _section(title: str, items: list, formatter=None) -> None:
        if not items:
            return
        lines.append(f"{title} ({len(items)}):")
        for item in items:
            if formatter:
                lines.append(formatter(item))
            else:
                lines.append(str(item))
        lines.append("")

    _section("SOURCE TYPE CHANGES", source_type_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("QUEST ID CHANGES", quest_id_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("QUEST CHAIN LENGTH CHANGES", chain_length_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": chain {d.old_value} -> {d.new_value}")

    _section("COORDINATE LOSSES", coord_losses,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": had coords -> lost coords")

    _section("COORDINATE GAINS", coord_gains,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": no coords -> gained coords")

    _section("ZONE CHANGES", zone_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("EXPANSION CHANGES", expansion_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("VENDOR NAME CHANGES", vendor_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("NPC ID CHANGES", npc_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("ACHIEVEMENT NAME CHANGES", achievement_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("PROFESSION NAME CHANGES", profession_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("FACTION CHANGES", faction_changes,
             lambda d: f"  decorID {d.decor_id} \"{d.name}\": {d.old_value} -> {d.new_value}")

    _section("OTHER FIELD CHANGES", other_changes)

    if new_items:
        lines.append(f"NEW ITEMS ({len(new_items)}):")
        lines.append("  (not a failure -- expected after new patch)")
        if len(new_items) <= 20:
            for did, name in new_items:
                lines.append(f"  decorID {did} \"{name}\"")
        else:
            for did, name in new_items[:10]:
                lines.append(f"  decorID {did} \"{name}\"")
            lines.append(f"  ... and {len(new_items) - 10} more")
        lines.append("")

    if removed_items:
        lines.append(f"REMOVED ITEMS ({len(removed_items)}):")
        if len(removed_items) > 5:
            lines.append("  WARNING: > 5 items removed -- this is suspicious!")
        for did, name in removed_items:
            lines.append(f"  decorID {did} \"{name}\"")
        lines.append("")

    # Summary
    lines.append("SEVERITY SUMMARY:")
    lines.append(f"  CRITICAL: {crit_count}  WARNING: {warn_count}  INFO: {info_count}")
    lines.append(f"  New items: {len(new_items)}  Removed items: {len(removed_items)}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Baseline generation
# ---------------------------------------------------------------------------

def generate_baseline(catalog: list[dict], quests: dict[str, dict]) -> dict[str, Any]:
    """Generate the full baseline structure from catalog and quest chain data."""
    items: dict[str, dict] = {}
    source_counter: Counter = Counter()
    expansion_counter: Counter = Counter()

    names: dict[str, str] = {}

    for item in catalog:
        decor_id = str(item["decorID"])
        fp = compute_item_fingerprint(item, quests)
        items[decor_id] = fp
        names[decor_id] = item.get("name", "")

        source_counter[fp["sourceType"]] += 1
        exp = fp.get("expansion", "Unknown")
        expansion_counter[exp] += 1

    baseline = {
        "version": "1.1",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "gameVersion": get_game_version(),
        "aggregates": {
            "totalItems": len(catalog),
            "bySourceType": dict(sorted(source_counter.items())),
            "byExpansion": dict(sorted(expansion_counter.items())),
        },
        "items": items,
        "names": names,
    }

    return baseline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a data regression baseline snapshot."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output file path (default: {DEFAULT_OUTPUT})",
    )
    args = parser.parse_args()

    # Load enriched catalog
    catalog_path = DATA_DIR / "enriched_catalog.json"
    if not catalog_path.exists():
        print(f"ERROR: enriched_catalog.json not found at {catalog_path}")
        sys.exit(1)
    with open(catalog_path, encoding="utf-8") as f:
        catalog = json.load(f)

    # Load quest chains
    quest_chains_path = DATA_DIR / "quest_chains.json"
    if not quest_chains_path.exists():
        print(f"ERROR: quest_chains.json not found at {quest_chains_path}")
        sys.exit(1)
    with open(quest_chains_path, encoding="utf-8") as f:
        quest_chains_data = json.load(f)
    quests = quest_chains_data.get("quests", {})

    # Generate baseline
    baseline = generate_baseline(catalog, quests)

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Write output
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(baseline, f, indent=2, ensure_ascii=False)

    # Print summary
    agg = baseline["aggregates"]
    print(f"Baseline generated: {args.output}")
    print(f"  Total items: {agg['totalItems']}")
    print(f"  Source types: {agg['bySourceType']}")
    print(f"  Expansions:   {agg['byExpansion']}")


if __name__ == "__main__":
    main()
