"""
cleanup_quest_chains.py — Remove false prereqs from bulk-imported storyline quests.

The enrich_quest_chains.py script has two bugs that create falsely long chains:
1. Storyline fallback: treats prior storyline quest as prereq when no Series data
2. Bulk import: imports entire zone storylines as linear prereq chains

This script fixes quest_chains.json by:
- Reading each cached Wowhead page to extract ONLY Series-based prereqs
- Reconstructing full Series chains (including intermediate quests)
- Clearing prereqs for bulk-imported quests NOT in any verified Series chain
- Applying manual fixes for chains verified outside of Wowhead Series data

Result: only prereqs verified by Wowhead Series data or manual review are retained.
"""

import json
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_quest_chains")

SCRIPT_DIR = Path(__file__).resolve().parent
QUEST_CHAINS_JSON = SCRIPT_DIR / "data" / "quest_chains.json"
CACHE_DIR = SCRIPT_DIR / "data" / "wowhead_cache"

# ---------------------------------------------------------------------------
# Manual fixes for chains verified outside Wowhead Series data.
# Format: quest_id -> {"prereqs": [...], "name": str (optional)}
# These override any other source of prereqs.
# ---------------------------------------------------------------------------
MANUAL_FIXES: dict[int, dict] = {
    # Elwynn Forest: The Escape chain (verified via Wowhead individual pages)
    # Chain: 60 → 26150 → 106 → 111 → 107 → 112 → 114
    60:    {"prereqs": []},                                   # Kobold Candles (start)
    26150: {"prereqs": [60],  "name": "A Visit With Maybell"},  # new entry
    106:   {"prereqs": [26150]},                              # Young Lovers
    111:   {"prereqs": [106]},                                # Speak with Gramma
    107:   {"prereqs": [111]},                                # Note to William
    112:   {"prereqs": [107]},                                # Collecting Kelp
    114:   {"prereqs": [112]},                                # The Escape (decor)
}


# Storylines with <= this many quests are treated as real quest chains.
# Longer storylines are usually zone-wide quest groupings (not linear prereqs).
MAX_STORYLINE_CHAIN_LENGTH = 20


def load_series_chains() -> tuple[dict[int, list[int]], set[int], dict[int, str]]:
    """Load all quest chain cache files and build verified prereqs.

    Uses two data sources from each cached Wowhead page:
    1. Series data (preferred) — short, verified quest chains
    2. Storyline data (fallback) — used only when no Series data exists AND
       the storyline has <= MAX_STORYLINE_CHAIN_LENGTH quests (short storylines
       are real quest chains; long ones are zone-wide groupings)

    Returns:
        verified_prereqs: quest_id -> [prereq_id] or [] (from verified data)
        cached_quest_ids: set of all quest IDs that have individual cache files
        series_names:     quest_id -> name (from Series/Storyline entries)
    """
    verified_prereqs: dict[int, list[int]] = {}
    cached_quest_ids: set[int] = set()
    series_names: dict[int, str] = {}
    cache_count = 0
    series_count = 0
    storyline_count = 0

    for cache_file in CACHE_DIR.glob("quest_chain_*.json"):
        parts = cache_file.stem.split("_")
        if len(parts) < 3:
            continue
        try:
            quest_id = int(parts[2])
        except ValueError:
            continue

        cache_count += 1
        cached_quest_ids.add(quest_id)

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        series = data.get("series", [])
        storyline = data.get("storyline", [])

        if series:
            # --- Series data (preferred source) ---
            series_count += 1
            chain_ids: list[int] = []
            for entry in series:
                sq = entry.get("quest_id")
                if sq:
                    chain_ids.append(sq)
                    name = entry.get("name")
                    if name:
                        series_names[sq] = name

            # Build prereqs for ALL quests in this Series chain
            for i, sq in enumerate(chain_ids):
                if i == 0:
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = []
                else:
                    prev_id = chain_ids[i - 1]
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = [prev_id]

        elif storyline and len(storyline) <= MAX_STORYLINE_CHAIN_LENGTH:
            # --- Short Storyline fallback ---
            # Short storylines (≤ threshold) are real quest chains, not zone
            # groupings. Build prereqs from the storyline order up to this quest.
            storyline_count += 1
            chain_ids = []
            for entry in storyline:
                sq = entry.get("quest_id")
                if sq:
                    chain_ids.append(sq)
                    name = entry.get("name")
                    if name:
                        series_names[sq] = name
                # Stop after the cached quest's position
                if entry.get("quest_id") == quest_id or entry.get("is_current"):
                    break

            for i, sq in enumerate(chain_ids):
                if i == 0:
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = []
                else:
                    prev_id = chain_ids[i - 1]
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = [prev_id]

        else:
            # No Series data AND either no Storyline or Storyline too long
            # (zone-wide grouping) — mark as verified empty
            verified_prereqs[quest_id] = []

    logger.info(
        "Loaded %d cache files: %d with Series, %d with short Storyline, %d empty",
        cache_count, series_count, storyline_count,
        cache_count - series_count - storyline_count,
    )
    logger.info(
        "Built verified prereqs for %d quests (%d with prereqs)",
        len(verified_prereqs), len([v for v in verified_prereqs.values() if v]),
    )
    return verified_prereqs, cached_quest_ids, series_names


