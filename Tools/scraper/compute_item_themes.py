#!/usr/bin/env python3
"""Compute theme scores for decor items.

Combines three data sources:
  1. Per-item tags from housing.wowdb.com (weight 3.0)
  2. Set membership voting — items in themed sets (weight = log(likes+1))
  3. Item name patterns — regex fallback (weight 1.0)

Outputs data/item_themes.json with per-item theme assignments and scores.

Theme groups:
  - Culture: racial/faction aesthetics (Elven, Dwarven, Orcish, etc.)
  - Aesthetic: build fantasies (Arcane, Macabre, Noble, Lorekeeper, Tavern, etc.)
"""

import json
import logging
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

logger = logging.getLogger("compute_themes")

DATA_DIR = Path(__file__).parent / "data"
SETS_PATH = DATA_DIR / "wowdb_sets.json"
ITEMS_PATH = DATA_DIR / "wowdb_item_tags.json"
CATALOG_PATH = DATA_DIR / "enriched_catalog.json"
OUTPUT_PATH = DATA_DIR / "item_themes.json"

# Source weights
ITEM_TAG_WEIGHT = 3.0  # Direct per-item tags (highest confidence)
NAME_PATTERN_WEIGHT = 1.0  # Regex name matching (lowest confidence)
# Set membership weight: log(likes + 1) per set appearance

# Per-theme name pattern weight overrides — high-signal patterns that should
# compete with item tags so they survive normalization on multi-themed items
NAME_PATTERN_BOOSTS: dict[str, float] = {
    "Sacred": 5.0,  # Sacred patterns are high-signal and definitive; needs to
                     # survive normalization against strong competing themes
}

# Sacred exclusions: items matching these patterns skip Sacred name-pattern scoring
# (prevents fel-corrupted altars, death librams, etc. from getting Sacred)
SACRED_EXCLUSIONS = [
    re.compile(r"\bFelblood\b", re.I),
    re.compile(r"\bCorrupted\b", re.I),
    re.compile(r"\bof the Dead\b", re.I),
    re.compile(r"\bNight Fae\b", re.I),
]

# Curated Sacred items: decorIDs that are definitively Sacred but can't be captured
# algorithmically because their Lorekeeper scores are overwhelmingly high
SACRED_GUARANTEED: set[int] = {
    7574,   # Replica Libram of Ancient Kings (Paladin OH holy text)
    7823,   # Replica Word of the Conclave (Priest OH sacred text)
    2513,   # Tome of Draenei Faith (Draenei religious text)
}

# Minimum score threshold (0-100 normalized) to keep a theme
MIN_SCORE_THRESHOLD = 25

# Minimum items for a theme to get its own filter entry
MIN_ITEMS_PER_THEME = 10

# ---------------------------------------------------------------------------
# Tag → Theme mapping
# ---------------------------------------------------------------------------

