"""Tests for pipeline_metadata.py — TOC parsing, metadata read/write, stamping."""

import json
from unittest.mock import patch

import pytest

import pipeline_metadata
from pipeline_metadata import (
    get_game_version,
    read_metadata,
    update_metadata,
    stamp_after_scrape,
    METADATA_FILENAME,
)


# ======================================================================
# get_game_version
# ======================================================================

class TestGetGameVersion:
    def test_reads_toc_interface(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n## Title: Test\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = get_game_version()
        assert result["interface"] == "120001"
        assert result["expansion"] == "Midnight"

    def test_missing_toc_returns_unknown(self, tmp_path):
        toc = tmp_path / "nonexistent.toc"
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = get_game_version()
        assert result["interface"] == "unknown"

    @pytest.mark.parametrize("interface,expansion", [
        # The code takes int(interface[:2]) as the major version.
        # 5-digit numbers: "10000"[:2] = "10" → 10, "20000"[:2] = "20" → 20, etc.
        # 6-digit numbers: "100002"[:2] = "10" → 10, "110000"[:2] = "11" → 11, etc.
        # Only 6-digit interface numbers from Dragonflight onward map correctly.
        ("100002", "Dragonflight"),
        ("110000", "The War Within"),
        ("120001", "Midnight"),
    ])
    def test_expansion_mapping(self, tmp_path, interface, expansion):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text(f"## Interface: {interface}\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = get_game_version()
        assert result["expansion"] == expansion

    def test_unknown_expansion(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 990000\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = get_game_version()
        assert "Unknown" in result["expansion"]

    def test_toc_with_extra_lines(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text(
            "## Title: HearthAndSeek\n"
            "## Notes: Test addon\n"
            "## Interface: 120001\n"
            "## Author: Test\n",
            encoding="utf-8",
        )
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = get_game_version()
        assert result["interface"] == "120001"


# ======================================================================
# read_metadata
# ======================================================================

class TestReadMetadata:
    def test_read_existing(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text('{"key": "value"}', encoding="utf-8")
        result = read_metadata(tmp_path)
        assert result == {"key": "value"}

    def test_read_missing_returns_empty(self, tmp_path):
        result = read_metadata(tmp_path)
        assert result == {}

    def test_read_invalid_json_returns_empty(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text("not json", encoding="utf-8")
        result = read_metadata(tmp_path)
        assert result == {}

    def test_read_complex_metadata(self, tmp_path):
        data = {
            "gameVersion": {"interface": "120001", "expansion": "Midnight"},
            "catalogDumpItems": 1667,
        }
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text(json.dumps(data), encoding="utf-8")
        result = read_metadata(tmp_path)
        assert result["catalogDumpItems"] == 1667
        assert result["gameVersion"]["expansion"] == "Midnight"

    def test_read_empty_file_returns_empty(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text("", encoding="utf-8")
        result = read_metadata(tmp_path)
        assert result == {}


# ======================================================================
# update_metadata
# ======================================================================

class TestUpdateMetadata:
    def test_creates_new_file(self, tmp_path):
        result = update_metadata(tmp_path, {"key": "value"})
        assert result["key"] == "value"
        assert "lastModified" in result
        # Verify file exists
        meta_file = tmp_path / METADATA_FILENAME
        assert meta_file.exists()
        stored = json.loads(meta_file.read_text(encoding="utf-8"))
        assert stored["key"] == "value"

    def test_merges_with_existing(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text('{"existing": "data"}', encoding="utf-8")
        result = update_metadata(tmp_path, {"new": "field"})
        assert result["existing"] == "data"
        assert result["new"] == "field"

    def test_overwrites_existing_key(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text('{"key": "old"}', encoding="utf-8")
        result = update_metadata(tmp_path, {"key": "new"})
        assert result["key"] == "new"

    def test_sets_last_modified(self, tmp_path):
        result = update_metadata(tmp_path, {})
        assert "lastModified" in result
        # Should be an ISO-ish format
        assert "T" in result["lastModified"]
        assert result["lastModified"].endswith("Z")

    def test_returns_merged_dict(self, tmp_path):
        result = update_metadata(tmp_path, {"a": 1, "b": 2})
        assert result["a"] == 1
        assert result["b"] == 2

    def test_file_is_valid_json(self, tmp_path):
        update_metadata(tmp_path, {"test": True})
        meta_file = tmp_path / METADATA_FILENAME
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        assert data["test"] is True


# ======================================================================
# stamp_after_scrape
# ======================================================================

class TestStampAfterScrape:
    def test_basic_stamp(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = stamp_after_scrape(tmp_path, source="wowhead")
        assert result["source"] == "wowhead"
        assert result["gameVersion"]["interface"] == "120001"
        assert "lastScrape" in result
        assert "lastModified" in result

    def test_with_files_written(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = stamp_after_scrape(
                tmp_path, source="wowdb", files_written=50, total_files=200
            )
        assert result["filesWrittenThisRun"] == 50
        assert result["totalFiles"] == 200

    def test_none_files_not_included(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = stamp_after_scrape(tmp_path, source="test")
        assert "filesWrittenThisRun" not in result
        assert "totalFiles" not in result

    def test_merges_with_previous(self, tmp_path):
        meta_file = tmp_path / METADATA_FILENAME
        meta_file.write_text('{"oldKey": "preserved"}', encoding="utf-8")
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            result = stamp_after_scrape(tmp_path, source="wowhead")
        assert result["oldKey"] == "preserved"
        assert result["source"] == "wowhead"

    def test_writes_to_disk(self, tmp_path):
        toc = tmp_path / "HearthAndSeek.toc"
        toc.write_text("## Interface: 120001\n", encoding="utf-8")
        with patch.object(pipeline_metadata, "TOC_FILE", toc):
            stamp_after_scrape(tmp_path, source="test")
        meta_file = tmp_path / METADATA_FILENAME
        assert meta_file.exists()
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        assert data["source"] == "test"
