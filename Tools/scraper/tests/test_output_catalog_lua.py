"""Tests for output_catalog_lua.py — source priority, detail extraction, cost parsing."""

import pytest

from output_catalog_lua import (
    get_primary_source_type,
    get_source_detail,
    parse_profession_name,
    get_achievement_name,
    get_vendor_name,
    extract_costs,
    compute_recently_added_ids,
)


# ======================================================================
# Helpers — item factory
# ======================================================================

def _item(sources=None, **kwargs):
    """Create a minimal item dict for testing."""
    item = {"sources": sources or []}
    item.update(kwargs)
    return item


def _src(type_, value=""):
    return {"type": type_, "value": value}


# ======================================================================
# get_primary_source_type — priority chain
# ======================================================================

class TestGetPrimarySourceType:
    def test_quest_highest_priority(self):
        item = _item([_src("Quest"), _src("Vendor")])
        assert get_primary_source_type(item) == "Quest"

    def test_achievement_over_vendor(self):
        """Achievement + Vendor → Achievement (vendor is just redemption)."""
        item = _item([_src("Achievement"), _src("Vendor")])
        assert get_primary_source_type(item) == "Achievement"

    def test_achievement_alone(self):
        item = _item([_src("Achievement")])
        assert get_primary_source_type(item) == "Achievement"

    def test_prey_from_category(self):
        item = _item([_src("Category", "Prey")])
        assert get_primary_source_type(item) == "Prey"

    def test_prey_case_insensitive(self):
        item = _item([_src("Category", "prey")])
        assert get_primary_source_type(item) == "Prey"

    def test_profession(self):
        item = _item([_src("Profession")])
        assert get_primary_source_type(item) == "Profession"

    def test_drop(self):
        item = _item([_src("Drop")])
        assert get_primary_source_type(item) == "Drop"

    def test_treasure(self):
        item = _item([_src("Treasure")])
        assert get_primary_source_type(item) == "Treasure"

    def test_vendor_alone(self):
        item = _item([_src("Vendor")])
        assert get_primary_source_type(item) == "Vendor"

    def test_shop(self):
        item = _item([_src("Shop")])
        assert get_primary_source_type(item) == "Shop"

    def test_faction_remapped_to_vendor(self):
        item = _item([_src("Faction")])
        assert get_primary_source_type(item) == "Vendor"

    def test_no_sources_returns_other(self):
        item = _item([])
        assert get_primary_source_type(item) == "Other"

    def test_none_sources_returns_other(self):
        item = {"sources": None}
        assert get_primary_source_type(item) == "Other"

    def test_quest_over_achievement(self):
        item = _item([_src("Quest"), _src("Achievement")])
        assert get_primary_source_type(item) == "Quest"

    def test_quest_over_drop_and_treasure(self):
        item = _item([_src("Quest"), _src("Drop"), _src("Treasure")])
        assert get_primary_source_type(item) == "Quest"

    def test_achievement_vendor_trumps_quest(self):
        """When Achievement+Vendor are both present, the special case fires
        before the priority loop, returning Achievement even if Quest is there."""
        item = _item([_src("Quest"), _src("Achievement"), _src("Vendor")])
        assert get_primary_source_type(item) == "Achievement"

    def test_shop_from_raw_text(self):
        item = _item([], sourceTextRaw="some|cFFFFD200Shop|rtext")
        assert get_primary_source_type(item) == "Shop"

    def test_ingame_shop_from_raw_text(self):
        item = _item([], sourceTextRaw="foo In-Game Shop|r bar")
        assert get_primary_source_type(item) == "Shop"

    @pytest.mark.parametrize("source_type", [
        "Quest", "Achievement", "Profession", "Drop", "Treasure", "Vendor", "Shop",
    ])
    def test_single_source_returns_itself(self, source_type):
        item = _item([_src(source_type)])
        assert get_primary_source_type(item) == source_type


# ======================================================================
# get_source_detail
# ======================================================================

