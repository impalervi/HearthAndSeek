"""
wh_zone_resolver.py - Resolve Wowhead zone IDs to zone names.

Uses the Wowhead tooltip API:
    GET https://nether.wowhead.com/tooltip/zone/{id}
    → {"name": "Gilneas City", "tooltip": "...", "icon": ""}

Results are cached in data/wowhead_cache/ (shared with other scrapers).

Usage as module:
    from wh_zone_resolver import resolve_zone_name, resolve_zone_names_batch
    name = resolve_zone_name(4755)  # → "Gilneas City"
    names = resolve_zone_names_batch({4755, 1519, 12})

Usage standalone (test mode):
    python wh_zone_resolver.py --test
    python wh_zone_resolver.py --lookup 4755 1519 12
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

TOOLTIP_API_URL = "https://nether.wowhead.com/tooltip/zone/{zone_id}"

# Rate limiting: pause between Wowhead API requests (in seconds)
REQUEST_DELAY = 2.0

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

logger = logging.getLogger("wh_zone_resolver")

# ---------------------------------------------------------------------------
# Static overrides for zone IDs where the API name needs correction
# ---------------------------------------------------------------------------

# These take priority over the API. Use sparingly — only when Wowhead returns
# a name that differs from the in-game display name or when we need a specific
# canonical form.
ZONE_NAME_OVERRIDES: dict[int, str] = {
    # Currently empty — API names match in-game names for all known zones.
    # Add entries here if edge cases are discovered:
    #   ZONE_ID: "Canonical In-Game Name",
}

# ---------------------------------------------------------------------------
# Cache helpers (same scheme as enrich_catalog.py / enrich_quest_givers.py)
# ---------------------------------------------------------------------------

def _cache_key(prefix: str, name: str) -> str:
    """Generate a safe filename for a cache entry."""
    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{prefix}_{readable}_{safe}.json"


def _cache_get(prefix: str, name: str) -> Optional[Any]:
    """Read a cached response, or return None if not cached."""
    path = CACHE_DIR / _cache_key(prefix, name)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _cache_put(prefix: str, name: str, data: Any) -> None:
    """Write a response to cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(prefix, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get_json(url: str) -> Optional[dict]:
    """
    Fetch a URL expecting JSON, with rate limiting and error handling.
    Returns parsed JSON dict or None.
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
                    logger.warning(
                        "HTTP %d for %s after %d retries.",
                        resp.status_code, url, max_retries,
                    )
                    return None

            if resp.status_code == 404:
                logger.debug("Zone not found (404): %s", url)
                return None

            if resp.status_code != 200:
                logger.warning("HTTP %d for %s", resp.status_code, url)
                return None

            return resp.json()

        except requests.RequestException as exc:
            logger.error("Request failed for %s: %s", url, exc)
            return None
        except json.JSONDecodeError as exc:
            logger.error("Invalid JSON from %s: %s", url, exc)
            return None

    return None


# ---------------------------------------------------------------------------
# In-memory cache (avoids re-reading disk cache within a single run)
# ---------------------------------------------------------------------------

_mem_cache: dict[int, Optional[str]] = {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_zone_name(wh_zone_id: Optional[int]) -> Optional[str]:
    """
    Resolve a Wowhead zone ID to a zone name.

    Resolution order:
      1. ZONE_NAME_OVERRIDES (static, highest priority)
      2. In-memory cache (within this process)
      3. Disk cache (wowhead_cache/zone_tooltip_*)
      4. Wowhead tooltip API (fetched, then cached)

    Returns the zone name string, or None if resolution fails.
    """
    if wh_zone_id is None:
        return None

    # 1. Static overrides
    if wh_zone_id in ZONE_NAME_OVERRIDES:
        return ZONE_NAME_OVERRIDES[wh_zone_id]

    # 2. In-memory cache
    if wh_zone_id in _mem_cache:
        return _mem_cache[wh_zone_id]

    # 3. Disk cache
    cache_name = str(wh_zone_id)
    cached = _cache_get("zone_tooltip", cache_name)
    if cached is not None:
        name = cached.get("name")
        _mem_cache[wh_zone_id] = name
        return name

    # 4. Fetch from API
    url = TOOLTIP_API_URL.format(zone_id=wh_zone_id)
    data = _rate_limited_get_json(url)

    if data and "name" in data:
        name = data["name"]
        _cache_put("zone_tooltip", cache_name, data)
        _mem_cache[wh_zone_id] = name
        logger.debug("Resolved zone %d -> %s (API)", wh_zone_id, name)
        return name

    # Cache the failure so we don't retry on next call
    _cache_put("zone_tooltip", cache_name, {"name": None, "error": True})
    _mem_cache[wh_zone_id] = None
    logger.warning("Failed to resolve zone ID %d", wh_zone_id)
    return None


def resolve_zone_names_batch(zone_ids: set[int]) -> dict[int, str]:
    """
    Pre-resolve a batch of zone IDs efficiently.

    Returns a dict mapping zone_id -> zone_name for all successfully resolved IDs.
    Logs summary statistics (cached vs fetched vs failed).
    """
    results: dict[int, str] = {}
    stats = {"cached": 0, "fetched": 0, "failed": 0, "override": 0}

    for zone_id in sorted(zone_ids):
        # Check if already resolved (override or memory)
        if zone_id in ZONE_NAME_OVERRIDES:
            results[zone_id] = ZONE_NAME_OVERRIDES[zone_id]
            stats["override"] += 1
            continue

        if zone_id in _mem_cache:
            if _mem_cache[zone_id] is not None:
                results[zone_id] = _mem_cache[zone_id]
            stats["cached"] += 1
            continue

        # Check disk cache
        cached = _cache_get("zone_tooltip", str(zone_id))
        if cached is not None:
            name = cached.get("name")
            _mem_cache[zone_id] = name
            if name:
                results[zone_id] = name
            stats["cached"] += 1
            continue

        # Need to fetch from API
        url = TOOLTIP_API_URL.format(zone_id=zone_id)
        data = _rate_limited_get_json(url)

        if data and "name" in data:
            name = data["name"]
            _cache_put("zone_tooltip", str(zone_id), data)
            _mem_cache[zone_id] = name
            results[zone_id] = name
            stats["fetched"] += 1
            logger.info("Resolved zone %d -> %s", zone_id, name)
        else:
            _cache_put("zone_tooltip", str(zone_id), {"name": None, "error": True})
            _mem_cache[zone_id] = None
            stats["failed"] += 1
            logger.warning("Failed to resolve zone ID %d", zone_id)

    logger.info(
        "Zone resolution: %d total | %d override | %d cached | %d fetched | %d failed",
        len(zone_ids), stats["override"], stats["cached"],
        stats["fetched"], stats["failed"],
    )

    return results


def clear_mem_cache() -> None:
    """Clear the in-memory cache (useful for testing)."""
    _mem_cache.clear()


# ---------------------------------------------------------------------------
# Standalone CLI
# ---------------------------------------------------------------------------

def _run_test() -> None:
    """Run a quick self-test against known zone IDs."""
    test_cases = {
        4755: "Gilneas City",
        1519: "Stormwind City",
        12: "Elwynn Forest",
        13647: "Thaldraszus",
        1: "Dun Morogh",
        1537: "Ironforge",
        8501: "Vol'dun",
    }

    passed = 0
    failed = 0

    print("=" * 50)
    print("wh_zone_resolver self-test")
    print("=" * 50)

    for zone_id, expected in test_cases.items():
        result = resolve_zone_name(zone_id)
        ok = result == expected
        status = "PASS" if ok else "FAIL"
        print(f"  [{status}] zone {zone_id}: got {result!r}, expected {expected!r}")
        if ok:
            passed += 1
        else:
            failed += 1

    print()
    print(f"Results: {passed} passed, {failed} failed")

    if failed > 0:
        sys.exit(1)


def _run_lookup(zone_ids: list[int]) -> None:
    """Look up specific zone IDs and print results."""
    for zone_id in zone_ids:
        name = resolve_zone_name(zone_id)
        if name:
            print(f"  {zone_id} -> {name}")
        else:
            print(f"  {zone_id} -> (not found)")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="Resolve Wowhead zone IDs to zone names via tooltip API.",
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Run self-test with known zone IDs",
    )
    parser.add_argument(
        "--lookup", type=int, nargs="+", metavar="ZONE_ID",
        help="Look up specific zone IDs",
    )
    args = parser.parse_args()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if args.test:
        _run_test()
    elif args.lookup:
        _run_lookup(args.lookup)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
