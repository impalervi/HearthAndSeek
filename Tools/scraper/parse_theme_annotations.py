#!/usr/bin/env python3
"""
parse_theme_annotations.py
Reads HearthAndSeekDB.themeAnnotations from the WoW SavedVariables file and
outputs data/manual_theme_annotations.json for use by compute_item_themes.py.

Usage:
    python parse_theme_annotations.py
    python parse_theme_annotations.py --input /path/to/HearthAndSeek.lua
"""

import argparse
import glob
import json
import sys
from pathlib import Path

# Reuse the Lua parser from the catalog dump script
from parse_catalog_dump import parse_lua_saved_vars

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OUTPUT_DIR = SCRIPT_DIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "manual_theme_annotations.json"

ADDON_NAME = "HearthAndSeek"
SAVED_VARS_FILENAME = f"{ADDON_NAME}.lua"


def _load_dev_config() -> dict:
    """Load dev.config.json from repo root."""
    cfg_path = REPO_ROOT / "dev.config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"dev.config.json not found at {cfg_path}. "
            "Copy dev.config.example.json to dev.config.json and set your WoW path."
        )
    return json.load(open(cfg_path, encoding="utf-8"))


def _get_wtf_base() -> Path:
    cfg = _load_dev_config()
    return Path(cfg["wowRetailDir"]) / "WTF" / "Account"


def find_saved_vars_file(override: str | None = None) -> Path:
    """Locate the HearthAndSeek.lua SavedVariables file."""
    if override:
        p = Path(override)
        if not p.is_file():
            print(f"ERROR: Specified input file does not exist: {p}")
            sys.exit(1)
        return p

    wtf_base = _get_wtf_base()
    pattern = str(wtf_base / "*" / "SavedVariables" / SAVED_VARS_FILENAME)
    matches = glob.glob(pattern)
    if not matches:
        print(f"ERROR: No SavedVariables file found matching: {pattern}")
        print("Make sure you have run /hs review in-game and then /reload to save.")
        sys.exit(1)

    if len(matches) > 1:
        print(f"Found {len(matches)} SavedVariables files, using first:")
        for m in matches:
            print(f"  {m}")

    return Path(matches[0])


def main():
    parser = argparse.ArgumentParser(
        description="Extract theme annotations from WoW SavedVariables"
    )
    parser.add_argument("--input", "-i", type=str, default=None,
                        help="Path to HearthAndSeek.lua SavedVariables file")
    parser.add_argument("--merge", action="store_true",
                        help="Merge with existing annotations instead of replacing")
    args = parser.parse_args()

    sv_path = find_saved_vars_file(args.input)
    print(f"Reading: {sv_path}")

    text = sv_path.read_text(encoding="utf-8", errors="replace")
    data = parse_lua_saved_vars(text)

    db = data.get("HearthAndSeekDB", {})
    annotations = db.get("themeAnnotations", {})

    if not annotations:
        print("No theme annotations found in SavedVariables.")
        print("Run /hs review in-game first, annotate items, then /reload to persist.")
        sys.exit(0)

    # Convert from Lua format to pipeline format
    # In-game format: { ["decorID"] = { name="...", aesthetics={...}, algorithmScores={...} } }
    # Pipeline format: { "decorID": { "name": "...", "aesthetics": [...], "algorithmScores": {...} } }
    output = {}
    for did_key, ann in annotations.items():
        did_str = str(did_key)
        aesthetics_raw = ann.get("aesthetics", {})
        algo_scores_raw = ann.get("algorithmScores", {})

        # Lua tables may be parsed as dict (string keys) or list (sequential)
        if isinstance(aesthetics_raw, list):
            aesthetics = aesthetics_raw
        elif isinstance(aesthetics_raw, dict):
            aesthetics = list(aesthetics_raw.values())
        else:
            aesthetics = []

        if isinstance(algo_scores_raw, dict):
            algo_scores = {str(k): v for k, v in algo_scores_raw.items()}
        else:
            algo_scores = {}

        output[did_str] = {
            "name": ann.get("name", ""),
            "aesthetics": aesthetics,
            "algorithmScores": algo_scores,
        }

    # Optionally merge with existing annotations
    if args.merge and OUTPUT_FILE.exists():
        existing = json.loads(OUTPUT_FILE.read_text(encoding="utf-8"))
        existing.update(output)
        output = existing
        print(f"Merged with existing annotations ({len(existing)} total).")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(output, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Extracted {len(annotations)} annotations to {OUTPUT_FILE}")
    print("Run compute_item_themes.py to apply these annotations.")


if __name__ == "__main__":
    main()
