#!/usr/bin/env bash
# Writes short CLAUDE.md warning stubs into the scraped cache directories.
# The cache dirs themselves are .gitignored so these stubs don't track;
# this script re-creates them on a fresh clone so the directory-local
# warnings are present for any agent that navigates in.
#
# The AUTHORITATIVE rules live in docs/CACHES.md — stubs just point there.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WOWHEAD_CACHE="$REPO_ROOT/Tools/scraper/data/wowhead_cache"
WOWDB_CACHE="$REPO_ROOT/Tools/scraper/data/wowdb_cache"
CANONICAL_RELPATH="docs/CACHES.md"

STUB_CONTENT='# STOP — CRITICAL CACHE DIRECTORY. DO NOT DELETE FILES HERE.

This directory holds scraped cache data representing hours of rate-limited
fetching. Do NOT bulk-delete, do NOT pattern-delete, do NOT assume empty
or null contents are failures.

**The binding rules live in `'"$CANONICAL_RELPATH"'` at the repo root.**
Read it before any cache-adjacent work, and follow the 5-rule protocol
(short version: get explicit user agreement + backup first).

If you want to refresh one specific entry, identify it by hash, tell the
user, and delete only that single file.'

for dir in "$WOWHEAD_CACHE" "$WOWDB_CACHE"; do
    mkdir -p "$dir"
    printf '%s\n' "$STUB_CONTENT" > "$dir/CLAUDE.md"
    echo "Wrote stub: $dir/CLAUDE.md"
done

echo "Cache stubs initialized. Canonical rules: $CANONICAL_RELPATH"
