#!/usr/bin/env python3
"""Manual theme reviewer for HearthAndSeek decor items.

Presents items with uncertain aesthetic assignments in a tkinter UI
for manual annotation. Saves results to data/manual_theme_annotations.json,
which is consumed by compute_item_themes.py on the next run.

Uncertainty criteria (an item is flagged if >= 3 of these apply):
  - Has an aesthetic score between 25-40 (borderline assignment)
  - Has 4+ aesthetic assignments (potential noise)
  - Top two scores within 15 points (near-tie)
  - Has a single 100 but also 2+ others (noisy)

Usage:
    python review_themes.py              # Review uncertain items
    python review_themes.py --all        # Review ALL themed items
    python review_themes.py --theme "Enchanted Grove"  # Review items in a specific aesthetic
"""

import json
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

DATA_DIR = Path(__file__).parent / "data"
THEMES_PATH = DATA_DIR / "item_themes.json"
CATALOG_PATH = DATA_DIR / "enriched_catalog.json"
ANNOTATIONS_PATH = DATA_DIR / "manual_theme_annotations.json"

AESTHETIC_THEMES = [
    "Arcane Sanctum", "Cottage Hearth", "Enchanted Grove", "Feast Hall",
    "Fel Forge", "Haunted Manor", "Royal Court", "Sacred Temple",
    "Scholar's Archive", "Seafarer's Haven", "Tinker's Workshop",
    "Void Rift", "War Room", "Wild Frontier", "Wild Garden",
]

# Theme colors for visual distinction
THEME_COLORS = {
    "Arcane Sanctum": "#9966ff", "Cottage Hearth": "#cc9933",
    "Enchanted Grove": "#66cc99", "Feast Hall": "#cc6600",
    "Fel Forge": "#33cc33", "Haunted Manor": "#996699",
    "Royal Court": "#ffcc33", "Sacred Temple": "#ffff66",
    "Scholar's Archive": "#cc9966", "Seafarer's Haven": "#cc6633",
    "Tinker's Workshop": "#66aacc", "Void Rift": "#9933cc",
    "War Room": "#b38040", "Wild Frontier": "#996633",
    "Wild Garden": "#339933",
}


def load_data():
    """Load theme data and catalog."""
    if not THEMES_PATH.exists():
        print("ERROR: item_themes.json not found. Run compute_item_themes.py first.")
        sys.exit(1)
    if not CATALOG_PATH.exists():
        print("ERROR: enriched_catalog.json not found.")
        sys.exit(1)

    with open(THEMES_PATH, encoding="utf-8") as f:
        themes = json.load(f)
    with open(CATALOG_PATH, encoding="utf-8") as f:
        catalog = json.load(f)

    # Build item info lookup
    item_info = {}
    for item in catalog:
        did = item.get("decorID")
        if did:
            item_info[did] = {
                "name": item.get("name", "Unknown"),
                "sourceType": item.get("sourceType", ""),
                "sourceDetail": item.get("sourceDetail", ""),
                "zone": item.get("zone", ""),
            }

    return themes, item_info


