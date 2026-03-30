"""
Tests for memory optimization in output_catalog_lua.py.

Verifies that nil/empty fields are stripped from the generated CatalogData.lua
output, reducing addon memory footprint without losing meaningful data.

Run with:  pytest Tools/scraper/tests/test_memory_optimization.py -v
"""

import re
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parents[3]  # HearthAndSeek/
LUA_FILE = REPO_ROOT / "Data" / "CatalogData.lua"

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
REQUIRED_FIELDS = {"decorID", "name", "sourceType"}

# Matches the item count comment at the top of the file
ITEM_COUNT_RE = re.compile(r"^-- Decorations:\s*(\d+)\s*items", re.MULTILINE)

# Matches top-level item blocks inside NS.CatalogData.Items: `    [1234] = {`
ITEM_BLOCK_RE = re.compile(r"^\s{4}\[(\d+)\] = \{", re.MULTILINE)

# Extracts key = value pairs at 8-space indent (item fields, not sub-tables)
FIELD_RE = re.compile(r"^\s{8}(\w+)\s*=\s*(.+?),?\s*$", re.MULTILINE)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def lua_content():
    if not LUA_FILE.exists():
        pytest.skip(f"CatalogData.lua not found: {LUA_FILE}")
    return LUA_FILE.read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def items_section(lua_content):
    """Extract only the NS.CatalogData.Items = { ... } section."""
    start_marker = "NS.CatalogData.Items = {"
    start = lua_content.find(start_marker)
    assert start != -1, "Could not find NS.CatalogData.Items in CatalogData.lua"
    # Find the next top-level NS.CatalogData assignment after Items
    next_section = re.search(r"^NS\.CatalogData\.\w+\s*=", lua_content[start + 1:],
                             re.MULTILINE)
    end = (start + 1 + next_section.start()) if next_section else len(lua_content)
    return lua_content[start:end]


@pytest.fixture(scope="session")
def parsed_items(items_section):
    """Parse item blocks from the Items section into {decorID: {field: value}}."""
    items = {}
    block_starts = list(ITEM_BLOCK_RE.finditer(items_section))
    for i, match in enumerate(block_starts):
        decor_id = int(match.group(1))
        start = match.end()
        end = block_starts[i + 1].start() if i + 1 < len(block_starts) else len(items_section)
        block_text = items_section[start:end]

        fields = {}
        for field_match in FIELD_RE.finditer(block_text):
            key = field_match.group(1)
            value = field_match.group(2).rstrip(",")
            fields[key] = value
        items[decor_id] = fields
    return items


@pytest.fixture(scope="session")
def declared_item_count(lua_content):
    """The item count declared in the file header comment."""
    m = ITEM_COUNT_RE.search(lua_content)
    assert m, "Could not find item-count comment in CatalogData.lua"
    return int(m.group(1))


# ===========================================================================
# TestNilFieldStripping
# ===========================================================================

class TestNilFieldStripping:
    """Verify that nil and empty-string fields are omitted from output."""

    def test_required_fields_always_present(self, parsed_items):
        """Every item must have decorID, name, and sourceType."""
        missing = []
        for decor_id, fields in parsed_items.items():
            for req in REQUIRED_FIELDS:
                if req not in fields:
                    missing.append((decor_id, req))
        assert not missing, (
            f"{len(missing)} items missing required fields: "
            f"{missing[:10]}{'...' if len(missing) > 10 else ''}"
        )

    def test_no_nil_assignments(self, items_section):
        """No field should be assigned the literal value nil."""
        nil_matches = re.findall(r"=\s*nil\s*,", items_section)
        assert len(nil_matches) == 0, (
            f"Found {len(nil_matches)} nil assignments in Items section"
        )

    def test_no_empty_string_assignments(self, items_section):
        """No field should be assigned an empty string."""
        empty_matches = re.findall(r'=\s*""\s*,', items_section)
        assert len(empty_matches) == 0, (
            f"Found {len(empty_matches)} empty-string assignments in Items section"
        )

    def test_optional_fields_present_when_valued(self, parsed_items):
        """Known items with real data should still have their optional fields."""
        # decorID 80: Vendor item with full coords and vendor info
        item_80 = parsed_items.get(80)
        assert item_80 is not None, "decorID 80 not found"
        for field in ("vendorName", "npcX", "npcY", "npcID", "zone", "mapID"):
            assert field in item_80, f"decorID 80 missing expected field '{field}'"

        # decorID 825: Quest item with questID and sourceDetail
        item_825 = parsed_items.get(825)
        assert item_825 is not None, "decorID 825 not found"
        assert "questID" in item_825, "decorID 825 (Quest) missing questID"
        assert "sourceDetail" in item_825, "decorID 825 (Quest) missing sourceDetail"

    def test_item_count_matches_header(self, parsed_items, declared_item_count):
        """Parsed item count should match the count declared in the header."""
        actual = len(parsed_items)
        assert actual == declared_item_count, (
            f"Header declares {declared_item_count} items, but parsed {actual}"
        )


# ===========================================================================
# TestDataIntegrity
# ===========================================================================

class TestDataIntegrity:
    """Verify structural integrity of the generated Lua data."""

    def test_all_items_have_source_type(self, parsed_items):
        """Every item block must include a sourceType field."""
        missing = [
            decor_id for decor_id, fields in parsed_items.items()
            if "sourceType" not in fields
        ]
        assert not missing, (
            f"{len(missing)} items missing sourceType: {missing[:10]}"
        )

    def test_vendor_items_have_vendor_name(self, parsed_items):
        """Most Vendor items should have a vendorName (allow a small number without)."""
        vendor_items = [
            (decor_id, fields) for decor_id, fields in parsed_items.items()
            if fields.get("sourceType") == '"Vendor"'
        ]
        missing = [
            decor_id for decor_id, fields in vendor_items
            if "vendorName" not in fields
        ]
        total = len(vendor_items)
        assert total > 0, "No Vendor items found"
        pct_present = (total - len(missing)) / total * 100
        assert pct_present >= 50, (
            f"Only {pct_present:.1f}% of Vendor items have vendorName "
            f"({len(missing)}/{total} missing): {missing[:10]}"
        )

    def test_quest_items_have_quest_id(self, parsed_items):
        """Most Quest items should have a questID (allow a small number without)."""
        quest_items = [
            (decor_id, fields) for decor_id, fields in parsed_items.items()
            if fields.get("sourceType") == '"Quest"'
        ]
        missing = [
            decor_id for decor_id, fields in quest_items
            if "questID" not in fields
        ]
        total = len(quest_items)
        assert total > 0, "No Quest items found"
        pct_present = (total - len(missing)) / total * 100
        assert pct_present >= 90, (
            f"Only {pct_present:.1f}% of Quest items have questID "
            f"({len(missing)}/{total} missing): {missing[:10]}"
        )
