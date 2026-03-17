#!/usr/bin/env bash
# backup_cache.sh — Create timestamped archives of pipeline data and cache directories.
#
# Creates tar.gz archives in a backups/ folder next to the data/ directory.
# Each archive is timestamped so multiple backups can coexist.
#
# Usage:
#   bash scripts/backup_cache.sh              # Archive everything (caches + data files)
#   bash scripts/backup_cache.sh --wowhead    # Archive only wowhead_cache
#   bash scripts/backup_cache.sh --wowdb      # Archive only wowdb_cache
#   bash scripts/backup_cache.sh --data       # Archive only loose data files (JSONs)
#
# IMPORTANT: Run this script BEFORE making any changes to pipeline scripts
# that affect cache reading/writing. This gives you a restore point if
# something goes wrong.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="$REPO_ROOT/Tools/scraper/data"
BACKUP_DIR="$DATA_DIR/backups"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"

# Parse arguments
DO_WOWHEAD=false
DO_WOWDB=false
DO_DATA=false

if [[ $# -eq 0 ]]; then
    DO_WOWHEAD=true
    DO_WOWDB=true
    DO_DATA=true
else
    for arg in "$@"; do
        case "$arg" in
            --wowhead) DO_WOWHEAD=true ;;
            --wowdb)   DO_WOWDB=true ;;
            --data)    DO_DATA=true ;;
            --help|-h)
                echo "Usage: bash scripts/backup_cache.sh [--wowhead] [--wowdb] [--data]"
                echo ""
                echo "Creates timestamped tar.gz archives of pipeline data and cache directories."
                echo "With no arguments, archives everything."
                echo ""
                echo "Options:"
                echo "  --wowhead   Archive wowhead_cache/ (~10K JSON files, ~620K compressed)"
                echo "  --wowdb     Archive wowdb_cache/ (~2K HTML files, ~37MB compressed)"
                echo "  --data      Archive loose data files (catalog_dump.json, overrides, etc.)"
                exit 0
                ;;
            *)
                echo "Unknown argument: $arg"
                echo "Usage: bash scripts/backup_cache.sh [--wowhead] [--wowdb] [--data]"
                exit 1
                ;;
        esac
    done
fi

mkdir -p "$BACKUP_DIR"

archive_cache() {
    local cache_name="$1"
    local cache_dir="$DATA_DIR/$cache_name"
    local archive="$BACKUP_DIR/${cache_name}_${TIMESTAMP}.tar.gz"

    if [[ ! -d "$cache_dir" ]]; then
        echo "WARNING: $cache_dir does not exist, skipping."
        return
    fi

    local file_count
    file_count="$(find "$cache_dir" -maxdepth 1 -type f ! -name 'CLAUDE.md' | wc -l)"
    if [[ "$file_count" -eq 0 ]]; then
        echo "WARNING: $cache_dir is empty, skipping."
        return
    fi

    echo "Archiving $cache_name ($file_count files)..."
    tar -czf "$archive" -C "$DATA_DIR" --exclude="CLAUDE.md" "$cache_name/"
    local size
    size="$(du -h "$archive" | cut -f1)"
    echo "  -> $archive ($size)"
}

archive_data_files() {
    local archive="$BACKUP_DIR/data_files_${TIMESTAMP}.tar.gz"

    # Collect all JSON/HTML files directly in data/ (not in subdirectories)
    local files=()
    while IFS= read -r -d '' f; do
        files+=("$(basename "$f")")
    done < <(find "$DATA_DIR" -maxdepth 1 -type f \( -name '*.json' -o -name '*.html' \) -print0)

    if [[ ${#files[@]} -eq 0 ]]; then
        echo "WARNING: No data files found in $DATA_DIR, skipping."
        return
    fi

    echo "Archiving ${#files[@]} data files..."
    tar -czf "$archive" -C "$DATA_DIR" "${files[@]}"
    local size
    size="$(du -h "$archive" | cut -f1)"
    echo "  -> $archive ($size)"
}

echo "=== HearthAndSeek Cache Backup ==="
echo "Timestamp: $TIMESTAMP"
echo ""

if $DO_WOWHEAD; then
    archive_cache "wowhead_cache"
fi

if $DO_WOWDB; then
    archive_cache "wowdb_cache"
fi

if $DO_DATA; then
    archive_data_files
fi

echo ""
echo "Done. Backups saved to: $BACKUP_DIR/"

# List recent backups
echo ""
echo "Recent backups:"
ls -lh "$BACKUP_DIR"/*.tar.gz 2>/dev/null | tail -10