class TestGetSourceDetail:
    def test_quest_detail(self):
        item = _item([_src("Quest", "My Quest")], quest="My Quest")
        assert get_source_detail(item, "Quest") == "My Quest"

    def test_achievement_detail(self):
        item = _item([_src("Achievement", "Collect All")], achievement="Collect All")
        assert get_source_detail(item, "Achievement") == "Collect All"

    def test_vendor_detail(self):
        item = _item([_src("Vendor", "NPC Name")], vendor="NPC Name")
        assert get_source_detail(item, "Vendor") == "NPC Name"

    def test_profession_detail(self):
        item = _item([_src("Profession", "Tailoring (50)")], profession="Tailoring (50)")
        assert get_source_detail(item, "Profession") == "Tailoring (50)"

    def test_prey_uses_achievement(self):
        item = _item([_src("Category", "Prey")], achievement="Some Achievement")
        assert get_source_detail(item, "Prey") == "Some Achievement"

    def test_drop_from_sources_array(self):
        item = _item([_src("Drop", "Boss Name")])
        assert get_source_detail(item, "Drop") == "Boss Name"

    def test_treasure_from_sources_array(self):
        item = _item([_src("Treasure", "Hidden Chest")])
        assert get_source_detail(item, "Treasure") == "Hidden Chest"

    def test_fallback_to_any_field(self):
        item = _item([], quest="Fallback Quest")
        assert get_source_detail(item, "Other") == "Fallback Quest"

    def test_empty_item_returns_empty(self):
        item = _item([])
        assert get_source_detail(item, "Other") == ""

    def test_fallback_order(self):
        """Fallback checks quest, vendor, achievement, profession in order."""
        item = _item([], vendor="V", achievement="A")
        assert get_source_detail(item, "Other") == "V"


# ======================================================================
# parse_profession_name
# ======================================================================

class TestParseProfessionName:
    @pytest.mark.parametrize("detail,expected", [
        ("Midnight Tailoring (50)", "Tailoring"),
        ("Alchemy", "Alchemy"),
        ("Blacksmithing (25)", "Blacksmithing"),
        ("Midnight Enchanting (10)", "Enchanting"),
        ("Engineering", "Engineering"),
        ("Jewelcrafting (75)", "Jewelcrafting"),
        ("Leatherworking", "Leatherworking"),
        ("Inscription (30)", "Inscription"),
        ("Cooking", "Cooking"),
        ("Mining (10)", "Mining"),
        ("Herbalism", "Herbalism"),
        ("Skinning (5)", "Skinning"),
    ])
    def test_known_professions(self, detail, expected):
        assert parse_profession_name(detail) == expected

    def test_junkyard_tinkering(self):
        assert parse_profession_name("Junkyard Tinkering (50)") == "Engineering"

    def test_empty_string(self):
        assert parse_profession_name("") == ""

    def test_unknown_profession(self):
        assert parse_profession_name("Something Else") == ""

    def test_case_insensitive(self):
        assert parse_profession_name("TAILORING") == "Tailoring"


# ======================================================================
# get_achievement_name
# ======================================================================

class TestGetAchievementName:
    def test_from_field(self):
        item = _item([], achievement="Decor Collector")
        assert get_achievement_name(item) == "Decor Collector"

    def test_from_sources_array(self):
        item = _item([_src("Achievement", "Decor Collector")])
        assert get_achievement_name(item) == "Decor Collector"

    def test_field_takes_priority(self):
        item = _item([_src("Achievement", "From Sources")], achievement="From Field")
        assert get_achievement_name(item) == "From Field"

    def test_no_achievement(self):
        item = _item([_src("Quest", "Some Quest")])
        assert get_achievement_name(item) == ""

    def test_empty_sources(self):
        item = _item([])
        assert get_achievement_name(item) == ""


# ======================================================================
# get_vendor_name
# ======================================================================

class TestGetVendorName:
    def test_from_field(self):
        item = _item([], vendor="Captain Lancy")
        assert get_vendor_name(item) == "Captain Lancy"

    def test_from_sources_array(self):
        item = _item([_src("Vendor", "Captain Lancy")])
        assert get_vendor_name(item) == "Captain Lancy"

    def test_field_takes_priority(self):
        item = _item([_src("Vendor", "From Sources")], vendor="From Field")
        assert get_vendor_name(item) == "From Field"

    def test_no_vendor(self):
        item = _item([_src("Quest", "Some Quest")])
        assert get_vendor_name(item) == ""

    def test_empty_sources(self):
        item = _item([])
        assert get_vendor_name(item) == ""


# ======================================================================
# extract_costs
# ======================================================================

