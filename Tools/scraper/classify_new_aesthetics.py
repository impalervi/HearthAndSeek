#!/usr/bin/env python3
"""Classify aesthetic themes for newly added decor items.

Uses item name, zone, source, and existing culture themes to suggest
aesthetic categories. Asks the user to confirm or override when uncertain.

Only processes items NOT already in visual_classifications.json, so it
never re-analyzes previously classified items.

Usage:
    python classify_new_aesthetics.py              # List new items needing aesthetics
    python classify_new_aesthetics.py --classify    # Interactive classification
    python classify_new_aesthetics.py --auto        # Auto-classify high-confidence only

Aesthetic themes:
    19=Arcane Sanctum, 20=Cottage Hearth, 21=Enchanted Grove,
    22=Feast Hall, 23=Fel Forge, 24=Haunted Manor, 25=Primal Camp,
    26=Royal Court, 27=Sacred Temple, 28=Scholar's Archive,
    29=Seafarer's Haven, 30=Tinker's Workshop, 31=Void Rift,
    32=War Room, 33=Wild Garden
"""

import json
import re
import sys
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
CATALOG_PATH = DATA_DIR / "enriched_catalog.json"
THEMES_PATH = DATA_DIR / "item_themes.json"
VISUAL_PATH = DATA_DIR / "montages" / "visual_classifications.json"
ANNOTATIONS_PATH = DATA_DIR / "manual_theme_annotations.json"

AESTHETIC_THEMES = {
    19: "Arcane Sanctum",
    20: "Cottage Hearth",
    21: "Enchanted Grove",
    22: "Feast Hall",
    23: "Fel Forge",
    24: "Haunted Manor",
    25: "Primal Camp",
    26: "Royal Court",
    27: "Sacred Temple",
    28: "Scholar's Archive",
    29: "Seafarer's Haven",
    30: "Tinker's Workshop",
    31: "Void Rift",
    32: "War Room",
    33: "Wild Garden",
}

AESTHETIC_BY_NAME = {v: k for k, v in AESTHETIC_THEMES.items()}

# ---------------------------------------------------------------------------
# Keyword-based aesthetic suggestion rules
# ---------------------------------------------------------------------------
# Each rule: (compiled regex, aesthetic name, confidence weight)
SUGGESTION_RULES: list[tuple[re.Pattern, str, float]] = [
    # Royal Court
    (re.compile(r"\b(ornate|gilded|royal|regal|elegant|noble|palace|throne|banner|tapestry|crest|heraldic|statue|monument|pedestal)\b", re.I), "Royal Court", 1.0),
    (re.compile(r"\b(chandelier|candelabra|velvet|silk|curtain|drape)\b", re.I), "Royal Court", 0.7),

    # Cottage Hearth
    (re.compile(r"\b(cottage|farmhouse|rustic|homespun|cozy|quilt|hearth|fireplace|kettle|broom|mop|bucket|basket|hay|straw|barrel)\b", re.I), "Cottage Hearth", 1.0),
    (re.compile(r"\b(wooden table|wooden chair|wooden bench|cupboard|pantry)\b", re.I), "Cottage Hearth", 0.7),

    # Scholar's Archive
    (re.compile(r"\b(book|tome|scroll|library|archive|inkwell|quill|lectern|manuscript|parchment|desk|globe)\b", re.I), "Scholar's Archive", 1.0),

    # War Room
    (re.compile(r"\b(weapon|sword|axe|shield|armor|war|battle|trophy|mounted head|pike|banner of war|war drum|training dummy)\b", re.I), "War Room", 1.0),
    (re.compile(r"\b(farstrider|ranger|sentinel|military|garrison|barracks)\b", re.I), "War Room", 0.8),

    # Wild Garden
    (re.compile(r"\b(flower|bush|tree|vine|moss|fern|hedge|garden|planter|pot.*plant|herb|bloom|blossom|leaf|botanical)\b", re.I), "Wild Garden", 1.0),

    # Primal Camp
    (re.compile(r"\b(hide|fur|pelt|bone|skull|totem|tribal|camp|tent|bonfire|crude|primitive|tusk)\b", re.I), "Primal Camp", 1.0),

    # Sacred Temple
    (re.compile(r"\b(altar|shrine|temple|chapel|holy|sacred|prayer|incense|bell|sanctum|libram|faith|divine)\b", re.I), "Sacred Temple", 1.0),

    # Haunted Manor
    (re.compile(r"\b(coffin|cobweb|spider|skull|skeleton|ghostly|haunted|tomb|crypt|gothic|gargoyle|dark|grim|sarcophagus)\b", re.I), "Haunted Manor", 1.0),

    # Arcane Sanctum
    (re.compile(r"\b(arcane|crystal|mana|ley|runic|enchant|magical|spell|mystic|ethereal|astral|nightborne)\b", re.I), "Arcane Sanctum", 1.0),

    # Feast Hall
    (re.compile(r"\b(feast|food|drink|ale|mead|wine|cheese|bread|pie|cook|kitchen|plate|mug|tankard|keg|cauldron|stew|roast|tavern|inn|pub)\b", re.I), "Feast Hall", 1.0),

    # Tinker's Workshop
    (re.compile(r"\b(gear|cog|wrench|machine|mechanical|steam|pipe|piston|gnomish|goblin|invention|gadget|workshop|anvil|forge|bellows)\b", re.I), "Tinker's Workshop", 1.0),

    # Void Rift
    (re.compile(r"\b(void|shadow|dark.*crystal|ethereal.*rift|k.areshi|cosmic|tentacle|eldritch)\b", re.I), "Void Rift", 1.0),

    # Fel Forge
    (re.compile(r"\b(fel|demon|infernal|legion|warlock|nether|hellfire)\b", re.I), "Fel Forge", 1.0),

    # Enchanted Grove
    (re.compile(r"\b(moonlit|fae|faerie|fairy|ardenweald|night.*bloom|enchanted.*tree|wisp|moth.*lamp|glowing.*mushroom|dreamcatcher)\b", re.I), "Enchanted Grove", 1.0),

    # Seafarer's Haven
    (re.compile(r"\b(ship|anchor|sail|nautical|maritime|compass|helm|porthole|rope|net|fish|coral|shell|lighthouse|dock)\b", re.I), "Seafarer's Haven", 1.0),
]

