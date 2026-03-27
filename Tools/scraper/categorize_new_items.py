#!/usr/bin/env python3
"""Show unthemed items and apply manual theme annotations.

Usage:
    # List all items without theme assignments:
    python categorize_new_items.py

    # Assign themes interactively (writes to manual_theme_annotations.json):
    python categorize_new_items.py --annotate

    # After annotating, regenerate themes and Lua:
    python compute_item_themes.py
    python output_catalog_lua.py

Theme IDs reference:
    Culture: 1=Blood Elf, 2=Dracthyr, 3=Draenei, 4=Dwarven, 5=Elven,
             6=Gilnean, 7=Gnomish, 8=Goblin, 9=Haranir, 10=Human,
             11=Night Elf, 12=Nightborne, 13=Orcish, 14=Pandaren,
             15=Tauren, 16=Troll, 17=Undead, 18=Void Elf
    Aesthetic: 19=Arcane Sanctum, 20=Cottage Hearth, 21=Enchanted Grove,
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
ANNOTATIONS_PATH = DATA_DIR / "manual_theme_annotations.json"

THEME_NAMES = {
    1: "Blood Elf", 2: "Dracthyr", 3: "Draenei", 4: "Dwarven", 5: "Elven",
    6: "Gilnean", 7: "Gnomish", 8: "Goblin", 9: "Haranir", 10: "Human",
    11: "Night Elf", 12: "Nightborne", 13: "Orcish", 14: "Pandaren",
    15: "Tauren", 16: "Troll", 17: "Undead", 18: "Void Elf",
    19: "Arcane Sanctum", 20: "Cottage Hearth", 21: "Enchanted Grove",
    22: "Feast Hall", 23: "Fel Forge", 24: "Haunted Manor", 25: "Primal Camp",
    26: "Royal Court", 27: "Sacred Temple", 28: "Scholar's Archive",
    29: "Seafarer's Haven", 30: "Tinker's Workshop", 31: "Void Rift",
    32: "War Room", 33: "Wild Garden",
}


def _strip_wow_codes(text):
    """Strip WoW color/formatting codes from text."""
    if not text:
        return ""
    text = re.sub(r"\|c[0-9a-fA-F]{8}", "", text)
    text = re.sub(r"\|r", "", text)
    text = re.sub(r"\|H[^|]*\|h", "", text)
    text = re.sub(r"\|h", "", text)
    text = re.sub(r"\|T[^|]*\|t", "", text)
    text = re.sub(r"\|n", " ", text)
    return text.strip()


def load_unthemed():
    """Return list of catalog items that have no theme assignment."""
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)
    with open(THEMES_PATH, encoding="utf-8") as f:
        themes = json.load(f)

    themed_ids = set(themes.get("items", {}).keys())
    unthemed = []
    for item in catalog:
        did = str(item.get("decorID", 0))
        if did not in themed_ids:
            unthemed.append(item)
    return sorted(unthemed, key=lambda x: x.get("decorID", 0))


def list_unthemed():
    """Print all unthemed items."""
    unthemed = load_unthemed()
    if not unthemed:
        print("All items have theme assignments!")
        return

    print(f"\n{'='*70}")
    print(f"  {len(unthemed)} UNTHEMED ITEMS")
    print(f"{'='*70}")
    print(f"  {'decorID':>7}  {'Name':<45}  Source")
    print(f"  {'-'*7}  {'-'*45}  {'-'*15}")
    for item in unthemed:
        did = item.get("decorID", 0)
        name = item.get("name", "?")[:45]
        source = _strip_wow_codes(
            item.get("sourceTextRaw", item.get("sourceText", ""))
        )[:20]
        print(f"  {did:>7}  {name:<45}  {source}")

    print(f"\n  Theme reference:")
    print(f"  Culture:   ", end="")
    for tid in range(1, 19):
        print(f"{tid}={THEME_NAMES[tid]}", end=", " if tid < 18 else "\n")
    print(f"  Aesthetic: ", end="")
    for tid in range(19, 34):
        print(f"{tid}={THEME_NAMES[tid]}", end=", " if tid < 33 else "\n")
    print(f"\n  To annotate: python categorize_new_items.py --annotate")


def annotate_interactive():
    """Interactive annotation mode: assign themes to unthemed items."""
    unthemed = load_unthemed()
    if not unthemed:
        print("All items have theme assignments!")
        return

    # Load existing annotations
    annotations = {}
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
            annotations = json.load(f)

    print(f"\n{'='*70}")
    print(f"  INTERACTIVE THEME ANNOTATION")
    print(f"  {len(unthemed)} unthemed items")
    print(f"{'='*70}")
    print(f"  Enter theme IDs separated by commas (e.g., '5,21' for Elven + Enchanted Grove)")
    print(f"  Enter 's' to skip, 'q' to quit and save, '?' for theme list")
    print()

    added = 0
    for item in unthemed:
        did = str(item.get("decorID", 0))
        name = item.get("name", "?")
        source = _strip_wow_codes(
            item.get("sourceTextRaw", item.get("sourceText", ""))
        )

        if did in annotations:
            print(f"  [{did}] {name} — already annotated, skipping")
            continue

        while True:
            response = input(f"\n  [{did}] {name} ({source})\n  Themes> ").strip()

            if response == "q":
                _save_annotations(annotations, added)
                return
            if response == "s" or response == "":
                break
            if response == "?":
                _print_theme_list()
                continue

            try:
                theme_ids = [int(x.strip()) for x in response.split(",")]
                invalid = [t for t in theme_ids if t not in THEME_NAMES]
                if invalid:
                    print(f"  Invalid theme IDs: {invalid}. Try again.")
                    continue

                theme_dict = {}
                for tid in theme_ids:
                    theme_dict[str(tid)] = 100  # Manual = full confidence
                annotations[did] = {"themes": theme_dict}
                added += 1
                names = [THEME_NAMES[t] for t in theme_ids]
                print(f"  -> Assigned: {', '.join(names)}")
                break
            except ValueError:
                print("  Invalid input. Enter numbers separated by commas.")

    _save_annotations(annotations, added)


def _save_annotations(annotations, added):
    """Save annotations to disk."""
    with open(ANNOTATIONS_PATH, "w", encoding="utf-8") as f:
        json.dump(annotations, f, indent=2, ensure_ascii=False)
    print(f"\n  Saved {added} new annotations to {ANNOTATIONS_PATH.name}")
    print(f"  Total annotations: {len(annotations)}")
    if added > 0:
        print(f"\n  Next steps:")
        print(f"    python compute_item_themes.py")
        print(f"    python output_catalog_lua.py")
        print(f"    bash scripts/deploy.sh")


def _print_theme_list():
    """Print the full theme reference."""
    print(f"\n  {'ID':>3}  {'Theme':<25}  Group")
    print(f"  {'-'*3}  {'-'*25}  {'-'*10}")
    for tid in range(1, 19):
        print(f"  {tid:>3}  {THEME_NAMES[tid]:<25}  Culture")
    for tid in range(19, 34):
        print(f"  {tid:>3}  {THEME_NAMES[tid]:<25}  Aesthetic")
    print()


if __name__ == "__main__":
    if "--annotate" in sys.argv:
        annotate_interactive()
    else:
        list_unthemed()