class TestExtractCosts:
    def test_empty_string(self):
        assert extract_costs("") == []

    def test_none_input(self):
        assert extract_costs(None) == []

    def test_single_currency(self):
        raw = "100|Hcurrency:824|h|Tinterface\\ICONS\\inv_misc_coin_01.blp:0|t|h"
        costs = extract_costs(raw)
        assert len(costs) == 1
        assert costs[0]["amount"] == 100
        assert costs[0]["currencyID"] == 824
        assert "inv_misc_coin_01" in costs[0]["iconPath"]

    def test_gold_cost(self):
        raw = "50|TINTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP:0|t"
        costs = extract_costs(raw)
        assert len(costs) == 1
        assert costs[0]["amount"] == 50
        assert costs[0]["currencyID"] == 0
        assert "GOLDICON" in costs[0]["iconPath"]

    def test_multiple_currencies(self):
        raw = (
            "100|Hcurrency:824|h|Ticon1:0|t|h "
            "200|Hcurrency:825|h|Ticon2:0|t|h"
        )
        costs = extract_costs(raw)
        assert len(costs) == 2
        assert costs[0]["currencyID"] == 824
        assert costs[1]["currencyID"] == 825

    def test_currency_plus_gold(self):
        raw = (
            "50|Hcurrency:824|h|Ticon:0|t|h "
            "10|TINTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP:0|t"
        )
        costs = extract_costs(raw)
        assert len(costs) == 2
        currency_ids = {c["currencyID"] for c in costs}
        assert 824 in currency_ids
        assert 0 in currency_ids

    def test_duplicate_currency_deduplicated(self):
        raw = (
            "100|Hcurrency:824|h|Ticon:0|t|h "
            "200|Hcurrency:824|h|Ticon:0|t|h"
        )
        costs = extract_costs(raw)
        assert len(costs) == 1
        assert costs[0]["amount"] == 100  # first match wins

    def test_no_cost_data(self):
        raw = "|cFFFFD200Quest: |rSome Quest"
        assert extract_costs(raw) == []

    def test_case_insensitive_currency(self):
        raw = "100|hcurrency:999|h|ticon:0|t|h"
        costs = extract_costs(raw)
        assert len(costs) == 1
        assert costs[0]["currencyID"] == 999

    def test_large_amount(self):
        raw = "99999|Hcurrency:1|h|Ticon:0|t|h"
        costs = extract_costs(raw)
        assert costs[0]["amount"] == 99999


# ======================================================================
# compute_recently_added_ids — batch-accumulation logic for the
# "Recently Added" filter in the Collections sidebar section
# ======================================================================

class TestComputeRecentlyAddedIds:
    def test_single_batch_meets_minimum(self):
        """When the newest batch alone has >=5 items, only that batch is
        returned — older batches stay hidden."""
        versions = {
            "1": {"date": "2026-04-21"},
            "2": {"date": "2026-04-21"},
            "3": {"date": "2026-04-21"},
            "4": {"date": "2026-04-21"},
            "5": {"date": "2026-04-21"},
            "6": {"date": "2026-04-21"},
            # Older batch — should be excluded
            "10": {"date": "2026-03-10"},
            "11": {"date": "2026-03-10"},
        }
        result = compute_recently_added_ids(versions)
        assert result == [1, 2, 3, 4, 5, 6]

    def test_small_batch_pulls_in_previous(self):
        """When the newest batch has <5 items, include prior batches
        atomically until we reach 5 or max_batches."""
        versions = {
            "1": {"date": "2026-04-21"},
            "2": {"date": "2026-04-21"},
            "3": {"date": "2026-04-21"},
            # Previous batch — needed to reach 5
            "10": {"date": "2026-03-10"},
            "11": {"date": "2026-03-10"},
            "12": {"date": "2026-03-10"},
            "13": {"date": "2026-03-10"},
            # Even-older batch — should be excluded (we already hit 5 after batch 2)
            "20": {"date": "2026-02-01"},
        }
        result = compute_recently_added_ids(versions)
        # batch 2026-04-21 (3 items) + whole prior batch 2026-03-10 (4 items) = 7
        assert result == [1, 2, 3, 10, 11, 12, 13]

    def test_whole_batch_atomicity(self):
        """When adding a batch would push the count past the minimum,
        the whole batch is still included (never truncated)."""
        versions = {
            "1": {"date": "2026-04-21"},
            # Previous batch has many items; we stop after adding it whole.
            "10": {"date": "2026-03-10"},
            "11": {"date": "2026-03-10"},
            "12": {"date": "2026-03-10"},
            "13": {"date": "2026-03-10"},
            "14": {"date": "2026-03-10"},
            "15": {"date": "2026-03-10"},
            "16": {"date": "2026-03-10"},
            # Third-newest should NOT be included — we already hit 5 after 2 batches.
            "20": {"date": "2026-02-01"},
        }
        result = compute_recently_added_ids(versions)
        assert 1 in result
        assert 16 in result  # entire prior batch included
        assert 20 not in result  # earlier batch excluded
        assert len(result) == 8

    def test_max_batches_cap(self):
        """Never include more than ``max_batches`` batches, even if the
        minimum hasn't been reached."""
        versions = {
            "1": {"date": "2026-04-21"},
            "2": {"date": "2026-04-20"},
            "3": {"date": "2026-04-19"},
            "4": {"date": "2026-04-18"},
            "5": {"date": "2026-04-17"},
        }
        result = compute_recently_added_ids(versions, min_items=10, max_batches=3)
        # Only 3 newest dates → 3 items total (still short of min_items=10)
        assert result == [1, 2, 3]

    def test_fewer_items_than_minimum_returns_all(self):
        """If the catalog has fewer than min_items spread across all
        available batches, return what we have rather than error."""
        versions = {
            "1": {"date": "2026-04-21"},
            "2": {"date": "2026-04-21"},
        }
        result = compute_recently_added_ids(versions)
        assert result == [1, 2]

    def test_ignores_entries_without_date(self):
        """Items missing the ``date`` field (older seed data, etc.) are
        skipped — they can't be placed into a batch."""
        versions = {
            "1": {"date": "2026-04-21"},
            "2": {"patch": "12.0.5"},         # no date
            "3": {"date": "2026-04-21"},
            "4": {},                          # empty
            "5": None,                        # not a dict
        }
        result = compute_recently_added_ids(versions)
        assert 2 not in result
        assert 4 not in result
        assert 5 not in result
        assert 1 in result and 3 in result

    def test_result_is_sorted(self):
        """Output is sorted ascending by decorID so the generated Lua is
        deterministic across runs."""
        versions = {str(did): {"date": "2026-04-21"} for did in [999, 1, 500, 100, 2]}
        result = compute_recently_added_ids(versions)
        assert result == sorted(result)

    def test_non_integer_decor_ids_skipped(self):
        """Malformed keys (non-integer strings) are ignored rather than
        raising."""
        versions = {
            "1": {"date": "2026-04-21"},
            "foo": {"date": "2026-04-21"},
            "2": {"date": "2026-04-21"},
        }
        result = compute_recently_added_ids(versions)
        assert result == [1, 2]

    def test_default_thresholds(self):
        """Document the defaults: min_items=5, max_batches=3."""
        import inspect
        sig = inspect.signature(compute_recently_added_ids)
        assert sig.parameters["min_items"].default == 5
        assert sig.parameters["max_batches"].default == 3

    def test_empty_input_returns_empty(self):
        assert compute_recently_added_ids({}) == []


