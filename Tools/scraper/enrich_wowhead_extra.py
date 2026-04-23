#!/usr/bin/env python3
"""
enrich_wowhead_extra.py - One-pass Wowhead scan to collect additional data
for each decor item: alternative sources, drop rates, profession skill
requirements, patch added, and vendor buy costs (cross-reference).

Fetches each item's Wowhead page and extracts all data in ONE pass per item.

Input:  data/enriched_catalog.json
Output: data/enriched_catalog_extra.json

All responses are cached in data/wowhead_cache/ so re-runs skip previously
fetched items.

Usage:
    python enrich_wowhead_extra.py
    python enrich_wowhead_extra.py --limit 10     # process only first 10 items
    python enrich_wowhead_extra.py --force         # re-fetch cached items
"""

import argparse
import json
import logging
import re
import sys
import time
import hashlib
from pathlib import Path
from typing import Any, Optional

import requests

# Wowhead sits behind Cloudflare, which rejects vanilla python-requests
# calls with HTTP 403 by fingerprinting the TLS handshake. curl_cffi
# impersonates Chrome's TLS/JA3 fingerprint so the request looks identical
# to a real browser and Cloudflare lets it through.
try:
    from curl_cffi import requests as _CURL_CFFI  # type: ignore
    _SCRAPER = _CURL_CFFI.Session(impersonate="chrome")
except ImportError:
    _SCRAPER = None

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
INPUT_FILE = DATA_DIR / "enriched_catalog.json"
OUTPUT_FILE = DATA_DIR / "enriched_catalog_extra.json"

REQUEST_DELAY = 2.0  # seconds between requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("enrich_wowhead_extra")

# ---------------------------------------------------------------------------
# Wowhead sourcemore type mapping
# ---------------------------------------------------------------------------
SOURCEMORE_TYPE_MAP = {
    1: "NPC",         # NPC (vendor or drop)
    2: "Treasure",    # Object / treasure chest
    4: "Container",   # Container (lockbox, etc.)
    5: "Quest",       # Quest reward
    6: "Profession",  # Spell/recipe (profession craft)
    10: "Starter",    # Starter gear
    12: "Item",       # Item (created from another item)
}


# ---------------------------------------------------------------------------
# Cache helpers (reuse pattern from enrich_catalog.py)
# ---------------------------------------------------------------------------

def _cache_key(item_id: int) -> str:
    return f"item_extra_{item_id}.json"


