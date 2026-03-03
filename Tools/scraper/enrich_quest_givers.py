"""
enrich_quest_givers.py - Extract quest-giver NPC data for quest chain entries.

For each quest in quest_chains.json, scrapes the Wowhead quest page to extract
the quest-giver (start) NPC:
  - NPC name and ID
  - Coordinates (x, y)
  - Wowhead zone ID and zone name (via wh_zone_resolver)

The data comes from the Mapper JavaScript object embedded in quest pages:
    new Mapper({
        "objectives":{"ZONE_ID":{
            "levels":[[
                {"type":1,"point":"start","name":"NPC Name",
                 "coord":[x,y],"id":NPC_ID,...},
                {"type":1,"point":"end",...}
            ]]
        }}
    });

Zone names are resolved authoritatively via the Wowhead tooltip API
(see wh_zone_resolver.py). No fragile HTML parsing or catalog cross-reference.

All Wowhead responses are cached in data/wowhead_cache/ (shared cache).

Input:  data/quest_chains.json
Output: data/quest_givers.json

Usage:
    python enrich_quest_givers.py [--force] [--refresh-zones]

Options:
    --force           Re-fetch all quests from Wowhead, ignoring cache
    --refresh-zones   Re-resolve zone names for all cached entries using the
                      authoritative tooltip API (fixes stale/wrong zone names)
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

from wh_zone_resolver import resolve_zone_name

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
QUEST_CHAINS_JSON = DATA_DIR / "quest_chains.json"
OUTPUT_FILE = DATA_DIR / "quest_givers.json"

# Rate limiting: pause between Wowhead requests (in seconds)
# Wowhead returns 403 for bulk scraping at lower delays
REQUEST_DELAY = 1.5

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
logger = logging.getLogger("enrich_quest_givers")


# ---------------------------------------------------------------------------
# Cache helpers (same scheme as enrich_catalog.py / enrich_quest_chains.py)
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

    Handles both 429 (explicit rate limit) and 403 (Wowhead's soft rate limit
    that appears during bulk scraping) with backoff + retry.
    """
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            _last_request_time = time.time()

            if resp.status_code in (429, 403):
                backoff = 30 * (attempt + 1)
                if attempt < max_retries - 1:
                    logger.warning(
                        "HTTP %d for %s. Waiting %ds (attempt %d/%d)...",
                        resp.status_code, url, backoff, attempt + 1, max_retries,
                    )
                    time.sleep(backoff)
                    continue
                else:
                    logger.warning("HTTP %d for %s after %d retries.", resp.status_code, url, max_retries)
                    return None

            if resp.status_code == 404:
                logger.debug("Quest page not found (404): %s", url)
                return None

            if resp.status_code != 200:
                logger.warning("HTTP %d for %s", resp.status_code, url)
                return None

            return resp.text

        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", url, exc)
            return None

    return None


# ---------------------------------------------------------------------------
# Mapper data extraction
# ---------------------------------------------------------------------------

def _extract_mapper_data(html: str) -> Optional[dict]:
    """
    Extract the Mapper JavaScript data from a Wowhead quest page.

    Wowhead quest pages contain:
        new Mapper({"objectives":{"ZONE_ID":{"levels":[[{...},{...}]]}}});

    Returns the parsed JSON object, or None if not found.
    """
    # Pattern: new Mapper({...}) — greedy capture of the JSON object
    # The Mapper call may have various whitespace and content
    match = re.search(
        r'new\s+Mapper\(\s*(\{.*?"objectives".*?\})\s*\)',
        html,
        re.DOTALL,
    )
    if not match:
        return None

    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        # Try a more conservative extraction: find just the objectives part
        pass

    return None


def _extract_start_npc_from_mapper(
    mapper_data: dict,
    preferred_zone_id: Optional[int] = None,
) -> Optional[dict]:
    """
    Extract the quest-giver ("start" point) NPC from Mapper data.

    Mapper structure:
        {"objectives": {"ZONE_ID": {"levels": [[ {point entries...} ]]}}}

    Each point entry:
        {"type":1, "point":"start", "name":"NPC Name",
         "coord":[x, y], "id": NPC_ID, "reactalliance":1, "reacthorde":-1}

    When ``preferred_zone_id`` is given and a start point exists in that zone,
    it is returned instead of the first zone encountered.  This prevents
    multi-zone quests (e.g. Gilneas quests that also appear in Thaldraszus)
    from picking the wrong zone.

    Returns: {npcId, npcName, x, y, whZoneID} or None.
    """
    objectives = mapper_data.get("objectives")
    if not objectives or not isinstance(objectives, dict):
        return None

    # Collect all start-point candidates
    candidates: list[dict] = []
    for zone_id_str, zone_data in objectives.items():
        if not isinstance(zone_data, dict):
            continue

        levels = zone_data.get("levels")
        if not levels or not isinstance(levels, list):
            continue

        for level_group in levels:
            if not isinstance(level_group, list):
                continue

            for entry in level_group:
                if not isinstance(entry, dict):
                    continue

                if entry.get("point") == "start":
                    coord = entry.get("coord")
                    if coord and isinstance(coord, list) and len(coord) >= 2:
                        candidates.append({
                            "npcId": entry.get("id"),
                            "npcName": entry.get("name"),
                            "x": coord[0],
                            "y": coord[1],
                            "whZoneID": int(zone_id_str) if zone_id_str.isdigit() else None,
                        })

    if not candidates:
        return None

    # Prefer the candidate matching the preferred zone
    if preferred_zone_id is not None:
        for c in candidates:
            if c["whZoneID"] == preferred_zone_id:
                return c

    # Fall back to first candidate
    return candidates[0]


