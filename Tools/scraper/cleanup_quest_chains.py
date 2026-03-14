"""
cleanup_quest_chains.py — Rebuild quest prereqs from verified Wowhead data.

The enrich_quest_chains.py bulk-import can create falsely long chains by
importing entire zone storylines as linear prereq chains. This script
rebuilds quest_chains.json using verified data sources:

1. Series data (preferred) — short, verified quest chains from Wowhead
2. Storyline data (fallback) — when no Series exists, uses the storyline
   order up to the quest's position
3. Smart chain management — fetches missing Series data from Wowhead for
   decor quests with long chains, truncates when no Series exists
4. Manual fixes — for chains verified outside of Wowhead Series/Storyline

Smart fallback rules:
  - Short Series (< MIN_SERIES_TO_OVERLAY entries) are skipped when a
    Storyline is available (short Series only cover the tail)
  - Storyline data is processed first; Series data extends but never
    overrides Storyline-connected quests
"""

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional

try:
    import requests
except ImportError:
    requests = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cleanup_quest_chains")

SCRIPT_DIR = Path(__file__).resolve().parent
QUEST_CHAINS_JSON = SCRIPT_DIR / "data" / "quest_chains.json"
ENRICHED_CATALOG = SCRIPT_DIR / "data" / "enriched_catalog.json"
CACHE_DIR = SCRIPT_DIR / "data" / "wowhead_cache"

# ---------------------------------------------------------------------------
# Chain length management constants
# ---------------------------------------------------------------------------

# Chains longer than this trigger a Wowhead fetch for missing Series data
LONG_CHAIN_THRESHOLD = 15

# Skip Series data when it has fewer than this many entries and a Storyline
# is available (short Series only cover the tail; Storyline is more complete)
MIN_SERIES_TO_OVERLAY = 5

# ---------------------------------------------------------------------------
# HTTP + cache helpers (mirrors enrich_quest_chains.py for shared cache)
# ---------------------------------------------------------------------------

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

REQUEST_DELAY = 0.75
_last_request_time = 0.0


def _cache_key(prefix: str, name: str) -> str:
    """Generate a safe filename for a cache entry (same as enrich_quest_chains)."""
    safe = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    readable = re.sub(r'[^\w\-]', '_', name)[:40]
    return f"{prefix}_{readable}_{safe}.json"


def cache_get(prefix: str, name: str) -> Optional[Any]:
    """Read a cached response."""
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