def load_annotations():
    """Load existing annotations if any."""
    if ANNOTATIONS_PATH.exists():
        with open(ANNOTATIONS_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_annotations(annotations):
    """Save annotations to disk."""
    ANNOTATIONS_PATH.write_text(
        json.dumps(annotations, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def find_uncertain_items(themes, item_info, filter_theme=None, show_all=False):
    """Identify items with uncertain aesthetic assignments."""
    theme_names = themes["metadata"]["theme_names"]
    items_data = themes["items"]

    # Build reverse map: themeID → theme name
    id_to_name = {int(k): v for k, v in theme_names.items()}

    uncertain = []

    for did_str, item_data in items_data.items():
        did = int(did_str)
        if did not in item_info:
            continue

        item_themes = item_data.get("themes", {})
        if not item_themes:
            continue

        # Extract aesthetic assignments
        aesthetics = {}
        for tid_str, score in item_themes.items():
            tid = int(tid_str)
            name = id_to_name.get(tid, "")
            if name in AESTHETIC_THEMES:
                aesthetics[name] = score

        if not aesthetics:
            continue

        # Filter by specific theme if requested
        if filter_theme and filter_theme not in aesthetics:
            continue

        if show_all:
            uncertain.append((did, aesthetics))
            continue

        # Uncertainty criteria — must meet at least THREE to be flagged
        flags = 0

        # 1. Has a borderline score (25-40)
        has_borderline = any(25 <= s <= 40 for s in aesthetics.values())
        if has_borderline:
            flags += 1

        # 2. Many aesthetics assigned (4+)
        if len(aesthetics) >= 4:
            flags += 1

        # 3. Top two scores within 15 points (near-tie)
        if len(aesthetics) >= 2:
            sorted_scores = sorted(aesthetics.values(), reverse=True)
            if sorted_scores[0] - sorted_scores[1] <= 15:
                flags += 1

        # 4. Has a single aesthetic at exactly 100 but also has 2+ others (noisy)
        top_count = sum(1 for s in aesthetics.values() if s == 100)
        if top_count == 1 and len(aesthetics) >= 3:
            flags += 1

        if flags >= 3:
            uncertain.append((did, aesthetics))

    # Sort by name for consistent ordering
    uncertain.sort(key=lambda x: item_info.get(x[0], {}).get("name", ""))
    return uncertain


class ThemeReviewer:
    """Tkinter UI for reviewing item theme assignments."""

    def __init__(self, root, items, item_info, annotations):
        self.root = root
        self.items = items
        self.item_info = item_info
        self.annotations = annotations
        self.index = 0
        self.checkboxes = {}
        self.checkbox_vars = {}
        self.unsaved_count = 0

        # Skip to first un-reviewed item
        for i, (did, _) in enumerate(self.items):
            if str(did) not in self.annotations:
                self.index = i
                break

        self.setup_ui()
        self.show_item()

    def setup_ui(self):
        self.root.title("HearthAndSeek Theme Reviewer")
        self.root.geometry("620x580")
        self.root.resizable(False, False)

        # Navigation bar
        nav_frame = ttk.Frame(self.root, padding=8)
        nav_frame.pack(fill=tk.X)

        self.progress_label = ttk.Label(nav_frame, text="", font=("Segoe UI", 10))
        self.progress_label.pack(side=tk.LEFT)

        ttk.Button(nav_frame, text="Save & Exit", command=self.save_and_exit).pack(side=tk.RIGHT, padx=2)
        ttk.Button(nav_frame, text="Skip >>", command=self.skip_to_next_unreviewed).pack(side=tk.RIGHT, padx=2)
        ttk.Button(nav_frame, text="Next >", command=self.next_item).pack(side=tk.RIGHT, padx=2)
        ttk.Button(nav_frame, text="< Prev", command=self.prev_item).pack(side=tk.RIGHT, padx=2)

        ttk.Separator(self.root, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # Item info section
        info_frame = ttk.LabelFrame(self.root, text="Item", padding=10)
        info_frame.pack(fill=tk.X, padx=10, pady=5)

        self.name_label = ttk.Label(info_frame, text="", font=("Segoe UI", 13, "bold"))
        self.name_label.pack(anchor=tk.W)

        self.id_label = ttk.Label(info_frame, text="", font=("Segoe UI", 9), foreground="#888")
        self.id_label.pack(anchor=tk.W)

        self.source_label = ttk.Label(info_frame, text="", font=("Segoe UI", 9), foreground="#666")
        self.source_label.pack(anchor=tk.W)

        # Current scores
        scores_frame = ttk.LabelFrame(self.root, text="Current Aesthetic Scores (from algorithm)", padding=10)
        scores_frame.pack(fill=tk.X, padx=10, pady=5)

        self.scores_label = ttk.Label(scores_frame, text="", font=("Consolas", 10), justify=tk.LEFT)
        self.scores_label.pack(anchor=tk.W)

        # Aesthetic selection
        select_frame = ttk.LabelFrame(self.root, text="Your Selection (check all that apply)", padding=10)
        select_frame.pack(fill=tk.BOTH, padx=10, pady=5, expand=True)

        # Create checkboxes in a 3-column grid
        for i, theme in enumerate(AESTHETIC_THEMES):
            var = tk.BooleanVar()
            self.checkbox_vars[theme] = var
            cb = ttk.Checkbutton(select_frame, text=theme, variable=var)
            row = i // 3
            col = i % 3
            cb.grid(row=row, column=col, sticky=tk.W, padx=10, pady=3)
            self.checkboxes[theme] = cb

        # Status & buttons
        bottom_frame = ttk.Frame(self.root, padding=8)
        bottom_frame.pack(fill=tk.X)

        self.status_label = ttk.Label(bottom_frame, text="", foreground="#888")
        self.status_label.pack(side=tk.LEFT)

        ttk.Button(bottom_frame, text="Confirm Selection", command=self.confirm).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom_frame, text="Clear All", command=self.clear_all).pack(side=tk.RIGHT, padx=2)
        ttk.Button(bottom_frame, text="Accept Algorithm", command=self.accept_current).pack(side=tk.RIGHT, padx=2)

        # Keyboard shortcuts
        self.root.bind("<Left>", lambda e: self.prev_item())
        self.root.bind("<Right>", lambda e: self.next_item())
        self.root.bind("<Return>", lambda e: self.confirm())
        self.root.bind("<Escape>", lambda e: self.save_and_exit())

    def show_item(self):
        if not self.items:
            self.name_label.config(text="No items to review!")
            return

        did, aesthetics = self.items[self.index]
        info = self.item_info.get(did, {})

        # Count reviewed
        reviewed = sum(1 for d, _ in self.items if str(d) in self.annotations)

        self.progress_label.config(
            text=f"Item {self.index + 1} of {len(self.items)}  |  "
                 f"Reviewed: {reviewed}/{len(self.items)}  |  "
                 f"Unsaved: {self.unsaved_count}"
        )

        self.name_label.config(text=info.get("name", "Unknown"))
        self.id_label.config(text=f"decorID: {did}")

        source = info.get("sourceType", "")
        detail = info.get("sourceDetail", "")
        zone = info.get("zone", "")
        source_parts = [s for s in [source, detail, zone] if s]
        self.source_label.config(text=" — ".join(source_parts) if source_parts else "")

        # Show current scores
        score_lines = []
        for theme, score in sorted(aesthetics.items(), key=lambda x: -x[1]):
            bar = "█" * (score // 10) + "░" * (10 - score // 10)
            score_lines.append(f"  {theme:<20} {bar} {score:3d}")
        self.scores_label.config(text="\n".join(score_lines) if score_lines else "  (none)")

        # Set checkboxes from annotation or current scores
        did_str = str(did)
        if did_str in self.annotations:
            saved = set(self.annotations[did_str].get("aesthetics", []))
            for theme in AESTHETIC_THEMES:
                self.checkbox_vars[theme].set(theme in saved)
            self.status_label.config(text="✓ Previously reviewed", foreground="#090")
        else:
            for theme in AESTHETIC_THEMES:
                self.checkbox_vars[theme].set(theme in aesthetics)
            self.status_label.config(text="Not yet reviewed", foreground="#888")

    def confirm(self):
        did, aesthetics = self.items[self.index]
        selected = [t for t in AESTHETIC_THEMES if self.checkbox_vars[t].get()]
        self.annotations[str(did)] = {
            "name": self.item_info.get(did, {}).get("name", ""),
            "aesthetics": selected,
            "algorithmScores": {t: s for t, s in aesthetics.items()},
        }
        self.unsaved_count += 1
        self.status_label.config(text="✓ Confirmed", foreground="#090")
        self.next_item()

    def accept_current(self):
        """Accept the algorithm's assignments as-is."""
        did, aesthetics = self.items[self.index]
        self.annotations[str(did)] = {
            "name": self.item_info.get(did, {}).get("name", ""),
            "aesthetics": list(aesthetics.keys()),
            "algorithmScores": {t: s for t, s in aesthetics.items()},
        }
        self.unsaved_count += 1
        self.status_label.config(text="✓ Accepted as-is", foreground="#090")
        self.next_item()

    def clear_all(self):
        for var in self.checkbox_vars.values():
            var.set(False)

    def next_item(self):
        if self.index < len(self.items) - 1:
            self.index += 1
            self.show_item()

    def prev_item(self):
        if self.index > 0:
            self.index -= 1
            self.show_item()

    def skip_to_next_unreviewed(self):
        """Jump to the next item that hasn't been reviewed yet."""
        start = self.index + 1
        for i in range(start, len(self.items)):
            did, _ = self.items[i]
            if str(did) not in self.annotations:
                self.index = i
                self.show_item()
                return
        # Wrap around
        for i in range(0, start):
            did, _ = self.items[i]
            if str(did) not in self.annotations:
                self.index = i
                self.show_item()
                return
        messagebox.showinfo("Done", "All items have been reviewed!")

    def save_and_exit(self):
        if self.unsaved_count > 0:
            save_annotations(self.annotations)
            print(f"Saved {len(self.annotations)} annotations to {ANNOTATIONS_PATH}")
        self.root.destroy()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Review item theme assignments")
    parser.add_argument("--all", action="store_true", help="Review ALL themed items")
    parser.add_argument("--theme", type=str, help="Review items in a specific aesthetic")
    args = parser.parse_args()

    themes, item_info = load_data()
    annotations = load_annotations()

    uncertain = find_uncertain_items(
        themes, item_info,
        filter_theme=args.theme,
        show_all=args.all,
    )

    if not uncertain:
        print("No uncertain items found!")
        return

    print(f"Found {len(uncertain)} items to review.")
    if annotations:
        already = sum(1 for d, _ in uncertain if str(d) in annotations)
        print(f"  ({already} already reviewed)")

    root = tk.Tk()
    ThemeReviewer(root, uncertain, item_info, annotations)
    root.mainloop()


if __name__ == "__main__":
    main()
