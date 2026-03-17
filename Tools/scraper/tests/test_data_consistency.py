"""
Data consistency tests for HearthAndSeek pipeline output.

These tests validate the GENERATED data files (enriched_catalog.json,
quest_chains.json, quest_givers.json, item_themes.json, CatalogData.lua)
for correctness, cross-referencing, and structural integrity.

Run with:  pytest Tools/scraper/tests/test_data_consistency.py -v
"""

import ast
import json
import re
from pathlib import Path

import pytest

from output_catalog_lua import get_primary_source_type

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]  # HearthAndSeek/
SCRAPER_DIR = REPO_ROOT / "Tools" / "scraper"
DATA_DIR = SCRAPER_DIR / "data"
LUA_DIR = REPO_ROOT / "Data"

# ---------------------------------------------------------------------------
# Valid constants
# ---------------------------------------------------------------------------
VALID_SOURCE_TYPES = {
    "Quest", "Vendor", "Achievement", "Prey", "Profession",
    "Drop", "Treasure", "Shop", "Other",
}

# ---------------------------------------------------------------------------
# Fixtures — loaded once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def enriched_catalog():
    path = DATA_DIR / "enriched_catalog.json"
    if not path.exists():
        pytest.skip(f"data file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def quest_chains():
    path = DATA_DIR / "quest_chains.json"
    if not path.exists():
        pytest.skip(f"data file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def quest_givers():
    path = DATA_DIR / "quest_givers.json"
    if not path.exists():
        pytest.skip(f"data file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def item_themes():
    path = DATA_DIR / "item_themes.json"
    if not path.exists():
        pytest.skip(f"data file not found: {path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="session")
def catalog_lua_text():
    path = LUA_DIR / "CatalogData.lua"
    if not path.exists():
        pytest.skip(f"data file not found: {path}")
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def zone_to_expansion():
    """Parse ZONE_TO_EXPANSION dict from output_catalog_lua.py source."""
    path = SCRAPER_DIR / "output_catalog_lua.py"
    if not path.exists():
        pytest.skip(f"source file not found: {path}")
    source = path.read_text(encoding="utf-8")

    # Extract the dict literal using a regex to find start, then balanced braces
    match = re.search(r"ZONE_TO_EXPANSION[^=]*=\s*\{", source)
    if not match:
        pytest.skip("Could not find ZONE_TO_EXPANSION in source")

    start = match.start()
    # Find the matching closing brace
    brace_depth = 0
    dict_start = source.index("{", start)
    pos = dict_start
    for pos in range(dict_start, len(source)):
        if source[pos] == "{":
            brace_depth += 1
        elif source[pos] == "}":
            brace_depth -= 1
            if brace_depth == 0:
                break
    dict_str = source[dict_start : pos + 1]

    # Remove comments
    dict_str = re.sub(r"#[^\n]*", "", dict_str)

    return ast.literal_eval(dict_str)


@pytest.fixture(scope="session")
def profession_names():
    """Parse PROFESSION_NAMES list from output_catalog_lua.py source."""
    path = SCRAPER_DIR / "output_catalog_lua.py"
    if not path.exists():
        pytest.skip(f"source file not found: {path}")
    source = path.read_text(encoding="utf-8")

    match = re.search(r"PROFESSION_NAMES\s*=\s*\[", source)
    if not match:
        pytest.skip("Could not find PROFESSION_NAMES in source")

    start = match.start()
    bracket_start = source.index("[", start)
    brace_depth = 0
    pos = bracket_start
    for pos in range(bracket_start, len(source)):
        if source[pos] == "[":
            brace_depth += 1
        elif source[pos] == "]":
            brace_depth -= 1
            if brace_depth == 0:
                break
    list_str = source[bracket_start : pos + 1]
    return ast.literal_eval(list_str)


# ===========================================================================
# Item Data Integrity
# ===========================================================================

class TestItemDataIntegrity:
    """Validate required fields, uniqueness, and value ranges."""

    def test_every_item_has_required_fields(self, enriched_catalog):
        required = {"decorID", "name"}
        missing = []
        for idx, item in enumerate(enriched_catalog):
            for field in required:
                if field not in item or item[field] is None:
                    missing.append((idx, item.get("decorID", "?"), field))
        assert not missing, f"Items missing required fields: {missing[:20]}"

    def test_every_item_has_source_type_determinable(self, enriched_catalog):
        """Every item must have at least one source in its sources array,
        a known source field, or a sourceTextRaw fallback (Shop/Other).
        Some items are unreleased/datamined with no source — allow a threshold."""
        no_source = []
        for item in enriched_catalog:
            sources = item.get("sources") or []
            has_field = any(
                item.get(f) for f in ("quest", "vendor", "achievement", "profession")
            )
            raw = item.get("sourceTextRaw") or ""
            has_raw_hint = bool(raw.strip())
            if not sources and not has_field and not has_raw_hint:
                no_source.append(item.get("decorID"))
        # A small number of items have no source data at all (unreleased,
        # datamined, or source not yet known). These become "Other" in the
        # Lua output. Allow up to 5% of total items.
        total = len(enriched_catalog)
        threshold = max(50, int(total * 0.05))
        assert len(no_source) <= threshold, (
            f"{len(no_source)} items with no source info "
            f"(threshold {threshold}): {no_source[:20]}"
        )
        if no_source:
            import warnings
            warnings.warn(
                f"{len(no_source)} items have no source info: {no_source[:10]}..."
            )

    def test_decor_id_is_unique(self, enriched_catalog):
        ids = [item["decorID"] for item in enriched_catalog]
        dupes = [did for did in ids if ids.count(did) > 1]
        assert not dupes, f"Duplicate decorIDs: {set(dupes)}"

    def test_quality_in_valid_range(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            q = item.get("quality")
            if q is not None and not (0 <= q <= 5):
                bad.append((item["decorID"], q))
        assert not bad, f"Items with invalid quality: {bad[:20]}"

    def test_source_types_are_valid(self, enriched_catalog):
        """Source types in the sources array should be recognized types
        (or pipeline-internal types like Category, Faction, NPC, etc.)."""
        # We check that at least *one* source type maps to a known output type.
        # Internal types (Category, Faction, NPC, Item, Container, Starter) are
        # used during processing but the final primary source should be valid.
        all_types = set()
        for item in enriched_catalog:
            for s in item.get("sources") or []:
                t = s.get("type")
                if t:
                    all_types.add(t)
        # Just report for awareness — internal types are expected
        known = VALID_SOURCE_TYPES | {
            "Category", "Faction", "NPC", "Item", "Container", "Starter",
        }
        unknown = all_types - known
        # Unknown types that aren't prefixed with "Unknown" are suspicious
        unexpected = {t for t in unknown if not t.startswith("Unknown")}
        assert not unexpected, f"Unexpected source types found: {unexpected}"


# ===========================================================================
# Source-Field Consistency
# ===========================================================================

class TestSourceFieldConsistency:
    """Validate that source-specific fields are populated correctly."""

    def test_quest_items_have_quest_id(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            if get_primary_source_type(item) == "Quest":
                qid = item.get("questID")
                if not qid or qid <= 0:
                    bad.append((item["decorID"], item["name"]))
        # Allow a small number of quest items without questID — these are
        # items where Wowhead data didn't resolve a quest ID but the
        # sourceTextRaw clearly says "Quest:". The pipeline still emits
        # them as sourceType=Quest with questID=0.
        threshold = 30
        assert len(bad) <= threshold, (
            f"{len(bad)} Quest items missing questID (threshold {threshold}): "
            f"{bad[:20]}"
        )

    def test_vendor_items_have_vendor_name(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            if get_primary_source_type(item) == "Vendor":
                if not item.get("vendor"):
                    bad.append((item["decorID"], item["name"]))
        assert not bad, (
            f"{len(bad)} Vendor items missing vendor name: {bad[:20]}"
        )

    def test_achievement_items_have_achievement_name(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            if get_primary_source_type(item) == "Achievement":
                if not item.get("achievement"):
                    bad.append((item["decorID"], item["name"]))
        assert not bad, (
            f"{len(bad)} Achievement items missing achievement name: {bad[:20]}"
        )

    def test_profession_items_have_valid_profession(
        self, enriched_catalog, profession_names
    ):
        # Extended list: some items use special profession names not in the
        # core PROFESSION_NAMES list (e.g., "Junkyard Tinkering" from Mechagon)
        extended_names = set(profession_names) | {
            "Junkyard Tinkering", "Abominable Stitching",
        }
        bad = []
        for item in enriched_catalog:
            if get_primary_source_type(item) == "Profession":
                prof = item.get("profession")
                if not prof:
                    bad.append((item["decorID"], item["name"], "empty"))
                else:
                    # profession field may be "Midnight Tailoring (50)" —
                    # just check that a known base name appears as a substring
                    if not any(pn in prof for pn in extended_names):
                        bad.append((item["decorID"], item["name"], prof))
        assert not bad, (
            f"{len(bad)} Profession items with invalid profession: {bad[:20]}"
        )


# ===========================================================================
# Coordinate Validation
# ===========================================================================

class TestCoordinateValidation:
    """Validate NPC coordinate fields."""

    def test_npc_coords_in_range(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            x = item.get("npcX")
            y = item.get("npcY")
            if x is not None and not (0 <= x <= 100):
                bad.append((item["decorID"], "npcX", x))
            if y is not None and not (0 <= y <= 100):
                bad.append((item["decorID"], "npcY", y))
        assert not bad, f"Items with out-of-range coords: {bad[:20]}"

    def test_npc_coords_both_or_neither(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            x = item.get("npcX")
            y = item.get("npcY")
            x_set = x is not None
            y_set = y is not None
            if x_set != y_set:
                bad.append((item["decorID"], f"npcX={x}", f"npcY={y}"))
        assert not bad, (
            f"Items with mismatched coord presence: {bad[:20]}"
        )

    def test_faction_vendor_coords_in_range(self, enriched_catalog):
        bad = []
        for item in enriched_catalog:
            fv = item.get("factionVendors") or {}
            for faction, data in fv.items():
                x = data.get("x")
                y = data.get("y")
                if x is not None and not (0 <= x <= 100):
                    bad.append((item["decorID"], faction, "x", x))
                if y is not None and not (0 <= y <= 100):
                    bad.append((item["decorID"], faction, "y", y))
        assert not bad, (
            f"Faction vendor coords out of range: {bad[:20]}"
        )


# ===========================================================================
# Zone Consistency
# ===========================================================================

class TestZoneConsistency:
    """Validate zone fields against ZONE_TO_EXPANSION mapping."""

    def test_item_zones_in_zone_to_expansion(
        self, enriched_catalog, zone_to_expansion
    ):
        unmapped = set()
        for item in enriched_catalog:
            zone = item.get("zone")
            if zone and zone not in zone_to_expansion:
                unmapped.add(zone)
        # Allow a small number of unmapped zones — new content patches may
        # add zones before ZONE_TO_EXPANSION is updated.
        threshold = 5
        assert len(unmapped) <= threshold, (
            f"{len(unmapped)} item zones not in ZONE_TO_EXPANSION "
            f"(threshold {threshold}): {sorted(unmapped)[:20]}"
        )
        if unmapped:
            import warnings
            warnings.warn(
                f"Unmapped zones (add to ZONE_TO_EXPANSION): {sorted(unmapped)}"
            )

    def test_no_unknown_expansion_items(
        self, enriched_catalog, zone_to_expansion
    ):
        """Warn (not fail) if items would get expansion='Unknown'."""
        unknown_zones = set()
        for item in enriched_catalog:
            zone = item.get("zone")
            if zone and zone_to_expansion.get(zone) == "Unknown":
                unknown_zones.add(zone)
        if unknown_zones:
            import warnings
            warnings.warn(
                f"Zones mapped to 'Unknown' expansion: {sorted(unknown_zones)}"
            )


# ===========================================================================
# Quest Chain Integrity
# ===========================================================================

class TestQuestChainIntegrity:
    """Validate quest chain data structure and cross-references."""

    def test_catalog_quest_ids_in_quest_chains(
        self, enriched_catalog, quest_chains
    ):
        """Every questID in the enriched catalog should exist in quest_chains
        (unless the item has skipQuestChain=True)."""
        qc_ids = set(quest_chains.get("quests", {}).keys())
        missing = []
        for item in enriched_catalog:
            qid = item.get("questID")
            if qid and qid > 0 and not item.get("skipQuestChain"):
                if str(qid) not in qc_ids:
                    missing.append((item["decorID"], qid, item["name"]))
        assert not missing, (
            f"{len(missing)} quest IDs from catalog missing in quest_chains: "
            f"{missing[:20]}"
        )

    def test_quest_prereqs_reference_existing_quests(self, quest_chains):
        quests = quest_chains.get("quests", {})
        qc_ids = set(quests.keys())
        broken = []
        for qid, data in quests.items():
            for prereq in data.get("prereqs", []):
                if str(prereq) not in qc_ids:
                    broken.append((qid, prereq))
        assert not broken, (
            f"{len(broken)} broken prereq links: {broken[:20]}"
        )

    def test_quest_chains_form_dag(self, quest_chains):
        """Quest chains should be a DAG — no cycles.
        Uses DFS-based cycle detection."""
        quests = quest_chains.get("quests", {})
        # Build adjacency: quest -> prereqs (edges FROM prereq TO quest,
        # but for cycle detection we check the prereq graph direction)
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {qid: WHITE for qid in quests}
        cycles = []

        def dfs(qid, path):
            color[qid] = GRAY
            for prereq in quests[qid].get("prereqs", []):
                prereq_str = str(prereq)
                if prereq_str not in quests:
                    continue  # broken link, tested separately
                if color[prereq_str] == GRAY:
                    cycles.append((qid, prereq_str, list(path)))
                    return
                if color[prereq_str] == WHITE:
                    dfs(prereq_str, path + [prereq_str])
            color[qid] = BLACK

        for qid in quests:
            if color[qid] == WHITE:
                dfs(qid, [qid])

        assert not cycles, (
            f"Cycles detected in quest chains: {cycles[:10]}"
        )

    def test_decor_quests_have_corresponding_item(
        self, enriched_catalog, quest_chains
    ):
        """Quests marked isDecorQuest should have at least one catalog item
        with that questID. A small number of orphans is acceptable — these
        are quests where the decor reward was reassigned to a different
        quest in the chain or removed in a patch."""
        catalog_quest_ids = set()
        for item in enriched_catalog:
            qid = item.get("questID")
            if qid and qid > 0:
                catalog_quest_ids.add(qid)

        quests = quest_chains.get("quests", {})
        orphan_decor_quests = []
        for qid_str, data in quests.items():
            if data.get("is_decor_quest"):
                if int(qid_str) not in catalog_quest_ids:
                    orphan_decor_quests.append(
                        (qid_str, data.get("name", "?"))
                    )
        threshold = 10
        assert len(orphan_decor_quests) <= threshold, (
            f"{len(orphan_decor_quests)} decor quests without catalog item "
            f"(threshold {threshold}): {orphan_decor_quests[:20]}"
        )
        if orphan_decor_quests:
            import warnings
            warnings.warn(
                f"Orphan decor quests (review): {orphan_decor_quests}"
            )


# ===========================================================================
# Cross-File Consistency
# ===========================================================================

class TestCrossFileConsistency:
    """Validate cross-references between data files."""

    def test_theme_decor_ids_exist_in_catalog(
        self, enriched_catalog, item_themes
    ):
        catalog_ids = {str(item["decorID"]) for item in enriched_catalog}
        theme_items = item_themes.get("items", {})
        missing = []
        for decor_id in theme_items:
            if decor_id not in catalog_ids:
                missing.append(decor_id)
        assert not missing, (
            f"{len(missing)} theme decorIDs not in catalog: {missing[:20]}"
        )

    def test_quest_givers_reference_existing_quests(
        self, quest_givers, quest_chains
    ):
        qc_ids = set(quest_chains.get("quests", {}).keys())
        givers = quest_givers.get("quest_givers", {})
        missing = []
        for qid_str in givers:
            if qid_str not in qc_ids:
                missing.append(qid_str)
        assert not missing, (
            f"{len(missing)} quest giver quest IDs not in quest_chains: "
            f"{missing[:20]}"
        )

    def test_theme_scores_in_valid_range(self, item_themes):
        bad = []
        for decor_id, data in item_themes.get("items", {}).items():
            for theme_id, score in data.get("themes", {}).items():
                if not (0 <= score <= 100):
                    bad.append((decor_id, theme_id, score))
        assert not bad, f"Theme scores out of range: {bad[:20]}"

    def test_quest_giver_coords_in_range(self, quest_givers):
        bad = []
        for qid, data in quest_givers.get("quest_givers", {}).items():
            x = data.get("x")
            y = data.get("y")
            if x is not None and not (0 <= x <= 100):
                bad.append((qid, "x", x))
            if y is not None and not (0 <= y <= 100):
                bad.append((qid, "y", y))
        assert not bad, f"Quest giver coords out of range: {bad[:20]}"


# ===========================================================================
# Index Table Validation (CatalogData.lua)
# ===========================================================================

class TestLuaIndexTable:
    """Validate that CatalogData.lua item count matches enriched_catalog."""

    def test_lua_item_count_matches_catalog(
        self, enriched_catalog, catalog_lua_text
    ):
        # Count items in Lua by finding all [decorID] = { patterns
        lua_ids = re.findall(r"^\s+\[(\d+)\] = \{", catalog_lua_text, re.MULTILINE)
        # The first batch of [id] = { entries are the Items table;
        # later ones may be ZoneToMapID etc., so use the header comment
        header_match = re.search(r"-- Decorations: (\d+) items", catalog_lua_text)
        if header_match:
            lua_count = int(header_match.group(1))
        else:
            # Fallback: count entries in Items table
            # Find range of NS.CatalogData.Items = { ... }
            items_start = catalog_lua_text.find("NS.CatalogData.Items = {")
            if items_start == -1:
                pytest.skip("Could not find Items table in CatalogData.lua")
            # Count [num] = { entries from that point until next NS. definition
            rest = catalog_lua_text[items_start:]
            next_ns = rest.find("\nNS.", 1)
            items_section = rest[:next_ns] if next_ns != -1 else rest
            lua_ids_in_section = re.findall(
                r"^\s+\[(\d+)\] = \{", items_section, re.MULTILINE
            )
            lua_count = len(lua_ids_in_section)

        catalog_count = len(enriched_catalog)
        assert lua_count == catalog_count, (
            f"CatalogData.lua has {lua_count} items, "
            f"enriched_catalog.json has {catalog_count}"
        )

    def test_lua_has_zone_to_mapid_table(self, catalog_lua_text):
        assert "ZoneToMapID" in catalog_lua_text, (
            "CatalogData.lua is missing ZoneToMapID table"
        )

    def test_lua_has_zone_to_expansion_table(self, catalog_lua_text):
        assert "ZoneToExpansionMap" in catalog_lua_text, (
            "CatalogData.lua is missing ZoneToExpansionMap table"
        )
