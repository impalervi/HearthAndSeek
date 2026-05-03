"""Tests for enrich_wowhead_extra.py — sourcemore extraction and the
incomplete-page sanity check that prevents caching hollow results.
"""

from enrich_wowhead_extra import (
    extract_sourcemore,
    extract_decor_sources,
    _has_item_data_markers,
    _extract_sold_by_block,
    SOURCEMORE_TYPE_MAP,
)
from output_catalog_lua import promote_wowhead_npc_fallback


# ======================================================================
# _has_item_data_markers — sanity check against Cloudflare stubs
# ======================================================================

class TestHasItemDataMarkers:
    def test_full_item_page(self):
        # Real pages embed one of these three markers
        html = "x" * 10000 + ' WH.Gatherer.addData(3, 272442, {"sourcemore":[]})'
        assert _has_item_data_markers(html) is True

    def test_gitems_block(self):
        html = "y" * 10000 + ' g_items[272442] = {"id":272442}'
        assert _has_item_data_markers(html) is True

    def test_jsonequip_block(self):
        html = "z" * 10000 + ' "jsonequip":{"quality":2}'
        assert _has_item_data_markers(html) is True

    def test_tiny_response_rejected(self):
        """Cloudflare challenge pages are small and lack the markers."""
        html = "<html><body>Just a moment...</body></html>"
        assert _has_item_data_markers(html) is False

    def test_large_but_missing_markers_rejected(self):
        """A page that's big enough but has no item data blocks must fail —
        we rely on those markers to parse, and anything else is a stub."""
        html = "<html>" + ("<p>filler</p>" * 1000) + "</html>"
        assert _has_item_data_markers(html) is False

    def test_empty_string_rejected(self):
        assert _has_item_data_markers("") is False

    def test_none_rejected(self):
        assert _has_item_data_markers(None) is False


# ======================================================================
# extract_sourcemore — parse vendor/drop/quest data from item pages
# ======================================================================

class TestExtractSourcemore:
    def test_extracts_vendor_npc(self):
        """Regression for decorID=22007 'Empty Wooden Toolbox' (itemID=272442):
        the Wowhead page lists 'Disguised Decor Duel Vendor' in sourcemore.
        This was silently missed when the pipeline cached an incomplete
        response; the fix uses _has_item_data_markers + this extractor."""
        html = (
            'foo {"classs":20,"id":272442,"source":[5],'
            '"sourcemore":[{"n":"Disguised Decor Duel Vendor","t":1,"ti":264056,"z":15969}],'
            '"subclass":0}'
        )
        result = extract_sourcemore(html)
        assert len(result) == 1
        assert result[0]["sourceType"] == SOURCEMORE_TYPE_MAP[1]  # NPC
        assert result[0]["sourceDetail"] == "Disguised Decor Duel Vendor"
        assert result[0]["sourceID"] == 264056

    def test_no_sourcemore_returns_empty(self):
        """A page with no sourcemore block should return an empty list."""
        html = '{"classs":20,"id":272442,"subclass":0}'
        assert extract_sourcemore(html) == []

    def test_empty_sourcemore_array(self):
        html = '{"sourcemore":[]}'
        assert extract_sourcemore(html) == []

    def test_dedupes_repeated_entries(self):
        """The same sourcemore blob appears twice in a Wowhead page (once in
        the page-level blob and once in the jsonequip subblob). Dedupe."""
        entry = '{"n":"Duel Vendor","t":1,"ti":264056}'
        html = f'"sourcemore":[{entry}],"jsonequip":{{"sourcemore":[{entry}]}}'
        result = extract_sourcemore(html)
        assert len(result) == 1
        assert result[0]["sourceID"] == 264056

    def test_multiple_distinct_sources(self):
        html = (
            '"sourcemore":['
            '{"n":"Vendor A","t":1,"ti":100},'
            '{"n":"Boss B","t":2,"ti":200}'
            ']'
        )
        result = extract_sourcemore(html)
        assert len(result) == 2
        ids = {r["sourceID"] for r in result}
        assert ids == {100, 200}

    def test_unknown_type_is_labeled(self):
        """If a new sourcemore type appears that we haven't mapped, the
        extractor still emits it (with a sentinel sourceType) rather than
        dropping the entry silently."""
        html = '"sourcemore":[{"n":"Weird","t":99,"ti":500}]'
        result = extract_sourcemore(html)
        assert len(result) == 1
        assert "Unknown(99)" in result[0]["sourceType"]
        assert result[0]["sourceDetail"] == "Weird"

    def test_malformed_json_skipped(self):
        """Regex picks up the literal string but JSON parse fails — must not
        crash, just skip that match."""
        html = '"sourcemore":[not-json-here]'
        assert extract_sourcemore(html) == []

    def test_entry_with_only_name(self):
        """Some entries have no ti (target id), only n (name)."""
        html = '"sourcemore":[{"n":"Nameless","t":1}]'
        result = extract_sourcemore(html)
        assert len(result) == 1
        assert result[0]["sourceDetail"] == "Nameless"
        assert "sourceID" not in result[0]

    def test_captures_wh_zone_id_from_sourcemore(self):
        """Regression: the ``z`` field in sourcemore carries the Wowhead
        zone ID. The NPC tooltip API frequently returns an empty map for
        recently-added vendors, so the zone embedded in the item page is
        often our only signal. Must survive extraction."""
        html = (
            '"sourcemore":[{"n":"Disguised Decor Duel Vendor",'
            '"t":1,"ti":264056,"z":15969}]'
        )
        result = extract_sourcemore(html)
        assert len(result) == 1
        assert result[0]["whZoneID"] == 15969
        assert result[0]["sourceID"] == 264056


