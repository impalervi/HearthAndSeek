# DecorDrive Scraper: Notes & Decisions

## Architecture Overview

The scraper pipeline consists of 5 stages run sequentially by `run_pipeline.py`:

1. **scrape_wowhead.py** - Scrapes Wowhead housing decor farming guides
2. **scrape_wowdb.py** - Scrapes WoWDB's structured housing decor catalog
3. **merge_sources.py** - Merges both data sources via name matching
4. **generate_routes.py** - Generates optimized regional tour packs
5. **output_lua.py** - Converts route JSON to Lua data files

Each stage reads/writes to `data/` so any stage can be re-run independently.

---

## Known Limitations

### Wowhead JavaScript Rendering

**Problem:** Wowhead guide pages increasingly use React/JavaScript hydration to render content. The static HTML returned by a plain `requests.get()` may contain only a skeleton layout with content rendered client-side.

**Mitigation strategies (in order of preference):**
1. **Static HTML parsing** (implemented): The scraper first attempts to parse the static HTML for item links, quest/NPC links, and `/way` commands. Many guide pages do include structured content in the initial HTML.
2. **Embedded JSON extraction** (implemented): Wowhead sometimes embeds guide data as JavaScript objects in `<script>` tags (e.g., `WH.markup.guide = {...}`). The scraper attempts to extract and parse these.
3. **Selenium/Playwright** (not implemented): A headless browser could render the full page. Not implemented to keep dependencies minimal and avoid rate-limiting issues.
4. **Manual data entry** (fallback): If automated scraping fails, items can be manually entered into `data/wowhead_vanilla.json` using the same JSON schema.

**Decision:** Ship the static HTML + embedded JSON approach. If it yields zero items, the pipeline continues gracefully (WoWDB data alone is sufficient for basic routing, just without coordinates).

### WoWDB Page Structure

**Problem:** WoWDB may change their HTML structure at any time. The scraper uses multiple fallback strategies:
1. Look for structured item card containers (class patterns like `decor-item`, `item-card`)
2. Fall back to finding parent elements of `/decor/{id}/` links
3. Last resort: extract any `/decor/{id}/` links found anywhere on the page

**Decision:** The multi-layer fallback approach ensures we capture at least item names and IDs even if the detailed metadata parsing breaks.

### Coordinate Data Gaps

**Problem:** Not all items will have coordinates from Wowhead. Coordinates come from `/way` commands embedded in guide text, which may not exist for every item.

**Decision:** Items without coordinates are included in route packs but placed at the end of their zone's visit sequence. The addon's navigation module will need to handle `nil` coordinates gracefully (skip waypoint setting, show text-only instructions).

### Fuzzy Name Matching

**Problem:** Item names between Wowhead and WoWDB may have minor differences (capitalization, punctuation, trailing spaces, "The" prefix, etc.).

**Decision:** Use `thefuzz` library with a Levenshtein ratio threshold of 85. This handles most naming variations while avoiding false matches. If `thefuzz` is not installed, the scraper falls back to exact (case-insensitive) matching only.

---

## Scraper Etiquette

- **User-Agent:** Custom UA string identifying the project and its educational purpose
- **Request delays:** 1.5-2.0 seconds between requests
- **No parallel requests:** All fetches are sequential
- **Pagination awareness:** WoWDB scraper stops early if a page returns zero items
- **Idempotent:** Running the pipeline twice produces the same output (given the same remote data)

---

## Route Optimization

The route optimizer uses a **nearest-neighbor heuristic**:

1. Start from a capital city (Stormwind for EK, Orgrimmar for Kalimdor)
2. Visit the nearest unvisited zone first (using approximate zone center coordinates)
3. Within each zone, visit the nearest item/NPC first (using in-zone coordinates)
4. TRAVEL steps are inserted between zone transitions

**Trade-offs:**
- Nearest-neighbor is not globally optimal (TSP is NP-hard) but produces reasonable results for the ~10-30 zones per region.
- Zone center coordinates are approximate and hand-coded. They determine visit order, not in-game navigation.
- Items without coordinates are appended at the end of their zone sequence.

---

## Lua Output Format

Generated Lua files follow the addon's data schema exactly:
- Uses `local _, NS = ...` namespace pattern
- Stores packs in `NS.Data.Packs["pack_id"]`
- Each step has: stepIndex, type, label, decorName, decorID, questID, npc, npcID, mapID, coords, zone, note
- `nil` values are written explicitly for clarity
- Files include a comment header noting they are auto-generated

---

## Zone-to-MapID Mapping

The `ZONE_MAP_IDS` dictionary in `scrape_wowhead.py` maps zone names to WoW `uiMapID` values. These are used for:
- Setting waypoints via `C_Map.SetUserWaypoint()` or TomTom
- Linking map pins in the step tracker UI

The map IDs are sourced from the WoW API documentation and Wowpedia. If a zone is missing, the step will have `mapID = nil` and the addon should handle this gracefully.

---

## Manual Data Entry Format

If scraping fails for any source, you can manually populate the JSON files. Here is the expected schema for each:

### wowhead_vanilla.json
```json
[
  {
    "item_name": "Hooded Iron Lantern",
    "item_id": 12345,
    "source_type": "Vendor",
    "zone": "Elwynn Forest",
    "expansion": "vanilla",
    "quest_id": null,
    "npc_name": "Captain Lancy Revshon",
    "npc_id": 49877,
    "coords": {"x": 67.6, "y": 72.8},
    "mapID": 37,
    "dungeon": null
  }
]
```

### wowdb_quests.json
```json
[
  {
    "decor_name": "Hooded Iron Lantern",
    "decor_id": 12345,
    "category": "Lighting",
    "subcategory": "Lanterns",
    "budget_cost": 50,
    "quest_source": "A Donation of Wool",
    "quest_zone": "Elwynn Forest",
    "vendor_npc": "Captain Lancy Revshon",
    "currency_cost": null,
    "interior_exterior": "Both",
    "tags": {
      "culture": ["Human"],
      "size": ["Small"],
      "style": ["Rustic"],
      "theme": []
    }
  }
]
```

---

## Future Improvements

1. **Additional expansion pages:** Add URLs for TBC, Wrath, MoP, etc. to `GUIDE_URLS` in `scrape_wowhead.py`
2. **Questie cross-reference:** Use Questie's open-source DB to verify/supplement NPC coordinates
3. **Headless browser fallback:** Add optional Playwright support for JavaScript-rendered pages
4. **Delta updates:** Track what changed between scraper runs to avoid regenerating unchanged packs
5. **Faction-specific routes:** Generate separate Alliance/Horde packs (currently faction-neutral)
6. **Achievement source support:** Extend WoWDB scraping to `?source_types=Achievement`
