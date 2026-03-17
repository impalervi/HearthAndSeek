"""
Data regression tests for HearthAndSeek pipeline output.

Compares the current pipeline output against a saved baseline snapshot
to detect unintended changes in source types, quest chains, coordinates,
zones, and other key item properties.

Run with:  pytest Tools/scraper/tests/test_data_regression.py -v -s
Generate baseline first:  python Tools/scraper/generate_baseline.py
"""

import json
from pathlib import Path

import pytest

from generate_baseline import compare_item, compute_item_fingerprint, format_report
from output_catalog_lua import get_primary_source_type

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]  # HearthAndSeek/
SCRAPER_DIR = REPO_ROOT / "Tools" / "scraper"
DATA_DIR = SCRAPER_DIR / "data"
TESTS_DIR = SCRAPER_DIR / "tests"
BASELINE_PATH = TESTS_DIR / "data_baseline.json"

# Maximum number of new items before we suspect a data duplication bug
NEW_ITEM_THRESHOLD = 200


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def baseline() -> dict:
    """Load the baseline snapshot."""
    if not BASELINE_PATH.exists():
        pytest.skip("No baseline file -- run generate_baseline.py first")
    with open(BASELINE_PATH, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def current_catalog() -> list[dict]:
    """Load the current enriched catalog."""
    path = DATA_DIR / "enriched_catalog.json"
    if not path.exists():
        pytest.skip(f"enriched_catalog.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def current_quests() -> dict[str, dict]:
    """Load the current quest chains data."""
    path = DATA_DIR / "quest_chains.json"
    if not path.exists():
        pytest.skip(f"quest_chains.json not found at {path}")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("quests", {})


@pytest.fixture(scope="session")
def current_fingerprints(current_catalog, current_quests) -> dict[str, dict]:
    """Compute fingerprints for all current catalog items."""
    fps: dict[str, dict] = {}
    for item in current_catalog:
        decor_id = str(item["decorID"])
        fps[decor_id] = compute_item_fingerprint(item, current_quests)
    return fps


@pytest.fixture(scope="session")
def current_names(current_catalog) -> dict[str, str]:
    """Map decorID -> name for the current catalog."""
    return {str(item["decorID"]): item.get("name", "(unknown)") for item in current_catalog}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_data_regression(baseline, current_fingerprints, current_names):
    """Compare current pipeline output against the baseline snapshot.

    Fails on CRITICAL or WARNING changes to existing items.
    New items are informational only (unless > threshold).
    Removed items fail if > 5.
    """
    baseline_items = baseline.get("items", {})
    baseline_names = baseline.get("names", {})

    # Collect all diffs for existing items
    all_diffs = []
    for decor_id, baseline_fp in baseline_items.items():
        if decor_id not in current_fingerprints:
            continue  # handled as removed item below
        current_fp = current_fingerprints[decor_id]
        name = current_names.get(decor_id, f"(decorID {decor_id})")
        item_diffs = compare_item(decor_id, name, baseline_fp, current_fp)
        all_diffs.extend(item_diffs)

    # Detect new and removed items
    baseline_ids = set(baseline_items.keys())
    current_ids = set(current_fingerprints.keys())

    new_ids = sorted(current_ids - baseline_ids, key=lambda x: int(x))
    removed_ids = sorted(baseline_ids - current_ids, key=lambda x: int(x))

    new_items = [(did, current_names.get(did, f"(decorID {did})")) for did in new_ids]
    removed_items = [(did, baseline_names.get(did, f"(decorID {did})")) for did in removed_ids]

    # Print full report before asserting
    report = format_report(all_diffs, new_items, removed_items)
    print()
    print("=" * 72)
    print("DATA REGRESSION REPORT")
    print("=" * 72)
    print(report)
    print("=" * 72)

    # Determine failures
    failure_diffs = [d for d in all_diffs if d.severity in ("CRITICAL", "WARNING")]
    removed_failure = len(removed_items) > 5
    new_item_failure = len(new_items) > NEW_ITEM_THRESHOLD

    failure_messages: list[str] = []
    if failure_diffs:
        crit = sum(1 for d in failure_diffs if d.severity == "CRITICAL")
        warn = sum(1 for d in failure_diffs if d.severity == "WARNING")
        failure_messages.append(
            f"{len(failure_diffs)} field regressions ({crit} critical, {warn} warning)"
        )
    if removed_failure:
        failure_messages.append(
            f"{len(removed_items)} items removed (threshold: 5)"
        )
    if new_item_failure:
        failure_messages.append(
            f"{len(new_items)} new items (threshold: {NEW_ITEM_THRESHOLD}) "
            f"-- possible data duplication bug"
        )

    if failure_messages:
        pytest.fail(
            "Data regression detected: " + "; ".join(failure_messages)
            + "\n\nSee report above for details. If these changes are intentional, "
            "regenerate the baseline with: python Tools/scraper/generate_baseline.py"
        )


def test_aggregate_counts(baseline, current_catalog, current_quests):
    """Verify aggregate counts haven't changed unexpectedly.

    This is a softer check -- prints differences but only fails on
    large unexpected drops in total item count.
    """
    baseline_agg = baseline.get("aggregates", {})
    baseline_total = baseline_agg.get("totalItems", 0)
    current_total = len(current_catalog)

    print()
    print(f"Baseline total: {baseline_total}, Current total: {current_total}")

    # Compare source type distribution
    baseline_by_source = baseline_agg.get("bySourceType", {})
    current_by_source: dict[str, int] = {}
    for item in current_catalog:
        st = get_primary_source_type(item)
        current_by_source[st] = current_by_source.get(st, 0) + 1

    all_source_types = set(baseline_by_source.keys()) | set(current_by_source.keys())
    source_diffs = []
    for st in sorted(all_source_types):
        old_count = baseline_by_source.get(st, 0)
        new_count = current_by_source.get(st, 0)
        if old_count != new_count:
            source_diffs.append(f"  {st}: {old_count} -> {new_count}")

    if source_diffs:
        print("Source type count changes:")
        for line in source_diffs:
            print(line)

    # Fail if we lost more than 10% of items (likely a data generation bug)
    if baseline_total > 0 and current_total < baseline_total * 0.9:
        pytest.fail(
            f"Total item count dropped significantly: {baseline_total} -> {current_total} "
            f"({baseline_total - current_total} items lost). "
            "This likely indicates a data generation issue."
        )