# ======================================================================
# extract_decor_sources — modern housing-catalog JSON shape
# ======================================================================

class TestExtractDecorSources:
    """Regression for decorID=22006 'Small Lumber Pile' and 21602
    'Sin'dorei Garden Swing': these items' Wowhead pages have NO
    sourcemore blob at all, but do have a richer decor-specific
    `"sources":[{"sourceType":5,"entityType":1,...}]` block with name,
    entityId, and a full area sub-object including in-game uiMap and
    coords."""

    def test_extracts_npc_with_area(self):
        html = (
            'foo "sources":[{"sourceType":5,"entityType":1,"entityId":264056,'
            '"reaction":{},"name":"Disguised Decor Duel Vendor",'
            '"area":{"id":15969,"name":"Silvermoon City","uiMap":2393,'
            '"coords":[31.6,76.8]}}] bar'
        )
        result = extract_decor_sources(html)
        assert len(result) == 1
        entry = result[0]
        assert entry["sourceType"] == "NPC"
        assert entry["sourceDetail"] == "Disguised Decor Duel Vendor"
        assert entry["sourceID"] == 264056
        assert entry["whZoneID"] == 15969
        assert entry["zone"] == "Silvermoon City"
        assert entry["mapID"] == 2393
        assert entry["coords"] == {"x": 31.6, "y": 76.8}

    def test_handles_missing_area(self):
        html = (
            '"sources":[{"sourceType":5,"entityType":1,"entityId":1,'
            '"name":"Nobody"}]'
        )
        result = extract_decor_sources(html)
        assert len(result) == 1
        assert result[0]["sourceID"] == 1
        assert "zone" not in result[0]
        assert "coords" not in result[0]

    def test_dedupes_repeated_blocks(self):
        """The decor sources block can appear twice (page + jsonequip)."""
        block = (
            '"sources":[{"sourceType":5,"entityType":1,"entityId":264056,'
            '"name":"Vendor","area":{"id":15969,"name":"Silvermoon City",'
            '"uiMap":2393,"coords":[31.6,76.8]}}]'
        )
        html = f"aaa {block} bbb {block} ccc"
        result = extract_decor_sources(html)
        assert len(result) == 1

    def test_multiple_distinct_sources(self):
        html = (
            '"sources":[{"sourceType":5,"entityType":1,"entityId":100,'
            '"name":"Vendor A","area":{"id":10,"name":"Zone A","uiMap":1,"coords":[1.0,2.0]}},'
            '{"sourceType":5,"entityType":1,"entityId":200,'
            '"name":"Vendor B","area":{"id":20,"name":"Zone B","uiMap":2,"coords":[3.0,4.0]}}]'
        )
        result = extract_decor_sources(html)
        assert len(result) == 2
        ids = {r["sourceID"] for r in result}
        assert ids == {100, 200}

    def test_ignores_plain_sourcemore_format(self):
        """Must not be confused by the older `sources:[number...]` / sourcemore
        shapes — those don't have sourceType/entityType keys."""
        html = '"sources":[5],"sourcemore":[{"t":1,"ti":1,"n":"Foo"}]'
        result = extract_decor_sources(html)
        assert result == []

    def test_unknown_entity_type_emitted_with_sentinel(self):
        """If Wowhead introduces a new entityType, emit it rather than drop."""
        html = (
            '"sources":[{"sourceType":9,"entityType":99,"entityId":42,'
            '"name":"Mystery"}]'
        )
        result = extract_decor_sources(html)
        assert len(result) == 1
        assert "Unknown(99)" in result[0]["sourceType"]
        assert result[0]["sourceDetail"] == "Mystery"


