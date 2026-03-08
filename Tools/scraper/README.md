# HearthAndSeek Scraper & Data Pipeline

Developer reference for the catalog dump workflow, data enrichment, and Lua
data generation for the addon.

## Quick Reference

### One-Command Pipeline

```bash
cd Tools/scraper

# Full pipeline: parse dump → enrich → generate Lua → deploy to WoW
python run_pipeline.py --deploy

# Common shortcuts:
python run_pipeline.py --skip-enrich --deploy   # Use cached Wowhead data
python run_pipeline.py --generate-only --deploy  # Only regenerate Lua files
python run_pipeline.py --force --deploy           # Re-fetch ALL Wowhead + WoWDB data
python run_pipeline.py --dry-run                  # See what would run
```

### Individual Scripts (Advanced)

```bash
cd Tools/scraper

python parse_catalog_dump.py      # 1a. Parse catalog SavedVariables dump
python parse_boss_dump.py         # 1b. Parse boss floor map dump
python parse_boss_dump.py --validate  # 1b. ...with cross-validation
python enrich_catalog.py          # 2. Enrich with Wowhead data
python enrich_quest_chains.py     # 3. Build quest prerequisite chains
python enrich_quest_givers.py     # 4. Extract quest-giver NPC coordinates
python scrape_wowdb.py --all      # 5. Scrape WoWDB community sets and item tags
python compute_item_themes.py     # 6. Compute aesthetic and culture theme scores
python enrich_wowhead_extra.py    # 7. Enrich with drop rates, skills, vendor costs
python output_catalog_lua.py      # 8. Generate Data/CatalogData.lua
python output_quest_chains_lua.py # 9. Generate Data/QuestChains.lua
```

### Output Files

| File | Description | Size |
|------|-------------|------|
| `data/catalog_dump.json` | Raw parsed dump | 1,667 items |
| `data/boss_dump.json` | Boss name → dungeon floor mapID | ~500-800 entries |
| `data/enriched_catalog.json` | Enriched with IDs, coords, factions | ~1.5MB |
| `data/quest_chains.json` | Quest chain prerequisite graph | 1,827 quests |
| `data/quest_givers.json` | Quest-giver NPC coordinates | ~1,800 quests |
| `data/wowdb_sets.json` | WoWDB community set data | ~500 sets |
| `data/wowdb_item_tags.json` | Per-item culture/style tags | ~1,667 items |
| `data/item_themes.json` | Theme assignments & scores | ~1,200 items |
| `data/enriched_catalog_extra.json` | Drop rates, vendor costs, profession skills | ~1,667 items |
| `../../Data/CatalogData.lua` | Addon Lua data | ~2.1MB |
| `../../Data/QuestChains.lua` | Addon quest chain data | ~548KB |

---

## Step-by-Step Pipeline

### Step 1: In-Game Catalog Dump

1. Log in to WoW retail with HearthAndSeek installed.
2. Run `/hs dump catalog` in chat. Scans decorIDs 1--20000 in batches.
3. Wait for "Catalog dump complete" message.
4. `/reload` to flush SavedVariables to disk:
   ```
   <WoWDir>/_retail_/WTF/Account/<ACCOUNT>/SavedVariables/HearthAndSeek.lua
   ```

### Step 1b: In-Game Boss Floor Map Dump

1. Enable dev mode: set `NS.DEV_MODE = true` in `Core/Constants.lua`.
2. Deploy and `/reload` in WoW.
3. Run `/hs dump bosses` in chat. Scans all Encounter Journal tiers/instances.
4. Wait for "Boss dump complete" message (~5-10 seconds).
5. `/reload` to flush SavedVariables to disk.
6. Disable dev mode: set `NS.DEV_MODE = false` in `Core/Constants.lua`.

The boss dump scans every instance in the Encounter Journal, resolving each
boss encounter to the correct dungeon floor mapID by searching the instance
base map + child maps + grandchild maps via `C_EncounterJournal.GetEncountersOnMap()`.