# Zone -> aesthetic hints (common associations)
ZONE_HINTS: dict[str, list[str]] = {
    "Silvermoon City": ["Royal Court", "Arcane Sanctum"],
    "Eversong Woods": ["Royal Court", "Enchanted Grove"],
    "Murder Row": ["Haunted Manor", "Void Rift"],
    "Stormwind City": ["Royal Court"],
    "Ironforge": ["Tinker's Workshop", "War Room"],
    "Orgrimmar": ["Primal Camp", "War Room"],
    "Thunder Bluff": ["Primal Camp", "Sacred Temple"],
    "Undercity": ["Haunted Manor"],
    "Darnassus": ["Enchanted Grove", "Sacred Temple"],
    "Zul'Aman": ["Primal Camp", "Sacred Temple"],
    "Hallowfall": ["Sacred Temple", "Royal Court"],
}

# Culture -> aesthetic hints
CULTURE_HINTS: dict[str, list[str]] = {
    "Blood Elf": ["Royal Court", "Arcane Sanctum"],
    "Night Elf": ["Enchanted Grove", "Sacred Temple"],
    "Undead": ["Haunted Manor"],
    "Orcish": ["Primal Camp", "War Room"],
    "Troll": ["Primal Camp", "Sacred Temple"],
    "Gnomish": ["Tinker's Workshop"],
    "Goblin": ["Tinker's Workshop"],
    "Dwarven": ["Feast Hall", "War Room"],
    "Draenei": ["Sacred Temple", "Arcane Sanctum"],
    "Pandaren": ["Sacred Temple", "Feast Hall"],
    "Gilnean": ["Haunted Manor", "Cottage Hearth"],
    "Haranir": ["Enchanted Grove", "Sacred Temple"],
    "Void Elf": ["Void Rift", "Arcane Sanctum"],
    "Dracthyr": ["Arcane Sanctum", "Royal Court"],
    "Human": ["Royal Court", "Cottage Hearth"],
    "Tauren": ["Primal Camp", "Sacred Temple"],
    "Nightborne": ["Arcane Sanctum", "Royal Court"],
    "Elven": ["Royal Court", "Enchanted Grove"],
}