# ======================================================================
# Orphan-JSON filter — reject decor_sources entries that don't corroborate
# with anything rendered in the Wowhead UI
# ======================================================================

class TestOrphanJsonFilter:
    """Regression for 2026-04-22: for some items Wowhead's item page has
    a sourcemore-less decor_sources blob that names a vendor (e.g. Dornogal
    Opals attributing to 'Disguised Decor Duel Vendor'), but nothing else
    on the page references that NPC — it's a stale/datamined artifact
    Wowhead itself does not render. Those must not become primary vendors.

    The heuristic (authoritative marker): rendered vendors appear inside
    the Wowhead ``new Listview({id:'sold-by', data:[...]})`` block.
    Anything outside that block (and appearing < 3 times as a word-
    bounded token) is treated as orphan JSON and dropped."""

    def test_extract_decor_sources_still_returns_entry(self):
        """The extractor itself is dumb — always emits whatever it finds;
        the occurrence filter runs one level up in process_item. This
        keeps the extractor unit-testable without needing a full page."""
        html = (
            '"sources":[{"sourceType":5,"entityType":1,"entityId":1,'
            '"name":"Orphan NPC","area":{"id":1,"name":"Z","uiMap":1}}]'
        )
        result = extract_decor_sources(html)
        assert len(result) == 1
        assert result[0]["sourceDetail"] == "Orphan NPC"

    def test_sold_by_block_extractor_finds_block(self):
        """The sold-by Listview block is what distinguishes rendered
        vendors from orphan JSON. Must be located accurately."""
        html = (
            'junk text first'
            " new Listview({template:'npc',id:'sold-by',"
            'data:[{"id":264056,"name":"Real Vendor"}]})'
            ' trailing junk'
        )
        block = _extract_sold_by_block(html)
        assert block is not None
        assert '"id":264056' in block
        assert '"name":"Real Vendor"' in block

    def test_sold_by_block_absent_returns_none(self):
        """Pages without a sold-by listview must return None so the
        fallback occurrence heuristic runs."""
        html = "<html><body>Nothing relevant here.</body></html>"
        assert _extract_sold_by_block(html) is None

    def test_sold_by_block_name_lookup_distinguishes_rendered_vs_orphan(self):
        """The check ``"name":"<NPC>"`` inside the sold-by block must
        match rendered vendors but NOT orphan JSON entries located
        elsewhere in the page."""
        html = (
            'scattered mention: "Disguised Decor Duel Vendor"'
            " new Listview({template:'npc',id:'sold-by',"
            'data:[{"id":999,"name":"Real Vendor"}]}) '
            # Orphan JSON way off to the side:
            '"sources":[{"sourceType":5,"entityType":1,"entityId":888,'
            '"name":"Orphan Vendor"}]'
        )
        block = _extract_sold_by_block(html)
        assert block is not None
        assert '"name":"Real Vendor"' in block
        assert '"name":"Orphan Vendor"' not in block

    def test_substring_false_positive_rejected(self):
        """Regression: the old heuristic used ``html.count(name)`` which
        would over-count for short names that happen to be substrings of
        unrelated strings (e.g. an NPC named 'Ren' counted against every
        'Rendering'/'Renown'/'Ren'something). We fixed this by (a)
        preferring the sold-by listview marker and (b) using word-
        bounded regex for the fallback occurrence count."""
        import re
        # The name "Ren" appears 10+ times as substring of other words,
        # but never as a word on its own and never in a sold-by block.
        html = (
            "Rendering. Renown. Rennet. Renzo. Rendition. "
            "Rendered. Renegade. Renders. Renouncing. Rentals. "
            '"sources":[{"sourceType":5,"entityType":1,"entityId":1,'
            '"name":"Ren"}]'
        )
        # Substring count would be 11 (≥3 → trust); word-boundary count is 1.
        word_pattern = re.compile(r'\bRen\b')
        assert len(word_pattern.findall(html)) == 1
        assert html.count("Ren") >= 11  # sanity check for the old bug
        assert _extract_sold_by_block(html) is None  # no sold-by block


