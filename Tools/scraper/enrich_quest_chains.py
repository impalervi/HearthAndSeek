"""
enrich_quest_chains.py - Build quest prerequisite chains for HearthAndSeek catalog quests.

Scrapes Wowhead quest pages to extract:
  1. Series data: immediate prev/next quests in a short sequence
  2. Storyline data: the full questline a quest belongs to

For each quest in the HearthAndSeek enriched catalog that has a questID, this script:
  - Fetches the Wowhead quest page
  - Parses the Series section (direct prerequisites/successors)
  - Parses the Storyline section (full ordered chain)
  - Recursively follows prerequisites that are NOT already in our catalog
  - Builds a complete chain graph

All Wowhead responses are cached in data/wowhead_cache/ (shared with enrich_catalog.py).

Output: data/quest_chains.json

Usage:
    python enrich_quest_chains.py [--force] [--max-depth N]

Options:
    --force       Re-fetch all quests, ignoring cache
    --max-depth   Maximum recursion depth for prerequisite chains (default: 20)
"""

import argparse
import json
import hashlib
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
ENRICHED_CATALOG = DATA_DIR / "enriched_catalog.json"
FACTION_QUEST_OVERRIDES = DATA_DIR / "faction_quest_overrides.json"
OUTPUT_FILE = DATA_DIR / "quest_chains.json"

# Rate limiting: pause between Wowhead requests (in seconds)
REQUEST_DELAY = 0.75

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_quest_chains")


# ---------------------------------------------------------------------------
# Cache helpers  (same scheme as enrich_catalog.py for shared cache dir)
# ---------------------------------------------------------------------------

def _cache_key(prefix: str, name: str) -> str:
    """Generate a safe filename for a cache entry."""
    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{prefix}_{readable}_{safe}.json"