def get_items_needing_aesthetics() -> list[dict]:
    """Return catalog items not yet in visual_classifications.json or annotations."""
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)

    # Load existing visual classifications
    visual_ids: set[int] = set()
    if VISUAL_PATH.exists():
        with open(VISUAL_PATH, encoding="utf-8") as f:
            visual_data = json.load(f)
        for r in visual_data.get("results", []):
            visual_ids.add(r["decorID"])

    # Load existing manual annotations
    annotation_ids: set[str] = set()
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
            annotations = json.load(f)
        annotation_ids = set(annotations.keys())

    # Load theme data for culture info
    culture_map: dict[int, list[str]] = {}
    if THEMES_PATH.exists():
        with open(THEMES_PATH, encoding="utf-8") as f:
            themes = json.load(f)
        theme_names = themes.get("metadata", {}).get("theme_names", {})
        culture_group = set()
        for g in themes.get("metadata", {}).get("theme_groups", []):
            if g["name"] == "Culture":
                culture_group = set(str(t) for t in g["themes"])
        for did_str, data in themes.get("items", {}).items():
            cultures = []
            for tid, score in data.get("themes", {}).items():
                if tid in culture_group and tid in theme_names:
                    cultures.append(theme_names[tid])
            if cultures:
                culture_map[int(did_str)] = cultures

    # Filter to items needing aesthetics
    needing = []
    for item in catalog:
        did = item["decorID"]
        if did in visual_ids:
            continue
        if str(did) in annotation_ids:
            continue
        item["_cultures"] = culture_map.get(did, [])
        needing.append(item)

    return sorted(needing, key=lambda x: x["decorID"])


def suggest_aesthetics(item: dict) -> list[tuple[str, float]]:
    """Suggest aesthetic themes for an item based on name, zone, and culture.

    Returns list of (aesthetic_name, confidence_score) sorted by score desc.
    """
    scores: dict[str, float] = {}

    name = item.get("name", "")
    zone = item.get("zone", "")
    cultures = item.get("_cultures", [])

    # 1. Name-based rules (strongest signal)
    for pattern, aesthetic, weight in SUGGESTION_RULES:
        if pattern.search(name):
            scores[aesthetic] = scores.get(aesthetic, 0) + weight * 2.0

    # 2. Zone hints (moderate signal)
    for z, hints in ZONE_HINTS.items():
        if zone and z.lower() in zone.lower():
            for hint in hints:
                scores[hint] = scores.get(hint, 0) + 0.5

    # 3. Culture hints (weakest signal)
    for culture in cultures:
        for c, hints in CULTURE_HINTS.items():
            if c.lower() in culture.lower():
                for hint in hints:
                    scores[hint] = scores.get(hint, 0) + 0.3

    # Normalize and sort
    if not scores:
        return []

    max_score = max(scores.values())
    results = [(name, score / max_score) for name, score in scores.items()]
    results.sort(key=lambda x: -x[1])
    return results


def print_item_context(item: dict, suggestions: list[tuple[str, float]]):
    """Print item details and suggestions for the user."""
    print(f"\n  {'─' * 60}")
    print(f"  decorID: {item['decorID']}  |  {item.get('name', '?')}")
    print(f"  Zone: {item.get('zone', '(none)')}")
    sources = item.get("sources", [])
    if sources:
        src_str = ", ".join(f"{s['type']}: {s['value']}" for s in sources[:3])
        print(f"  Source: {src_str}")
    cultures = item.get("_cultures", [])
    if cultures:
        print(f"  Culture: {', '.join(cultures)}")

    if suggestions:
        print(f"\n  Suggested aesthetics:")
        for i, (aes, conf) in enumerate(suggestions[:5]):
            conf_label = "HIGH" if conf >= 0.8 else "medium" if conf >= 0.5 else "low"
            marker = ">>>" if conf >= 0.8 else "  >" if conf >= 0.5 else "   "
            aid = AESTHETIC_BY_NAME.get(aes, "?")
            print(f"    {marker} [{aid}] {aes} ({conf_label}, {conf:.0%})")
    else:
        print(f"\n  No suggestions — manual classification needed")


