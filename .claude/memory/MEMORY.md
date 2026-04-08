# Hearth & Seek: Decor Catalog — Project Memory

## Project Overview
- WoW addon: RestedXP-style guide for collecting Player Housing decorations
- Display name: "Hearth & Seek: Decor Catalog" (renamed from DecorDrive)
- Addon folder name: `HearthAndSeek` (TOC: `HearthAndSeek.toc`)
- SavedVariables: `HearthAndSeekDB`, frame names: `HearthAndSeek*` (fully renamed from DecorDrive)
- Repo: `d:\Programming\WoWAddons\HearthAndSeek`
- WoW retail addons: `D:\Games\World of Warcraft\_retail_\Interface\AddOns\`
- Interface version: 120001 (Midnight 12.0)
- Slash commands: `/hs` (primary), `/hseek`, `/hearthandseek`
- DEV_MODE: gates `/hs dump` and `/hs debug` commands (false for releases)
- License: MIT (Copyright 2025-2026 ImpalerV)
- CurseForge slug: `hearth-and-seek`
- GitHub: `ImpalerV/HearthAndSeek`

## Critical API
- `C_HousingCatalog.GetCatalogEntryInfoByRecordID(1, decorID, true)` — entryType MUST be `1`
- decorID = Wowhead Gatherer Type 201 IDs; MAX_DECOR_ID = 20000
- Ownership: `quantity > 0 or numPlaced > 0 or remainingRedeemable > 0`
- **In Storage** = `quantity + remainingRedeemable` (matches Blizzard UI formula)
- `quantity` = fully instantiated items; `remainingRedeemable` = lazily-instantiated
- `firstAcquisitionBonus == 0` = alternative ownership check
- Fields: `quantity`, `numPlaced`, `remainingRedeemable`, `placementCost`, `firstAcquisitionBonus`

## Data Pipeline (see [pipeline.md](pipeline.md))
1. In-game `/hseek dump catalog` (DEV_MODE) → `/reload` → SavedVariables
2. `parse_catalog_dump.py` → `data/catalog_dump.json` (1,667 items)
   - `--merge`: auto-stamps new items into `data/item_versions.json` (prompts for WoW patch)
3. `enrich_catalog.py` → `data/enriched_catalog.json` (Wowhead cross-ref)
4. `output_catalog_lua.py` → `Data/CatalogData.lua` (~1.1MB)
   - Loads `data/item_versions.json` → emits `patchAdded` + `dateAdded` per item
- Source priority: Quest > Achievement > Prey > Profession > Drop > Treasure > Vendor
- DROP_FIXUPS table for items with incomplete Wowhead data
- Item versioning: `data/item_versions.json` tracks patch + date each item was added
- Full docs: `Tools/scraper/README.md`

## Zone → uiMapID
- Pipeline-emitted via `ZONE_TO_MAPID` dict in `output_catalog_lua.py`
- Runtime: simple table lookup in `NS.CatalogData.ZoneToMapID` (no HereBeDragons)
- `ResolveNavigableMap()` walks up parent chain (up to 4 levels) to find Continent → handles nested zones like Valdrakken→Thaldraszus→Dragon Isles
- `VENDOR_COORDS` dict in output_catalog_lua.py: curated vendor locations (~180 entries). Overrides npcID, coords, and zone when Wowhead data is wrong/missing.
- `WH_ZONE_ID_TO_NAME` in `enrich_catalog.py` maps Wowhead zone IDs → zone names (86 entries)
- Zone-aware NPC matching: when multiple NPCs share a name, picks the one in the item's zone
- Coordinate safety guard: `coordsMismatch` flag clears coords when NPC is in a different zone
- Sold-by fallback (Phase 4b): recovers coords from item "Sold by" pages when NPC page has none

## UI Architecture
- BackdropTemplate, XML templates, CallbackHandler events
- Catalog: CatalogFrame.lua (shell+sidebar), CatalogGrid.lua (grid+filtering), CatalogDetail.lua (3D+waypoint)
- Dynamic filter counts: single-pass computes filtered items + per-dimension counts
- Collection state filter: Collected/Not Collected/Redeemable (all checked by default)
- Boss encounter lookup: EJ_GetEncounterInfoByIndex → C_EncounterJournal.GetEncountersOnMap → correct floor map
- Tooltip hints: Ctrl+Click preview, Right-Click achievement, Ctrl+Right-Click map

## Lua 5.1 Compatibility
- WoW uses LuaJIT but linter targets Lua 5.1
- Use `repeat...break...until true` instead of `goto continue` for continue pattern
- WoW API globals (CreateFrame, GameTooltip, C_Map, etc.) trigger linter warnings — expected

## Deploy
```bash
bash d:/Programming/WoWAddons/HearthAndSeek/scripts/deploy.sh
```
- Config-driven: reads paths from `deploy.config.json` or `dev.config.json` (both gitignored)
- Deploy script at `scripts/deploy.sh`
- Pipeline: `python Tools/scraper/run_pipeline.py --deploy`
- **IMPORTANT**: Target folder is `HearthAndSeek/` NOT `HideAndSeek/`. A stale `HideAndSeek/` folder exists — ignore it.

## Repo Structure (post-cleanup)
- `Docs/archive/` — archived design docs (DecorDrive-era)
- `Changelogs/` — version changelogs (v1.0.0.txt)
- `scripts/` — deploy.sh, deploy.ps1
- `Project_Images/` — logo JPEG + Screenshots/
- `dev.config.example.json` + `deploy.config.example.json` — template configs

## User Preferences
- Prefers parallel agent execution for complex tasks
- Wants collaborative discussion on high-level decisions
- Doesn't want to depend on other addon databases

## WoW Patch Version
- [Current patch and ask-before-assuming rule](project_wow_patch.md)

## Release
- [Release zips go in dist/, never /tmp](feedback_release_zip_location.md)

## Data Safety
- [CRITICAL: Never overwrite catalog database, always --merge, always backup](feedback_data_safety.md)

## Git / Commit Rules
- **Author**: All commits must be authored by `impalervi <impalervv@gmail.com>`
- **NO Claude attribution**: Never include `Co-Authored-By: Claude` or any Claude mention in commits
- Set author explicitly: `--author="impalervi <impalervv@gmail.com>"`
- [Do not merge to main without explicit instruction](feedback_no_merge_main.md)