def _extract_start_npc_from_markup(html: str) -> Optional[dict]:
    """
    Fallback: extract quest-giver NPC ID from WH.markup.printHtml data.

    Wowhead pages contain:
        WH.markup.printHtml("...[url=/npc=NPCID/slug]NPC Name[/url]...", ...)

    where the "Start:" line links to the quest-giver NPC.
    """
    # Look for Start: [url=/npc=ID/...] patterns in markup data
    match = re.search(
        r'Start:.*?\[url=/npc=(\d+)/[^\]]*\]([^\[]+)\[/url\]',
        html,
        re.DOTALL,
    )
    if match:
        return {
            "npcId": int(match.group(1)),
            "npcName": match.group(2).strip(),
            "x": None,
            "y": None,
            "whZoneID": None,
        }

    return None


def _extract_zone_id_from_html(html: str) -> Optional[int]:
    """Extract the primary Wowhead zone ID from the quest page."""
    match = re.search(r'\[zone=(\d+)\]', html)
    if match:
        return int(match.group(1))
    match = re.search(r'href="[^"]*?/zone=(\d+)', html)
    if match:
        return int(match.group(1))
    return None


# ---------------------------------------------------------------------------
# Single quest enrichment
# ---------------------------------------------------------------------------

def fetch_quest_giver(
    quest_id: int,
    force: bool = False,
    refresh_zones: bool = False,
) -> Optional[dict]:
    """
    Fetch quest-giver NPC data for a single quest from Wowhead.

    Args:
        quest_id:       The Wowhead quest ID.
        force:          Re-fetch from Wowhead, ignoring cache entirely.
        refresh_zones:  Re-resolve zone names for cached entries using the
                        authoritative tooltip API (wh_zone_resolver).

    Returns:
        {
            "quest_id": int,
            "npcId": int or None,
            "npcName": str or None,
            "x": float or None,
            "y": float or None,
            "whZoneID": int or None,
            "zoneName": str or None,
        }
    or None if the quest page couldn't be fetched.
    """
    cache_name = str(quest_id)

    if not force:
        cached = cache_get("quest_giver", cache_name)
        if cached is not None:
            if refresh_zones and cached.get("whZoneID"):
                # Re-resolve zone name from authoritative API
                new_zone = resolve_zone_name(cached["whZoneID"])
                if new_zone and new_zone != cached.get("zoneName"):
                    logger.info(
                        "  Quest %d: zone %r -> %r",
                        quest_id, cached.get("zoneName"), new_zone,
                    )
                    cached["zoneName"] = new_zone
                    cache_put("quest_giver", cache_name, cached)
                elif new_zone and not cached.get("zoneName"):
                    cached["zoneName"] = new_zone
                    cache_put("quest_giver", cache_name, cached)
                return cached

            # Backfill zone name if missing from cache (new field)
            if cached.get("npcId") and not cached.get("zoneName") and cached.get("whZoneID"):
                zone = resolve_zone_name(cached["whZoneID"])
                if zone:
                    cached["zoneName"] = zone
                    cache_put("quest_giver", cache_name, cached)
            return cached

    url = f"https://www.wowhead.com/quest={quest_id}"
    html = _rate_limited_get(url)

    if not html:
        result = {
            "quest_id": quest_id,
            "npcId": None,
            "npcName": None,
            "x": None,
            "y": None,
            "whZoneID": None,
            "zoneName": None,
            "fetch_error": True,
        }
        cache_put("quest_giver", cache_name, result)
        return result

    # Primary: extract from Mapper JavaScript data
    mapper_data = _extract_mapper_data(html)
    start_npc = None
    if mapper_data:
        # Extract the quest's primary zone ID from HTML to prefer the correct
        # zone when the mapper contains multiple (e.g. Gilneas + Thaldraszus).
        preferred_zone = _extract_zone_id_from_html(html)
        start_npc = _extract_start_npc_from_mapper(mapper_data, preferred_zone)

    # Fallback: extract from WH.markup data (NPC ID only, no coords)
    if not start_npc:
        start_npc = _extract_start_npc_from_markup(html)

    if start_npc:
        zone_name = resolve_zone_name(start_npc.get("whZoneID"))
        result = {
            "quest_id": quest_id,
            "npcId": start_npc["npcId"],
            "npcName": start_npc["npcName"],
            "x": start_npc["x"],
            "y": start_npc["y"],
            "whZoneID": start_npc["whZoneID"],
            "zoneName": zone_name,
        }
    else:
        result = {
            "quest_id": quest_id,
            "npcId": None,
            "npcName": None,
            "x": None,
            "y": None,
            "whZoneID": None,
            "zoneName": None,
        }

    cache_put("quest_giver", cache_name, result)
    return result


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract quest-giver NPC data for HearthAndSeek quest chains.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch all quests from Wowhead, ignoring cache entirely",
    )
    parser.add_argument(
        "--refresh-zones", action="store_true",
        help="Re-resolve zone names for cached entries using the tooltip API",
    )
    args = parser.parse_args()

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # Load quest chains
    if not QUEST_CHAINS_JSON.exists():
        logger.error("Quest chains file not found: %s", QUEST_CHAINS_JSON)
        logger.error("Run enrich_quest_chains.py first.")
        sys.exit(1)

    with open(QUEST_CHAINS_JSON, "r", encoding="utf-8") as f:
        chains_data = json.load(f)

    quests = chains_data.get("quests", {})
    quest_ids = sorted(int(qid) for qid in quests.keys())

    logger.info("Loaded %d quests from quest_chains.json", len(quest_ids))

    if args.refresh_zones:
        logger.info("")
        logger.info("=" * 60)
        logger.info("Re-resolving zone names via Wowhead tooltip API")
        logger.info("=" * 60)

        # Pre-collect all unique zone IDs from cache for batch resolution
        unique_zone_ids: set[int] = set()
        for quest_id in quest_ids:
            cached = cache_get("quest_giver", str(quest_id))
            if cached and cached.get("whZoneID"):
                unique_zone_ids.add(cached["whZoneID"])

        logger.info("Found %d unique zone IDs across %d quests", len(unique_zone_ids), len(quest_ids))

        # Pre-resolve all zone IDs in batch (efficient: logs stats, caches results)
        from wh_zone_resolver import resolve_zone_names_batch
        resolve_zone_names_batch(unique_zone_ids)

    logger.info("")
    logger.info("=" * 60)
    logger.info("Enriching quest-giver NPC data from Wowhead")
    logger.info("=" * 60)

    # Process each quest
    results: dict[int, dict] = {}
    fetched = 0
    cached_hits = 0
    with_coords = 0
    with_npc_only = 0
    no_data = 0

    for i, quest_id in enumerate(quest_ids):
        # Check cache first for progress reporting
        cached = None if args.force else cache_get("quest_giver", str(quest_id))

        if cached is not None:
            cached_hits += 1
            # Use fetch_quest_giver to handle refresh-zones logic
            data = fetch_quest_giver(
                quest_id,
                force=False,
                refresh_zones=args.refresh_zones,
            )
        else:
            fetched += 1
            if fetched % 50 == 0 or fetched == 1:
                logger.info(
                    "[%d/%d] Fetching quest-giver data (fetched: %d, cached: %d)...",
                    i + 1, len(quest_ids), fetched, cached_hits,
                )
            data = fetch_quest_giver(quest_id, force=args.force)

        if data and data.get("npcId"):
            results[quest_id] = data
            if data.get("x") is not None and data.get("y") is not None:
                with_coords += 1
            else:
                with_npc_only += 1
        else:
            no_data += 1

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("QUEST-GIVER ENRICHMENT SUMMARY")
    logger.info("=" * 60)
    logger.info("Total quests processed:    %d", len(quest_ids))
    logger.info("  Fetched from Wowhead:    %d", fetched)
    logger.info("  From cache:              %d", cached_hits)
    logger.info("Results:")
    logger.info("  With coordinates:        %d", with_coords)
    logger.info("  NPC ID only (no coords): %d", with_npc_only)
    logger.info("  No quest-giver data:     %d", no_data)

    # Write output
    output = {
        "metadata": {
            "total_quests": len(quest_ids),
            "with_coordinates": with_coords,
            "with_npc_only": with_npc_only,
            "no_data": no_data,
        },
        "quest_givers": {
            str(qid): qdata
            for qid, qdata in sorted(results.items())
        },
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("Output: %s", OUTPUT_FILE)
    logger.info(
        "Coverage: %d/%d quests have quest-giver data (%.0f%%)",
        len(results), len(quest_ids),
        100 * len(results) / len(quest_ids) if quest_ids else 0,
    )


if __name__ == "__main__":
    main()