def compute_chain_length(quest_id: int, quests: dict) -> int:
    """Compute the transitive chain length by following prereqs[0]."""
    length = 0
    current = str(quest_id)
    seen = set()
    while current in quests and current not in seen:
        seen.add(current)
        length += 1
        prereqs = quests[current].get("prereqs", [])
        if prereqs:
            current = str(prereqs[0])
        else:
            break
    return length


def main():
    # Load quest_chains.json
    with open(QUEST_CHAINS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    quests = data.get("quests", {})
    logger.info("Loaded %d quests from quest_chains.json", len(quests))

    # Get Series-verified prereqs from cache
    verified_prereqs, cached_quest_ids, series_names = load_series_chains()

    # Compute before stats for decor quests
    decor_quests = {qid: q for qid, q in quests.items() if q.get("is_decor_quest")}
    before_stats = {}
    for qid in decor_quests:
        before_stats[qid] = compute_chain_length(int(qid), quests)

    # --- Step 1: Apply Series-verified prereqs and clear unverified ones ---
    cleared_count = 0
    series_fixed_count = 0
    series_added_count = 0

    for quest_id_str, quest_data in quests.items():
        quest_id = int(quest_id_str)
        old_prereqs = quest_data.get("prereqs", [])

        if quest_id in verified_prereqs:
            # Quest is in a verified Series chain — use verified prereqs
            new_prereqs = verified_prereqs[quest_id]
            if old_prereqs != new_prereqs:
                quest_data["prereqs"] = new_prereqs
                series_fixed_count += 1
        else:
            # Not in any verified Series chain — clear prereqs
            if old_prereqs:
                quest_data["prereqs"] = []
                cleared_count += 1

    # --- Step 2: Add missing Series chain quests ---
    # Some quests in Series chains might not exist in quest_chains.json yet
    for sq_id, sq_prereqs in verified_prereqs.items():
        sq_str = str(sq_id)
        if sq_str not in quests:
            quests[sq_str] = {
                "quest_id": sq_id,
                "name": series_names.get(sq_id, f"Quest #{sq_id}"),
                "prereqs": sq_prereqs,
                "storyline_name": None,
                "is_decor_quest": False,
            }
            series_added_count += 1

    # --- Step 3: Apply manual fixes ---
    manual_fixed = 0
    for quest_id, fix in MANUAL_FIXES.items():
        quest_id_str = str(quest_id)
        if quest_id_str in quests:
            old = quests[quest_id_str].get("prereqs", [])
            quests[quest_id_str]["prereqs"] = fix["prereqs"]
            if "name" in fix:
                quests[quest_id_str]["name"] = fix["name"]
            if old != fix["prereqs"]:
                manual_fixed += 1
        else:
            # Create new entry for manual fix
            quests[quest_id_str] = {
                "quest_id": quest_id,
                "name": fix.get("name", f"Quest #{quest_id}"),
                "prereqs": fix["prereqs"],
                "storyline_name": "Elwynn Forest",
                "is_decor_quest": False,
            }
            manual_fixed += 1

    logger.info("Series-fixed prereqs: %d quests", series_fixed_count)
    logger.info("Cleared unverified prereqs: %d quests", cleared_count)
    logger.info("Added missing Series quests: %d", series_added_count)
    logger.info("Applied manual fixes: %d", manual_fixed)

    # Compute after stats for decor quests
    # Re-scan since decor set might have changed
    decor_quests = {qid: q for qid, q in quests.items() if q.get("is_decor_quest")}
    after_stats = {}
    for qid in decor_quests:
        after_stats[qid] = compute_chain_length(int(qid), quests)

    # Print comparison table
    print("\n=== DECOR QUEST CHAIN LENGTH CHANGES ===")
    print(f"{'QuestID':<10} {'Name':<45} {'Before':>6} {'After':>6} {'Change':>8}")
    print("-" * 80)

    total_before = 0
    total_after = 0
    changed_count = 0

    for qid in sorted(decor_quests.keys(), key=lambda x: before_stats.get(x, 0), reverse=True):
        name = decor_quests[qid].get("name", "?")[:44]
        before = before_stats.get(qid, 0)
        after = after_stats.get(qid, 0)
        total_before += before
        total_after += after

        if before != after:
            changed_count += 1
            change = after - before
            change_str = f"{change:+d}"
            print(f"{qid:<10} {name:<45} {before:>6} {after:>6} {change_str:>8}")

    print("-" * 80)
    print(f"{'TOTAL':<10} {changed_count} chains changed{'':<24} {total_before:>6} {total_after:>6}")

    # Show all decor chains with their final lengths
    print("\n=== ALL DECOR CHAINS (after cleanup) ===")
    print(f"{'Length':>6}  {'Count':>5}")
    print("-" * 15)
    length_dist: dict[int, int] = {}
    for qid in decor_quests:
        l = after_stats.get(qid, 0)
        length_dist[l] = length_dist.get(l, 0) + 1
    for l in sorted(length_dist.keys()):
        print(f"{l:>6}  {length_dist[l]:>5}")

    # Check for remaining chains > 10
    long_chains = []
    for qid in decor_quests:
        length = after_stats.get(qid, 0)
        if length > 10:
            long_chains.append((qid, decor_quests[qid].get("name", "?"), length))

    if long_chains:
        print(f"\n=== REMAINING DECOR CHAINS > 10 ({len(long_chains)} total) ===")
        for qid, name, length in sorted(long_chains, key=lambda x: x[2], reverse=True):
            print(f"  {qid:<10} {name:<45} length={length}")
    else:
        print("\nNo decor chains > 10 remaining!")

    # Check for circular dependencies
    print("\n=== CIRCULAR DEPENDENCY CHECK ===")
    cycles_found = 0
    for qid_str, qdata in quests.items():
        prereqs = qdata.get("prereqs", [])
        if not prereqs:
            continue  # No prereqs = no cycle possible
        seen = set()
        current = qid_str
        is_cycle = False
        while current in quests:
            if current in seen:
                is_cycle = True
                break
            seen.add(current)
            p = quests[current].get("prereqs", [])
            if p:
                current = str(p[0])
            else:
                break
        if is_cycle:
            cycles_found += 1
            if cycles_found <= 10:
                print(f"  CYCLE: quest {qid_str} -> ... -> {current}")
    if cycles_found == 0:
        print("  No circular dependencies found!")
    else:
        print(f"  Total cycles: {cycles_found}")

    # Update metadata
    data["metadata"]["total_quests"] = len(quests)
    data["metadata"]["prereq_quests"] = sum(
        1 for q in quests.values() if q.get("prereqs"))

    # Write cleaned quest_chains.json
    with open(QUEST_CHAINS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Written cleaned quest_chains.json")

    # Summary stats
    total_with_prereqs = sum(1 for q in quests.values() if q.get("prereqs"))
    print(f"\n=== SUMMARY ===")
    print(f"Total quests: {len(quests)}")
    print(f"Quests with prereqs: {total_with_prereqs}")
    print(f"Quests without prereqs: {len(quests) - total_with_prereqs}")
    print(f"Decor quests: {len(decor_quests)}")
    print(f"Decor chains changed: {changed_count}")

    # Items to manually verify
    print(f"\n=== ITEMS TO CHECK MANUALLY ===")
    print("These decor quests had chains > 1 after cleanup (verify in-game):")
    for qid in sorted(decor_quests.keys(), key=lambda x: after_stats.get(x, 0), reverse=True):
        length = after_stats.get(qid, 0)
        if length > 1:
            name = decor_quests[qid].get("name", "?")
            storyline = decor_quests[qid].get("storyline_name", "")
            print(f"  {qid:<10} {name:<40} chain={length:<3} storyline={storyline}")


if __name__ == "__main__":
    main()
