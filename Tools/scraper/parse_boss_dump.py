#!/usr/bin/env python3
"""
parse_boss_dump.py
Reads HearthAndSeekDB.bossDump from WoW SavedVariables and outputs boss_dump.json.

Usage:
    python parse_boss_dump.py                    # Parse and output JSON
    python parse_boss_dump.py --validate         # Also cross-check vs fixups table
    python parse_boss_dump.py -i path/to/file    # Specify SavedVariables file
"""

import argparse
import json
import re
import sys
from pathlib import Path

from parse_catalog_dump import find_saved_vars_file, parse_lua_saved_vars

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "boss_dump.json"
OUTPUT_CATALOG_PY = SCRIPT_DIR / "output_catalog_lua.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Parse HearthAndSeekDB.bossDump from WoW SavedVariables into JSON.",
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="Path to a HearthAndSeek.lua SavedVariables file. "
             "If omitted, auto-discovers under the default WTF folder.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help=f"Output JSON path (default: {OUTPUT_FILE}).",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Cross-reference results against boss_floor_fixups in output_catalog_lua.py.",
    )
    return parser.parse_args()


def _parse_fixups_from_output_catalog() -> dict[str, int]:
    """Extract boss_floor_fixups dict from output_catalog_lua.py source code.

    Parses lines like:
        "vanessa vancleef": 292,            # Deadmines (heroic-only, ...)
    Returns {boss_name_lower: floorMapID}.
    """
    if not OUTPUT_CATALOG_PY.exists():
        return {}
    text = OUTPUT_CATALOG_PY.read_text(encoding="utf-8")
    # Find the boss_floor_fixups dict block
    start = text.find("boss_floor_fixups")
    if start == -1:
        return {}
    # Find the closing brace
    brace_start = text.find("{", start)
    if brace_start == -1:
        return {}
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i + 1
                break
    block = text[brace_start:brace_end]
    # Parse entries like: "boss name": 292,
    pattern = re.compile(r'"([^"]+)"\s*:\s*(\d+)')
    result: dict[str, int] = {}
    for m in pattern.finditer(block):
        result[m.group(1)] = int(m.group(2))
    return result


def _parse_aliases_from_output_catalog() -> dict[str, str]:
    """Extract boss_name_aliases dict from output_catalog_lua.py source code.

    Returns {alias: full_name}.
    """
    if not OUTPUT_CATALOG_PY.exists():
        return {}
    text = OUTPUT_CATALOG_PY.read_text(encoding="utf-8")
    start = text.find("boss_name_aliases")
    if start == -1:
        return {}
    brace_start = text.find("{", start)
    if brace_start == -1:
        return {}
    depth = 0
    brace_end = brace_start
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                brace_end = i + 1
                break
    block = text[brace_start:brace_end]
    pattern = re.compile(r'"([^"]+)"\s*:\s*"([^"]+)"')
    result: dict[str, str] = {}
    for m in pattern.finditer(block):
        result[m.group(1)] = m.group(2)
    return result