# ======================================================================
# SOURCE_PRIORITY alignment — the Lua addon has NS.SourcePriority in
# Core/Utils.lua that drives multi-source rendering order. Both lists
# must stay identical so the Python-side primary pick and the Lua-side
# render ordering agree. Any divergence here would cause a multi-source
# item like "Eversong Lantern" Painting to get Achievement primary in
# the baked data but render Vendor-first (or vice versa) in the UI.
# ======================================================================

class TestSourcePriorityAlignment:
    def test_python_matches_lua(self):
        """Parse Core/Utils.lua for NS.SourcePriority and assert it
        matches the Python SOURCE_PRIORITY list exactly (order + set)."""
        import re
        from pathlib import Path
        from output_catalog_lua import SOURCE_PRIORITY

        utils_lua = (Path(__file__).resolve().parent.parent.parent.parent
                     / "Core" / "Utils.lua")
        content = utils_lua.read_text(encoding="utf-8")
        m = re.search(r"NS\.SourcePriority\s*=\s*\{(.+?)\}", content, re.DOTALL)
        assert m, f"NS.SourcePriority not found in {utils_lua}"
        lua_items = re.findall(r'"([^"]+)"', m.group(1))
        assert lua_items == SOURCE_PRIORITY, (
            f"Python SOURCE_PRIORITY and Lua NS.SourcePriority diverged:\n"
            f"  Python: {SOURCE_PRIORITY}\n"
            f"  Lua:    {lua_items}\n"
            f"Update both or the UI render order will disagree with the "
            f"baked primary-source pick."
        )

    def test_priority_includes_all_source_types(self):
        """Every source type the generator may emit as a primary
        sourceType must appear in SOURCE_PRIORITY — otherwise a
        multi-source item with that primary would sort unpredictably."""
        from output_catalog_lua import SOURCE_PRIORITY
        # Derived from NS.SourceColors keys in Core/Constants.lua and
        # the source-type dispatch in get_primary_source_type.
        EXPECTED = {
            "Quest", "Achievement", "Prey", "Profession",
            "Drop", "Treasure", "Vendor", "Shop", "Other",
        }
        assert set(SOURCE_PRIORITY) == EXPECTED, (
            f"SOURCE_PRIORITY missing/extra types: "
            f"missing={EXPECTED - set(SOURCE_PRIORITY)}, "
            f"extra={set(SOURCE_PRIORITY) - EXPECTED}"
        )