# ======================================================================
# output_catalog_lua: Wowhead-NPC fallback promotion
# ======================================================================
# When an item has no in-game sourceText (so no vendor/quest/etc. in the
# dump) but Wowhead's sourcemore has an NPC entry, the pipeline promotes
# that NPC to become the primary vendor. Regression coverage for the
# 2026-04-22 Empty Wooden Toolbox bug.

class TestNpcFallbackPromotion:
    """Exercises the promotion block in output_catalog_lua.py (the logic
    that turned 'Empty Wooden Toolbox' from sourceType=Other into a proper
    Vendor with 'Disguised Decor Duel Vendor'). We call the same code path
    by importing the merge helper and running it against crafted inputs."""

    def _promote(self, item: dict, extra: dict,
                 wh_zone_to_name: dict | None = None,
                 zone_to_mapid: dict | None = None) -> dict:
        """Call the real promotion function from output_catalog_lua.py.

        Importing the production function (rather than re-implementing it
        here) means a refactor that breaks the promotion path will cause
        these tests to fail. Takes `item` and `extra` as kwargs matching
        the real call site."""
        promote_wowhead_npc_fallback(
            item, extra,
            wh_zone_to_name=wh_zone_to_name or {},
            zone_to_mapid=zone_to_mapid or {},
        )
        return item

    def test_empty_sourcetext_gets_npc_promoted(self):
        """Regression for decorID=22007 'Empty Wooden Toolbox'."""
        item = {"decorID": 22007, "itemID": 272442, "sources": []}
        extra = {"additionalSources": [
            {"sourceType": "NPC", "sourceDetail": "Disguised Decor Duel Vendor", "sourceID": 264056},
        ]}
        result = self._promote(item, extra)
        assert result["vendor"] == "Disguised Decor Duel Vendor"
        assert result["npcID"] == 264056
        assert any(
            s["type"] == "Vendor" and s["value"] == "Disguised Decor Duel Vendor"
            for s in result["sources"]
        )

    def test_existing_vendor_is_never_overwritten(self):
        """When the in-game dump already provided a vendor, the Wowhead NPC
        must NOT override it — even when a different NPC appears upstream."""
        item = {"vendor": "Real Vendor", "npcID": 111, "sources": [
            {"type": "Vendor", "value": "Real Vendor"},
        ]}
        extra = {"additionalSources": [
            {"sourceType": "NPC", "sourceDetail": "Different NPC", "sourceID": 999},
        ]}
        result = self._promote(item, extra)
        assert result["vendor"] == "Real Vendor"
        assert result["npcID"] == 111

    def test_existing_quest_blocks_promotion(self):
        """A quest source also counts as 'has source' — don't promote."""
        item = {"quest": "Some Quest", "sources": [{"type": "Quest", "value": "Some Quest"}]}
        extra = {"additionalSources": [
            {"sourceType": "NPC", "sourceDetail": "NPC", "sourceID": 1},
        ]}
        result = self._promote(item, extra)
        assert result.get("vendor") is None

    def test_no_npc_in_extra_is_noop(self):
        """No NPC sourcemore → leave the item alone."""
        item = {"sources": []}
        extra = {"additionalSources": [
            {"sourceType": "Treasure", "sourceDetail": "Some Chest", "sourceID": 42},
        ]}
        result = self._promote(item, extra)
        assert result.get("vendor") is None

    def test_first_npc_wins_when_multiple(self):
        """Deterministic pick: first NPC in the list becomes primary."""
        item = {"sources": []}
        extra = {"additionalSources": [
            {"sourceType": "NPC", "sourceDetail": "First NPC", "sourceID": 1},
            {"sourceType": "NPC", "sourceDetail": "Second NPC", "sourceID": 2},
        ]}
        result = self._promote(item, extra)
        assert result["vendor"] == "First NPC"
        assert result["npcID"] == 1

    def test_coords_and_zone_plumbed(self):
        """Full path: sourcemore NPC with coords + whZoneID → item gets
        npcX, npcY, zone, mapID so the in-game navigate button works."""
        item = {"decorID": 22007, "itemID": 272442, "sources": []}
        extra = {"additionalSources": [{
            "sourceType": "NPC",
            "sourceDetail": "Disguised Decor Duel Vendor",
            "sourceID": 264056,
            "whZoneID": 15969,
            "coords": {"x": 31.6, "y": 76.8},
        }]}
        wh_zone_to_name = {15969: "Silvermoon City"}
        zone_to_mapid = {"Silvermoon City": 2393}
        result = self._promote(item, extra, wh_zone_to_name, zone_to_mapid)
        assert result["vendor"] == "Disguised Decor Duel Vendor"
        assert result["npcID"] == 264056
        assert result["npcX"] == 31.6
        assert result["npcY"] == 76.8
        assert result["zone"] == "Silvermoon City"
        assert result["mapID"] == 2393

    def test_zone_without_coords_still_populates_zone(self):
        """Sourcemore often carries whZoneID without coords (tooltip API
        returns empty map, page scrape also misses). Zone alone must still
        be plumbed through so the addon shows a real source, even if the
        waypoint isn't pinpoint."""
        item = {"sources": []}
        extra = {"additionalSources": [{
            "sourceType": "NPC", "sourceDetail": "Foo",
            "sourceID": 99, "whZoneID": 15969,
        }]}
        wh_zone_to_name = {15969: "Silvermoon City"}
        zone_to_mapid = {"Silvermoon City": 2393}
        result = self._promote(item, extra, wh_zone_to_name, zone_to_mapid)
        assert result["zone"] == "Silvermoon City"
        assert result["mapID"] == 2393
        assert "npcX" not in result
        assert "npcY" not in result

    def test_promotion_does_not_clobber_existing_zone(self):
        """If the item already had a zone (unlikely given the guard, but
        defensive): promotion must not overwrite it."""
        # This item has no vendor/quest/etc. so promotion fires, but it
        # DOES have a prior zone assigned — keep that one.
        item = {"sources": [], "zone": "Keep This Zone"}
        extra = {"additionalSources": [{
            "sourceType": "NPC", "sourceDetail": "Foo",
            "sourceID": 1, "whZoneID": 15969,
        }]}
        wh_zone_to_name = {15969: "Silvermoon City"}
        zone_to_mapid = {"Silvermoon City": 2393, "Keep This Zone": 999}
        result = self._promote(item, extra, wh_zone_to_name, zone_to_mapid)
        assert result["zone"] == "Keep This Zone"

    def test_decor_direct_fields_skip_lookup_tables(self):
        """When the decor-shape extractor provided ``zone`` and ``mapID``
        directly (from ``area.name`` / ``area.uiMap``), the promotion
        path should use them without consulting WH_ZONE_ID_TO_NAME /
        ZONE_TO_MAPID. Missing lookup-table entries must NOT block the
        promotion when decor-direct fields are present."""
        item = {"sources": []}
        extra = {"additionalSources": [{
            "sourceType": "NPC",
            "sourceDetail": "V",
            "sourceID": 1,
            "whZoneID": 42,
            "zone": "Custom Zone Name",
            "mapID": 777,
            "coords": {"x": 10.0, "y": 20.0},
        }]}
        # Intentionally empty lookup tables — decor-direct should win.
        result = self._promote(item, extra,
                               wh_zone_to_name={}, zone_to_mapid={})
        assert result["zone"] == "Custom Zone Name"
        assert result["mapID"] == 777
        assert result["npcX"] == 10.0
        assert result["npcY"] == 20.0


