# Scan & Update Decor Items

Guided workflow for discovering newly added decor items in WoW and updating the HearthAndSeek catalog.

## Skill Definition

- name: scan-decor
- description: Scan for new in-game decor items and run the data pipeline to update the catalog
- user_invocable: true

## Instructions

This skill walks through the full new-item discovery and cataloging workflow. Follow each phase in order, pausing for user action where noted.

### Phase 1: Prepare for In-Game Dump

1. Start from `main`. Determine the next minor version by bumping the patch
   component of the current `## Version:` line in `HearthAndSeek.toc` (e.g.
   `1.5.2` → `1.5.3`). Then:
   - Create a release branch off `main`: `release/v<next>` (e.g.
     `release/v1.5.3`).
   - Off that release branch, create a feature branch named
     `feature/update-item-database-<YYYY-MM-DD>` using today's date.
   - Do NOT bump the version in the TOC or `Core/Constants.lua` yet; that
     happens later, only if new items are actually found and merged.
2. Enable DEV_MODE in `Core/Constants.lua`:
   ```lua
   NS.DEV_MODE = true
   ```
3. Clean deploy to the game folder using `deploy.config.json` includes:
   - `HearthAndSeek.toc`, `Core`, `Data`, `Modules`, `UI`, `Libs`, `Media`
   - Target: `D:/Games/World of Warcraft/_retail_/Interface/AddOns/HearthAndSeek/`
4. Tell the user to do the following in-game:
   - `/reload` to pick up the deploy
   - `/hs dump new` to scan for new items (scans decorIDs 1-40000, skips known items)
   - Wait for "Incremental dump complete: X new items found" message
   - `/reload` again to flush SavedVariables to disk
5. Wait for user confirmation before proceeding.

### Phase 2: Back Up the Database

**CRITICAL: Always create a backup before any data modification.**

1. Back up all pipeline data files:
   ```bash
   cd d:/Programming/WoWAddons/HearthAndSeek/Tools/scraper/data
   tar -czf backups/data_files_$(date +%Y%m%d_%H%M%S).tar.gz \
       catalog_dump.json enriched_catalog.json quest_chains.json \
       quest_givers.json item_themes.json item_versions.json \
       enriched_catalog_extra.json wowdb_item_tags.json 2>/dev/null
   ```
2. Verify the backup was created:
   ```bash
   ls -lh backups/data_files_*.tar.gz | tail -1
   ```
3. Report the backup filename and size to the user.

### Phase 3: Parse and Merge the Dump

**IMPORTANT:** Use `--merge` flag to add new items to the existing catalog, NOT replace it.

1. The SavedVariables file is at: `D:/Games/World of Warcraft/_retail_/WTF/Account/82479387#1/SavedVariables/HearthAndSeek.lua`
2. The parse script prompts for the current WoW patch version interactively. Ask the user what patch version the game is on (e.g., `12.0.1`), then pipe it in:
   ```bash
   cd d:/Programming/WoWAddons/HearthAndSeek/Tools/scraper
   echo "<patch_version>" | python parse_catalog_dump.py --merge
   ```
3. Verify the merge result:
   - The output should show "Merged N new items into M existing (TOTAL total)"
   - TOTAL should equal the previous catalog count + N new items
   - If the count is wrong, restore from the backup and investigate
4. Cross-check against `enriched_catalog.json` to ensure no items were lost:
   ```python
   python -c "
   import json
   enriched = json.load(open('data/enriched_catalog.json'))
   dump = json.load(open('data/catalog_dump.json'))
   e_ids = {i['decorID'] for i in enriched}
   d_ids = {i['decorID'] for i in dump}
   missing = e_ids - d_ids
   if missing:
       print(f'WARNING: {len(missing)} items in enriched but not in dump: {sorted(missing)}')
   else:
       print(f'OK: dump ({len(d_ids)}) contains all enriched items ({len(e_ids)}) + {len(d_ids - e_ids)} new')
   "
   ```

**NEVER run `parse_catalog_dump.py` without `--merge` when doing incremental scans.** Running without `--merge` replaces the entire catalog_dump.json with only the new items from SavedVariables.

### Phase 4: Run the Pipeline

Recommend **full pipeline starting from enrich_catalog** for new items since they need Wowhead enrichment. The cache handles previously-fetched data, so only new items trigger network requests.

**IMPORTANT:** Since Phase 3 already parsed and merged the dump, always start from `enrich_catalog` to avoid the pipeline's `parse_catalog_dump` stage overwriting `catalog_dump.json` without `--merge`.

```bash
python run_pipeline.py --from enrich_catalog --deploy
```

Other modes (only use if user requests):
- **Skip enrichment** (use cached Wowhead data): `python run_pipeline.py --from output_catalog_lua --deploy`
- **Resume from a failed stage**: `python run_pipeline.py --from <stage_name> --deploy`

Pipeline stages in order:
1. `parse_catalog_dump` - Parse SavedVariables dump
2. `parse_boss_dump` - Parse boss floor map dump
3. `enrich_catalog` - Cross-reference with Wowhead
4. `enrich_quest_chains` - Build quest prerequisite chains
5. `cleanup_quest_chains` - Clean up prereqs
6. `enrich_quest_givers` - Extract NPC coordinates
7. `scrape_wowdb` - Scrape WoWDB community tags
8. `compute_item_themes` - Compute theme scores
9. `enrich_wowhead_extra` - Drop rates, vendor costs, profession skills
10. `output_catalog_lua` - Generate `Data/CatalogData.lua`
11. `output_quest_chains_lua` - Generate `Data/QuestChains.lua`

### Phase 5: Theme Assignment (if needed)

#### 5a: Culture Themes
Check for items missing culture themes:
```bash
python categorize_new_items.py
```

If there are unthemed items, inform the user they can annotate interactively:
```bash
python categorize_new_items.py --annotate
```

#### 5b: Aesthetic Themes
New items are NOT in `visual_classifications.json` (that file comes from montage grid visual review of existing items). Instead, use `classify_new_aesthetics.py` which only processes items not already classified:

1. List items needing aesthetic classification:
```bash
python classify_new_aesthetics.py
```

2. Auto-classify high-confidence items first (confidence >= 0.8):
```bash
python classify_new_aesthetics.py --auto
```

3. If items remain at low confidence, classify interactively (shows suggestions, user picks):
```bash
python classify_new_aesthetics.py --classify
```

Results are saved to `data/manual_theme_annotations.json`. This file is additive — it never touches existing annotations or visual_classifications.json.

#### 5c: Regenerate
After all theme assignment, recompute themes and regenerate the Lua data:
```bash
python compute_item_themes.py
python output_catalog_lua.py
```

### Phase 6: Create Post-Update Backup

**After a successful pipeline run, create a fresh backup of the updated data:**

```bash
cd d:/Programming/WoWAddons/HearthAndSeek/Tools/scraper/data
tar -czf backups/data_files_$(date +%Y%m%d_%H%M%S).tar.gz \
    catalog_dump.json enriched_catalog.json quest_chains.json \
    quest_givers.json item_themes.json item_versions.json \
    enriched_catalog_extra.json wowdb_item_tags.json 2>/dev/null
```

Verify and report the backup filename and size.

### Phase 7: Finalize

1. Disable DEV_MODE in `Core/Constants.lua`:
   ```lua
   NS.DEV_MODE = false
   ```
2. Clean deploy the final result to the game folder.
3. Tell the user to `/reload` in-game to verify the new items appear in the catalog.
4. Report a summary: how many new items were added, any issues encountered.
5. Ask the user if they want to commit the changes.
