#!/usr/bin/env bash
# deploy.sh — Copy HearthAndSeek addon to WoW retail AddOns folder.
# Usage: ./deploy.sh
#
# Reads the WoW path from deploy.config.json (or dev.config.json as fallback).
# Copies Core/, Data/, Modules/, UI/, Libs/, Media/, and the TOC file.
# Does NOT copy Tools/, Docs/, or other dev-only directories.

set -euo pipefail

SRC="$(cd "$(dirname "$0")/.." && pwd)"

# Read wowRetailDir from deploy.config.json or dev.config.json
CONFIG=""
for cfg in "$SRC/deploy.config.json" "$SRC/dev.config.json"; do
    if [ -f "$cfg" ]; then
        CONFIG="$cfg"
        break
    fi
done

if [ -z "$CONFIG" ]; then
    echo "ERROR: No deploy.config.json or dev.config.json found."
    echo "Copy deploy.config.example.json and set your WoW path."
    exit 1
fi

# Extract wowDir (deploy.config) or wowRetailDir (dev.config) using python
CONFIG_WIN="$CONFIG"
# Convert MSYS/Git-Bash paths to Windows for Python
if command -v cygpath &>/dev/null; then
    CONFIG_WIN="$(cygpath -w "$CONFIG")"
fi
WOW_DIR=$(python3 -c "
import json
cfg = json.load(open(r'$CONFIG_WIN', encoding='utf-8'))
print(cfg.get('wowDir') or cfg.get('wowRetailDir', ''))
")

if [ -z "$WOW_DIR" ]; then
    echo "ERROR: Could not read WoW path from $CONFIG"
    exit 1
fi

ADDON_NAME="HearthAndSeek"
DEST="$WOW_DIR/Interface/AddOns/$ADDON_NAME"

if [ ! -d "$DEST" ]; then
    echo "Creating $DEST"
    mkdir -p "$DEST"
fi

# Addon directories to sync
DIRS=(Core Data Modules UI Libs Media)

for dir in "${DIRS[@]}"; do
    if [ -d "$SRC/$dir" ]; then
        # Remove dest dir first to avoid stale files, then copy fresh
        rm -rf "$DEST/$dir"
        cp -r "$SRC/$dir" "$DEST/$dir"
    fi
done

# TOC file
cp "$SRC/$ADDON_NAME.toc" "$DEST/$ADDON_NAME.toc"

echo "Deployed $ADDON_NAME to $DEST"