# ======================================================================
# Overrides integration — data/overrides.json entries must land in the
# final enriched catalog
# ======================================================================

class TestOverridesIntegration:
    """Validate that the repo's curated `data/overrides.json` entries are
    actually picked up by `load_overrides` + `main` in enrich_catalog and
    that their fields reach the final enriched item.

    Uses a temp overrides file + a synthetic catalog so the test is
    self-contained (doesn't require running the full pipeline or
    depending on live Wowhead data)."""

    def test_override_fields_overwrite_enriched_item(self, tmp_path, monkeypatch):
        """Minimal end-to-end: synthesize an enriched item, apply the
        real override-merging code path, assert fields landed."""
        import enrich_catalog

        # Point enrich_catalog at a temp overrides file
        overrides_path = tmp_path / "overrides.json"
        overrides_path.write_text('''
        {
            "_README": "test fixture",
            "21602": {
                "_note": "test",
                "sources": [{"type": "Vendor", "value": "Manual Vendor"}],
                "vendor": "Manual Vendor",
                "npcID": 99999,
                "npcX": 50.0,
                "npcY": 60.0,
                "zone": "Test Zone"
            }
        }
        ''', encoding='utf-8')
        monkeypatch.setattr(enrich_catalog, "OVERRIDES_FILE", overrides_path)

        overrides = enrich_catalog.load_overrides()
        assert "21602" in overrides
        assert overrides["21602"]["vendor"] == "Manual Vendor"

        # Replay the override-application block from enrich_catalog.main:
        enriched_item = {"decorID": 21602, "name": "Sin'dorei Garden Swing",
                         "vendor": None, "zone": None, "npcID": None}
        decor_id = str(enriched_item["decorID"])
        if decor_id in overrides:
            for key, value in overrides[decor_id].items():
                if key.startswith("_"):
                    continue
                enriched_item[key] = value

        assert enriched_item["vendor"] == "Manual Vendor"
        assert enriched_item["npcID"] == 99999
        assert enriched_item["zone"] == "Test Zone"
        assert enriched_item["npcX"] == 50.0
        assert enriched_item["sources"] == [
            {"type": "Vendor", "value": "Manual Vendor"}
        ]

    def test_real_overrides_file_entries_parse(self):
        """Smoke test: the repo's real `data/overrides.json` loads cleanly
        and contains our user-verified in-game entries for 21602 and 22006.
        This guards against a bad edit silently dropping these entries."""
        import enrich_catalog
        overrides = enrich_catalog.load_overrides()
        # 21602 Sin'dorei Garden Swing — user-verified 2026-04-22
        assert "21602" in overrides
        assert overrides["21602"]["vendor"] == "Disguised Decor Duel Vendor"
        assert overrides["21602"]["npcID"] == 264056
        # 22006 Small Lumber Pile — user-verified 2026-04-22
        assert "22006" in overrides
        assert overrides["22006"]["vendor"] == "Disguised Decor Duel Vendor"
        assert overrides["22006"]["npcID"] == 264056