def cache_get(item_id: int) -> Optional[dict]:
    path = CACHE_DIR / _cache_key(item_id)
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def cache_put(item_id: int, data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / _cache_key(item_id)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Rate-limited HTTP
# ---------------------------------------------------------------------------

_last_request_time = 0.0


def _rate_limited_get(url: str) -> Optional[str]:
    global _last_request_time

    elapsed = time.time() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)

    # Use curl_cffi's Chrome-impersonating session when available so
    # Wowhead's Cloudflare TLS fingerprint check passes; fall back to
    # plain requests if the dependency isn't installed.
    client = _SCRAPER if _SCRAPER is not None else requests

    try:
        resp = client.get(url, headers=HEADERS, timeout=30)
        _last_request_time = time.time()

        if resp.status_code == 429:
            logger.warning("Rate limited (429). Waiting 30 seconds...")
            time.sleep(30)
            resp = client.get(url, headers=HEADERS, timeout=30)
            _last_request_time = time.time()

        if resp.status_code == 404:
            logger.debug("404 for %s", url)
            return None

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.text

    except requests.RequestException as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None
    except Exception as exc:
        # curl_cffi raises its own exception hierarchy (TooManyRedirects, etc.)
        # that isn't a subclass of RequestException. Treat any other fetch
        # error as a transient failure and continue.
        logger.warning("Fetch error for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Listview data extraction (reused from enrich_catalog.py)
# ---------------------------------------------------------------------------

def _extract_listview_data(html: str, listview_id: str) -> list[dict]:
    """Extract data array from a Wowhead Listview block with the given id."""
    id_pattern = (
        r"new\s+Listview\(\s*\{.*?"
        r"""id:\s*['"]""" + re.escape(listview_id) + r"""['"]"""
    )
    id_match = re.search(id_pattern, html, re.DOTALL)
    if not id_match:
        return []

    rest = html[id_match.end():]
    data_match = re.search(r'data:\s*\[', rest)
    if not data_match:
        return []

    arr_start = rest.index('[', data_match.start())
    depth = 0
    arr_end = None
    in_string = False
    escape_next = False
    for i in range(arr_start, len(rest)):
        c = rest[i]
        if escape_next:
            escape_next = False
            continue
        if c == '\\' and in_string:
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == '[':
            depth += 1
        elif c == ']':
            depth -= 1
            if depth == 0:
                arr_end = i + 1
                break

    if arr_end is None:
        return []

    json_str = rest[arr_start:arr_end]
    try:
        data = json.loads(json_str)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    return []


# ---------------------------------------------------------------------------
# Data extractors
# ---------------------------------------------------------------------------

def _fetch_npc_coords_from_page(npc_id: int) -> Optional[dict]:
    """Fallback: scrape coords out of the full NPC HTML page.

    The Wowhead NPC page embeds a Listview block with `"coords":[[x,y]...]`.
    This catches NPCs whose tooltip API (nether.wowhead.com/tooltip/npc/ID)
    returns an empty ``map`` object (seen on several recently-added
    housing vendors)."""
    client = _SCRAPER if _SCRAPER is not None else requests
    try:
        resp = client.get(f"https://www.wowhead.com/npc={npc_id}",
                          headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception as exc:
        logger.warning("NPC page fetch failed for npcID=%d: %s", npc_id, exc)
        return None

    # "coords":[[x,y]] or "coords":[[x,y],[x2,y2],...]
    m = re.search(r'"coords"\s*:\s*\[\s*\[\s*([\d.]+)\s*,\s*([\d.]+)\s*\]', html)
    if not m:
        return None
    try:
        return {"coords": {"x": float(m.group(1)), "y": float(m.group(2))}}
    except (TypeError, ValueError):
        return None


def fetch_npc_tooltip_coords(npc_id: int) -> Optional[dict]:
    """Fetch NPC coords + Wowhead zone id.

    Primary source: the tooltip API at
    ``https://nether.wowhead.com/tooltip/npc/<id>`` which returns
    ``{"map": {"zone": <whZoneID>, "coords": {"0": [[x,y],...]}}}`` when
    Wowhead has a map for the NPC. For some newly-added vendors the
    tooltip API returns an empty ``"map":{}`` even though the full NPC
    page has coords — so we fall back to scraping the page HTML.

    Returns ``{"coords": {"x", "y"}, "whZoneID": int}`` or ``None`` when
    neither source yields any data. Cached per-NPC.
    """
    cache_path = CACHE_DIR / f"npc_tooltip_{npc_id}.json"
    if cache_path.exists():
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cached = json.load(fh)
            # Return cached when it has either coords or a zone. If both
            # are missing the cache is a negative result — still return it
            # to avoid re-fetching, but the caller may skip using it.
            return cached
        except Exception:
            pass

    client = _SCRAPER if _SCRAPER is not None else requests
    try:
        resp = client.get(f"https://nether.wowhead.com/tooltip/npc/{npc_id}",
                          headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            data = None
        else:
            data = resp.json()
    except Exception as exc:
        logger.warning("NPC tooltip fetch failed for npcID=%d: %s", npc_id, exc)
        data = None

    result = {"coords": None, "whZoneID": None}
    map_data = (data or {}).get("map") if isinstance(data, dict) else None
    if isinstance(map_data, dict):
        if map_data.get("zone") is not None:
            result["whZoneID"] = map_data.get("zone")
        for _, pairs in (map_data.get("coords") or {}).items():
            if isinstance(pairs, list) and pairs and isinstance(pairs[0], list) and len(pairs[0]) >= 2:
                result["coords"] = {"x": pairs[0][0], "y": pairs[0][1]}
                break

    # Fallback: if tooltip API gave no coords, try scraping the NPC page.
    if result["coords"] is None:
        page_result = _fetch_npc_coords_from_page(npc_id)
        if page_result and page_result.get("coords"):
            result["coords"] = page_result["coords"]

    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)
    except Exception:
        pass

    if result["coords"] is None and result["whZoneID"] is None:
        return None
    return result


def _extract_sold_by_block(html: str) -> Optional[str]:
    """Return the raw JSON/JS text of Wowhead's sold-by Listview block, or
    None if the page doesn't render one.

    The item page renders vendors via:
        new Listview({ id: 'sold-by', ..., data: [ {...}, {...} ] });

    We return everything between ``id:'sold-by'`` and its matching close
    so callers can substring-search for e.g. ``"id":<npcID>`` to confirm
    an NPC is actually rendered as a seller. Returning the raw substring
    (rather than parsed JSON) avoids fighting Wowhead's trailing-comma
    and bare-identifier JS, which would require a JS-aware parser.
    """
    if not html:
        return None
    # Match both `id: 'sold-by'` and `id:"sold-by"` styles, with any
    # intervening whitespace.
    m = re.search(r"id\s*:\s*['\"]sold-by['\"]", html)
    if not m:
        return None
    # Find the containing `new Listview({ ... })` by scanning outward.
    # Walk backward to the matching `{` of the Listview config object,
    # then forward counting braces to find the matching `}`.
    start = html.rfind("{", 0, m.start())
    if start < 0:
        return None
    depth = 1
    i = start + 1
    while i < len(html) and depth > 0:
        ch = html[i]
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                break
        elif ch == '"':
            # skip string literal so braces inside strings don't derail
            i += 1
            while i < len(html) and html[i] != '"':
                if html[i] == '\\':
                    i += 2
                    continue
                i += 1
        i += 1
    if depth != 0:
        return None
    return html[start:i + 1]


def _has_item_data_markers(html: str) -> bool:
    """Return True if the response looks like a real Wowhead item page.

    We rely on sourcemore extraction (and friends) pulling data out of the
    `WH.Gatherer.addData(...)` / `g_items[...]` / `"jsonequip":{...}` blobs
    the page embeds. A Cloudflare challenge stub or truncated response can
    200 OK but contain none of those markers; in that case every extractor
    silently returns empty and we'd cache a hollow result. This check lets
    the caller distinguish "item genuinely has no sourcemore" from "page
    didn't load what we needed"."""
    if not html or len(html) < 4096:
        # Real item pages are ~30KB+; anything tiny is almost certainly a
        # challenge/error page, regardless of HTTP status.
        return False
    return (
        "WH.Gatherer.addData" in html
        or "g_items[" in html
        or '"jsonequip"' in html
    )


def extract_decor_sources(html: str) -> list[dict]:
    """Extract housing decor sources from the Wowhead item-page JSON.

    Wowhead emits a decor-specific shape alongside the legacy sourcemore
    blob; for some (often newly added) items the sourcemore entry is
    missing entirely but this shape is present:

        "sources":[{"sourceType":5,"entityType":1,"entityId":264056,
                    "name":"Disguised Decor Duel Vendor",
                    "area":{"id":15969,"name":"Silvermoon City",
                            "uiMap":2393,"coords":[31.6,76.8]}}]

    This is strictly richer than sourcemore — it includes the in-game
    ``uiMap`` (WoW mapID) and named zone, so we don't need the
    Wowhead-zone-id-to-name translation for these entries.

    Returns a list of entries in the same canonical shape as
    ``extract_sourcemore`` plus optional ``mapID``, ``zone``, ``coords``,
    ``whZoneID`` when the ``area`` sub-object was present. We currently
    treat ``entityType == 1`` as NPC (matches every housing decor example
    we've observed); unknown types are still emitted so the caller can
    see them, with a sentinel sourceType.
    """
    results: list[dict] = []
    seen: set[tuple[str, int | str]] = set()

    # Find each `"sources":[...]` block whose first entry is a decor-shape
    # object (has `sourceType` + `entityType`). The JSON may contain nested
    # `[coords]` arrays, so we can't use a flat `[^\]]*?` regex — scan
    # forward counting brackets to find the matching close.
    for m in re.finditer(r'"sources"\s*:\s*\[\s*(?=\{\s*"sourceType")', html):
        start = m.end() - 1  # index of the opening '{' of the first object
        depth = 1
        i = start
        # Walk to find the matching close of the array's '['
        # The opening '[' is at (m.end() - position of '{' minus 1), find it:
        arr_open = html.rfind('[', 0, m.end())
        depth = 1
        i = arr_open + 1
        while i < len(html) and depth > 0:
            ch = html[i]
            if ch == '[':
                depth += 1
            elif ch == ']':
                depth -= 1
                if depth == 0:
                    break
            elif ch == '"':
                # skip string literal
                i += 1
                while i < len(html) and html[i] != '"':
                    if html[i] == '\\':
                        i += 2
                        continue
                    i += 1
            i += 1
        if depth != 0:
            continue
        block = html[arr_open:i + 1]
        try:
            arr = json.loads(block)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(arr, list):
            continue
        for entry in arr:
            if not isinstance(entry, dict):
                continue
            et = entry.get("entityType")
            eid = entry.get("entityId")
            name = entry.get("name")
            # entityType 1 == NPC in every observed case
            source_type = "NPC" if et == 1 else f"Unknown({et})"
            key = (source_type, eid or name or "")
            if key in seen:
                continue
            seen.add(key)
            out = {"sourceType": source_type}
            if name:
                out["sourceDetail"] = name
            if eid:
                out["sourceID"] = eid
            area = entry.get("area")
            if isinstance(area, dict):
                wh_zone = area.get("id")
                zone_name = area.get("name")
                ui_map = area.get("uiMap")
                coords = area.get("coords")
                if wh_zone is not None:
                    out["whZoneID"] = wh_zone
                if zone_name:
                    out["zone"] = zone_name
                if ui_map:
                    out["mapID"] = ui_map
                if isinstance(coords, list) and len(coords) >= 2:
                    out["coords"] = {"x": coords[0], "y": coords[1]}
            results.append(out)
    return results


def extract_sourcemore(html: str) -> list[dict]:
    """
    Extract sourcemore array from Wowhead page JavaScript.

    Wowhead embeds source data in:
      WH.Gatherer.addData(ITEM_TYPE, ID, {"sourcemore":[...],...});
    or in g_items[ID] = {"sourcemore":[...],...};
    """
    sources = []
    seen = set()

    # Pattern 1: WH.Gatherer.addData blocks
    for m in re.finditer(r'"sourcemore"\s*:\s*\[([^\]]*)\]', html):
        try:
            arr = json.loads("[" + m.group(1) + "]")
            for entry in arr:
                if isinstance(entry, dict):
                    t = entry.get("t")
                    ti = entry.get("ti")  # type instance ID (npcID, questID, etc.)
                    n = entry.get("n")    # name
                    z = entry.get("z")    # Wowhead zone ID (may be present even
                                          # when the NPC tooltip has no map data)
                    source_type = SOURCEMORE_TYPE_MAP.get(t, f"Unknown({t})")
                    key = (source_type, ti or n or "")
                    if key not in seen:
                        seen.add(key)
                        src = {"sourceType": source_type}
                        if n:
                            src["sourceDetail"] = n
                        if ti:
                            src["sourceID"] = ti
                        if z is not None:
                            src["whZoneID"] = z
                        sources.append(src)
        except (json.JSONDecodeError, TypeError):
            continue

    return sources


def extract_drop_rate(html: str) -> Optional[float]:
    """
    Extract drop rate from the 'dropped-by' Listview block.

    Each NPC entry has 'count' and 'outof' fields. Take the highest drop rate.
    Some entries have 'percentOverride' instead.
    """
    entries = _extract_listview_data(html, "dropped-by")
    if not entries:
        return None

    best_rate = 0.0
    for entry in entries:
        percent = entry.get("percentOverride")
        if percent is not None and isinstance(percent, (int, float)):
            best_rate = max(best_rate, float(percent))
            continue

        count = entry.get("count", 0)
        outof = entry.get("outof", 0)
        if outof > 0 and count > 0:
            rate = (count / outof) * 100
            best_rate = max(best_rate, rate)

    return round(best_rate, 2) if best_rate > 0 else None


def extract_profession_skill(html: str) -> Optional[str]:
    """
    Extract profession skill from the 'created-by' Listview block.

    Each spell entry has 'name' (spell name) and 'learnedat' (skill level).
    Format: "Inscription (50)"
    """
    entries = _extract_listview_data(html, "created-by")
    if not entries:
        return None

    for entry in entries:
        name = entry.get("name", "")
        learnedat = entry.get("learnedat", 0)
        if name and learnedat:
            return f"{name} ({learnedat})"
        elif name:
            return name

    return None


def extract_vendor_costs(html: str) -> list[dict]:
    """
    Extract vendor buy costs from the 'sold-by' Listview block.

    Each NPC entry has a 'cost' field:
      [goldInCopper, [[currencyID, amount], ...]]
    or just [goldInCopper] when gold-only.
    """
    entries = _extract_listview_data(html, "sold-by")
    if not entries:
        return []

    # Take the first vendor's cost (they should all be the same)
    for entry in entries:
        cost = entry.get("cost")
        if not cost or not isinstance(cost, list):
            continue

        costs = []
        gold_copper = cost[0] if len(cost) > 0 else 0
        if gold_copper and isinstance(gold_copper, (int, float)) and gold_copper > 0:
            # Convert copper to gold
            gold = gold_copper / 10000
            costs.append({
                "amount": int(gold) if gold == int(gold) else gold,
                "currencyID": 0,  # 0 = gold
            })

        if len(cost) > 1 and isinstance(cost[1], list):
            for currency_entry in cost[1]:
                if isinstance(currency_entry, list) and len(currency_entry) >= 2:
                    costs.append({
                        "currencyID": currency_entry[0],
                        "amount": currency_entry[1],
                    })

        if costs:
            return costs

    return []


def extract_patch_added(html: str) -> Optional[str]:
    """
    Extract the patch version when the item was added.

    Wowhead embeds this in:
      "added":"11.1.0" or added: "11.1.0"
    Or in the tooltip as "Added in patch X.Y.Z"
    """
    # Pattern 1: JSON field "added":"X.Y.Z"
    m = re.search(r'"added"\s*:\s*"(\d+\.\d+\.\d+)"', html)
    if m:
        return m.group(1)

    # Pattern 2: data-added="XXXXX" (build number, not useful)
    # Pattern 3: "Added in patch X.Y.Z" in tooltip text
    m = re.search(r'Added in patch (\d+\.\d+\.\d+)', html)
    if m:
        return m.group(1)

    return None


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_item(item_id: int, force: bool = False) -> Optional[dict]:
    """Fetch and extract all extra data for a single item."""
    if not force:
        cached = cache_get(item_id)
        if cached is not None:
            return cached

    url = f"https://www.wowhead.com/item={item_id}"
    html = _rate_limited_get(url)
    if not html:
        result = {"itemID": item_id, "error": "fetch_failed"}
        cache_put(item_id, result)
        return result

    # Sanity check: a genuinely successful page has the WH.Gatherer/g_items
    # markers. A Cloudflare challenge stub or truncated response can return
    # HTTP 200 + valid HTML but no item data, and our extractors would
    # silently yield empty results. Don't cache those — treat as a transient
    # failure so a later run can re-fetch once Wowhead responds properly.
    if not _has_item_data_markers(html):
        logger.warning("Incomplete Wowhead page for itemID=%d (%d bytes); "
                       "skipping cache write so a retry can backfill",
                       item_id, len(html))
        return {"itemID": item_id, "error": "incomplete_page"}

    # Merge both extractors.
    #
    # extract_sourcemore uses the legacy format Wowhead's visible UI still
    # renders ("Sold by" listview, breadcrumbs). If an NPC is in sourcemore,
    # we trust it — it appears ~5-7 times across the page.
    #
    # extract_decor_sources parses the modern housing-catalog JSON. It is
    # strictly richer (zone name + in-game mapID + coords) but sometimes
    # carries stale / unverified / datamined defaults that Wowhead itself
    # does NOT render anywhere visible (observed 2026-04-22 on several
    # items: Dornogal Opals and Suramar Arcfruit Bowl). Those items have
    # ONLY the orphan JSON blob; the NPC name appears just once in the
    # whole page. Never promote such an entry to a primary vendor — use
    # the decor shape only to enrich NPCs that sourcemore already confirms.
    sm_entries = extract_sourcemore(html)
    decor_entries = extract_decor_sources(html)

    # Early-warning signal: if the page clearly contains a decor-shape
    # block (`"sources":[{"sourceType"` pattern) but our extractor yielded
    # nothing, Wowhead may have changed the shape. Log so upstream drift
    # is surfaced early instead of silently producing empty results.
    if decor_entries == [] and '"sources":[{"sourceType"' in html:
        logger.warning(
            "itemID=%d: Wowhead page contains a decor-shape sources block but "
            "extract_decor_sources() returned 0 entries — extractor may be out "
            "of sync with Wowhead's HTML structure.", item_id,
        )

    # Trust a decor_sources NPC only when the page's visible UI actually
    # renders it. The authoritative marker is Wowhead's "Sold By" Listview
    # block: rendered vendors appear inside
    #   new Listview({id:'sold-by',..., data:[{...,"name":"<NPC>",...}]})
    # Orphan JSON entries (stale/datamined defaults) never appear in that
    # block. This is more robust than a plain substring count, which can
    # be fooled by short names that happen to be substrings of unrelated
    # strings on the page.
    #
    # We also fall back to an occurrence-count heuristic (≥ 3) for NPCs
    # rendered in unusual places (breadcrumbs, cross-sell panels, etc.)
    # that we haven't explicitly catalogued. The occurrence count uses
    # word-boundary matching to avoid the substring pitfall.
    OCCURRENCE_THRESHOLD = 3
    sold_by_block = _extract_sold_by_block(html)
    trusted_decor: list[dict] = []
    orphan_names: list[str] = []
    for entry in decor_entries:
        name = entry.get("sourceDetail") or ""
        npc_id = entry.get("sourceID")
        if not name:
            continue
        # Primary signal: NPC appears in the sold-by listview data, either
        # by name (quoted) or by id (unambiguous).
        rendered_in_soldby = False
        if sold_by_block:
            id_marker = f'"id":{npc_id}' if npc_id is not None else None
            name_marker = f'"name":"{name}"'
            if (name_marker in sold_by_block
                    or (id_marker and id_marker in sold_by_block)):
                rendered_in_soldby = True
        # Secondary signal: NPC name appears several times in the page as
        # a whole token (not a substring). This catches "rendered but not
        # in sold-by" corner cases like item breadcrumbs or tab lists.
        name_pattern = re.compile(r'\b' + re.escape(name) + r'\b')
        occurrences = len(name_pattern.findall(html))
        if rendered_in_soldby or occurrences >= OCCURRENCE_THRESHOLD:
            trusted_decor.append(entry)
        else:
            orphan_names.append(f"{name} (occ={occurrences})")
    if orphan_names:
        logger.info(
            "  itemID=%d: dropped %d decor_sources entry(ies) that appear only "
            "in isolated JSON (not rendered by Wowhead UI): %s",
            item_id, len(orphan_names), orphan_names,
        )

    by_key: dict[tuple, dict] = {}
    for entry in sm_entries + trusted_decor:
        key = (entry.get("sourceType"),
               entry.get("sourceID") or entry.get("sourceDetail") or "")
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = entry
        else:
            # Merge: fields present in either win (the decor shape often
            # has zone/mapID/coords that sourcemore lacks).
            for k, v in entry.items():
                if v is not None and existing.get(k) in (None, ""):
                    existing[k] = v
    additional_sources = list(by_key.values())

    # For each NPC sourcemore entry, opportunistically fetch tooltip coords
    # so downstream stages can wire up the navigate button without having
    # to re-derive the location. The NPC tooltip API frequently returns an
    # empty `"map":{}` — especially for new / recently added vendors — so
    # we preserve the `whZoneID` that came from the sourcemore blob even
    # when coords are missing. Zone alone is enough for the addon to show
    # a real source, while coords enable precise waypoints when available.
    for entry in additional_sources:
        if entry.get("sourceType") != "NPC":
            continue
        npc_id = entry.get("sourceID")
        if not npc_id:
            continue
        tooltip = fetch_npc_tooltip_coords(int(npc_id))
        if tooltip:
            if tooltip.get("coords"):
                entry["coords"] = tooltip["coords"]
            # Prefer tooltip zone only if one wasn't already in sourcemore.
            if tooltip.get("whZoneID") and entry.get("whZoneID") is None:
                entry["whZoneID"] = tooltip["whZoneID"]

    result = {
        "itemID": item_id,
        "additionalSources": additional_sources,
        "dropRate": extract_drop_rate(html),
        "professionSkill": extract_profession_skill(html),
        "wowheadVendorCosts": extract_vendor_costs(html),
        "patchAdded": extract_patch_added(html),
    }

    cache_put(item_id, result)
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="One-pass Wowhead scan for extra item data.",
    )
    parser.add_argument(
        "--limit", "-l", type=int, default=0,
        help="Process only the first N items (0 = all).",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Re-fetch items even if cached.",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="Skip the first N items.",
    )
    parser.add_argument(
        "--items", type=str, default="",
        help="Comma-separated itemIDs to process exclusively (implies --force "
             "for those items). Useful for back-filling a handful of items "
             "whose cache is empty due to a transient Cloudflare response.",
    )
    parser.add_argument(
        "--decor-ids", type=str, default="",
        help="Comma-separated decorIDs to resolve to itemIDs and re-fetch "
             "(implies --force). Useful when you know the decor ID from a "
             "/hs dump but not the itemID.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)

    if not INPUT_FILE.exists():
        logger.error("Input file not found: %s", INPUT_FILE)
        sys.exit(1)

    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    logger.info("Loaded %d items from %s", len(catalog), INPUT_FILE)

    # Filter to items with valid itemIDs
    items_to_process = [
        item for item in catalog
        if item.get("itemID") and item["itemID"] > 0
    ]
    logger.info("Items with valid itemID: %d", len(items_to_process))

    # --items / --decor-ids: narrow to an explicit set and force re-fetch.
    # This is the primary back-fill path when specific items are stuck with
    # empty Wowhead enrichment due to a prior transient response.
    target_item_ids: set[int] = set()
    if args.items:
        target_item_ids.update(
            int(x) for x in args.items.split(",") if x.strip().isdigit()
        )
    if args.decor_ids:
        decor_targets = {int(x) for x in args.decor_ids.split(",") if x.strip().isdigit()}
        for it in catalog:
            if it.get("decorID") in decor_targets and it.get("itemID"):
                target_item_ids.add(int(it["itemID"]))
    if target_item_ids:
        before = len(items_to_process)
        items_to_process = [
            it for it in items_to_process if int(it["itemID"]) in target_item_ids
        ]
        args.force = True  # explicit target list implies force-refresh
        logger.info(
            "Targeted run: %d item(s) requested, %d matched catalog entries (was %d)",
            len(target_item_ids), len(items_to_process), before,
        )
        missing = target_item_ids - {int(it["itemID"]) for it in items_to_process}
        if missing:
            logger.warning("Targets not found in catalog: %s", sorted(missing))

    if args.offset:
        items_to_process = items_to_process[args.offset:]
        logger.info("Skipping first %d items, %d remaining", args.offset, len(items_to_process))

    if args.limit:
        items_to_process = items_to_process[:args.limit]
        logger.info("Processing first %d items only", len(items_to_process))

    # Check how many are already cached
    cached_count = sum(1 for item in items_to_process if cache_get(item["itemID"]) is not None)
    need_fetch = len(items_to_process) - cached_count if not args.force else len(items_to_process)
    logger.info("Already cached: %d, Need to fetch: %d", cached_count, need_fetch)
    if need_fetch > 0:
        est_time = need_fetch * REQUEST_DELAY
        logger.info("Estimated time: %.0f seconds (%.1f minutes)", est_time, est_time / 60)

    # Process items
    results: dict[int, dict] = {}
    stats = {
        "total": len(items_to_process),
        "cached": 0,
        "fetched": 0,
        "errors": 0,
        "with_sources": 0,
        "with_drop_rate": 0,
        "with_profession": 0,
        "with_vendor_cost": 0,
        "with_patch": 0,
    }

    for i, item in enumerate(items_to_process, 1):
        item_id = item["itemID"]
        decor_id = item.get("decorID", "?")

        # Check cache first
        if not args.force:
            cached = cache_get(item_id)
            if cached is not None:
                results[item_id] = cached
                stats["cached"] += 1
                # Tally stats from cache
                if cached.get("additionalSources"):
                    stats["with_sources"] += 1
                if cached.get("dropRate"):
                    stats["with_drop_rate"] += 1
                if cached.get("professionSkill"):
                    stats["with_profession"] += 1
                if cached.get("wowheadVendorCosts"):
                    stats["with_vendor_cost"] += 1
                if cached.get("patchAdded"):
                    stats["with_patch"] += 1
                continue

        logger.info("[%d/%d] Fetching itemID=%d (decorID=%s) %s",
                    i, stats["total"], item_id, decor_id, item.get("name", ""))

        result = process_item(item_id, force=args.force)

        if result and not result.get("error"):
            results[item_id] = result
            stats["fetched"] += 1
            if result.get("additionalSources"):
                stats["with_sources"] += 1
            if result.get("dropRate"):
                stats["with_drop_rate"] += 1
            if result.get("professionSkill"):
                stats["with_profession"] += 1
            if result.get("wowheadVendorCosts"):
                stats["with_vendor_cost"] += 1
            if result.get("patchAdded"):
                stats["with_patch"] += 1
        else:
            stats["errors"] += 1

    # Write merged output. When running a targeted subset (--items /
    # --decor-ids), preserve existing entries so we don't destroy the
    # rest of the catalog's enrichment data. Format is a dict keyed by
    # itemID string (consumed by output_catalog_lua.py); stay compatible.
    is_targeted = bool(args.items) or bool(args.decor_ids)

    # `results` is dict[int, dict] (or list in older code paths). Normalize
    # to a string-keyed dict for the on-disk format.
    if isinstance(results, dict):
        items_map = {str(k): v for k, v in results.items() if v}
    elif isinstance(results, list):
        items_map = {str(e.get("itemID")): e for e in results if e.get("itemID")}
    else:
        items_map = {}

    if is_targeted and OUTPUT_FILE.exists():
        try:
            with open(OUTPUT_FILE, encoding="utf-8") as fh:
                prior = json.load(fh)
            prior_items = prior.get("items") if isinstance(prior, dict) else None
            if isinstance(prior_items, dict):
                merged = dict(prior_items)
                merged.update(items_map)  # new entries overwrite stale ones
                items_map = merged
                logger.info(
                    "Targeted run: merged %d updated item(s) into existing "
                    "%s (now %d entries total)",
                    stats["fetched"], OUTPUT_FILE.name, len(items_map),
                )
            elif isinstance(prior_items, list):
                # Old format (list) — convert and merge
                merged = {str(e.get("itemID")): e for e in prior_items if e.get("itemID")}
                merged.update(items_map)
                items_map = merged
        except Exception as exc:
            logger.warning("Failed to merge into existing %s: %s — writing "
                           "targeted subset only", OUTPUT_FILE.name, exc)

    output = {
        "metadata": {
            "total_items": stats["total"],
            "items_fetched": stats["fetched"],
            "items_cached": stats["cached"],
            "items_errored": stats["errors"],
            "targeted_run": is_targeted,
        },
        "items": items_map,
    }

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info("")
    logger.info("=" * 60)
    logger.info("ENRICHMENT EXTRA SUMMARY")
    logger.info("=" * 60)
    logger.info("Total items:          %d", stats["total"])
    logger.info("Fetched from Wowhead: %d", stats["fetched"])
    logger.info("Loaded from cache:    %d", stats["cached"])
    logger.info("Errors:               %d", stats["errors"])
    logger.info("With alt sources:     %d", stats["with_sources"])
    logger.info("With drop rate:       %d", stats["with_drop_rate"])
    logger.info("With profession:      %d", stats["with_profession"])
    logger.info("With vendor costs:    %d", stats["with_vendor_cost"])
    logger.info("With patch added:     %d", stats["with_patch"])
    logger.info("")
    logger.info("Output: %s", OUTPUT_FILE)

    # Stamp cache metadata (non-fatal if it fails)
    try:
        from pipeline_metadata import stamp_after_scrape
        wh_files = len([f for f in CACHE_DIR.iterdir() if f.suffix == ".json" and f.name != "_metadata.json"])
        stamp_after_scrape(CACHE_DIR, source="wowhead", total_files=wh_files)
    except Exception as exc:
        logger.warning("Failed to update cache metadata: %s", exc)


if __name__ == "__main__":
    main()