def _rate_limited_get(url: str) -> Optional[str]:
    """Fetch a URL with rate limiting."""
    global _last_request_time

    if requests is None:
        logger.warning("requests library not available, skipping fetch: %s", url)
        return None

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

        if resp.status_code in (404, 403):
            logger.warning("HTTP %d: %s", resp.status_code, url)
            return None

        if resp.status_code != 200:
            logger.warning("HTTP %d for %s", resp.status_code, url)
            return None

        return resp.text
    except Exception as exc:
        logger.error("Request failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Wowhead quest page parsing (mirrors enrich_quest_chains.py)
# ---------------------------------------------------------------------------

def _parse_series_table(html: str, quest_id: int) -> list[dict]:
    """Parse <table class="series"> from Wowhead quest page."""
    results = []
    series_match = re.search(
        r'<table\s+class="series"[^>]*>(.*?)</table>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not series_match:
        return results

    table_html = series_match.group(1)
    for row_match in re.finditer(r'<tr>(.*?)</tr>', table_html, re.DOTALL):
        row_html = row_match.group(1)
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
            bold_match = re.search(r'<b>([^<]+)</b>', row_html)
            if bold_match:
                results.append({
                    "quest_id": quest_id,
                    "name": bold_match.group(1).strip(),
                    "is_current": True,
                })
    return results


def _parse_storyline_list(html: str, quest_id: int) -> tuple[Optional[str], list[dict]]:
    """Parse storyline section from Wowhead quest page."""
    storyline_name = None
    results = []

    container_match = re.search(
        r'<div\s+class="quick-facts-storyline-list"[^>]*>(.*?)</div>',
        html, re.DOTALL | re.IGNORECASE,
    )
    if not container_match:
        return storyline_name, results

    container_html = container_match.group(1)
    ol_match = re.search(r'<ol[^>]*>(.*?)</ol>', container_html, re.DOTALL)
    if not ol_match:
        return storyline_name, results

    ol_html = ol_match.group(1)
    for li_match in re.finditer(r'<li[^>]*?(?:class="([^"]*)")?[^>]*>(.*?)</li>', ol_html, re.DOTALL):
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

    # Extract storyline name
    container_start = html.find('class="quick-facts-storyline-list"')
    if container_start > 0:
        search_area = html[max(0, container_start - 500):container_start]
        name_match = re.search(
            r'<a[^>]*href="[^"]*?/storyline/[^"]*"[^>]*>([^<]+)</a>',
            search_area,
        )
        if name_match:
            storyline_name = name_match.group(1).strip()

    return storyline_name, results


def fetch_quest_series(quest_id: int) -> Optional[dict]:
    """Fetch and parse Series + Storyline data for a quest from Wowhead.

    Returns cached data if available, otherwise fetches from Wowhead.
    Result format matches enrich_quest_chains.py cache format.
    """
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
    name = None
    pattern = rf'\$\.extend\(g_quests\[{quest_id}\],\s*(\{{.*?\}})\)'
    match = re.search(pattern, html, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            name = data.get("name")
        except json.JSONDecodeError:
            pass
    if not name:
        title_match = re.search(
            r'<title>([^<]+?)(?:\s*-\s*Quest\s*-\s*World of Warcraft)?</title>',
            html, re.IGNORECASE,
        )
        if title_match:
            name = title_match.group(1).strip()

    series = _parse_series_table(html, quest_id)
    storyline_name, storyline = _parse_storyline_list(html, quest_id)

    result = {
        "quest_id": quest_id,
        "name": name,
        "series": series,
        "storyline_name": storyline_name,
        "storyline": storyline,
        "prereqs": [],
    }
    cache_put("quest_chain", str(quest_id), result)
    return result


# ---------------------------------------------------------------------------
# Manual fixes for chains verified outside Wowhead Series data.
# Format: quest_id -> {"prereqs": [...], "name": str (optional),
#                       "storyline_name": str (optional)}
# These override any other source of prereqs.
# ---------------------------------------------------------------------------
MANUAL_FIXES: dict[int, dict] = {
    # === Elwynn Forest: The Escape chain ===
    # Verified via Wowhead individual quest pages.
    # Chain: 60 → 26150 → 106 → 111 → 107 → 112 → 114
    60:    {"prereqs": []},                                   # Kobold Candles (start)
    26150: {"prereqs": [60],  "name": "A Visit With Maybell"},  # new entry
    106:   {"prereqs": [26150]},                              # Young Lovers
    111:   {"prereqs": [106]},                                # Speak with Gramma
    107:   {"prereqs": [111]},                                # Note to William
    112:   {"prereqs": [107]},                                # Collecting Kelp
    114:   {"prereqs": [112]},                                # The Escape (decor)

    # === The Light of K'aresh ===
    # Quest 86820 is position 1 of "The Light of K'aresh" (4 quests).
    # Parser mistakenly picked up "Catch Up: War Within: Wrapper 4" storyline
    # (21 entries) instead. The actual series is:
    # 86820 → 86456 → 86457 → 86458
    86820: {"prereqs": []},                                   # Manaforge Omega: Dimensius Looms (start)

    # === Burning Steppes: Shadowforge Lamppost (decorID 11131) ===
    # Wowhead Series only shows the last 3 quests, but Storyline confirms
    # the full chain starts from "Done Nothing Wrong" (9 quests).
    # Alliance chain: 28172 → 28174 → 28177 → 28178 → 28179 → 28180 → 28181 → 28182 → 28183
    28172: {"prereqs": [],      "name": "Done Nothing Wrong"},         # Alliance start
    28174: {"prereqs": [28172], "name": "Burning Vengeance"},
    28177: {"prereqs": [28174], "name": "Stocking Up"},
    28178: {"prereqs": [28177], "name": "A Future Project"},
    28179: {"prereqs": [28178], "name": "Mud Hunter"},
    28180: {"prereqs": [28179], "name": "The Sand, the Cider, and the Orb"},
    28181: {"prereqs": [28180]},                                       # Warlocks Have the Neatest Stuff
    28182: {"prereqs": [28181]},                                       # Shadow Boxing
    28183: {"prereqs": [28182]},                                       # Return to Keeshan (decor)
    # Horde chain: 28417 → 28418 → 28419 → 28420 → 28421 → 28422 → 28423 → 28424 → 28425
    28417: {"prereqs": [],      "name": "Done Nothing Wrong"},         # Horde start
    28418: {"prereqs": [28417], "name": "Burning Vengeance"},
    28419: {"prereqs": [28418], "name": "Stocking Up"},
    28420: {"prereqs": [28419], "name": "A Future Project"},
    28421: {"prereqs": [28420], "name": "Mud Hunter"},
    28422: {"prereqs": [28421], "name": "The Sand, the Cider, and the Orb"},
    28423: {"prereqs": [28422]},                                       # Warlocks Have the Neatest Stuff
    28424: {"prereqs": [28423]},                                       # Shadow Boxing
    28425: {"prereqs": [28424]},                                       # Return to Ariok (decor)

    # === Silverpine Forest: full 53-quest storyline ===
    # Wowhead storyline + comments confirm the full chain from "The Warchief
    # Cometh" (26965) through to "Pyrewood's Fall" (27550) at position 53.
    # Also fixes chain for "Lordaeron" (27098, decor) at position 19.
    # Long chain — UI has smart truncation (CHAIN_MAX_VISIBLE) for display.
    26965: {"prereqs": []},                                            # 1. The Warchief Cometh (start)
    26992: {"prereqs": [26965]},                                       # 2. Agony Abounds
    26995: {"prereqs": [26992]},                                       # 3. Guts and Gore
    26989: {"prereqs": [26995]},                                       # 4. The Gilneas Liberation Front
    26998: {"prereqs": [26989]},                                       # 5. Iterating Upon Success
    27039: {"prereqs": [26998]},                                       # 6. Dangerous Intentions
    27045: {"prereqs": [27039]},                                       # 7. Waiting to Exsanguinate
    27056: {"prereqs": [27045]},                                       # 8. Belmont's Report
    27065: {"prereqs": [27056]},                                       # 9. The Warchief's Fleet
    27082: {"prereqs": [27065]},                                       # 10. Playing Dirty
    27069: {"prereqs": [27082]},                                       # 11. Steel Thunder
    27073: {"prereqs": [27069]},                                       # 12. Give 'em Hell!
    27088: {"prereqs": [27073]},                                       # 13. It's Only Poisonous if You Ingest It
    27093: {"prereqs": [27088]},                                       # 14. Lost in the Darkness
    27094: {"prereqs": [27093]},                                       # 15. Deeper into Darkness
    27096: {"prereqs": [27094]},                                       # 16. Orcs are in Order
    27097: {"prereqs": [27096]},                                       # 17. Rise, Forsaken
    27099: {"prereqs": [27097]},                                       # 18. No Escape
    27098: {"prereqs": [27099]},                                       # 19. Lordaeron (decor)
    27180: {"prereqs": [27098]},                                       # 20. Honor the Dead
    27226: {"prereqs": [27180]},                                       # 21. Hair of the Dog
    27231: {"prereqs": [27226]},                                       # 22. Reinforcements from Fenris
    27232: {"prereqs": [27231]},                                       # 23. The Waters Run Red...
    27181: {"prereqs": [27232]},                                       # 24. Excising the Taint
    27193: {"prereqs": [27181]},                                       # 25. Seek and Destroy
    27194: {"prereqs": [27193]},                                       # 26. Cornered and Crushed!
    27195: {"prereqs": [27194]},                                       # 27. Nowhere to Run
    27290: {"prereqs": [27195]},                                       # 28. To Forsaken Forward Command
    27333: {"prereqs": [27290]},                                       # 29. Losing Ground
    27342: {"prereqs": [27333]},                                       # 30. In Time, All Will Be Revealed
    27345: {"prereqs": [27342]},                                       # 31. The F.C.D.
    27322: {"prereqs": [27345]},                                       # 32. Korok the Colossus
    27349: {"prereqs": [27322]},                                       # 33. Break in Communications: Dreadwatch Outpost
    27350: {"prereqs": [27349]},                                       # 34. Break in Communications: Rutsak's Guard
    27364: {"prereqs": [27350]},                                       # 35. On Whose Orders?
    27401: {"prereqs": [27364]},                                       # 36. What Tomorrow Brings
    27405: {"prereqs": [27401]},                                       # 37. Fall Back!
    27406: {"prereqs": [27405]},                                       # 38. A Man Named Godfrey
    27423: {"prereqs": [27406]},                                       # 39. Resistance is Futile
    27438: {"prereqs": [27423]},                                       # 40. The Great Escape
    27472: {"prereqs": [27438]},                                       # 41. Rise, Godfrey
    27474: {"prereqs": [27472]},                                       # 42. Breaking the Barrier
    27475: {"prereqs": [27474]},                                       # 43. Unyielding Servitors
    27476: {"prereqs": [27475]},                                       # 44. Dalar Dawnweaver
    27478: {"prereqs": [27476]},                                       # 45. Relios the Relic Keeper
    27484: {"prereqs": [27478]},                                       # 46. Only One May Enter
    27512: {"prereqs": [27484]},                                       # 47. Transdimensional Warfare: Chapter I
    27513: {"prereqs": [27512]},                                       # 48. Transdimensional Warfare: Chapter II
    27518: {"prereqs": [27513]},                                       # 49. Transdimensional Warfare: Chapter III
    27542: {"prereqs": [27518]},                                       # 50. Taking the Battlefront
    27547: {"prereqs": [27542]},                                       # 51. Of No Consequence
    27548: {"prereqs": [27547]},                                       # 52. Lessons in Fear
    27550: {"prereqs": [27548]},                                       # 53. Pyrewood's Fall (decor)

    # === Escalation: Path of the Last Emperor ===
    # Wowhead comments + Series: 32806 → 32807 → 32816
    # (quest 32815 exists between 32807 and 32816 on Wowhead but is not
    #  in our quest chains data; linking 32816 directly to 32807)
    32806: {"prereqs": []},                                            # The King and the Council (start)
    32807: {"prereqs": [32806]},                                       # The Warchief and the Darkness
    32816: {"prereqs": [32807]},                                       # Path of the Last Emperor (decor)

    # === Azsuna versus Azshara: The Head of the Snake ===
    # Wowhead Series: 37530 "Save Yourself" is quest #17, 37470 is #18.
    37470: {"prereqs": [37530]},                                       # The Head of the Snake (decor)

    # === Archdruid of the Vale: The Nightmare Lord ===
    # Wowhead storyline + comments confirm linear chain:
    # 38382 → 39383 → 39384 → 40573
    38382: {"prereqs": []},                                            # Archdruid of the Vale (start)
    39383: {"prereqs": [38382]},                                       # Dishonored
    39384: {"prereqs": [39383]},                                       # The Corruptor
    40573: {"prereqs": [39384]},                                       # The Nightmare Lord (decor)

    # === Origins: Bringer of the Light ===
    # Wowhead storyline: 44009 "A Falling Star" → 44257 "A Falling Star" → 44004
    # (two quests share the name "A Falling Star"; 44257 is the intermediate step)
    44257: {"prereqs": [44009], "name": "A Falling Star"},             # A Falling Star (step 2)
    44004: {"prereqs": [44257]},                                       # Bringer of the Light (decor)

    # === Allies of the Alliance: Kul Tiran — Allegiance of Kul Tiras ===
    # Wowhead storyline: 20-quest chain, all quests exist but prereqs
    # were not connected by the pipeline. Full verified chain:
    54706: {"prereqs": []},                                            # Made in Kul Tiras (start)
    55039: {"prereqs": [54706]},                                       # The Master Shipwright
    55043: {"prereqs": [55039]},                                       # Fish Tales and Distant Sails
    54708: {"prereqs": [55043]},                                       # Home, Home On the Range
    54721: {"prereqs": [54708]},                                       # I'm Too Old for This Ship
    54723: {"prereqs": [54721]},                                       # Covering Our Masts
    54725: {"prereqs": [54723]},                                       # The Deep Ones
    54726: {"prereqs": [54725]},                                       # Frame Work
    54727: {"prereqs": [54726]},                                       # Team Carry
    54728: {"prereqs": [54727]},                                       # This Lumber is Haunted
    54729: {"prereqs": [54728]},                                       # The Bleak Hills
    54732: {"prereqs": [54729]},                                       # Drop It!
    55136: {"prereqs": [54732]},                                       # Her Dog Days Are Over
    54733: {"prereqs": [55136]},                                       # Make it Wright
    54730: {"prereqs": [54733]},                                       # Gorak Tul's Influence
    54731: {"prereqs": [54733]},                                       # Balance in All Things
    54734: {"prereqs": [54731]},                                       # Summons from Dorian
    54735: {"prereqs": [54734]},                                       # A Worthy Crew
    54851: {"prereqs": [54735]},                                       # Blessing of the Tides
    53720: {"prereqs": [54851]},                                       # Allegiance of Kul Tiras (decor)

    # === Return to the Scouting Map: Reports Returned ===
    # Wowhead comments confirm Zul'Aman campaign (quest 91062 "Broken
    # Bridges") must be completed before this quest becomes available.
    91087: {"prereqs": [91062]},                                       # Reports Returned (decor)
}


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def load_series_chains() -> tuple[dict[int, list[int]], set[int], dict[int, str]]:
    """Load all quest chain cache files and build verified prereqs.

    Uses two data sources from each cached Wowhead page:
    1. Series data (preferred) — short, verified quest chains
    2. Storyline data (fallback) — when no Series exists, uses the storyline
       order up to the quest's position

    Returns:
        verified_prereqs: quest_id -> [prereq_id] or [] (from verified data)
        cached_quest_ids: set of all quest IDs that have individual cache files
        series_names:     quest_id -> name (from Series/Storyline entries)
    """
    verified_prereqs: dict[int, list[int]] = {}
    cached_quest_ids: set[int] = set()
    series_names: dict[int, str] = {}
    cache_count = 0
    series_count = 0
    storyline_count = 0

    for cache_file in CACHE_DIR.glob("quest_chain_*.json"):
        parts = cache_file.stem.split("_")
        if len(parts) < 3:
            continue
        try:
            quest_id = int(parts[2])
        except ValueError:
            continue

        cache_count += 1
        cached_quest_ids.add(quest_id)

        try:
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        series = data.get("series", [])
        storyline = data.get("storyline", [])

        # --- Always process Storyline first (if available) ---
        # The Storyline contains the full zone quest order up to quest_id's
        # position.  Build prereqs for ALL quests in the Storyline up to
        # (and including) the cached quest.  This connects the full chain
        # even when Series data only covers a short tail.
        if storyline:
            storyline_count += 1
            sl_chain: list[int] = []
            for entry in storyline:
                sq = entry.get("quest_id")
                if sq:
                    sl_chain.append(sq)
                    name = entry.get("name")
                    if name and sq not in series_names:
                        series_names[sq] = name
                # Stop after the cached quest's position
                if entry.get("quest_id") == quest_id or entry.get("is_current"):
                    break

            for i, sq in enumerate(sl_chain):
                if i == 0:
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = []
                else:
                    prev_id = sl_chain[i - 1]
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = [prev_id]

        # --- Then overlay Series data (if available) ---
        # Short Series (< MIN_SERIES_TO_OVERLAY entries) only cover the tail
        # of the chain.  When a Storyline is available, skip them entirely
        # and rely on the Storyline for the full chain.
        # Longer Series are overlaid but never override Storyline-connected
        # quests (the Storyline provides fuller context).
        sl_chain_set = set(sl_chain) if storyline else set()
        use_series = series and (len(series) >= MIN_SERIES_TO_OVERLAY
                                 or not storyline)
        if use_series:
            series_count += 1
            chain_ids: list[int] = []
            for entry in series:
                sq = entry.get("quest_id")
                if sq:
                    chain_ids.append(sq)
                    name = entry.get("name")
                    if name:
                        series_names[sq] = name

            for i, sq in enumerate(chain_ids):
                if i == 0:
                    # Don't clear prereqs set by Storyline for Series start
                    if sq not in verified_prereqs:
                        verified_prereqs[sq] = []
                else:
                    prev_id = chain_ids[i - 1]
                    # If this quest was already connected by Storyline,
                    # keep the Storyline prereqs (they provide the full
                    # chain).  Only use Series for quests NOT in storyline.
                    if sq in sl_chain_set and sq in verified_prereqs:
                        continue
                    verified_prereqs[sq] = [prev_id]

            # The cached quest_id may or may not appear in the Series list
            if chain_ids and quest_id not in verified_prereqs:
                verified_prereqs[quest_id] = [chain_ids[-1]]
        elif series:
            # Short Series skipped in favor of Storyline — still collect names
            for entry in series:
                sq = entry.get("quest_id")
                name = entry.get("name")
                if sq and name:
                    series_names[sq] = name

        elif not storyline:
            # No storyline and no series — mark as verified empty
            verified_prereqs[quest_id] = []

    logger.info(
        "Loaded %d cache files: %d with Series, %d with Storyline, %d empty",
        cache_count, series_count, storyline_count,
        cache_count - series_count - storyline_count,
    )
    logger.info(
        "Built verified prereqs for %d quests (%d with prereqs)",
        len(verified_prereqs), len([v for v in verified_prereqs.values() if v]),
    )
    return verified_prereqs, cached_quest_ids, series_names


def compute_chain_length(quest_id: int, quests: dict) -> int:
    """Compute the transitive chain length by following prereqs[0]."""
    length = 0
    current = str(quest_id)
    seen = set()
    while current in quests and current not in seen:
        seen.add(current)
        length += 1
        prereqs = quests[current].get("prereqs", [])
        if prereqs:
            current = str(prereqs[0])
        else:
            break
    return length


def get_chain_list(quest_id: int, quests: dict) -> list[str]:
    """Get the full chain as a list of quest ID strings, from root to quest."""
    chain = []
    current = str(quest_id)
    seen = set()
    while current in quests and current not in seen:
        seen.add(current)
        chain.append(current)
        prereqs = quests[current].get("prereqs", [])
        if prereqs:
            current = str(prereqs[0])
        else:
            break
    chain.reverse()  # root first
    return chain


def fetch_missing_series_data(
    decor_quests: dict,
    quests: dict,
    cached_quest_ids: set[int],
) -> dict[int, dict]:
    """Fetch Wowhead Series data for decor quests with long chains.

    Only fetches for quests that:
    1. Have chain length > LONG_CHAIN_THRESHOLD
    2. Don't already have a cache file with Series data

    Returns: quest_id -> parsed Wowhead data (series, storyline, etc.)
    """
    fetched: dict[int, dict] = {}
    fetch_needed = []

    for qid_str, qdata in decor_quests.items():
        qid = int(qid_str)
        chain_len = compute_chain_length(qid, quests)
        if chain_len <= LONG_CHAIN_THRESHOLD:
            continue

        # Check if we already have cached data with series
        cached = cache_get("quest_chain", str(qid))
        if cached and cached.get("series"):
            continue  # Already have series data

        fetch_needed.append((qid, chain_len))

    if not fetch_needed:
        logger.info("No decor quests need Series data fetch")
        return fetched

    logger.info(
        "Fetching Series data from Wowhead for %d decor quests with long chains...",
        len(fetch_needed),
    )

    for qid, chain_len in sorted(fetch_needed, key=lambda x: x[1], reverse=True):
        logger.info("  Fetching quest %d (chain=%d)...", qid, chain_len)
        data = fetch_quest_series(qid)
        if data:
            fetched[qid] = data
            series_len = len(data.get("series", []))
            storyline_len = len(data.get("storyline", []))
            if series_len:
                logger.info("    -> Found Series (%d entries)", series_len)
            elif storyline_len:
                logger.info("    -> No Series, has Storyline (%d entries)", storyline_len)
            else:
                logger.info("    -> No Series or Storyline data")

    logger.info("Fetched %d quest pages from Wowhead", len(fetched))
    return fetched


def apply_series_from_fetched(
    fetched_data: dict[int, dict],
    quests: dict,
    series_names: dict[int, str],
) -> int:
    """Apply Series data from freshly-fetched Wowhead pages.

    For quests where we found Series data, rebuild the chain using the
    Series order (which is the verified short chain).

    Returns the number of chains fixed.
    """
    fixed = 0

    for quest_id, data in fetched_data.items():
        series = data.get("series", [])
        if not series:
            continue

        # Build the series chain IDs (entries before the current quest)
        chain_ids: list[int] = []
        found_self = False
        for entry in series:
            sq = entry.get("quest_id")
            if sq == quest_id or entry.get("is_current"):
                found_self = True
                break
            if sq:
                chain_ids.append(sq)
                name = entry.get("name")
                if name:
                    series_names[sq] = name

        if not found_self:
            # Quest not found in its own series — skip
            logger.warning(
                "  Quest %d not found in its own series (%d entries), skipping",
                quest_id, len(series),
            )
            continue

        # Set prereqs for all series quests
        for i, sq in enumerate(chain_ids):
            sq_str = str(sq)
            if i == 0:
                if sq_str in quests:
                    quests[sq_str]["prereqs"] = []
                else:
                    quests[sq_str] = {
                        "quest_id": sq,
                        "name": series_names.get(sq, f"Quest #{sq}"),
                        "prereqs": [],
                        "storyline_name": data.get("storyline_name"),
                        "is_decor_quest": False,
                    }
            else:
                prev_id = chain_ids[i - 1]
                if sq_str in quests:
                    quests[sq_str]["prereqs"] = [prev_id]
                else:
                    quests[sq_str] = {
                        "quest_id": sq,
                        "name": series_names.get(sq, f"Quest #{sq}"),
                        "prereqs": [prev_id],
                        "storyline_name": data.get("storyline_name"),
                        "is_decor_quest": False,
                    }

        # Set the decor quest's prereq to the last series entry
        qid_str = str(quest_id)
        if chain_ids:
            quests[qid_str]["prereqs"] = [chain_ids[-1]]
        else:
            quests[qid_str]["prereqs"] = []

        # Store series_chain for UI toggle
        if chain_ids:
            quests[qid_str]["series_chain"] = chain_ids

        fixed += 1
        logger.info(
            "  Fixed quest %d: chain %d -> %d (Series)",
            quest_id,
            compute_chain_length(quest_id, quests),
            len(chain_ids) + 1,
        )

    return fixed



def main():
    # Load quest_chains.json
    with open(QUEST_CHAINS_JSON, "r", encoding="utf-8") as f:
        data = json.load(f)

    quests = data.get("quests", {})
    logger.info("Loaded %d quests from quest_chains.json", len(quests))

    # Identify decor quests
    decor_quests = {qid: q for qid, q in quests.items() if q.get("is_decor_quest")}

    # Compute before stats
    before_stats = {}
    for qid in decor_quests:
        before_stats[qid] = compute_chain_length(int(qid), quests)

    # --- Step 1: Fetch missing Series data from Wowhead ---
    # For decor quests with long chains and no cached Series data
    cached_quest_ids: set[int] = set()
    for cache_file in CACHE_DIR.glob("quest_chain_*.json"):
        parts = cache_file.stem.split("_")
        if len(parts) >= 3:
            try:
                cached_quest_ids.add(int(parts[2]))
            except ValueError:
                pass

    fetched_data = fetch_missing_series_data(decor_quests, quests, cached_quest_ids)

    # --- Step 2: Load Series-verified prereqs from cache ---
    # (now includes any freshly-fetched data)
    verified_prereqs, cached_quest_ids, series_names = load_series_chains()

    # --- Step 3: Apply Series-verified prereqs and clear unverified ones ---
    cleared_count = 0
    series_fixed_count = 0
    series_added_count = 0

    for quest_id_str, quest_data in quests.items():
        quest_id = int(quest_id_str)
        old_prereqs = quest_data.get("prereqs", [])

        if quest_id in verified_prereqs:
            new_prereqs = verified_prereqs[quest_id]
            if old_prereqs != new_prereqs:
                quest_data["prereqs"] = new_prereqs
                series_fixed_count += 1
        else:
            if old_prereqs:
                quest_data["prereqs"] = []
                cleared_count += 1

    # --- Step 4: Add missing Series chain quests ---
    for sq_id, sq_prereqs in verified_prereqs.items():
        sq_str = str(sq_id)
        if sq_str not in quests:
            quests[sq_str] = {
                "quest_id": sq_id,
                "name": series_names.get(sq_id, f"Quest #{sq_id}"),
                "prereqs": sq_prereqs,
                "storyline_name": None,
                "is_decor_quest": False,
            }
            series_added_count += 1

    # --- Step 5: Apply freshly-fetched Series data ---
    # Some quests now have Series data from Wowhead that wasn't in cache before
    fetch_fixed = apply_series_from_fetched(fetched_data, quests, series_names)

    # --- Step 6: Apply manual fixes ---
    manual_fixed = 0
    for quest_id, fix in MANUAL_FIXES.items():
        quest_id_str = str(quest_id)
        if quest_id_str in quests:
            old = quests[quest_id_str].get("prereqs", [])
            quests[quest_id_str]["prereqs"] = fix["prereqs"]
            if "name" in fix:
                quests[quest_id_str]["name"] = fix["name"]
            if old != fix["prereqs"]:
                manual_fixed += 1
        else:
            quests[quest_id_str] = {
                "quest_id": quest_id,
                "name": fix.get("name", f"Quest #{quest_id}"),
                "prereqs": fix["prereqs"],
                "storyline_name": fix.get("storyline_name"),
                "is_decor_quest": False,
            }
            manual_fixed += 1

    logger.info("")
    logger.info("=== CLEANUP RESULTS ===")
    logger.info("Series-fixed prereqs: %d quests", series_fixed_count)
    logger.info("Cleared unverified prereqs: %d quests", cleared_count)
    logger.info("Added missing Series quests: %d", series_added_count)
    logger.info("Fixed from Wowhead fetch: %d", fetch_fixed)
    logger.info("Applied manual fixes: %d", manual_fixed)

    # Compute after stats for decor quests
    decor_quests = {qid: q for qid, q in quests.items() if q.get("is_decor_quest")}
    after_stats = {}
    for qid in decor_quests:
        after_stats[qid] = compute_chain_length(int(qid), quests)

    # Print comparison table
    print("\n=== DECOR QUEST CHAIN LENGTH CHANGES ===")
    print(f"{'QuestID':<10} {'Name':<45} {'Before':>6} {'After':>6} {'Change':>8}")
    print("-" * 80)

    total_before = 0
    total_after = 0
    changed_count = 0

    for qid in sorted(decor_quests.keys(), key=lambda x: before_stats.get(x, 0), reverse=True):
        name = decor_quests[qid].get("name", "?")[:44]
        before = before_stats.get(qid, 0)
        after = after_stats.get(qid, 0)
        total_before += before
        total_after += after

        if before != after:
            changed_count += 1
            change = after - before
            change_str = f"{change:+d}"
            print(f"{qid:<10} {name:<45} {before:>6} {after:>6} {change_str:>8}")

    print("-" * 80)
    print(f"{'TOTAL':<10} {changed_count} chains changed{'':<24} {total_before:>6} {total_after:>6}")

    # Show all decor chains with their final lengths
    print("\n=== ALL DECOR CHAINS (after cleanup) ===")
    print(f"{'Length':>6}  {'Count':>5}")
    print("-" * 15)
    length_dist: dict[int, int] = {}
    for qid in decor_quests:
        l = after_stats.get(qid, 0)
        length_dist[l] = length_dist.get(l, 0) + 1
    for l in sorted(length_dist.keys()):
        print(f"{l:>6}  {length_dist[l]:>5}")

    # Check for remaining chains > 10
    long_chains = []
    for qid in decor_quests:
        length = after_stats.get(qid, 0)
        if length > 10:
            long_chains.append((qid, decor_quests[qid].get("name", "?"), length))

    if long_chains:
        print(f"\n=== REMAINING DECOR CHAINS > 10 ({len(long_chains)} total) ===")
        for qid, name, length in sorted(long_chains, key=lambda x: x[2], reverse=True):
            print(f"  {qid:<10} {name:<45} length={length}")
    else:
        print("\nNo decor chains > 10 remaining!")

    # Check for and fix circular dependencies
    print("\n=== CIRCULAR DEPENDENCY CHECK ===")
    cycles_fixed = 0
    for qid_str, qdata in quests.items():
        prereqs = qdata.get("prereqs", [])
        if not prereqs:
            continue
        seen = set()
        current = qid_str
        is_cycle = False
        while current in quests:
            if current in seen:
                is_cycle = True
                break
            seen.add(current)
            p = quests[current].get("prereqs", [])
            if p:
                current = str(p[0])
            else:
                break
        if is_cycle:
            # Break the cycle by clearing prereqs of the current quest
            old_prereqs = quests[qid_str].get("prereqs", [])
            quests[qid_str]["prereqs"] = []
            cycles_fixed += 1
            if cycles_fixed <= 10:
                print(f"  FIXED CYCLE: quest {qid_str} (cleared prereqs {old_prereqs})")
    if cycles_fixed == 0:
        print("  No circular dependencies found!")
    else:
        print(f"  Fixed {cycles_fixed} circular dependencies")

    # Update metadata
    data["metadata"]["total_quests"] = len(quests)
    data["metadata"]["prereq_quests"] = sum(
        1 for q in quests.values() if q.get("prereqs"))

    # Write cleaned quest_chains.json
    with open(QUEST_CHAINS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    logger.info("Written cleaned quest_chains.json")

    # Summary stats
    total_with_prereqs = sum(1 for q in quests.values() if q.get("prereqs"))
    print(f"\n=== SUMMARY ===")
    print(f"Total quests: {len(quests)}")
    print(f"Quests with prereqs: {total_with_prereqs}")
    print(f"Quests without prereqs: {len(quests) - total_with_prereqs}")
    print(f"Decor quests: {len(decor_quests)}")
    print(f"Decor chains changed: {changed_count}")

    # Items to manually verify
    print(f"\n=== ITEMS TO CHECK MANUALLY ===")
    print("These decor quests had chains > 1 after cleanup (verify in-game):")
    for qid in sorted(decor_quests.keys(), key=lambda x: after_stats.get(x, 0), reverse=True):
        length = after_stats.get(qid, 0)
        if length > 1:
            name = decor_quests[qid].get("name", "?")
            storyline = decor_quests[qid].get("storyline_name", "")
            print(f"  {qid:<10} {name:<40} chain={length:<3} storyline={storyline}")


if __name__ == "__main__":
    main()
