---
name: Data Safety - Never Overwrite Catalog Database
description: CRITICAL - Never run parse_catalog_dump.py without --merge. Always backup before any data pipeline operation. The catalog database is critical and data loss is unacceptable.
type: feedback
---

The HearthAndSeek catalog database (`Tools/scraper/data/catalog_dump.json` and related files) is CRITICAL. Data loss is unacceptable.

Rules:
1. **NEVER** run `parse_catalog_dump.py` without `--merge` when doing incremental scans. Without `--merge`, it replaces the entire catalog with only new items from SavedVariables.
2. **NEVER** run `run_pipeline.py` from the beginning after a manual merge — the first stage (`parse_catalog_dump`) will overwrite the merged data. Always use `--from enrich_catalog`.
3. **ALWAYS** create a backup before ANY data modification step.
4. **ALWAYS** create a backup after a successful pipeline run.
5. **ALWAYS** verify item counts after any operation that touches the catalog.

A safeguard was added to `run_pipeline.py` (line ~353) that automatically passes `--merge` when `catalog_dump.json` already exists, but this should not be relied upon as the sole protection.

6. **EVERY TIME** any database/catalog field or attribute is updated (even a single field fix), create a backup BEFORE and AFTER the change.
7. A PreToolUse hook (`.claude/settings.json`) automatically creates backups when editing `Tools/scraper/data/*.json` files. This covers direct edits but NOT pipeline script runs — always manually backup before/after running pipeline scripts.