Parse the dump:
```bash
python parse_boss_dump.py              # Output: data/boss_dump.json
python parse_boss_dump.py --validate   # Cross-validate against fixups
```

The `--validate` flag cross-references the EJ dump results against the
`boss_floor_fixups` and `boss_name_aliases` tables in `output_catalog_lua.py`,
reporting which fixups are still needed (deeper floors the EJ can't resolve),
which aliases resolve correctly, and any mismatches to investigate.

### Step 2: Parse the Dump

```bash
python parse_catalog_dump.py
# or with explicit path:
python parse_catalog_dump.py --input /path/to/HearthAndSeek.lua
```

Reads the SavedVariables file, strips WoW formatting codes (`|cXXXXXXXX`,
`|r`, `|n`, `|H..|h`, `|T..|t`), and outputs `data/catalog_dump.json`.

### Step 3: Enrich with Wowhead

```bash
python enrich_catalog.py
```

Cross-references each item against Wowhead to resolve:
- Quest names -> questIDs (+ faction from quest `side` field)
- Vendor names -> npcIDs + coordinates (+ faction from NPC `react` field)
- "Decor Treasure Hunt" ambiguous quests -> resolved via item reward matching

Results cached in `data/wowhead_cache/` to avoid re-scraping. Outputs
`data/enriched_catalog.json` and `data/enrichment_lookups.json`.

### Step 4: Generate Lua Data

```bash
python output_catalog_lua.py
```

Reads `data/enriched_catalog.json` and generates `Data/CatalogData.lua` with:

- **`NS.CatalogData.Items`** -- dictionary keyed by decorID (all fields)
- **`NS.CatalogData.BySource`** -- decorIDs grouped by source type
- **`NS.CatalogData.ByExpansion`** -- decorIDs grouped by expansion
- **`NS.CatalogData.ByProfession`** -- decorIDs grouped by profession name
- **`NS.CatalogData.NameIndex`** -- alphabetically sorted for search
- **`NS.CatalogData.BossFloorMaps`** -- boss/instance name → dungeon floor mapID
- **`NS.CatalogData.ExpansionOrder`** / **`NS.CatalogData.SourceOrder`** / **`NS.CatalogData.ProfessionOrder`**

#### Source Type Derivation

Source type is derived by priority:
```
Quest > Achievement > Prey > Profession > Drop > Treasure > Vendor > Other
```

Key rules:
- **Achievement > Vendor**: When both present, Achievement wins (vendor is
  just the redemption mechanism after completing the achievement).
- **Prey detection**: Items with a Category source value "Prey" get their
  own source type.
- **Faction → Vendor**: "Faction" source types are remapped to "Vendor".

#### Expansion Derivation

Expansion is derived from a `ZONE_TO_EXPANSION` dictionary mapping ~115 zone
names. Items without a zone go to "Unknown".

#### Drop Item Fixups

Some Drop items from Wowhead have incomplete data (dungeon name instead of
boss name, missing zone). The `DROP_FIXUPS` table in `output_catalog_lua.py`
manually corrects known cases:

```python
DROP_FIXUPS = {
    4401: ("Deadmines", "Vanessa VanCleef"),  # was just "Deadmines"
    840:  ("Darkshore", "..."),                # was missing zone
    # ...
}
```

The `DROP_VALUE_TO_ZONE` table maps Drop source values to zones for items
that have a Drop value but no explicit zone field.

#### Profession Name Parsing

Profession source details like "Midnight Tailoring (50)" are parsed to
extract the base profession name ("Tailoring") using the `PROFESSION_NAMES`
list. This enables per-profession sub-filtering in the UI.

#### Per-Item Fields

Each item in `NS.CatalogData.Items` has these fields:
| Field | Type | Description |
|-------|------|-------------|
| `decorID` | number | Wowhead Gatherer Type 201 ID |
| `name` | string | Display name |
| `itemID` | number | WoW item ID |
| `quality` | number | 0=Poor, 1=Common, 2=Uncommon, 3=Rare, 4=Epic, 5=Legendary |
| `iconTexture` | number | FileID for item icon |
| `asset` | number | 3D model asset ID |
| `zone` | string | Zone name (for waypoints) |
| `sourceType` | string | Vendor/Quest/Achievement/Prey/Profession/Drop/Treasure/Other |
| `sourceDetail` | string | Boss name, quest name, vendor name, etc. |
| `achievementName` | string | Achievement name (even if not primary source) |
| `vendorName` | string | Vendor name (even if not primary source) |
| `professionName` | string | Parsed profession name (Tailoring, etc.) |
| `questID` | number | Quest ID for quest-source items |
| `npcID` | number | NPC ID (vendor, quest giver) |
| `npcX`, `npcY` | number | Coordinates (0-100 scale) |
| `faction` | string | "alliance", "horde", or "" |
| `isAllowedIndoors` | boolean | Placement restriction |
| `isAllowedOutdoors` | boolean | Placement restriction |
| `size` | number | Decoration size |
| `expansion` | string | Derived expansion name |

### Step 4b: Enrich Quest Chains

```bash
python enrich_quest_chains.py
```

Extracts quest prerequisite chains for all quest-source decorations:
- Reads `data/enriched_catalog.json` to find 181 quest-source items
- Fetches each quest's Wowhead page, parsing Series (`<table class="series">`)
  and Storyline (`<div class="quick-facts-storyline-list">`) sections
- Builds full chains using storyline data: each quest in a storyline becomes a
  prereq of the next, giving complete chains even when recursive prereq fetches
  fail (Wowhead returns 403 for many older/restricted quest pages)
- Results cached in `data/wowhead_cache/` alongside catalog cache

Output: `data/quest_chains.json` with metadata (1,827 quests, 52 storylines,
chains up to 138 quests deep).

### Step 4c: Enrich Quest Givers

```bash
python enrich_quest_givers.py
```

Extracts quest-giver NPC data for every quest in the chain graph:
- Reads `data/quest_chains.json` for the list of all quest IDs
- Fetches each quest's Wowhead page and parses the `Mapper` JavaScript data:
  ```javascript
  new Mapper({"objectives":{"ZONE_ID":{"levels":[[
    {"point":"start","name":"NPC Name","coord":[x,y],"id":NPC_ID,...}
  ]]}}});
  ```
- Extracts the `"point":"start"` entry for the quest-giver NPC
- Falls back to WH.markup `Start: [url=/npc=ID]` if Mapper data unavailable
- Resolves Wowhead zone IDs to zone names using the enriched catalog mapping
- Results cached in `data/wowhead_cache/` (prefix: `quest_giver_`)

Output: `data/quest_givers.json` — maps questID to `{npcId, npcName, x, y,
zoneName}`. First run takes ~23 min (1,827 quests × 0.75s), subsequent runs
use cache.

### Step 4d: Generate Quest Chain Lua

```bash
python output_quest_chains_lua.py
```

Reads `data/quest_chains.json` + `data/quest_givers.json` and generates
`Data/QuestChains.lua` with:

- **`NS.QuestChains`** -- dictionary keyed by questID with `{name, prereqs, isDecorQuest, storyline, giverName, giverID, giverX, giverY, giverZone}`
- **`NS.QuestSuccessors`** -- reverse lookup: questID → list of quests that require it
- **`NS.DecorQuestIDs`** -- set table `[questID] = true` for quick membership test

The addon uses this data to:
1. Walk the prerequisite chain backwards from a decor reward quest to the root
2. Check `C_QuestLog.IsQuestFlaggedCompleted()` for each quest in the chain
3. Display a color-coded chain visualization (green=done, yellow=next, grey=locked)
4. Navigate to the quest-giver NPC of the first incomplete quest in the chain

### Step 4e: Scrape WoWDB Theme Data

```bash
python scrape_wowdb.py --all
```

Scrapes housing.wowdb.com for community-curated set data and per-item
culture/style/class/theme tags. Uses a local cache in `data/wowdb_cache/` —
cached pages are not re-fetched unless `--no-cache` is passed.

Output: `data/wowdb_sets.json` (~500 community sets with likes data) and
`data/wowdb_item_tags.json` (per-item tags for ~1,667 items).

### Step 4f: Compute Theme Scores

```bash
python compute_item_themes.py
```

Computes aesthetic and culture theme assignments using a three-source scoring
system (per-item tags, community set voting, item name regex patterns).
Reads `data/enriched_catalog.json`, `data/wowdb_sets.json`, and
`data/wowdb_item_tags.json`. WoWDB files are optional — the script continues
with reduced scoring if they are missing.

Output: `data/item_themes.json` (~1,200 themed items with scores).

### Step 4g: Enrich with Extra Wowhead Data

```bash
python enrich_wowhead_extra.py
```

One-pass Wowhead scan per item for optional additional data: alternative
sources, drop rates, profession skill requirements, and vendor buy costs.
Results cached in `data/wowhead_cache/`.

Output: `data/enriched_catalog_extra.json`.

### Step 5: Deploy

Copy the addon folders to WoW:
```bash
# From repo root:
./deploy.sh
# Or run the pipeline with --deploy:
python Tools/scraper/run_pipeline.py --deploy
```

Then `/reload` in WoW.

---

## Runtime Features (Addon-Side)

### Ownership Cache

The addon builds an ownership cache at runtime using:
```lua
C_HousingCatalog.GetCatalogEntryInfoByRecordID(1, decorID, true)
```
Returns: `quantity` (stored), `numPlaced`, `remainingRedeemable`.

### Zone Name → uiMapID Resolution

The addon resolves zone names to Blizzard uiMapIDs **at runtime** using
HereBeDragons-2.0 (bundled in `Libs/`). This means:
- **No static mapID table to maintain** -- HBD handles it automatically
- The `npcMapID` field from Wowhead enrichment is NOT used by the addon
- When HBD is updated for a new patch, zone resolution updates automatically

### Boss Encounter Lookup

For Drop items, the addon uses a two-tier lookup for dungeon floor maps:

1. **Pipeline data** (`NS.CatalogData.BossFloorMaps`): Pre-computed boss name
   → floor mapID mappings generated by `output_catalog_lua.py` from
   `data/boss_dump.json`. Includes aliases for instance names (e.g.,
   "deadmines" maps to the same floor as "vanessa vancleef").
2. **EJ fallback**: For bosses not in the baked table, the addon queries the
   Encounter Journal API at runtime:
   ```lua
   EJ_GetNumTiers() → EJ_SelectTier() → EJ_GetInstanceByIndex()
   → EJ_SelectInstance() → EJ_GetEncounterInfoByIndex()
   ```

This enables:
- **Correct dungeon floor navigation**: Right-clicking a Drop item opens the
  map to the exact floor where the boss is located (not just the dungeon
  entrance), using `C_Map.GetMapChildrenInfo()` +
  `C_EncounterJournal.GetEncountersOnMap()`.
- **Achievement panel opening**: Right-clicking Achievement/Prey items opens
  the achievement panel to the correct achievement.

### Quest Chain Visualization

For Quest-source items, the detail panel shows the full prerequisite chain with
color-coded status:
- **Green** (with checkmark): quest completed by the player
- **Yellow** (with `>`): next quest to do
- **Grey**: locked (requires earlier quests)
- **Orange `*`**: the decor reward quest in the chain

The waypoint button adapts: "Navigate (Next: questName)" points to the first
incomplete quest's NPC, or "Chain Complete — Navigate" when all are done.

Chain data comes from `NS.QuestChains` (loaded from `Data/QuestChains.lua`).
The addon walks prereqs backwards to build an ordered list, then checks each
with `C_QuestLog.IsQuestFlaggedCompleted()`.

### Theme Data (Aesthetic & Culture Filters)

The addon supports filtering decorations by theme — aesthetic styles and
cultural origins (Elven, Orcish, Human, Troll, etc.).

**Theme groups:**
- **Culture** (24 themes): Elven, Human, Orcish, Troll, Dwarven, Gnomish,
  Tauren, Undead, Draenei, Goblin, Pandaren, Vulpera, Dracthyr, Earthen,
  Haranir, Vrykul, and sub-races
- **Aesthetic** (15 themes): Arcane Sanctum, Cottage Hearth, Enchanted Grove,
  Feast Hall, Fel Forge, Haunted Manor, Royal Court, Sacred Temple,
  Scholar's Archive, Seafarer's Haven, Tinker's Workshop, Void Rift,
  War Room, Wild Frontier, Wild Garden

**Culture themes** use a three-source algorithm in `compute_item_themes.py`:
1. Per-item tags (weight 3.0) from housing.wowdb.com
2. Community set voting (weight = log(likes+1)) from WoWDB
3. Item name patterns (weight 1.0) — regex fallback

**Aesthetic themes** use visual classification from item thumbnail analysis.
See [VISUAL_CLASSIFICATION.md](VISUAL_CLASSIFICATION.md) for the full
process, category definitions, and instructions for classifying new items.

Override priority: manual annotations > visual classifications > algorithm.

**Pipeline flow:**
```
scrape_wowdb.py --all → wowdb_sets.json + wowdb_item_tags.json
compute_item_themes.py → item_themes.json (culture from algorithm, aesthetics from visual)
output_catalog_lua.py → reads item_themes.json, adds _themeIDs and _themeScores per item
```

All three stages are integrated into `run_pipeline.py` and run automatically.

### Dynamic Filter Counts

Sidebar filter counts are recomputed after every filter change. For each
dimension (Source, Expansion, Quality, Profession, Collection), the count
shows how many items pass all OTHER active filters. This enables intuitive
combo filtering (e.g., checking "Classic" expansion updates profession counts
to show only Classic profession items).

---

## New Content Update Checklist

When Blizzard releases new content (dungeons, raids, zones, decorations),
follow these steps to update all HearthAndSeek data.

### Phase 1: In-Game Data Collection

1. **Enable dev mode**: Set `NS.DEV_MODE = true` in `Core/Constants.lua`
2. **Check scan range**: Verify `MAX_DECOR_ID` in `Modules/CatalogDumper.lua`
   is high enough — new patches may add IDs beyond 20000, bump if needed
3. **Deploy** to WoW and `/reload`
4. **Catalog dump**: `/hs dump catalog` → wait for "Catalog dump complete" → `/reload`
5. **Boss dump**: `/hs dump bosses` → wait for "Boss dump complete" → `/reload`
6. **Disable dev mode**: Set `NS.DEV_MODE = false` in `Core/Constants.lua`

### Phase 2: Run the Pipeline

```bash
cd Tools/scraper
python run_pipeline.py --deploy
```

The enrichment scripts use caching — only NEW items trigger Wowhead lookups.
Existing cache in `data/wowhead_cache/` and `data/wowdb_cache/` is reused
automatically. Theme data (WoWDB scraping + theme computation) and extra
Wowhead enrichment (drop rates, vendor costs) are now included in the
pipeline and run automatically.

### Phase 3: Validate Boss Data

```bash
python parse_boss_dump.py --validate
```

Check output for:
- **Matches**: Confirms pipeline data matches previously known floor maps
- **Mismatches**: Investigate — may indicate EJ API changes or data errors
- **New entries**: Bosses from new dungeons/raids not previously in our data

### Phase 4: Check for Warnings and Fix Manually

**Zone warnings**: `output_catalog_lua.py` will log unmapped zones:
```
WARNING: Unmapped zone (mapped to Unknown): New Zone Name
```
Fix: add entries to `ZONE_TO_EXPANSION` and `ZONE_TO_CONTINENT` in
`output_catalog_lua.py`, then re-run:
```bash
python run_pipeline.py --generate-only --deploy
```

**Drop fixups**: Check for Drop items with missing boss names or zones.
Fix: add entries to `DROP_FIXUPS` or `DROP_VALUE_TO_ZONE` in
`output_catalog_lua.py`.

**Dungeon entrances**: New dungeons need `DUNGEON_ENTRANCES` entries in
`output_catalog_lua.py` (zone name + coordinates for the dungeon portal).

**New expansion**: Add to `EXPANSION_ORDER` in `output_catalog_lua.py`
and map its zones in `ZONE_TO_EXPANSION`.

### Phase 5: Classify New Items Visually

New decorations need aesthetic theme assignments via visual classification.
See [VISUAL_CLASSIFICATION.md](VISUAL_CLASSIFICATION.md) for the full
process. Quick summary:

```bash
python download_thumbnails.py                    # Download new thumbnails
python build_montages.py --unthemed --cols 4 --rows 4  # Build montage grids
# Feed montages to AI classifier (see VISUAL_CLASSIFICATION.md Step 4)
# Merge results into data/montages/visual_classifications.json
python build_validation_page.py --unclear        # Review uncertain items
python run_pipeline.py --generate-only --deploy  # Regenerate with new themes
```

### Phase 6: Test In-Game

1. Deploy: `python run_pipeline.py --generate-only --deploy`
2. `/reload` in WoW
3. `/hs` → verify:
   - New decorations appear in the catalog
   - Drop items show correct boss names and dungeon floor maps
   - Quest items have correct quest chains
   - Waypoints navigate to the right locations

### Nuclear Option (Stale Cache)

If you suspect cached Wowhead data is wrong or outdated:
```bash
python run_pipeline.py --clear-cache --deploy  # Delete cache, re-fetch everything
```
This re-fetches ALL Wowhead pages (rate-limited, may take 30-60 minutes).

### Quick Reference Table

| What to check | Where | When |
|---------------|-------|------|
| `NS.DEV_MODE` | `Core/Constants.lua` | Enable before dumps, disable after |
| `MAX_DECOR_ID` | `Modules/CatalogDumper.lua:16` | If new decor IDs > 20000 |
| `ZONE_TO_EXPANSION` | `output_catalog_lua.py` | If new zones added |
| `ZONE_TO_CONTINENT` | `output_catalog_lua.py` | If new zones added |
| `EXPANSION_ORDER` | `output_catalog_lua.py` | If new expansion released |
| `DROP_FIXUPS` | `output_catalog_lua.py` | If new dungeons have bad Wowhead data |
| `DROP_NPC_IDS` | `output_catalog_lua.py` | If new boss NPCs need explicit IDs |
| `DUNGEON_ENTRANCES` | `output_catalog_lua.py` | If new dungeons added |
| `HereBeDragons-2.0` | `Libs/` | If new zones need waypoint support |
| `visual_classifications.json` | `data/montages/` | New items need visual aesthetic classification |
| `manual_theme_annotations.json` | `data/` | Human overrides for ambiguous items |
| `AESTHETIC_THEMES` | `compute_item_themes.py` | If new aesthetic categories added |
| `themeColors` | `UI/CatalogFrame.lua` | Must match `AESTHETIC_THEMES` |

---

## File Reference

### Scripts

| Script | Input | Output |
|--------|-------|--------|
| `parse_catalog_dump.py` | WTF SavedVariables | `data/catalog_dump.json` |
| `parse_boss_dump.py` | WTF SavedVariables | `data/boss_dump.json` |
| `enrich_catalog.py` | `data/catalog_dump.json` | `data/enriched_catalog.json`, `data/enrichment_lookups.json` |
| `enrich_quest_chains.py` | `data/enriched_catalog.json` | `data/quest_chains.json` |
| `enrich_quest_givers.py` | `data/quest_chains.json` | `data/quest_givers.json` |
| `scrape_wowdb.py` | housing.wowdb.com | `data/wowdb_sets.json`, `data/wowdb_item_tags.json` |
| `compute_item_themes.py` | `data/enriched_catalog.json` + WoWDB data | `data/item_themes.json` |
| `enrich_wowhead_extra.py` | `data/enriched_catalog.json` | `data/enriched_catalog_extra.json` |
| `output_catalog_lua.py` | `data/enriched_catalog.json` + optional enrichments | `../../Data/CatalogData.lua` |
| `output_quest_chains_lua.py` | `data/quest_chains.json` + `data/quest_givers.json` | `../../Data/QuestChains.lua` |
| `output_lua.py` | (shared utility) | Lua serialization helpers |
| `download_thumbnails.py` | `data/wowdb_cache/` | `data/thumbnails/` (256px PNGs) |
| `build_montages.py` | `data/thumbnails/` + `data/item_themes.json` | `data/montages/*.png` |
| `build_validation_page.py` | `data/montages/visual_classifications.json` | `data/validation_review.html` |

### Data Files

| File | Description |
|------|-------------|
| `data/catalog_dump.json` | Raw parsed dump (1,667 items) |
| `data/boss_dump.json` | Boss name → dungeon floor mapID (~500-800 entries) |
| `data/enriched_catalog.json` | Enriched with IDs, coords, factions |
| `data/enrichment_lookups.json` | Reusable lookup tables from enrichment |
| `data/quest_chains.json` | Quest chain data (1,827 quests, 52 storylines) |
| `data/quest_givers.json` | Quest-giver NPC data (coordinates per quest) |
| `data/wowdb_sets.json` | WoWDB community set data (~500 sets) |
| `data/wowdb_item_tags.json` | Per-item culture/style/class/theme tags |
| `data/item_themes.json` | Computed theme assignments & scores (~1,200 items) |
| `data/enriched_catalog_extra.json` | Drop rates, vendor costs, profession skills |
| `data/wowhead_cache/` | Cached Wowhead API responses |
| `data/wowdb_cache/` | Cached WoWDB HTML responses |
| `data/montages/visual_classifications.json` | Visual aesthetic classifications (1,659 items) |
| `data/manual_theme_annotations.json` | Human-reviewed aesthetic overrides (~50 items) |
| `data/thumbnails/` | Cached WoWDB item thumbnails (gitignored, re-downloadable) |
| `../../Data/CatalogData.lua` | Generated Lua data for the addon (~2.1MB) |
| `../../Data/QuestChains.lua` | Generated quest chain data (~548KB) |

### Key Constants

| Constant | Location | Purpose |
|----------|----------|---------|
| `MAX_DECOR_ID` | `Modules/CatalogDumper.lua:16` | Upper bound of decorID scan range |
| `ZONE_TO_EXPANSION` | `output_catalog_lua.py` | Maps zone names to expansion names (~115 entries) |
| `SOURCE_PRIORITY` | `output_catalog_lua.py:38` | Source type derivation order |
| `DROP_FIXUPS` | `output_catalog_lua.py` | Manual corrections for incomplete Drop data |
| `DROP_VALUE_TO_ZONE` | `output_catalog_lua.py` | Maps Drop source values to zone names |
| `PROFESSION_NAMES` | `output_catalog_lua.py:42` | Known profession names for parsing |
| `TAG_TO_THEME` | `compute_item_themes.py` | WoWDB tag → theme ID mappings (~56 entries) |
| `CULTURE_THEMES` | `compute_item_themes.py` | 24 race/culture theme definitions |
| `AESTHETIC_THEMES` | `compute_item_themes.py` | 15 aesthetic style theme definitions |
| `NAME_PATTERNS` | `compute_item_themes.py` | 50+ regex fallback patterns for theme scoring |

---

## Data Sources & APIs

### In-Game APIs (Addon Runtime)

| API | Purpose |
|-----|---------|
| `C_HousingCatalog.GetCatalogEntryInfoByRecordID(1, decorID, true)` | Item ownership, storage, costs |
| `HereBeDragons-2.0:GetAllMapIDs()` / `GetLocalizedMap(id)` | Zone name → uiMapID resolution |
| `EJ_GetEncounterInfoByIndex()` / `C_EncounterJournal.GetEncountersOnMap()` | Boss encounter → dungeon floor |
| `C_Map.GetMapChildrenInfo(mapID)` | Dungeon floor hierarchy |
| `GetAchievementInfo()` / `GetCategoryList()` | Achievement name → ID lookup |
| `C_QuestLog.IsQuestFlaggedCompleted(questID)` | Quest completion status |

### External Data Sources (Pipeline)

| Source | URL Pattern | Purpose |
|--------|-------------|---------|
| Wowhead Gatherer | `wowhead.com/gatherer/type/201/{decorID}` | Decoration metadata |
| Wowhead Quest | `wowhead.com/quest={questID}` | Quest data, prerequisites, quest-giver NPC |
| Wowhead NPC | `wowhead.com/npc={npcID}` | NPC coordinates, faction |
| Wowhead Item | `wowhead.com/item={itemID}` | Drop rates, vendor costs, profession skills |
| WoWDB Housing | `housing.wowdb.com` | Community sets, per-item theme tags |

---

## Deployment Note

`Tools/` is excluded from the WoW client deploy. The `CatalogDumper.lua`
module ships with the addon (lives in `Modules/`), but the dump data itself
(`HearthAndSeekDB.catalogDump`) stays in WTF SavedVariables.

Intermediate JSON files under `Tools/scraper/data/` are gitignored.

## Requirements

```bash
pip install -r requirements.txt   # requests, beautifulsoup4, lxml, Pillow
```

| Script | Dependencies |
|--------|-------------|
| `parse_catalog_dump.py` | Python stdlib only |
| `parse_boss_dump.py` | Python stdlib only |
| `enrich_catalog.py` | `requests` |
| `enrich_quest_chains.py` | `requests` |
| `enrich_quest_givers.py` | `requests` |
| `scrape_wowdb.py` | `requests`, `beautifulsoup4`, `lxml` |
| `compute_item_themes.py` | Python stdlib only |
| `enrich_wowhead_extra.py` | `requests` |
| `download_thumbnails.py` | `Pillow` |
| `build_montages.py` | `Pillow` |
| `build_validation_page.py` | Python stdlib only |
| `output_catalog_lua.py` | Python stdlib + `output_lua.py` |
| `output_quest_chains_lua.py` | Python stdlib + `output_lua.py` |
| `run_pipeline.py` | Python stdlib only (orchestrates the others) |

---

## Known Limitations & Risks

### Wowhead HTML Scraping

All enrichment data comes from scraping Wowhead HTML pages via regex. This is
inherently fragile:
- **If Wowhead changes page structure**, the regex patterns will break silently
- **403/404 errors** for some quest pages (especially old content) — these
  quests get placeholder names like "Quest #12345"
- **Rate limiting**: Scripts pause 0.75s between requests and back off 30s on
  HTTP 429. Running too aggressively can get your IP temporarily blocked.

### Cache

- Wowhead responses are cached indefinitely in `data/wowhead_cache/`
- **Cache never expires** — if Wowhead corrects data, our cache keeps the old
  version
- Use `--clear-cache` or manually delete `data/wowhead_cache/` to force refresh
- Cache is shared between `enrich_catalog.py` and `enrich_quest_chains.py`

### Hardcoded Mappings

Several mappings are maintained manually and can become stale:
- `ZONE_TO_EXPANSION` (~300 entries) — new zones default to "Unknown"
- `DROP_FIXUPS` — manually corrects Wowhead data for specific dungeons
- `MAX_DECOR_ID` — upper bound of the in-game scan range

### Coordinate Data

- **Vendor/NPC coordinates** come from Wowhead tooltip API and HTML scraping
  (~50% of Quest+Vendor, ~80% of Achievement+Vendor items have coords)
- **Quest-giver coordinates** come from the Mapper JavaScript embedded in
  Wowhead quest pages — each quest can have its own start NPC location
- Coordinates are on a 0-100 scale (Wowhead format)
- Zone names are resolved to Blizzard uiMapIDs at addon runtime via
  HereBeDragons-2.0 (no static mapping needed)

### Quest Chain Data

- Quest prerequisite chains are built by walking backwards from decor reward
  quests
- **Branching chains** (multiple prereqs) currently follow only the first
  prereq in the linear chain view
- Some quest names come from backfilling series/storyline data when direct
  Wowhead fetches fail (10/46 placeholder names were resolved this way)
- Chain depth is capped at 20 by default (`--max-depth` flag)