# Maps wowdb tags (lowercase) to our canonical theme names.
# Sources: item culture/style/theme CSS-class tags, and set tags.
TAG_TO_THEME: dict[str, str] = {
    # Culture — specific races
    "blood elf": "Blood Elf",
    "night elf": "Night Elf",
    "nightborne": "Nightborne",
    "void elf": "Void Elf",
    "elven": "Elven",
    "human": "Human",
    "kul tiran": "Kul Tiran",
    "gilnean": "Gilnean",
    "gilnean / worgen": "Gilnean",
    "worgen": "Gilnean",
    "dwarven": "Dwarven",
    "bronzebeard dwarf": "Dwarven",
    "bronzebeard dwarf (ironforge)": "Dwarven",
    "dark iron dwarf": "Dark Iron",
    "wildhammer dwarf": "Dwarven",
    "gnomish": "Gnomish",
    "draenei": "Draenei",
    "orcish": "Orcish",
    "goblin": "Goblin",
    "tauren": "Tauren",
    "troll": "Troll",
    "zandalari troll": "Zandalari",
    "undead": "Undead",
    "pandaren": "Pandaren",
    "vulpera": "Vulpera",
    "dracthyr": "Dracthyr",
    "earthen": "Earthen",
    "earthen-dornish": "Earthen",
    "earthen (dornish)": "Earthen",
    "haranir": "Haranir",
    "vrykul": "Vrykul",
    # Note: "alliance" and "horde" faction tags intentionally NOT mapped —
    # they're too broad and cause false positives (e.g., Blood Elf item tagged
    # "Horde" getting scored as Orcish). Specific race tags handle these items.

    # Aesthetic — item style tags
    "elegant": "Noble",
    "lavish": "Noble",
    "light": "Sacred",       # wowdb "Light" items are sacred (Sanctum of Light, Naaru, etc.)
    "magical": "Arcane",
    "nature": "Nature",
    "spooky": "Macabre",
    "dark": "Macabre",
    "fae": "Fae",
    "mechanical": "Tinker",
    "void": "Void",
    "fel": "Fel",
    "simple": "Rustic",
    "casual": "Rustic",
    "bold": "Rugged",
    "pirate": "Pirate",
    # Dropped (too vague / catch-all): cozy, cute, whimsical, romantic
    # Dropped (seasonal, too few items): spring, fall, summer

    # Aesthetic — tag-theme CSS class tags (wowdb meta-aesthetics)
    "opulent": "Noble",
    "rugged": "Rugged",
    "folk": "Rustic",

    # Aesthetic — set tags (room types from community sets)
    "library": "Lorekeeper",
    "kitchen": "Tavern",
    "dining room": "Tavern",
    "wine cellar": "Tavern",
    "pantry": "Tavern",
    "breakfast room": "Tavern",
    "trophy room": "Armory",
}

# Theme → group assignment
THEME_GROUPS: dict[str, str] = {}  # Built dynamically below

CULTURE_THEMES = {
    "Elven", "Blood Elf", "Night Elf", "Nightborne", "Void Elf",
    "Human", "Kul Tiran", "Gilnean",
    "Dwarven", "Dark Iron",
    "Gnomish", "Draenei",
    "Orcish", "Goblin", "Tauren", "Troll", "Zandalari",
    "Undead", "Pandaren", "Vulpera", "Dracthyr",
    "Earthen", "Haranir", "Vrykul",
}
AESTHETIC_THEMES = {
    "Arcane",       # Mage tower, crystal chamber, enchanting workshop
    "Macabre",      # Haunted house, dark sanctum, crypt
    "Noble",        # Palace, ballroom, formal dining, gilded halls
    "Rustic",       # Cottage, farmhouse, homespun, pastoral
    "Rugged",       # War camp, frontier lodge, outpost
    "Nature",       # Druidic grove, wilderness, greenhouse
    "Fae",          # Enchanted forest, Ardenweald, dreamscape
    "Void",         # Shadow realm, K'areshi, cosmic horror
    "Fel",          # Demonic lair, Legion, infernal
    "Tinker",       # Gnomish workshop, steampunk lab, engineering
    "Pirate",       # Ship cabin, coastal tavern, nautical
    "Lorekeeper",   # Scholar's study, library, dark academia
    "Tavern",       # Pub, kitchen, feast hall, inn
    "Armory",       # War room, armory, trophy hall, barracks
    "Sacred",       # Chapel, shrine, temple, holy sanctum
}

for t in CULTURE_THEMES:
    THEME_GROUPS[t] = "Culture"
for t in AESTHETIC_THEMES:
    THEME_GROUPS[t] = "Aesthetic"

# Merge targets: if a theme has < MIN_ITEMS_PER_THEME items, merge into parent
MERGE_TARGETS: dict[str, str] = {
    "Blood Elf": "Elven",
    "Night Elf": "Elven",
    "Nightborne": "Elven",
    "Void Elf": "Elven",
    "Kul Tiran": "Human",
    "Gilnean": "Human",
    "Dark Iron": "Dwarven",
    "Zandalari": "Troll",
    "Earthen": "Dwarven",
}

