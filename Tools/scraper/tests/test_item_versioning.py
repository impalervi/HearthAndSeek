"""Tests for item versioning — stamp_new_versions and Lua emission of patchAdded/dateAdded."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch

from parse_catalog_dump import _stamp_new_versions


# ======================================================================
# Helpers
# ======================================================================

def _write_versions(tmp_path, versions: dict):
    """Write a versions dict to item_versions.json in tmp_path."""
    path = tmp_path / "item_versions.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(versions, f)
    return path


def _read_versions(tmp_path) -> dict:
    """Read item_versions.json from tmp_path."""
    path = tmp_path / "item_versions.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ======================================================================
# _stamp_new_versions — core stamping logic
# ======================================================================

class TestStampNewVersions:
    def test_stamps_new_items(self, tmp_path):
        """New decorIDs get stamped with patch and today's date."""
        _write_versions(tmp_path, {})
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([100, 200], tmp_path)
        versions = _read_versions(tmp_path)
        assert "100" in versions
        assert "200" in versions
        assert versions["100"]["patch"] == "12.0.1"
        assert versions["200"]["patch"] == "12.0.1"
        # Date should be today in ISO format
        assert len(versions["100"]["date"]) == 10  # YYYY-MM-DD

    def test_preserves_existing_entries(self, tmp_path):
        """Existing entries are not overwritten when new items are stamped."""
        _write_versions(tmp_path, {
            "80": {"patch": "11.0.7", "date": "2025-12-01"},
        })
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([100], tmp_path)
        versions = _read_versions(tmp_path)
        assert versions["80"] == {"patch": "11.0.7", "date": "2025-12-01"}
        assert versions["100"]["patch"] == "12.0.1"

    def test_skips_already_versioned_items(self, tmp_path):
        """Items already in versions file are not re-stamped."""
        _write_versions(tmp_path, {
            "100": {"patch": "11.0.7", "date": "2025-12-01"},
        })
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([100, 200], tmp_path)
        versions = _read_versions(tmp_path)
        # decorID 100 should keep its original data
        assert versions["100"]["patch"] == "11.0.7"
        assert versions["100"]["date"] == "2025-12-01"
        # decorID 200 should be newly stamped
        assert versions["200"]["patch"] == "12.0.1"

    def test_creates_file_if_missing(self, tmp_path):
        """If item_versions.json doesn't exist, creates it."""
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([100], tmp_path)
        versions = _read_versions(tmp_path)
        assert "100" in versions
        assert versions["100"]["patch"] == "12.0.1"

    def test_empty_patch_skips_stamping(self, tmp_path):
        """If user enters empty patch, nothing is written."""
        _write_versions(tmp_path, {})
        with patch("builtins.input", return_value=""):
            _stamp_new_versions([100], tmp_path)
        versions = _read_versions(tmp_path)
        assert versions == {}

    def test_output_sorted_by_numeric_id(self, tmp_path):
        """Output keys are sorted numerically, not lexicographically."""
        _write_versions(tmp_path, {})
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([300, 80, 1500], tmp_path)
        versions = _read_versions(tmp_path)
        keys = list(versions.keys())
        assert keys == ["80", "300", "1500"]

    def test_int_to_string_key_conversion(self, tmp_path):
        """Integer decorIDs are stored as string keys in JSON."""
        _write_versions(tmp_path, {})
        with patch("builtins.input", return_value="12.0.1"):
            _stamp_new_versions([42], tmp_path)
        versions = _read_versions(tmp_path)
        assert "42" in versions
        assert 42 not in versions


# ======================================================================
# Version data in Lua emission
# ======================================================================

class TestVersionLuaEmission:
    """Test the patchAdded/dateAdded fallback logic used in serialize_item."""

    def test_version_patch_takes_priority(self):
        """_versionPatch takes priority over _patchAdded."""
        item = {"_versionPatch": "12.0.1", "_patchAdded": "11.0.7"}
        patch_added = item.get("_versionPatch") or item.get("_patchAdded")
        assert patch_added == "12.0.1"

    def test_falls_back_to_wowhead_patch(self):
        """When _versionPatch is absent, _patchAdded is used."""
        item = {"_patchAdded": "11.0.7"}
        patch_added = item.get("_versionPatch") or item.get("_patchAdded")
        assert patch_added == "11.0.7"

    def test_empty_version_patch_falls_back(self):
        """Empty string _versionPatch falls back to _patchAdded."""
        item = {"_versionPatch": "", "_patchAdded": "11.0.7"}
        patch_added = item.get("_versionPatch") or item.get("_patchAdded")
        assert patch_added == "11.0.7"

    def test_no_patch_data_at_all(self):
        """When neither field exists, result is None/falsy."""
        item = {}
        patch_added = item.get("_versionPatch") or item.get("_patchAdded")
        assert not patch_added

    def test_date_added_from_version(self):
        """_versionDate is emitted as dateAdded."""
        item = {"_versionDate": "2026-03-28"}
        assert item.get("_versionDate") == "2026-03-28"

    def test_no_date_added_when_missing(self):
        """No dateAdded when _versionDate is absent."""
        item = {}
        assert item.get("_versionDate") is None


# ======================================================================
# item_versions.json data integrity (requires generated data)
# ======================================================================

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


class TestItemVersionsDataIntegrity:
    @pytest.fixture(scope="class")
    def versions(self):
        path = DATA_DIR / "item_versions.json"
        if not path.exists():
            pytest.skip("item_versions.json not found")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @pytest.fixture(scope="class")
    def catalog(self):
        path = DATA_DIR / "enriched_catalog.json"
        if not path.exists():
            pytest.skip("enriched_catalog.json not found")
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def test_all_catalog_items_have_versions(self, versions, catalog):
        """Every item in enriched_catalog.json has an entry in item_versions.json."""
        missing = []
        for item in catalog:
            did = str(item.get("decorID", ""))
            if did and did not in versions:
                missing.append(did)
        assert missing == [], f"Missing version entries for decorIDs: {missing[:10]}"

    def test_all_entries_have_required_fields(self, versions):
        """Every entry has both 'patch' and 'date' fields."""
        for did, entry in versions.items():
            assert "patch" in entry, f"decorID {did} missing 'patch'"
            assert "date" in entry, f"decorID {did} missing 'date'"

    def test_date_format(self, versions):
        """All dates are in YYYY-MM-DD format."""
        import re
        date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for did, entry in versions.items():
            assert date_re.match(entry["date"]), f"decorID {did} has invalid date: {entry['date']}"

    def test_patch_format(self, versions):
        """All patches look like version strings (X.Y.Z)."""
        import re
        patch_re = re.compile(r"^\d+\.\d+\.\d+$")
        for did, entry in versions.items():
            assert patch_re.match(entry["patch"]), f"decorID {did} has invalid patch: {entry['patch']}"

    def test_keys_are_numeric_strings(self, versions):
        """All keys are string representations of positive integers."""
        for did in versions:
            assert did.isdigit(), f"Non-numeric key: {did}"
            assert int(did) > 0, f"Non-positive key: {did}"
