# Cache Safety — Authoritative Rules

**This is the binding source of truth for how to treat the scraped caches
under `Tools/scraper/data/`. These rules apply to any human and any agent
(Claude or otherwise) working in this repo.**

The cache directories themselves are `.gitignore`d, so the short
`CLAUDE.md` stubs inside them are not tracked and may be missing in a
fresh clone. This file is the canonical copy — stubs in the cache dirs
point back here.

## Affected directories

- `Tools/scraper/data/wowhead_cache/` — ~10,600 JSON files of scraped
  Wowhead responses (decor sources, quest pages, NPC pages, item pages).
- `Tools/scraper/data/wowdb_cache/` — ~1,950 HTML snapshots from
  housing.wowdb.com (decor item pages, community sets).

A full re-scrape of either takes **30–60+ minutes** with rate limiting.
Some upstream pages can disappear or change structure at any time, so
the cached copy is sometimes the **only reliable source** of that data.

## Hard rules

1. **Never run bulk cache cleanup without an explicit, in-conversation
   instruction from the user.** Do NOT remove files based on your own
   interpretation of what's "stale" or "broken" — even if the on-disk
   content looks like `null`, `[]`, `{"error": ...}`, or anything else
   you think is a failure marker. Many of those are **legitimate
   cached results meaning "no data"**, and clearing them forces a full
   re-scrape.

2. **Never chain `rm`, `find -delete`, or `os.remove` over a cache
   directory as part of a larger task.** If a cache-touching step
   seems needed, STOP, describe the exact files you would delete and
   why, and wait for explicit user confirmation. "User told me to
   retry enrichment" is NOT confirmation to touch the cache.

3. **If you must invalidate a specific cache entry**, identify it by
   the cache-key hash in its filename, delete only that one file, and
   say what you did. Never pattern-match broadly (`*.json`, etc.).

4. **Always run `scripts/backup_cache.sh` first** when a cache-affecting
   change is unavoidable. The backup is the safety net.

5. **When Wowhead 403s come back**: a `null` or empty-list cached
   response can mean EITHER "Wowhead 403'd this page" OR "this page
   legitimately returned no matching data". You cannot tell from
   content alone. Do not delete such entries to "retry" them. If the
   ratio of 403s to successes becomes a problem, **fix the fetcher**
   (switch HTTP client, add browser fingerprinting) rather than
   purging the cache.

## Why these rules exist (real incidents)

- **2026-04-21**: An agent ran a one-liner that removed every cache
  file whose content matched `None`, `[]`, or `{"error": "fetch_failed"}`.
  1628 files were deleted. Most were **legitimate "no data" cached
  results**, not failures. Recovery required a ~45-minute re-scrape of
  1500+ items. This exact mistake is the reason for rule #1.

## The nuclear option

Some scripts have a `--clear-cache` flag (or equivalent). It is a
**last resort** and still requires explicit user agreement before use.
Never invoke it to "speed things up" or "reset state" — those are never
good reasons to discard hours of scraped data.

## Bootstrapping warning stubs in fresh clones

The cache dirs are gitignored so the in-cache `CLAUDE.md` stubs won't
appear on a fresh checkout. The first time an agent opens a new clone
and is about to create or touch a cache directory, it should copy the
short stubs from this file's companion `scripts/init_cache_readmes.sh`
(or create minimal stubs referencing this doc) so the directory-local
warning is present.