def validate_against_fixups(dump_entries: list[dict]) -> None:
    """Cross-reference dump results against boss_floor_fixups and
    boss_name_aliases in output_catalog_lua.py.

    Reports:
    - Fixup bosses that are now resolved in the dump (fixup may be removable)
    - Fixup bosses still unresolved (fixup still needed)
    - Aliases that resolve correctly
    - Aliases whose full name is missing from the dump
    """
    fixups = _parse_fixups_from_output_catalog()
    aliases = _parse_aliases_from_output_catalog()

    if not fixups and not aliases:
        print("No boss_floor_fixups or boss_name_aliases found in output_catalog_lua.py.")
        return

    # Build dump lookup: lowercase boss name -> floorMapID
    dump_lookup: dict[str, int] = {}
    for e in dump_entries:
        key = e["bossName"].lower()
        dump_lookup[key] = e["floorMapID"]

    print("\n" + "=" * 60)
    print("VALIDATION: boss_floor_fixups")
    print("=" * 60)
    print(f"  Fixups defined:  {len(fixups)}")
    print(f"  Aliases defined: {len(aliases)}")
    print(f"  Dump entries:    {len(dump_entries)}")

    # Check fixups
    still_needed = []
    now_resolved = []
    mismatches = []

    for boss, expected_map in sorted(fixups.items()):
        if boss in dump_lookup:
            if dump_lookup[boss] == expected_map:
                now_resolved.append((boss, expected_map))
            else:
                mismatches.append((boss, expected_map, dump_lookup[boss]))
        else:
            still_needed.append((boss, expected_map))

    print(f"\n  Fixup bosses now resolved in dump:  {len(now_resolved)}")
    print(f"  Fixup bosses still unresolved:      {len(still_needed)}")
    print(f"  Fixup/dump mismatches:              {len(mismatches)}")

    if now_resolved:
        print("\n  RESOLVED (fixup matches dump — could potentially be removed):")
        for boss, map_id in now_resolved:
            print(f"    {boss}: {map_id}")

    if mismatches:
        print("\n  MISMATCHES (fixup != dump — investigate!):")
        for boss, fixup_val, dump_val in mismatches:
            print(f"    {boss}: fixup={fixup_val}, dump={dump_val}")

    if still_needed:
        print("\n  STILL NEEDED (not in dump, fixup required):")
        for boss, map_id in still_needed:
            print(f"    {boss}: {map_id}")

    # Check aliases
    if aliases:
        print(f"\n  Alias validation:")
        for alias, full_name in sorted(aliases.items()):
            if full_name in dump_lookup:
                print(f"    OK: \"{alias}\" -> \"{full_name}\" (floor={dump_lookup[full_name]})")
            else:
                # Check if the full name is in fixups
                if full_name in fixups:
                    print(f"    OK (via fixup): \"{alias}\" -> \"{full_name}\" (floor={fixups[full_name]})")
                else:
                    print(f"    MISSING: \"{alias}\" -> \"{full_name}\" (not in dump or fixups!)")

    # Summary of Drop item coverage
    print(f"\n  Coverage: {len(dump_lookup)} bosses from EJ dump"
          f" + {len(still_needed)} fixups"
          f" + {len(aliases)} aliases")


def main() -> None:
    args = parse_args()
    output_file = Path(args.output) if args.output else OUTPUT_FILE

    # 1. Find and read the SavedVariables file
    sv_path = find_saved_vars_file(args.input)
    print(f"Reading: {sv_path}")

    sv_text = sv_path.read_text(encoding="utf-8", errors="replace")
    data = parse_lua_saved_vars(sv_text)

    # 2. Extract bossDump
    decor_db = data.get("HearthAndSeekDB")
    if not decor_db or not isinstance(decor_db, dict):
        print("ERROR: HearthAndSeekDB not found in SavedVariables.")
        sys.exit(1)

    boss_dump_raw = decor_db.get("bossDump")
    if not boss_dump_raw:
        print("ERROR: HearthAndSeekDB.bossDump is empty or missing.")
        print("Run /hseek dump bosses in-game, then /reload to save.")
        sys.exit(1)

    # Normalize: bossDump may be a dict with numeric keys (Lua array)
    if isinstance(boss_dump_raw, dict):
        raw_list = [boss_dump_raw[k] for k in sorted(boss_dump_raw.keys(),
                    key=lambda x: int(x) if str(x).isdigit() else 0)]
    elif isinstance(boss_dump_raw, list):
        raw_list = boss_dump_raw
    else:
        print(f"ERROR: Unexpected bossDump type: {type(boss_dump_raw)}")
        sys.exit(1)

    # 3. Process entries
    entries: list[dict] = []
    unresolved = 0
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        floor_map_id = raw.get("floorMapID", 0)
        if isinstance(floor_map_id, float):
            floor_map_id = int(floor_map_id)
        if floor_map_id == 0:
            unresolved += 1
        entries.append({
            "bossName": raw.get("bossName", ""),
            "encounterID": int(raw.get("encounterID", 0)),
            "instanceID": int(raw.get("instanceID", 0)),
            "instanceName": raw.get("instanceName", ""),
            "floorMapID": floor_map_id,
            "baseMapID": int(raw.get("baseMapID", 0)),
            "baseMapSource": raw.get("baseMapSource", "none"),
        })

    # Sort by instance name, then boss name
    entries.sort(key=lambda e: (e["instanceName"].lower(), e["bossName"].lower()))

    # 4. Write JSON
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)

    # 5. Summary
    instances = sorted(set(e["instanceName"] for e in entries))
    print(f"\nWrote {len(entries)} boss floor map entries to {output_file}")
    print(f"  Instances with resolved floors: {len(instances)}")
    print(f"  Unresolved encounters (skipped): {unresolved}")
    print(f"  Total raw entries:               {len(raw_list)}")

    # 6. Optional validation
    if args.validate:
        validate_against_fixups(entries)


if __name__ == "__main__":
    main()
