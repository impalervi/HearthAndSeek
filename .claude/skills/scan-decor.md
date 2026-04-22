# Scan & Update Decor Items

Guided workflow for discovering newly added decor items in WoW and updating the HearthAndSeek catalog.

## Skill Definition

- name: scan-decor
- description: Scan for new in-game decor items and run the data pipeline to update the catalog
- user_invocable: true

## Instructions

This skill walks through the full new-item discovery and cataloging workflow. Follow each phase in order, pausing for user action where noted.

### Hard rule: NEVER touch the caches

`Tools/scraper/data/wowhead_cache/` and `Tools/scraper/data/wowdb_cache/`
are sacred. Files in those directories represent **hours of rate-limited
scraping** and are the only reliable copy of community data that can
change or disappear upstream. Before doing ANY cache-modifying operation:

1. **Stop.** Tell the user exactly which files you want to delete and why.
2. **Wait for explicit in-conversation agreement** before proceeding.
   "Re-run enrichment" is NOT agreement to touch the cache.
3. **Never** run `rm`, `find -delete`, `os.remove`, or any pattern-based
   deletion over the cache as part of a larger task.
4. **Never assume** that `null`, `[]`, `{}`, or `{"error": ...}` contents
   mean a cache entry is stale. They can be legitimate "no data" results.
5. If the user agrees to a cache change, **run `scripts/backup_cache.sh`
   first** as a safety net.

The authoritative, binding version of these rules (plus the incident
history that motivated them) lives in `docs/CACHES.md` at the repo root.
Read it before any cache-adjacent work. The short `CLAUDE.md` stubs
inside each cache directory just point back there.

On a fresh clone the cache dirs may not exist yet; the pipeline creates
them on first run. If stubs are missing, run
`scripts/init_cache_readmes.sh` to re-create them.

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

**ALWAYS run the pipeline in the background** with output redirected to a log file, and poll the log periodically to show the user progress. The pipeline can take 90 seconds to several minutes depending on how many new items need Wowhead/WoWDB enrichment, and a silent foreground wait looks like the agent has frozen.

Pattern:
```bash
# Start the pipeline in the background, tee'ing output to a log
python run_pipeline.py --from enrich_catalog --deploy > /tmp/pipeline.log 2>&1
# ^ launch via the Bash tool with run_in_background: true

# Then poll /tmp/pipeline.log at sensible intervals using BashOutput,
# surfacing the latest [INFO] STAGE lines to the user as progress updates.
# Do NOT block on a long-running foreground Bash call.
```

Show the user each stage transition as it happens (e.g. "enrich_catalog → enrich_quest_chains"), plus the final PIPELINE SUMMARY when the process exits. If polling shows no progress for >60s, investigate — don't assume it's still making progress.

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

#### Shared-name vendor handling (automatic)

Some NPC names are reused across multiple zones (e.g. "Rae'ana" exists in
both The Waking Shores and Silvermoon City). Without handling, the pipeline
would collapse them to a single NPC and route items to the wrong one.

This is now handled automatically:

- `enrich_catalog.py` collects the set of zones each vendor name appears in
  (counting both primary and alt vendors). When a name appears in more than
  one zone, it resolves a separate NPC per zone via `lookup_npc_full` with a
  zone hint, then writes the full per-zone map to
  `Tools/scraper/data/vendor_zones.json`.
- `output_catalog_lua.py` loads that file at module import and merges it
  into `VENDOR_COORDS_BY_ZONE`. Per-item serialization then calls
  `resolve_vendor_override(name, item_zone)` which prefers the zone-specific
  entry and falls back to the name-only entry in `VENDOR_COORDS`.
- The enrichment log prints each detected shared-name vendor with its
  per-zone npcIDs; surface that summary to the user in the final report
  (Phase 8) so they know which names were split.
- If a shared-name vendor is NOT being auto-detected correctly, add a
  manual entry in `VENDOR_COORDS_BY_ZONE` (inside `output_catalog_lua.py`)
  — manual entries win over auto-loaded ones.

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

### Phase 6b: Manual overrides (when needed)

If the user flags an item whose attribution is wrong (Wowhead got it
wrong, or the in-game dump returned empty `sourceText` and the Wowhead
UI doesn't corroborate the decor-shape JSON), add a manual entry to
`Tools/scraper/data/overrides.json` rather than loosening the
auto-promotion heuristics. The schema is:

```json
"<decorID>": {
  "_note": "one-line description of what's being overridden and why",
  "_reason": "in_game_verified | zone_fixup | achievement_fix | wowhead_wrong",
  "_verified_date": "YYYY-MM-DD",
  "sources": [{"type": "Vendor", "value": "<NPC name>"}],
  "vendor": "<NPC name>",
  "npcID": <npcID>,
  "npcX": <x>, "npcY": <y>,
  "zone": "<zone name>"
}
```

Fields starting with `_` are ignored by the merge; everything else
overwrites the enriched item. After adding entries, re-run
`enrich_catalog` (or just apply the overrides to `enriched_catalog.json`
in-place) and regenerate the Lua with `output_catalog_lua.py`.

See `docs/WOWHEAD_EXTRACTION.md` for the full design of why this is
necessary and when to reach for it.

### Phase 7: Finalize

1. Disable DEV_MODE in `Core/Constants.lua`:
   ```lua
   NS.DEV_MODE = false
   ```
2. Clean deploy the final result to the game folder.
3. Tell the user to `/reload` in-game to verify the new items appear in the catalog.

### Phase 8: Final Report (ALWAYS required)

After all other phases are complete, produce a report to the user containing:

1. **Full list of NEW items added** — print every item's full name
   alongside its `decorID` **and the categories it was labeled with**.
   Do not truncate or summarize into groups. This is the ground-truth
   record of what entered the catalog this run.

   For each item, include:
   - `decorID` and full name
   - **WoW categories** — map `categoryIDs` via `CATEGORY_NAMES` and
     `subcategoryIDs` via `SUBCATEGORY_NAMES` (both live in
     `Tools/scraper/output_catalog_lua.py`). Show as e.g.
     `Lighting → Small Lights`. If there are multiple, join with `, `.
   - **Themes** — for each id in `themeIDs`, look up the name in the
     `Themes` table in `Data/CatalogData.lua` (or the theme groups map
     used by the pipeline). Separate aesthetic vs. culture themes when
     possible, e.g. `Aesthetic: Void Rift · Culture: Void Elf`.
   - If an item has no themes assigned, say `(no themes)` — that's a
     signal the manual annotation step missed it.

   Format as a markdown table or a structured list so the user can scan
   it quickly. Example row:
   `22183  Void Flame Candle — Lighting → Small Lights · Void Rift`

2. **Full list of FILTERED items** — print every item the pipeline dropped,
   with its `decorID`, full name, and the **specific reason** it was
   filtered (e.g. "Blizzard internal [DNT]/[AUTOGEN] placeholder"). Read
   this from `Tools/scraper/data/filtered_items.json` which
   `parse_catalog_dump.py` now writes on every merge. If the file is empty
   or missing, say "No items were filtered".

3. **Any issues or warnings encountered** — e.g. Wowhead rate-limit/Cloudflare
   blocks that prevented filling in drop rates / vendor costs / profession
   skills for some new items. Be explicit about what was left incomplete so
   the user can decide whether to re-run the enrichment later.

4. Ask the user if they want to commit the changes.