def cache_get(prefix: str, name: str) -> Optional[Any]:
    """Read a cached response, or return None if not cached."""
    path = CACHE_DIR / _cache_key(prefix, name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_put(prefix: str, name: str, data: Any) -> None:
    """Write a response to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(prefix, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get(url: str) -> Optional[str]:
    """
    Fetch a URL with rate limiting and error handling.
    Returns raw response text or None.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        _last_request_time = time.time()

        if resp.status_code == 429:
            logger.warning("Rate limited (429). Waiting 30 seconds...")
            time.sleep(30)
            resp = requests.get(url, headers=HEADERS, timeout=30)
            _last_request_time = time.time()

        if resp.status_code == 404:
            logger.warning("Quest not found (404): %s", url)
            return None

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.text

    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Quest name extraction from g_quests
# ---------------------------------------------------------------------------

def _extract_quest_name_from_html(html: str, quest_id: int) -> Optional[str]:
    """
    Extract the quest name from the g_quests[ID] embedded JavaScript data.

    Wowhead pages contain:
        $.extend(g_quests[QUESTID], {"name":"Quest Name",...});
    """
    pattern = rf'\$\.extend\(g_quests\[{quest_id}\],\s*(\{{.*?\}})\)'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return data.get("name")
        except json.JSONDecodeError:
            pass

    # Fallback: parse <title> tag
    title_match = re.search(r'<title>([^<]+?)(?:\s*-\s*Quest\s*-\s*World of Warcraft)?</title>', html, re.IGNORECASE)
    if title_match:
        return title_match.group(1).strip()

    return None


# ---------------------------------------------------------------------------
# Series / Storyline parsing
# ---------------------------------------------------------------------------

def _parse_series_table(html: str, quest_id: int) -> list[dict]:
    """
    Parse the Series section from Wowhead quest page HTML.

    The Series section is inside the Quick Facts infobox as:
        <th>Series</th>
        ...
        <table class="series">
            <tr><th>1.</th><td><div><a href="/quest=NNNN/slug">Name</a></div></td></tr>
            <tr><th>2.</th><td><div><b>Current Quest</b></div></td></tr>
        </table>

    Current quest is wrapped in <b> tags (no link).
    Other quests have <a href="/quest=NNNN/..."> links.
    """
    results = []

    # Find <table class="series"> and extract its rows
    series_match = re.search(
        r'<table\s+class="series"[^>]*>(.*?)</table>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not series_match:
        return results

    table_html = series_match.group(1)

    # Parse each <tr> row
    for row_match in re.finditer(r'<tr>(.*?)</tr>', table_html, re.DOTALL):
        row_html = row_match.group(1)

        # Check for a quest link
        link_match = re.search(
            r'<a[^>]*href="[^"]*?/quest=(\d+)[^"]*"[^>]*>([^<]+)</a>',
            row_html,
        )
        if link_match:
            results.append({
                "quest_id": int(link_match.group(1)),
                "name": link_match.group(2).strip(),
                "is_current": False,
            })
        else:
            # Current quest: wrapped in <b> tags
            bold_match = re.search(r'<b>([^<]+)</b>', row_html)
            if bold_match:
                results.append({
                    "quest_id": quest_id,
                    "name": bold_match.group(1).strip(),
                    "is_current": True,
                })

    return results


def _parse_one_storyline(container_html: str, quest_id: int) -> list[dict]:
    """Parse quest entries from a single storyline container's HTML."""
    results = []
    ol_match = re.search(r'<ol[^>]*>(.*?)</ol>', container_html, re.DOTALL)
    if not ol_match:
        return results

    ol_html = ol_match.group(1)
    for li_match in re.finditer(
        r'<li[^>]*?(?:class="([^"]*)")?[^>]*>(.*?)</li>',
        ol_html, re.DOTALL,
    ):
        li_class = li_match.group(1) or ""
        li_content = li_match.group(2)
        is_current = "current" in li_class

        link_match = re.search(
            r'<a[^>]*href="[^"]*?/quest=(\d+)[^"]*"[^>]*>([^<]+)</a>',
            li_content,
        )
        if link_match:
            results.append({
                "quest_id": int(link_match.group(1)),
                "name": link_match.group(2).strip(),
                "is_current": is_current,
            })
        else:
            text = re.sub(r'<[^>]+>', '', li_content).strip()
            if text:
                results.append({
                    "quest_id": quest_id,
                    "name": text,
                    "is_current": True,
                })
    return results


def _parse_storyline_list(html: str, quest_id: int) -> tuple[Optional[str], list[dict]]:
    """Parse the first Storyline section (backward-compat wrapper)."""
    all_storylines = _parse_all_storylines(html, quest_id)
    if all_storylines:
        return all_storylines[0]["name"], all_storylines[0]["quests"]
    return None, []


def _parse_all_storylines(html: str, quest_id: int) -> list[dict]:
    """
    Parse ALL Storyline sections from a Wowhead quest page.

    Some quests have multiple storylines (e.g. a focused sub-storyline
    AND a broad zone storyline).  Each is inside its own
    ``<div class="quick-facts-storyline-list">`` preceded by a
    storyline name link.

    Returns a list of::

        [
            {"name": "The Zanchuli Council", "quests": [{quest_id, name, is_current}, ...]},
            {"name": "Unrest in Zuldazar",   "quests": [{...}, ...]},
        ]
    """
    storylines: list[dict] = []

    # Find ALL storyline containers
    for container_match in re.finditer(
        r'<div\s+class="quick-facts-storyline-list"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE,
    ):
        quests = _parse_one_storyline(container_match.group(1), quest_id)
        if not quests:
            continue

        # Extract storyline name from the link before this container
        container_start = container_match.start()
        search_area = html[max(0, container_start - 500):container_start]
        name_match = re.search(
            r'<a[^>]*href="[^"]*?/storyline/[^"]*"[^>]*>([^<]+)</a>',
            search_area,
        )
        sl_name = name_match.group(1).strip() if name_match else None

        storylines.append({"name": sl_name, "quests": quests})

    return storylines


def _parse_series_and_storyline(html: str, quest_id: int) -> dict:
    """
    Parse the Series and Storyline sections from a Wowhead quest page.

    Series is in a <table class="series"> inside the Quick Facts infobox.
    Storylines are in ``<div class="quick-facts-storyline-list">`` elements
    (there may be multiple — e.g. a focused sub-storyline AND a zone-wide one).

    Returns:
        {
            "series": [...],
            "storyline_name": str or None,   # first storyline name (compat)
            "storyline": [...],              # first storyline quests (compat)
            "storylines": [                  # ALL storylines
                {"name": str, "quests": [...]},
                ...
            ],
        }
    """
    series = _parse_series_table(html, quest_id)
    all_storylines = _parse_all_storylines(html, quest_id)

    # Backward compat: first storyline → "storyline" / "storyline_name"
    first_name = all_storylines[0]["name"] if all_storylines else None
    first_quests = all_storylines[0]["quests"] if all_storylines else []

    return {
        "series": series,
        "storyline_name": first_name,
        "storyline": first_quests,
        "storylines": all_storylines,
    }


# ---------------------------------------------------------------------------
# Single quest scraper
# ---------------------------------------------------------------------------

def fetch_quest_chain_data(quest_id: int, force: bool = False) -> Optional[dict]:
    """
    Fetch and parse quest chain data for a single quest from Wowhead.

    Returns:
        {
            "quest_id": int,
            "name": str,
            "series": [...],       # immediate prev/next quests
            "storyline_name": str,  # name of the broader storyline
            "storyline": [...],     # full storyline quest list
            "prereqs": [int, ...],  # direct prerequisite quest IDs
        }
    or None if the quest page couldn't be fetched.
    """
    if not force:
        cached = cache_get("quest_chain", str(quest_id))
        if cached is not None:
            return cached

    url = f"https://www.wowhead.com/quest={quest_id}"
    html = _rate_limited_get(url)

    if not html:
        result = {
            "quest_id": quest_id,
            "name": None,
            "series": [],
            "storyline_name": None,
            "storyline": [],
            "prereqs": [],
            "fetch_error": True,
        }
        cache_put("quest_chain", str(quest_id), result)
        return result

    # Extract quest name
    name = _extract_quest_name_from_html(html, quest_id)

    # Parse series and storyline
    chain_data = _parse_series_and_storyline(html, quest_id)

    # Determine direct prerequisite from the series data:
    # The series is sequential, so only the immediate predecessor is the
    # direct prereq (not all prior entries).
    prereqs = []
    if chain_data["series"]:
        last_before_current = None
        found_self = False
        for entry in chain_data["series"]:
            if entry["quest_id"] == quest_id or entry["is_current"]:
                found_self = True
                break
            if entry["quest_id"]:
                last_before_current = entry["quest_id"]
        # Only use series prereqs if the quest actually appears in its own
        # series — some quests are missing from their series on Wowhead,
        # and the loop would fall through to the last entry (wrong prereq).
        if found_self and last_before_current:
            prereqs = [last_before_current]

    # If no series data, try to determine prereqs from the storyline:
    # The quest immediately before us in the storyline is likely a prereq.
    if not prereqs and chain_data["storyline"]:
        found_self = False
        for i, entry in enumerate(chain_data["storyline"]):
            if entry["quest_id"] == quest_id or entry["is_current"]:
                found_self = True
                if i > 0 and chain_data["storyline"][i - 1].get("quest_id"):
                    prereqs.append(chain_data["storyline"][i - 1]["quest_id"])
                break
        # If the quest isn't in its own storyline either, prereqs stays empty.

    result = {
        "quest_id": quest_id,
        "name": name,
        "series": chain_data["series"],
        "storyline_name": chain_data["storyline_name"],
        "storyline": chain_data["storyline"],
        "storylines": chain_data.get("storylines", []),
        "prereqs": prereqs,
    }

    cache_put("quest_chain", str(quest_id), result)
    return result


# ---------------------------------------------------------------------------
# Recursive chain builder
# ---------------------------------------------------------------------------

def build_quest_chains(
    seed_quest_ids: list[int],
    max_depth: int = 20,
    force: bool = False,
    catalog_quest_names: dict[int, str] | None = None,
) -> dict[int, dict]:
    """
    Build complete prerequisite chains for a set of seed quest IDs.

    Starting from the seed quests (the ones that reward decor items), this
    recursively follows prerequisite chains backwards until it reaches quests
    with no prerequisites or hits the max depth.

    Returns a dict keyed by quest_id with the chain data for every quest
    encountered (seeds + all their prerequisites).
    """
    all_quests: dict[int, dict] = {}
    queue: list[tuple[int, int]] = [(qid, 0) for qid in seed_quest_ids]
    visited: set[int] = set()

    total_seeds = len(seed_quest_ids)
    fetched_count = 0

    while queue:
        quest_id, depth = queue.pop(0)

        if quest_id in visited:
            continue
        visited.add(quest_id)

        if depth > max_depth:
            logger.warning(
                "Max depth %d reached for questID=%d, stopping recursion",
                max_depth, quest_id,
            )
            continue

        fetched_count += 1
        is_seed = quest_id in seed_quest_ids
        prefix = "SEED" if is_seed else f"PREREQ(d={depth})"
        logger.info(
            "[%d/%d+] %s questID=%d",
            fetched_count, total_seeds, prefix, quest_id,
        )

        data = fetch_quest_chain_data(quest_id, force=force)
        if not data:
            logger.warning("  -> Failed to fetch quest %d", quest_id)
            continue

        name = data.get("name") or f"Quest #{quest_id}"

        # Recompute immediate predecessor from raw series data (the cache
        # may store stale multi-prereq lists from an older algorithm).
        # Series = actual required prerequisites; storyline = zone narrative
        # order (not enforced). Only use series for seed (decor) quests.
        series = data.get("series", [])
        prereqs = []
        if series:
            last_before_current = None
            found_self = False
            for entry in series:
                if entry.get("quest_id") == quest_id or entry.get("is_current"):
                    found_self = True
                    break
                if entry.get("quest_id"):
                    last_before_current = entry["quest_id"]
            # Only use series prereqs if the quest actually appears in its own
            # series — avoids circular deps from fall-through.
            if found_self and last_before_current:
                prereqs = [last_before_current]
            elif not found_self:
                logger.warning(
                    "  quest %d not found in its own series (%d entries), "
                    "skipping series-based prereqs",
                    quest_id, len(series),
                )
        if not prereqs and not series and data.get("prereqs"):
            # No series data at all — fall back to cached prereqs (storyline-derived).
            # Don't use cached prereqs when series exists but quest wasn't found in
            # it — cached prereqs are likely stale/broken from a previous buggy run.
            # The storyline walk from other seed quests will fill in correct prereqs.
            prereqs = [p for p in data["prereqs"] if p != quest_id]

        if prereqs:
            logger.info("  -> %s -- prereqs: %s", name, prereqs)
        elif data.get("series") or data.get("storyline"):
            logger.info("  -> %s -- no prereqs (first in chain)", name)
        else:
            logger.info("  -> %s -- standalone (no series/storyline)", name)

        # For seed quests with both series and storyline, store the series
        # chain separately.  The UI can offer a toggle: short series chain
        # vs. full storyline chain.
        series_chain: list[int] | None = None
        if is_seed and series and data.get("storyline"):
            sc_ids = []
            found_current = False
            for entry in series:
                eid = entry.get("quest_id")
                if eid == quest_id or entry.get("is_current"):
                    found_current = True
                    break
                if eid:
                    sc_ids.append(eid)
            # Only use if we actually found the current quest in the series;
            # otherwise the series data is unreliable (quest may not appear
            # in its own series due to Wowhead data quirks).
            if found_current and sc_ids:
                series_chain = sc_ids
                # Ensure every series quest has an entry in all_quests with
                # the correct name and prereqs from the series order.
                # The series gives authoritative ordering — use it to set
                # prereqs for quests that may not appear in their own series.
                prev_series_qid = None
                for entry in series:
                    eid = entry.get("quest_id")
                    if not eid or eid == quest_id or entry.get("is_current"):
                        break
                    if eid not in all_quests:
                        all_quests[eid] = {
                            "quest_id": eid,
                            "name": entry.get("name") or f"Quest #{eid}",
                            "prereqs": [prev_series_qid] if prev_series_qid else [],
                            "storyline_name": data.get("storyline_name"),
                            "is_decor_quest": eid in seed_quest_ids,
                        }
                    elif not all_quests[eid]["prereqs"] and prev_series_qid:
                        # Fill in prereqs from series order if quest had empty
                        # prereqs (e.g. quest wasn't found in its own series).
                        all_quests[eid]["prereqs"] = [prev_series_qid]
                    prev_series_qid = eid

        # If we couldn't compute prereqs (e.g. quest not in its own series),
        # preserve any prereqs that were already set by another quest's storyline.
        if not prereqs and quest_id in all_quests and all_quests[quest_id].get("prereqs"):
            prereqs = all_quests[quest_id]["prereqs"]

        quest_entry: dict = {
            "quest_id": quest_id,
            "name": name,
            "prereqs": prereqs,
            "storyline_name": data.get("storyline_name"),
            "is_decor_quest": is_seed,
        }
        if series_chain:
            quest_entry["series_chain"] = series_chain
        all_quests[quest_id] = quest_entry

        # Use storyline data from seed quests to build complete chains.
        # The storyline list from successfully-fetched seed quests gives us
        # the full ordered quest chain with names, avoiding 403 issues on
        # recursive prereq fetches.
        # NOTE: Storylines often contain race variants (same quest name,
        # different IDs). We deduplicate by name to avoid bloated chains.
        if is_seed and data.get("storyline"):
            storyline = data["storyline"]
            storyline_name = data.get("storyline_name")
            prev_qid = None
            seen_names: set[str] = set()
            for sl_entry in storyline:
                sl_qid = sl_entry.get("quest_id")
                sl_name = sl_entry.get("name")
                if not sl_qid:
                    continue
                # Skip race/class variants (same name, different quest ID)
                if sl_name and sl_name in seen_names:
                    continue
                if sl_name:
                    seen_names.add(sl_name)
                if sl_qid not in all_quests:
                    # Don't set storyline-derived prereqs for seed quests —
                    # they'll get accurate prereqs from their own series data
                    # when processed as seeds later.
                    is_sl_seed = sl_qid in seed_quest_ids
                    all_quests[sl_qid] = {
                        "quest_id": sl_qid,
                        "name": sl_entry.get("name") or f"Quest #{sl_qid}",
                        "prereqs": [] if is_sl_seed else ([prev_qid] if prev_qid else []),
                        "storyline_name": storyline_name,
                        "is_decor_quest": is_sl_seed,
                    }
                    # Don't mark seed quests as visited — they need to be
                    # processed from the main queue to get their series prereqs.
                    if not is_sl_seed:
                        visited.add(sl_qid)
                elif not all_quests[sl_qid].get("prereqs") and prev_qid:
                    # Fill in prereqs from storyline if we had no data.
                    # But never overwrite seed quests — their series data is authoritative.
                    if not all_quests[sl_qid].get("is_decor_quest"):
                        all_quests[sl_qid]["prereqs"] = [prev_qid]
                # Update name if we only had "Quest #NNNNN"
                sl_name = sl_entry.get("name")
                if sl_name and all_quests[sl_qid]["name"].startswith("Quest #"):
                    all_quests[sl_qid]["name"] = sl_name
                prev_qid = sl_qid

        # Enqueue prerequisites for recursive fetching
        for prereq_id in prereqs:
            if prereq_id not in visited:
                queue.append((prereq_id, depth + 1))

    # -----------------------------------------------------------------------
    # Post-processing: backfill placeholder names from series/storyline data.
    # Some prerequisite quests return 403/404 on direct fetch, so their name
    # is None and becomes "Quest #NNNNN". But their correct name often exists
    # in the series/storyline data of other (successfully fetched) quests.
    # -----------------------------------------------------------------------
    placeholder_quests = {
        qid for qid, qdata in all_quests.items()
        if qdata["name"].startswith("Quest #")
    }
    if placeholder_quests:
        logger.info("")
        logger.info("Backfilling %d placeholder quest names from series/storyline data...",
                     len(placeholder_quests))
        name_lookup: dict[int, str] = {}
        for qid in all_quests:
            cached = cache_get("quest_chain", str(qid))
            if not cached:
                continue
            for entry in cached.get("series", []) + cached.get("storyline", []):
                eq = entry.get("quest_id")
                en = entry.get("name")
                if eq and en and eq in placeholder_quests and eq not in name_lookup:
                    name_lookup[eq] = en

        backfilled = 0
        for qid in placeholder_quests:
            if qid in name_lookup:
                all_quests[qid]["name"] = name_lookup[qid]
                logger.info("  quest %d: %s", qid, name_lookup[qid])
                backfilled += 1
        logger.info("Backfilled %d / %d placeholder names", backfilled, len(placeholder_quests))

    # -----------------------------------------------------------------------
    # Final pass: resolve remaining placeholder names from enriched catalog.
    # Phase 6 of enrich_catalog.py discovers quest names from Wowhead item
    # pages and stores them in item["quest"]. Use these as a fallback for
    # quests whose direct Wowhead quest page returned 403.
    # -----------------------------------------------------------------------
    remaining = {
        qid for qid, qdata in all_quests.items()
        if qdata["name"].startswith("Quest #")
    }
    if remaining and catalog_quest_names:
        resolved = 0
        for qid in remaining:
            if qid in catalog_quest_names:
                all_quests[qid]["name"] = catalog_quest_names[qid]
                logger.info("  quest %d: %s (from enriched catalog)", qid, catalog_quest_names[qid])
                resolved += 1
        if resolved:
            logger.info("Resolved %d placeholder names from enriched catalog", resolved)

    # -----------------------------------------------------------------------
    # Post-processing: detect and fix circular prerequisites (any length).
    # Uses DFS to find all cycles, including 3+ hop chains like A→B→C→A.
    # Breaks each cycle by removing the edge where a non-decor quest points
    # back to a decor quest (likely a successor, not a true prereq).
    # -----------------------------------------------------------------------
    def _find_cycles() -> list[list[int]]:
        """Find all cycles in the prereq graph via DFS."""
        WHITE, GRAY, BLACK = 0, 1, 2
        color: dict[int, int] = {qid: WHITE for qid in all_quests}
        path: list[int] = []
        cycles: list[list[int]] = []

        def dfs(node: int) -> None:
            color[node] = GRAY
            path.append(node)
            for pred in all_quests[node].get("prereqs", []):
                if pred not in all_quests:
                    continue
                if color[pred] == GRAY:
                    # Found cycle — extract from path
                    idx = path.index(pred)
                    cycles.append(list(path[idx:]))
                elif color[pred] == WHITE:
                    dfs(pred)
            path.pop()
            color[node] = BLACK

        for qid in all_quests:
            if color[qid] == WHITE:
                dfs(qid)
        return cycles

    cycles = _find_cycles()
    if cycles:
        logger.info("Found %d circular prerequisite cycle(s), fixing...", len(cycles))
        for cycle in cycles:
            # Find the best edge to break. The cycle is [A, B, C] meaning
            # A→B→C→A (each points to the next via prereqs, last wraps).
            # Prefer breaking where a non-decor quest has a decor quest as
            # prereq (likely a successor relationship, not a true prereq).
            best_idx = 0
            for i in range(len(cycle)):
                qid = cycle[i]
                pred = cycle[(i + 1) % len(cycle)]
                if all_quests.get(pred, {}).get("is_decor_quest") and \
                   not all_quests.get(qid, {}).get("is_decor_quest"):
                    best_idx = i
                    break

            break_qid = cycle[best_idx]
            remove_pred = cycle[(best_idx + 1) % len(cycle)]
            old_prereqs = all_quests[break_qid].get("prereqs", [])
            all_quests[break_qid]["prereqs"] = [
                p for p in old_prereqs if p != remove_pred
            ]

            cycle_str = " → ".join(str(q) for q in cycle) + " → " + str(cycle[0])
            logger.warning(
                "Fixed cycle: %s (broke %d → %d)", cycle_str, break_qid, remove_pred
            )
        logger.info("Fixed %d circular prerequisite cycle(s)", len(cycles))

    return all_quests


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build quest prerequisite chains for HearthAndSeek catalog quests.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch all quests, ignoring cache",
    )
    parser.add_argument(
        "--max-depth", type=int, default=20,
        help="Maximum recursion depth for prerequisite chains (default: 20)",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load enriched catalog to get seed quest IDs
    if not ENRICHED_CATALOG.exists():
        logger.error("Enriched catalog not found: %s", ENRICHED_CATALOG)
        logger.error("Run enrich_catalog.py first.")
        sys.exit(1)

    with open(ENRICHED_CATALOG, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Extract unique quest IDs
    seed_quest_ids_set = set(
        item["questID"]
        for item in catalog
        if item.get("questID")
    )

    # Also include cross-faction quest IDs from faction_quest_overrides.json
    if FACTION_QUEST_OVERRIDES.exists():
        with open(FACTION_QUEST_OVERRIDES, "r", encoding="utf-8") as f:
            faction_overrides = json.load(f)
        faction_quest_count = 0
        for entry in faction_overrides.values():
            for faction in ("Alliance", "Horde"):
                if faction in entry and entry[faction].get("questID"):
                    qid = entry[faction]["questID"]
                    if qid not in seed_quest_ids_set:
                        seed_quest_ids_set.add(qid)
                        faction_quest_count += 1
        if faction_quest_count:
            logger.info("Added %d cross-faction quest IDs from faction_quest_overrides.json",
                         faction_quest_count)

    seed_quest_ids = sorted(seed_quest_ids_set)

    logger.info("Loaded %d catalog items, %d unique quest IDs", len(catalog), len(seed_quest_ids))
    logger.info("")
    logger.info("=" * 60)
    logger.info("Phase 1: Fetching quest chain data from Wowhead")
    logger.info("=" * 60)

    # Build quest name lookup from enriched catalog (Phase 6 discovers names
    # from Wowhead item pages — use as fallback for 403'd quest pages)
    catalog_quest_names: dict[int, str] = {}
    for item in catalog:
        qid = item.get("questID")
        qname = item.get("quest")
        if qid and qname:
            catalog_quest_names[qid] = qname

    # Build the complete chain graph
    all_quests = build_quest_chains(
        seed_quest_ids,
        max_depth=args.max_depth,
        force=args.force,
        catalog_quest_names=catalog_quest_names,
    )

    # -----------------------------------------------------------------------
    # Analysis and output
    # -----------------------------------------------------------------------

    logger.info("")
    logger.info("=" * 60)
    logger.info("QUEST CHAIN SUMMARY")
    logger.info("=" * 60)

    total_quests = len(all_quests)
    decor_quests = sum(1 for q in all_quests.values() if q.get("is_decor_quest"))
    prereq_quests = total_quests - decor_quests
    quests_with_prereqs = sum(1 for q in all_quests.values() if q.get("prereqs"))
    standalone_quests = sum(
        1 for q in all_quests.values()
        if q.get("is_decor_quest") and not q.get("prereqs")
    )

    # Compute chain lengths (walk prereqs back to root for each decor quest)
    chain_lengths = []
    for qid, qdata in all_quests.items():
        if qdata.get("is_decor_quest"):
            length = 0
            current = qid
            seen = set()
            while current in all_quests and current not in seen:
                seen.add(current)
                prereqs = all_quests[current].get("prereqs", [])
                if prereqs:
                    length += 1
                    current = prereqs[0]  # Follow first prereq
                else:
                    break
            chain_lengths.append((qid, qdata.get("name", ""), length))

    chain_lengths.sort(key=lambda x: -x[2])

    logger.info("Total quests in graph:        %d", total_quests)
    logger.info("  Decor reward quests (seeds): %d", decor_quests)
    logger.info("  Prerequisite quests:         %d", prereq_quests)
    logger.info("Decor quests with prereqs:    %d", quests_with_prereqs)
    logger.info("Decor quests standalone:      %d", standalone_quests)

    if chain_lengths:
        logger.info("")
        logger.info("Longest chains:")
        for qid, name, length in chain_lengths[:10]:
            if length > 0:
                logger.info("  questID=%-6d chain=%d  %s", qid, length, name)

    # Count unique storylines
    storylines = set()
    for q in all_quests.values():
        sl = q.get("storyline_name")
        if sl:
            storylines.add(sl)
    if storylines:
        logger.info("")
        logger.info("Unique storylines referenced: %d", len(storylines))
        for sl in sorted(storylines):
            logger.info("  - %s", sl)

    # Write output
    output = {
        "metadata": {
            "total_quests": total_quests,
            "decor_quests": decor_quests,
            "prereq_quests": prereq_quests,
            "unique_storylines": len(storylines),
        },
        "quests": {
            str(qid): qdata
            for qid, qdata in sorted(all_quests.items())
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("Output: %s", OUTPUT_FILE)


if __name__ == "__main__":
    main()
