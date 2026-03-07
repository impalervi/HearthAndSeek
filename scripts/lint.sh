#!/usr/bin/env bash
# Static analysis for HearthAndSeek Lua files
# Uses luacheck with WoW addon configuration (.luacheckrc)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
LUACHECK="$REPO_DIR/tools/bin/luacheck.exe"

if [ ! -f "$LUACHECK" ]; then
    echo "Error: luacheck not found at $LUACHECK"
    echo "Download from: https://github.com/lunarmodules/luacheck/releases"
    exit 1
fi

cd "$REPO_DIR"

echo "Running luacheck on HearthAndSeek..."
echo ""

"$LUACHECK" \
    Core/*.lua \
    UI/*.lua \
    --config .luacheckrc \
    "$@"
