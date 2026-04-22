"""Tests for shared-name vendor resolution and the auto-generated
vendor_zones.json flow in output_catalog_lua.py.
"""

import json

import output_catalog_lua as ocl


# ======================================================================
# resolve_vendor_override — zone-first, name-fallback
# ======================================================================

class TestResolveVendorOverride:
    def test_zone_specific_entry_wins(self, monkeypatch):
        """When (name, zone) exists in VENDOR_COORDS_BY_ZONE, it takes
        precedence over the name-only entry in VENDOR_COORDS."""
        monkeypatch.setitem(ocl.VENDOR_COORDS, "TestVendor",
                            {"npcID": 111, "x": 1.0, "y": 2.0, "mapID": 99, "zone": "OldZone"})
        monkeypatch.setitem(ocl.VENDOR_COORDS_BY_ZONE, ("TestVendor", "NewZone"),
                            {"npcID": 222, "x": 3.0, "y": 4.0, "mapID": 88, "zone": "NewZone"})

        result = ocl.resolve_vendor_override("TestVendor", "NewZone")
        assert result["npcID"] == 222
        assert result["zone"] == "NewZone"

    def test_falls_back_to_name_only_when_zone_unknown(self, monkeypatch):
        """If the item's zone has no zone-specific entry, fall back to
        VENDOR_COORDS[name] (preserves old behavior for vendors whose
        zone doesn't match any by-zone entry)."""
        monkeypatch.setitem(ocl.VENDOR_COORDS, "TestVendor",
                            {"npcID": 111, "x": 1.0, "y": 2.0, "mapID": 99, "zone": "OldZone"})
        monkeypatch.setitem(ocl.VENDOR_COORDS_BY_ZONE, ("TestVendor", "NewZone"),
                            {"npcID": 222, "x": 3.0, "y": 4.0, "mapID": 88, "zone": "NewZone"})

        # Item is in "OldZone" — no by-zone entry → falls back to name-only
        result = ocl.resolve_vendor_override("TestVendor", "OldZone")
        assert result["npcID"] == 111

    def test_nil_zone_uses_name_only(self, monkeypatch):
        """When item has no zone, only VENDOR_COORDS is consulted."""
        monkeypatch.setitem(ocl.VENDOR_COORDS, "TestVendor",
                            {"npcID": 111, "x": 1.0, "y": 2.0, "mapID": 99, "zone": "OldZone"})
        result = ocl.resolve_vendor_override("TestVendor", None)
        assert result["npcID"] == 111

    def test_unknown_vendor_returns_none(self):
        """A vendor with no entries anywhere returns None."""
        assert ocl.resolve_vendor_override("VendorThatDoesNotExist", "SomeZone") is None

    def test_raeana_silvermoon_routes_to_silvermoon_npc(self):
        """Regression: 'Rae'ana' exists in The Waking Shores (188265) and
        Silvermoon City (255495). An item with zone='Silvermoon City' must
        route to the Silvermoon NPC, not the Waking Shores one."""
        # These entries come from VENDOR_COORDS (Waking Shores default)
        # plus the auto-loaded vendor_zones.json (Silvermoon by-zone).
        result = ocl.resolve_vendor_override("Rae'ana", "Silvermoon City")
        assert result is not None, "Rae'ana should resolve for Silvermoon City"
        assert result["npcID"] == 255495, (
            f"Expected Silvermoon NPC 255495, got {result['npcID']} "
            "— shared-name vendor not routing by zone"
        )
        assert result["zone"] == "Silvermoon City"

    def test_raeana_waking_shores_preserves_legacy_npc(self):
        """Regression: old items in The Waking Shores must still get
        NPC 188265 — the zone-aware fix must not break existing routing."""
        result = ocl.resolve_vendor_override("Rae'ana", "The Waking Shores")
        assert result is not None
        assert result["npcID"] == 188265, (
            f"Expected Waking Shores NPC 188265, got {result['npcID']}"
        )


# ======================================================================
# _autoload_vendor_zones — JSON loader behavior (via the real file)
# ======================================================================

class TestAutoloadVendorZones:
    def _vendor_zones_path(self):
        return ocl.Path(ocl.__file__).resolve().parent / "data" / "vendor_zones.json"

    def test_autoloader_registers_entries(self):
        """The autoloader populates VENDOR_COORDS_BY_ZONE with a valid
        entry for every (name, zone) pair whose zone is in ZONE_TO_MAPID.

        Uses the real vendor_zones.json produced by enrich_catalog.py. We
        pick one entry from the file and verify it is auto-loaded with
        the expected npcID and mapID."""
        path = self._vendor_zones_path()
        if not path.exists():
            return  # enrichment hasn't been run yet in this environment
        data = json.loads(path.read_text(encoding="utf-8"))
        # Walk the file and check that every loadable entry is present.
        loadable = 0
        for name, zone_map in data.items():
            for zone, info in zone_map.items():
                if zone not in ocl.ZONE_TO_MAPID:
                    continue
                key = (name, zone)
                loaded = ocl.VENDOR_COORDS_BY_ZONE.get(key)
                assert loaded is not None, (
                    f"Autoloader missed {key!r}; vendor_zones.json lists it "
                    f"and ZONE_TO_MAPID has {zone!r}"
                )
                assert loaded["npcID"] == info["npcID"], (
                    f"npcID mismatch for {key!r}: file={info['npcID']}, loaded={loaded['npcID']}"
                )
                assert loaded["mapID"] == ocl.ZONE_TO_MAPID[zone]
                loadable += 1
        assert loadable > 0, "No entries in vendor_zones.json were loadable"

    def test_manual_entries_win_over_autoloaded(self):
        """Manual entries in VENDOR_COORDS_BY_ZONE are preserved when the
        autoloader re-runs."""
        key = ("Manual Wins Vendor", "Stormwind City")
        manual = {"npcID": 1, "x": 0, "y": 0, "mapID": 84, "zone": "Stormwind City"}
        try:
            ocl.VENDOR_COORDS_BY_ZONE[key] = manual
            # Call the autoloader; it should NOT overwrite our manual entry
            # even if vendor_zones.json had a different value for the same key.
            ocl._autoload_vendor_zones()
            assert ocl.VENDOR_COORDS_BY_ZONE[key]["npcID"] == 1
        finally:
            ocl.VENDOR_COORDS_BY_ZONE.pop(key, None)


# ======================================================================
# vendor_zones.json shape & integrity (works on the real file if present)
# ======================================================================

class TestVendorZonesJsonIntegrity:
    def test_file_is_valid_json_or_absent(self):
        """vendor_zones.json should be a dict of dicts when present."""
        path = ocl.Path(ocl.__file__).resolve().parent / "data" / "vendor_zones.json"
        if not path.exists():
            return  # skip — no enrichment has run yet
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)
        for name, zone_map in data.items():
            assert isinstance(name, str)
            assert isinstance(zone_map, dict)
            assert len(zone_map) >= 1, f"{name}: empty zone map"
            for zone, info in zone_map.items():
                assert isinstance(zone, str) and zone, f"{name}: invalid zone key"
                assert "npcID" in info and isinstance(info["npcID"], int)

    def test_every_entry_has_at_least_one_zone(self):
        """Every shared-name vendor entry must describe at least one zone."""
        path = ocl.Path(ocl.__file__).resolve().parent / "data" / "vendor_zones.json"
        if not path.exists():
            return
        data = json.loads(path.read_text(encoding="utf-8"))
        for name, zone_map in data.items():
            assert zone_map, f"{name} has no zone entries in vendor_zones.json"
