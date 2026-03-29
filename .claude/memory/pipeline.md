# DecorDrive Data Pipeline Details

## Master Script
`python run_pipeline.py --deploy` runs the full pipeline + deploys to WoW.
Flags: `--skip-enrich`, `--generate-only`, `--force`, `--clear-cache`, `--from STAGE`

## Full Pipeline Order
1. In-game `/dd dump` → `/reload` → SavedVariables flushed
2. `parse_catalog_dump.py` → strips WoW formatting, outputs catalog_dump.json
3. `enrich_catalog.py` → Wowhead cross-ref (quest/NPC IDs, coords, factions)
4. `enrich_quest_chains.py` → Wowhead quest chain scraping (prereqs + storylines)
5. `enrich_quest_givers.py` → Wowhead Mapper data → quest-giver NPC coords
6. `output_catalog_lua.py` → generates Data/CatalogData.lua
7. `output_quest_chains_lua.py` → generates Data/QuestChains.lua

## Source Type Priority
Quest > Achievement > Prey > Profession > Drop > Treasure > Vendor > Other

## Enrichment Caching
- `data/wowhead_cache/` stores per-NPC and per-quest Wowhead responses
- Cache never expires — use `--clear-cache` to force refresh
- Rate limited: 0.75s between requests, 30s backoff on HTTP 429
- Quest name backfill: series/storyline data fills in names for 403'd pages

## Generated Lua Structure
- `NS.CatalogData.Items[decorID]` - 21 fields per item
- `NS.CatalogData.BySource/ByExpansion/ByProfession` - index tables
- `NS.CatalogData.NameIndex` - sorted {decorID, lowercaseName} pairs
- `NS.QuestChains[questID]` - {name, prereqs, isDecorQuest, storyline, giverName, giverID, giverX, giverY, giverZone}
- `NS.QuestSuccessors[questID]` - reverse lookup
- `NS.DecorQuestIDs[questID]` = true - set table

## Hardcoded Values (Patch Maintenance)
| What | Where | When to update |
|------|-------|---------------|
| `MAX_DECOR_ID` | `Modules/CatalogDumper.lua:16` | If new IDs > 20000 |
| `ZONE_TO_EXPANSION` | `output_catalog_lua.py` (~300 entries) | New zones |
| `EXPANSION_ORDER` | `output_catalog_lua.py` | New expansion |
| `DROP_FIXUPS` | `output_catalog_lua.py` | New dungeons with bad data |
| `PROFESSION_NAMES` | `output_catalog_lua.py` | New professions |
| `_WH_ZONE_FALLBACKS` | `enrich_quest_givers.py` | New zones not in catalog |

## Known Limitations
- Wowhead HTML scraping is fragile (regex-based, breaks on structure changes)
- ~50% Quest+Vendor and ~80% Achievement+Vendor items have vendor NPC coordinates
- Quest-giver coords from Mapper data: 1,727/1,827 quests (95%), 177/181 decor quests (98%)
- 36/46 placeholder quest names remain unresolved (deep prereqs with no refs)
- Branching quest chains follow only first prereq in linear view
- Windows-only path in parse_catalog_dump.py (hardcoded D:\Games\...)