# Always-merge: these merge regardless of item count (visually similar sub-races)
ALWAYS_MERGE: dict[str, str] = {
    "Kul Tiran": "Human",      # Maritime Human variant
    "Zandalari": "Troll",      # Gold-trimmed Troll variant
    "Earthen": "Dwarven",      # TWW titan-forged stone, same family as Dwarven
}

# ---------------------------------------------------------------------------
# Name patterns (regex → theme, for items without wowdb tags)
# ---------------------------------------------------------------------------

NAME_PATTERNS: list[tuple[re.Pattern, str]] = [
    # Culture
    (re.compile(r"\b(Kaldorei|Sin'dorei|Shal'dorei|Ren'dorei)\b", re.I), "Elven"),
    (re.compile(r"\bElven\b", re.I), "Elven"),
    (re.compile(r"\bNight Elf\b", re.I), "Night Elf"),
    (re.compile(r"\bBlood Elf\b", re.I), "Blood Elf"),
    (re.compile(r"\bNightborne\b", re.I), "Nightborne"),
    (re.compile(r"\b(Dwarven|Dwarf)\b", re.I), "Dwarven"),
    (re.compile(r"\bDark Iron\b", re.I), "Dark Iron"),
    (re.compile(r"\b(Orcish|Orc)\b", re.I), "Orcish"),
    (re.compile(r"\bGoblin\b", re.I), "Goblin"),
    (re.compile(r"\bTauren\b", re.I), "Tauren"),
    (re.compile(r"\b(Troll|Zandalari)\b", re.I), "Troll"),
    (re.compile(r"\b(Pandaren|Pandaria)\b", re.I), "Pandaren"),
    (re.compile(r"\bGnomish\b", re.I), "Gnomish"),
    (re.compile(r"\bVrykul\b", re.I), "Vrykul"),
    (re.compile(r"\bHaranir\b", re.I), "Haranir"),
    (re.compile(r"\bEarthen\b", re.I), "Earthen"),
    (re.compile(r"\bDraenei\b", re.I), "Draenei"),
    (re.compile(r"\bGilnean\b", re.I), "Gilnean"),
    (re.compile(r"\bVulpera\b", re.I), "Vulpera"),
    (re.compile(r"\bUndead\b", re.I), "Undead"),

    # Aesthetic — Arcane (mage tower, crystal chamber, enchanting)
    (re.compile(r"\b(Arcane|Mystic|Enchanted|Enchanting)\b", re.I), "Arcane"),
    (re.compile(r"\b(Runic|Rune)\b", re.I), "Arcane"),

    # Aesthetic — Macabre (haunted house, dark sanctum, crypt)
    (re.compile(r"\b(Spooky|Haunted|Ghastly|Macabre|Cursed)\b", re.I), "Macabre"),
    (re.compile(r"\b(Coffin|Cobweb|Tombstone|Gravestone|Skull)\b", re.I), "Macabre"),

    # Aesthetic — Noble (palace, ballroom, gilded halls)
    (re.compile(r"\b(Ornate|Gilded|Regal|Opulent)\b", re.I), "Noble"),

    # Aesthetic — Rustic (cottage, farmhouse, homespun)
    (re.compile(r"\b(Rustic|Farmhouse|Weathered|Homespun)\b", re.I), "Rustic"),

    # Aesthetic — Nature (druidic grove, wilderness)
    (re.compile(r"\b(Verdant|Overgrown|Mossy)\b", re.I), "Nature"),

    # Aesthetic — Fae (enchanted forest, Ardenweald, dreamscape)
    (re.compile(r"\b(Fae|Faerie|Dreamrift|Ardenweald)\b", re.I), "Fae"),

    # Aesthetic — Void (shadow realm, K'areshi, cosmic horror)
    (re.compile(r"\b(Void|Ethereal)\b", re.I), "Void"),

    # Aesthetic — Fel (demonic lair, Legion, infernal)
    (re.compile(r"\b(Fel|Demonic|Felfire)\b", re.I), "Fel"),

    # Aesthetic — Tinker (gnomish workshop, steampunk lab)
    (re.compile(r"\b(Mechanical|Clockwork|Gearwork|Tinker)\b", re.I), "Tinker"),

    # Aesthetic — Pirate (ship cabin, coastal, nautical)
    (re.compile(r"\b(Pirate|Buccaneer|Swashbuckler)\b", re.I), "Pirate"),

    # Aesthetic — Lorekeeper (library, scholar's study, dark academia)
    (re.compile(r"\b(Books?|Tomes?|Codex|Grimoire)\b", re.I), "Lorekeeper"),
    (re.compile(r"\b(Scrolls?|Parchment|Manuscript)\b", re.I), "Lorekeeper"),
    (re.compile(r"\b(Bookshelf|Bookcase|Library)\b", re.I), "Lorekeeper"),
    (re.compile(r"\b(Quill|Lectern|Scribe)\b", re.I), "Lorekeeper"),
    (re.compile(r"\bArchive\b", re.I), "Lorekeeper"),

    # Aesthetic — Tavern (pub, kitchen, feast hall, inn)
    (re.compile(r"\b(Tavern|Innkeeper)\b", re.I), "Tavern"),
    (re.compile(r"\b(Keg|Barrel)\b", re.I), "Tavern"),
    (re.compile(r"\b(Mug|Tankard|Stein|Goblet|Chalice)\b", re.I), "Tavern"),
    (re.compile(r"\b(Brew|Wine|Ale|Mead)\b", re.I), "Tavern"),
    (re.compile(r"\b(Platter|Feast|Banquet)\b", re.I), "Tavern"),
    (re.compile(r"\b(Stove|Oven|Kettle|Cooking)\b", re.I), "Tavern"),
    (re.compile(r"\b(Decanter|Flagon|Carafe)\b", re.I), "Tavern"),
    (re.compile(r"\bFood Cart\b", re.I), "Tavern"),

    # Aesthetic — Armory (war room, armory, trophy hall)
    (re.compile(r"\bBanner\b", re.I), "Armory"),
    (re.compile(r"\b(Weapon Rack|Spear Rack|Shield Wall)\b", re.I), "Armory"),
    (re.compile(r"\bTrophy\b", re.I), "Armory"),
    (re.compile(r"\bWar (Table|Map|Drum|Brazier|Chandelier|Planning)\b", re.I), "Armory"),
    (re.compile(r"\bBarricade\b", re.I), "Armory"),
    (re.compile(r"\bTraining Dummy\b", re.I), "Armory"),
    (re.compile(r"\b(Flag|Standard)\b", re.I), "Armory"),

    # Aesthetic — Sacred (altars, shrines, chapels, Light/Holy sanctum)
    # Weight boosted via NAME_PATTERN_BOOSTS so these survive normalization
    (re.compile(r"\b(Sacred|Holy|Divine|Sanctified)\b", re.I), "Sacred"),
    (re.compile(r"(?<!Moon-)\bBlessed\b", re.I), "Sacred"),
    (re.compile(r"\b(Altar|Shrine)\b", re.I), "Sacred"),
    (re.compile(r"\b(Cathedral|Chapel|Abbey)\b", re.I), "Sacred"),
    (re.compile(r"\b(Censer|Votive)\b", re.I), "Sacred"),
    (re.compile(r"\bNaaru\b", re.I), "Sacred"),
    (re.compile(r"\b(Light-Infused|Lightwell|Lightforged|Light-Touched)\b", re.I), "Sacred"),
    (re.compile(r"\bSilver Hand\b", re.I), "Sacred"),
    (re.compile(r"\bNetherlight\b", re.I), "Sacred"),
    (re.compile(r"\bConclave\b", re.I), "Sacred"),
    (re.compile(r"\bKarabor\b", re.I), "Sacred"),
    (re.compile(r"\bLibram\b", re.I), "Sacred"),
    (re.compile(r"\bAspirant\b", re.I), "Sacred"),
    (re.compile(r"\bPrayer\b", re.I), "Sacred"),
    (re.compile(r"\bStained Glass\b", re.I), "Sacred"),
    (re.compile(r"\bSanctum of\b", re.I), "Sacred"),
    (re.compile(r"\b(of|by) the Light\b", re.I), "Sacred"),
    (re.compile(r"\bLight Blooms\b", re.I), "Sacred"),
    (re.compile(r"\bRadiant\b", re.I), "Sacred"),
    (re.compile(r"\bDraenei Faith\b", re.I), "Sacred"),
    (re.compile(r"\bHigh Exarch\b", re.I), "Sacred"),
    (re.compile(r"\bBlooming Light\b", re.I), "Sacred"),
]


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------