def classify_interactive():
    """Interactive classification of new items."""
    items = get_items_needing_aesthetics()
    if not items:
        print("All items have aesthetic classifications!")
        return

    # Load existing annotations
    annotations: dict = {}
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
            annotations = json.load(f)

    print(f"\n{'=' * 64}")
    print(f"  AESTHETIC CLASSIFICATION — {len(items)} new items")
    print(f"{'=' * 64}")
    print(f"  Enter aesthetic IDs (e.g., '26' for Royal Court, '26,32' for multiple)")
    print(f"  Enter 'y' to accept top suggestion, 's' to skip, 'q' to quit and save")
    print(f"  Enter '?' for full theme list\n")

    classified = 0
    for item in items:
        suggestions = suggest_aesthetics(item)
        print_item_context(item, suggestions)

        while True:
            prompt = "  Aesthetic IDs: "
            try:
                response = input(prompt).strip().lower()
            except (EOFError, KeyboardInterrupt):
                response = "q"

            if response == "q":
                break
            elif response == "s":
                break
            elif response == "?":
                print(f"\n  Aesthetic themes:")
                for aid, aname in sorted(AESTHETIC_THEMES.items()):
                    print(f"    [{aid}] {aname}")
                print()
                continue
            elif response == "y" and suggestions:
                # Accept top high-confidence suggestions
                accepted = [s[0] for s in suggestions if s[1] >= 0.8]
                if not accepted:
                    accepted = [suggestions[0][0]]
                did_str = str(item["decorID"])
                annotations[did_str] = {"aesthetics": accepted}
                print(f"  -> Accepted: {', '.join(accepted)}")
                classified += 1
                break
            else:
                # Parse aesthetic IDs
                try:
                    ids = [int(x.strip()) for x in response.split(",") if x.strip()]
                    names = []
                    valid = True
                    for aid in ids:
                        if aid in AESTHETIC_THEMES:
                            names.append(AESTHETIC_THEMES[aid])
                        else:
                            print(f"  Unknown aesthetic ID: {aid}")
                            valid = False
                    if valid and names:
                        did_str = str(item["decorID"])
                        annotations[did_str] = {"aesthetics": names}
                        print(f"  -> Classified: {', '.join(names)}")
                        classified += 1
                        break
                except ValueError:
                    print("  Invalid input. Enter IDs like '26' or '26,32'")

        if response == "q":
            break

    # Save annotations
    sorted_ann = dict(sorted(annotations.items(), key=lambda x: int(x[0])))
    with open(ANNOTATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_ann, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {classified} new annotations ({len(sorted_ann)} total)")
    print(f"  Run: python compute_item_themes.py && python output_catalog_lua.py")


def classify_auto():
    """Auto-classify items with high-confidence suggestions only.

    Items below the confidence threshold are listed for manual review.
    """
    items = get_items_needing_aesthetics()
    if not items:
        print("All items have aesthetic classifications!")
        return

    annotations: dict = {}
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
            annotations = json.load(f)

    auto_classified = 0
    needs_review: list[dict] = []

    for item in items:
        suggestions = suggest_aesthetics(item)
        high_conf = [s for s in suggestions if s[1] >= 0.8]

        if high_conf:
            did_str = str(item["decorID"])
            aesthetics = [s[0] for s in high_conf]
            annotations[did_str] = {"aesthetics": aesthetics}
            auto_classified += 1
            print(f"  [AUTO] {item['decorID']:>6} {item['name'][:40]:<40} -> {', '.join(aesthetics)}")
        else:
            needs_review.append(item)

    # Save
    sorted_ann = dict(sorted(annotations.items(), key=lambda x: int(x[0])))
    with open(ANNOTATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(sorted_ann, f, indent=2, ensure_ascii=False)

    print(f"\n  Auto-classified: {auto_classified}")
    if needs_review:
        print(f"  Needs manual review: {len(needs_review)}")
        for item in needs_review:
            suggestions = suggest_aesthetics(item)
            top = suggestions[0] if suggestions else ("(none)", 0)
            print(f"    {item['decorID']:>6} {item['name'][:40]:<40} best guess: {top[0]} ({top[1]:.0%})")
        print(f"\n  Run: python classify_new_aesthetics.py --classify")
    else:
        print(f"  All items classified!")
    print(f"  Then: python compute_item_themes.py && python output_catalog_lua.py")


def list_items():
    """List items needing aesthetic classification."""
    items = get_items_needing_aesthetics()
    if not items:
        print("All items have aesthetic classifications!")
        return

    print(f"\n{'=' * 70}")
    print(f"  {len(items)} ITEMS NEEDING AESTHETIC CLASSIFICATION")
    print(f"{'=' * 70}")

    for item in items:
        suggestions = suggest_aesthetics(item)
        top = suggestions[0] if suggestions else ("(none)", 0)
        conf_label = "HIGH" if top[1] >= 0.8 else "med" if top[1] >= 0.5 else "low"
        cultures = ", ".join(item.get("_cultures", []))
        print(f"  {item['decorID']:>6}  {item['name'][:35]:<35}  "
              f"{cultures[:15]:<15}  -> {top[0]} ({conf_label})")

    print(f"\n  To auto-classify high-confidence: python classify_new_aesthetics.py --auto")
    print(f"  To classify interactively:        python classify_new_aesthetics.py --classify")


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Classify aesthetic themes for newly added decor items"
    )
    parser.add_argument("--classify", action="store_true",
                        help="Interactive classification mode")
    parser.add_argument("--auto", action="store_true",
                        help="Auto-classify high-confidence items only")
    args = parser.parse_args()

    if args.classify:
        classify_interactive()
    elif args.auto:
        classify_auto()
    else:
        list_items()


if __name__ == "__main__":
    main()