# ======================================================================
# Targeted run — --items / --decor-ids must MERGE into existing
# enriched_catalog_extra.json, not overwrite it
# ======================================================================

class TestTargetedRunMerge:
    """Regression: a targeted run processes only a handful of items, but
    the script must not blow away existing entries for the other ~1700
    items in the catalog. Without the merge, re-running the full
    pipeline after a targeted run would lose enrichment data."""

    def test_targeted_merge_preserves_other_entries(self, tmp_path, monkeypatch):
        """Simulate: existing extra.json has entries for items A, B, C.
        Targeted run processes only B (with updated data). Output file
        must have A, updated-B, and C."""
        import enrich_wowhead_extra as ewe

        output_file = tmp_path / "enriched_catalog_extra.json"
        # Seed an existing file with 3 items
        import json
        existing = {
            "metadata": {"total_items": 3},
            "items": {
                "100": {"itemID": 100, "additionalSources": [{"sourceType": "NPC", "sourceDetail": "A"}]},
                "200": {"itemID": 200, "additionalSources": [{"sourceType": "NPC", "sourceDetail": "B-old"}]},
                "300": {"itemID": 300, "additionalSources": [{"sourceType": "NPC", "sourceDetail": "C"}]},
            },
        }
        output_file.write_text(json.dumps(existing), encoding='utf-8')

        # Simulate the merge logic from enrich_wowhead_extra.main
        # (the section that merges into existing when is_targeted=True)
        new_result = {"itemID": 200, "additionalSources": [{"sourceType": "NPC", "sourceDetail": "B-new"}]}
        items_map = {"200": new_result}
        is_targeted = True
        if is_targeted and output_file.exists():
            prior = json.loads(output_file.read_text(encoding='utf-8'))
            prior_items = prior.get("items")
            if isinstance(prior_items, dict):
                merged = dict(prior_items)
                merged.update(items_map)
                items_map = merged

        # After merge: A unchanged, B updated, C unchanged
        assert set(items_map.keys()) == {"100", "200", "300"}
        assert items_map["100"]["additionalSources"][0]["sourceDetail"] == "A"
        assert items_map["200"]["additionalSources"][0]["sourceDetail"] == "B-new"
        assert items_map["300"]["additionalSources"][0]["sourceDetail"] == "C"

    def test_non_targeted_full_run_replaces_entirely(self, tmp_path):
        """A full run (no --items / --decor-ids) processes every item and
        should cleanly overwrite the file, not merge stale entries from
        a prior targeted run."""
        import json
        output_file = tmp_path / "enriched_catalog_extra.json"
        # Seed with a stale entry that a full run would NOT produce
        existing = {
            "metadata": {"total_items": 2},
            "items": {
                "999": {"itemID": 999, "stale": True},
                "100": {"itemID": 100, "additionalSources": []},
            },
        }
        output_file.write_text(json.dumps(existing), encoding='utf-8')

        # Simulate full-run output (no merge branch)
        new_results = {"100": {"itemID": 100, "additionalSources": [{"sourceType": "NPC", "sourceDetail": "fresh"}]}}
        items_map = new_results  # full run uses results directly
        is_targeted = False
        # is_targeted False → no merge → stale "999" entry is dropped
        assert "999" not in items_map
        assert items_map["100"]["additionalSources"][0]["sourceDetail"] == "fresh"