def compute_themes() -> None:
    """Compute theme scores for all catalog items."""
    logger.info("=== Computing Item Themes ===")

    # Load catalog
    if not CATALOG_PATH.exists():
        logger.error("enriched_catalog.json not found")
        sys.exit(1)
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    item_names: dict[int, str] = {}
    for item in catalog:
        did = item.get("decorID")
        if did:
            item_names[did] = item.get("name", "")
    logger.info("  Catalog: %d items", len(item_names))

    # Load wowdb item tags
    item_tags: dict[str, dict] = {}
    if ITEMS_PATH.exists():
        with open(ITEMS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        item_tags = data.get("items", {})
        logger.info("  Item tags: %d items loaded", len(item_tags))
    else:
        logger.warning("  wowdb_item_tags.json not found — skipping item tags")

    # Load wowdb sets
    sets_data: dict[str, dict] = {}
    if SETS_PATH.exists():
        with open(SETS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        sets_data = data.get("sets", {})
        logger.info("  Sets: %d loaded", len(sets_data))
    else:
        logger.warning("  wowdb_sets.json not found — skipping set voting")

    # ---------------------------------------------------------------------------
    # Source 1: Per-item tags (weight 3.0)
    # ---------------------------------------------------------------------------
    # scores[decorID][theme] = accumulated raw score
    scores: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    items_from_tags = 0
    for did_str, tags in item_tags.items():
        did = int(did_str)
        if did not in item_names:
            continue

        tagged = False
        for category in ("culture", "style", "class", "theme"):
            for tag_name in tags.get(category, []):
                theme = TAG_TO_THEME.get(tag_name.lower())
                if theme:
                    scores[did][theme] += ITEM_TAG_WEIGHT
                    tagged = True
        if tagged:
            items_from_tags += 1

    logger.info("  Source 1 (item tags): %d items scored", items_from_tags)

    # ---------------------------------------------------------------------------
    # Source 2: Set membership voting (weight = log(likes + 1))
    # ---------------------------------------------------------------------------
    items_from_sets = 0
    sets_used = 0

    for set_info in sets_data.values():
        set_tags = set_info.get("tags", [])
        likes = set_info.get("likes", 0)
        decor_ids = set_info.get("decorIDs", [])

        if not set_tags or not decor_ids:
            continue

        # Map set tags to themes
        set_themes: list[str] = []
        for tag_name in set_tags:
            theme = TAG_TO_THEME.get(tag_name.lower())
            if theme:
                set_themes.append(theme)

        # Also try to extract themes from set name (skip Sacred — set names
        # like "Secret Void Shrine" or "Inspired by the Light" are too noisy)
        set_name = set_info.get("name", "")
        for pattern, theme in NAME_PATTERNS:
            if theme == "Sacred":
                continue
            if pattern.search(set_name):
                if theme not in set_themes:
                    set_themes.append(theme)

        if not set_themes:
            continue

        sets_used += 1
        weight = math.log(likes + 1)

        for did in decor_ids:
            if did not in item_names:
                continue
            for theme in set_themes:
                scores[did][theme] += weight
            items_from_sets += 1

    logger.info("  Source 2 (set voting): %d sets used, %d item-theme pairs",
                sets_used, items_from_sets)

    # ---------------------------------------------------------------------------
    # Source 3: Name patterns (weight 1.0, boosted for some themes)
    # ---------------------------------------------------------------------------
    items_from_names = 0
    for did, name in item_names.items():
        if not name:
            continue
        matched = False
        for pattern, theme in NAME_PATTERNS:
            if pattern.search(name):
                # Skip Sacred for excluded items (e.g. Felblood Altar, Libram of the Dead)
                if theme == "Sacred" and any(ex.search(name) for ex in SACRED_EXCLUSIONS):
                    continue
                weight = NAME_PATTERN_BOOSTS.get(theme, NAME_PATTERN_WEIGHT)
                scores[did][theme] += weight
                matched = True
        if matched:
            items_from_names += 1

    logger.info("  Source 3 (name patterns): %d items scored", items_from_names)

    # ---------------------------------------------------------------------------
    # Normalize scores: per-item, scale to 0-100 relative to max
    # ---------------------------------------------------------------------------
    normalized: dict[int, dict[str, int]] = {}

    for did, theme_scores in scores.items():
        if not theme_scores:
            continue

        max_score = max(theme_scores.values())
        if max_score <= 0:
            continue

        item_themes: dict[str, int] = {}
        for theme, raw_score in theme_scores.items():
            norm = int(raw_score / max_score * 100)
            if norm >= MIN_SCORE_THRESHOLD:
                item_themes[theme] = norm

        if item_themes:
            normalized[did] = item_themes

    # Apply curated Sacred guarantees for items that can't be captured
    # algorithmically (Lorekeeper scores too dominant)
    for did in SACRED_GUARANTEED:
        if did in item_names:
            if did not in normalized:
                normalized[did] = {}
            if "Sacred" not in normalized[did]:
                normalized[did]["Sacred"] = MIN_SCORE_THRESHOLD
                logger.info("  Curated Sacred: %d (%s)", did, item_names[did])

    logger.info("  Items with themes: %d / %d (%.1f%%)",
                len(normalized), len(item_names),
                len(normalized) / len(item_names) * 100 if item_names else 0)

    # ---------------------------------------------------------------------------
    # Count items per theme and apply merge threshold
    # ---------------------------------------------------------------------------
    theme_item_counts: dict[str, int] = defaultdict(int)
    for item_themes in normalized.values():
        for theme in item_themes:
            theme_item_counts[theme] += 1

    logger.info("\n  Theme counts before merging:")
    for group_name in ("Culture", "Aesthetic"):
        themes_in_group = sorted(
            [(t, c) for t, c in theme_item_counts.items()
             if THEME_GROUPS.get(t) == group_name],
            key=lambda x: -x[1],
        )
        logger.info("    %s:", group_name)
        for theme, count in themes_in_group:
            if theme in ALWAYS_MERGE:
                marker = " <-- ALWAYS MERGE"
            elif count < MIN_ITEMS_PER_THEME and theme in MERGE_TARGETS:
                marker = " <-- MERGE"
            else:
                marker = ""
            logger.info("      %-20s %4d items%s", theme, count, marker)

    # Merge themes: always-merge (visually similar sub-races) + small themes
    merge_count = 0
    for theme, target in MERGE_TARGETS.items():
        should_merge = (
            theme in ALWAYS_MERGE
            or theme_item_counts.get(theme, 0) < MIN_ITEMS_PER_THEME
        )
        if should_merge:
            reason = "always-merge" if theme in ALWAYS_MERGE else "< %d items" % MIN_ITEMS_PER_THEME
            logger.info("  Merging '%s' (%d items) → '%s' (%s)",
                        theme, theme_item_counts.get(theme, 0), target, reason)
            # Re-label in all items
            for did, item_themes in normalized.items():
                if theme in item_themes:
                    old_score = item_themes.pop(theme)
                    # Keep higher score if target already exists
                    item_themes[target] = max(item_themes.get(target, 0), old_score)
                    merge_count += 1

    if merge_count:
        logger.info("  Merged %d theme assignments", merge_count)

    # Remove themes with < MIN_ITEMS_PER_THEME even after merging
    # (for themes without merge targets, like standalone culture themes)
    theme_item_counts_final: dict[str, int] = defaultdict(int)
    for item_themes in normalized.values():
        for theme in item_themes:
            theme_item_counts_final[theme] += 1

    small_themes = {
        t for t, c in theme_item_counts_final.items()
        if c < MIN_ITEMS_PER_THEME
    }
    if small_themes:
        logger.info("  Dropping themes with < %d items: %s",
                     MIN_ITEMS_PER_THEME, sorted(small_themes))
        for did in list(normalized):
            for theme in small_themes:
                normalized[did].pop(theme, None)
            if not normalized[did]:
                del normalized[did]

    # ---------------------------------------------------------------------------
    # Build output structure
    # ---------------------------------------------------------------------------

    # Assign numeric IDs to themes (sorted within groups)
    theme_id_map: dict[str, int] = {}
    next_id = 1

    # Recount after merging
    final_counts: dict[str, int] = defaultdict(int)
    for item_themes in normalized.values():
        for theme in item_themes:
            final_counts[theme] += 1

    group_themes: dict[str, list[str]] = {"Culture": [], "Aesthetic": []}
    for theme in sorted(final_counts.keys()):
        group = THEME_GROUPS.get(theme)
        if group:
            group_themes[group].append(theme)

    group_ids = {"Culture": 1, "Aesthetic": 2}

    for group_name in ("Culture", "Aesthetic"):
        for theme in sorted(group_themes[group_name]):
            theme_id_map[theme] = next_id
            next_id += 1

    # Build per-item output
    items_output: dict[str, dict] = {}
    for did, item_themes in normalized.items():
        themes_with_ids: dict[str, int] = {}
        for theme, score in item_themes.items():
            tid = theme_id_map.get(theme)
            if tid:
                themes_with_ids[str(tid)] = score
        if themes_with_ids:
            items_output[str(did)] = {"themes": themes_with_ids}

    # Build ByTheme index (sorted by score desc)
    by_theme: dict[int, list[int]] = defaultdict(list)
    for did_str, item_data in items_output.items():
        did = int(did_str)
        for tid_str, score in item_data["themes"].items():
            by_theme[int(tid_str)].append((did, score))

    for tid in by_theme:
        by_theme[tid].sort(key=lambda x: -x[1])
        by_theme[tid] = [did for did, _ in by_theme[tid]]

    # Build metadata
    theme_groups_meta = []
    theme_group_themes: dict[int, list[int]] = {}
    for group_name in ("Culture", "Aesthetic"):
        gid = group_ids[group_name]
        tids = [theme_id_map[t] for t in sorted(group_themes[group_name])]
        theme_groups_meta.append({
            "id": gid,
            "name": group_name,
            "themes": tids,
        })
        theme_group_themes[gid] = tids

    theme_names: dict[str, str] = {}
    for theme, tid in theme_id_map.items():
        theme_names[str(tid)] = theme

    output = {
        "metadata": {
            "theme_groups": theme_groups_meta,
            "theme_names": theme_names,
            "theme_group_themes": {str(k): v for k, v in theme_group_themes.items()},
            "items_themed": len(items_output),
            "items_unthemed": len(item_names) - len(items_output),
            "theme_item_counts": {
                theme_names[str(tid)]: len(dids)
                for tid, dids in by_theme.items()
            },
        },
        "items": items_output,
        "by_theme": {str(k): v for k, v in by_theme.items()},
    }

    OUTPUT_PATH.write_text(json.dumps(output, indent=2), encoding="utf-8")
    logger.info("\nSaved to %s", OUTPUT_PATH)
    logger.info("  %d items themed, %d unthemed",
                len(items_output), len(item_names) - len(items_output))

    # Final stats
    logger.info("\n  Final theme counts:")
    for group_name in ("Culture", "Aesthetic"):
        gid = group_ids[group_name]
        tids = theme_group_themes[gid]
        logger.info("    %s:", group_name)
        for tid in tids:
            theme = theme_names[str(tid)]
            count = len(by_theme.get(tid, []))
            logger.info("      [%2d] %-20s %4d items", tid, theme, count)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    compute_themes()
    logger.info("Done!")


if __name__ == "__main__":
    main()
