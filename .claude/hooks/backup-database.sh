#!/bin/bash
# Pre-edit hook: creates a backup of database files before modification.
# Called automatically by Claude Code when editing files in Tools/scraper/data/

BACKUP_DIR="d:/Programming/WoWAddons/HearthAndSeek/Tools/scraper/data/backups"
DATA_DIR="d:/Programming/WoWAddons/HearthAndSeek/Tools/scraper/data"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/pre_edit_${TIMESTAMP}.tar.gz"

# Ensure backup directory exists
mkdir -p "$BACKUP_DIR"

# Create backup of all critical database files
tar -czf "$BACKUP_FILE" \
    -C "$DATA_DIR" \
    catalog_dump.json \
    enriched_catalog.json \
    quest_chains.json \
    quest_givers.json \
    item_themes.json \
    item_versions.json \
    enriched_catalog_extra.json \
    wowdb_item_tags.json \
    manual_theme_annotations.json \
    2>/dev/null

BACKUP_SIZE=$(wc -c < "$BACKUP_FILE" 2>/dev/null || echo "0")

# Report to stderr (visible to user) and allow the edit to proceed
echo "BACKUP CREATED: pre_edit_${TIMESTAMP}.tar.gz (${BACKUP_SIZE} bytes)" >&2
exit 0
