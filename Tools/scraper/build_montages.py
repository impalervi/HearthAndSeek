#!/usr/bin/env python3
"""Build montage grid images for visual aesthetic classification.

Creates labeled contact sheets of item thumbnails for batch review.

Usage:
    python build_montages.py [--unthemed] [--sample N] [--cols 4] [--rows 4]
"""

import argparse
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = Path(__file__).resolve().parent
THUMB_DIR = SCRIPT_DIR / "data" / "thumbnails"
MONTAGE_DIR = SCRIPT_DIR / "data" / "montages"
CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"
THEMES_JSON = SCRIPT_DIR / "data" / "item_themes.json"

CELL_SIZE = 200       # Thumbnail display size
LABEL_HEIGHT = 40     # Space for text labels
PADDING = 8
BG_COLOR = (30, 30, 30, 255)
LABEL_BG = (20, 20, 20, 255)
TEXT_COLOR = (220, 220, 220, 255)
GRID_LINE_COLOR = (60, 60, 60, 255)


def load_items():
    """Load catalog and theme data, return item dicts keyed by decorID."""
    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    with open(THEMES_JSON, "r", encoding="utf-8") as f:
        themes_data = json.load(f)

    theme_names = themes_data["metadata"]["theme_names"]
    aesthetic_ids = set()
    for group in themes_data["metadata"]["theme_groups"]:
        if group["name"] == "Aesthetic":
            aesthetic_ids = set(str(t) for t in group["themes"])

    items = {}
    for item in catalog:
        did = str(item["decorID"])
        item_themes = themes_data["items"].get(did, {}).get("themes", {})
        # Extract aesthetic theme names and scores
        aesthetics = {}
        for tid, score in item_themes.items():
            if tid in aesthetic_ids:
                aesthetics[theme_names[tid]] = score
        items[did] = {
            "decorID": item["decorID"],
            "name": item["name"],
            "zone": item.get("zone", ""),
            "sourceType": (item.get("sources", [{}])[0].get("type", "")
                          if item.get("sources") else ""),
            "expansion": item.get("expansion", ""),
            "aesthetics": aesthetics,
            "has_thumb": (THUMB_DIR / f"{did}.png").exists(),
        }
    return items, themes_data


def get_unthemed_ids(items, themes_data):
    """Return decorIDs with no themes at all."""
    themed = set(themes_data["items"].keys())
    return sorted([did for did in items if did not in themed], key=int)


def get_all_ids(items):
    """Return all decorIDs sorted."""
    return sorted(items.keys(), key=int)


def build_montage(item_ids, items, cols, rows, output_path, sheet_num=0):
    """Build a single montage grid image."""
    cell_w = CELL_SIZE + PADDING * 2
    cell_h = CELL_SIZE + LABEL_HEIGHT + PADDING * 2
    img_w = cols * cell_w + PADDING
    img_h = rows * cell_h + PADDING

    img = Image.new("RGBA", (img_w, img_h), BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to load a font
    try:
        font = ImageFont.truetype("arial.ttf", 13)
        font_small = ImageFont.truetype("arial.ttf", 11)
    except (OSError, IOError):
        font = ImageFont.load_default()
        font_small = font

    for idx, did in enumerate(item_ids):
        if idx >= cols * rows:
            break
        col = idx % cols
        row = idx // cols
        x = PADDING + col * cell_w
        y = PADDING + row * cell_h

        item = items[did]

        # Draw cell background
        draw.rectangle([x, y, x + cell_w - PADDING, y + cell_h - PADDING],
                       fill=LABEL_BG, outline=GRID_LINE_COLOR)

        # Draw thumbnail
        thumb_path = THUMB_DIR / f"{did}.png"
        if thumb_path.exists():
            thumb = Image.open(thumb_path).convert("RGBA")
            thumb = thumb.resize((CELL_SIZE, CELL_SIZE), Image.LANCZOS)
            img.paste(thumb, (x + PADDING, y + PADDING), thumb)
        else:
            # No thumbnail placeholder
            draw.rectangle([x + PADDING, y + PADDING,
                           x + PADDING + CELL_SIZE, y + PADDING + CELL_SIZE],
                          fill=(50, 50, 50, 255))
            draw.text((x + PADDING + 60, y + PADDING + 90), "No image",
                     fill=(150, 150, 150, 255), font=font)

        # Draw label: decorID + name (truncated)
        label_y = y + PADDING + CELL_SIZE + 2
        name = item["name"]
        if len(name) > 26:
            name = name[:24] + ".."
        draw.text((x + PADDING + 2, label_y), f"#{did}", fill=(180, 180, 80, 255),
                 font=font_small)
        draw.text((x + PADDING + 2, label_y + 14), name, fill=TEXT_COLOR,
                 font=font_small)

    img.save(output_path, "PNG", optimize=True)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="Build montage grids")
    parser.add_argument("--unthemed", action="store_true",
                        help="Only include unthemed items")
    parser.add_argument("--all", action="store_true",
                        help="Include all items")
    parser.add_argument("--sample", type=int, default=0,
                        help="Random sample of themed items for validation")
    parser.add_argument("--cols", type=int, default=4)
    parser.add_argument("--rows", type=int, default=4)
    args = parser.parse_args()

    items, themes_data = load_items()
    items_per_sheet = args.cols * args.rows

    MONTAGE_DIR.mkdir(parents=True, exist_ok=True)

    if args.unthemed:
        target_ids = get_unthemed_ids(items, themes_data)
        prefix = "unthemed"
    elif args.all:
        target_ids = get_all_ids(items)
        prefix = "all"
    elif args.sample:
        import random
        themed = list(themes_data["items"].keys())
        random.seed(42)
        target_ids = sorted(random.sample(themed, min(args.sample, len(themed))),
                          key=int)
        prefix = "sample"
    else:
        target_ids = get_unthemed_ids(items, themes_data)
        prefix = "unthemed"

    # Filter to items with thumbnails
    target_ids = [did for did in target_ids if items[did]["has_thumb"]]

    num_sheets = math.ceil(len(target_ids) / items_per_sheet)
    print(f"Building {num_sheets} montage sheets for {len(target_ids)} items "
          f"({args.cols}x{args.rows} per sheet)")

    # Also write metadata JSON for each sheet
    sheets_meta = []
    for i in range(num_sheets):
        start = i * items_per_sheet
        batch = target_ids[start:start + items_per_sheet]
        out_path = MONTAGE_DIR / f"{prefix}_{i+1:03d}.png"
        build_montage(batch, items, args.cols, args.rows, out_path, i)

        sheet_items = []
        for did in batch:
            item = items[did]
            sheet_items.append({
                "decorID": int(did),
                "name": item["name"],
                "zone": item["zone"],
                "sourceType": item["sourceType"],
                "expansion": item["expansion"],
                "existingAesthetics": item["aesthetics"],
            })
        sheets_meta.append({
            "sheet": i + 1,
            "file": out_path.name,
            "items": sheet_items,
        })
        print(f"  Sheet {i+1}: {len(batch)} items -> {out_path.name}")

    # Save metadata
    meta_path = MONTAGE_DIR / f"{prefix}_metadata.json"
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(sheets_meta, f, indent=2)
    print(f"Metadata: {meta_path}")


if __name__ == "__main__":
    main()
