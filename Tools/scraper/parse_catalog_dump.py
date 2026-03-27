#!/usr/bin/env python3
"""
parse_catalog_dump.py
Reads HearthAndSeekDB.catalogDump from the WoW SavedVariables file and
outputs a parsed JSON dataset at data/catalog_dump.json.

Usage:
    python parse_catalog_dump.py
    python parse_catalog_dump.py --input /path/to/HearthAndSeek.lua
    python parse_catalog_dump.py --output /path/to/output.json
"""

import argparse
import glob
import json
import re
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Path configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
OUTPUT_DIR = SCRIPT_DIR / "data"
OUTPUT_FILE = OUTPUT_DIR / "catalog_dump.json"

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

WTF_BASE = _get_wtf_base()


# ---------------------------------------------------------------------------
# Minimal Lua table parser
# ---------------------------------------------------------------------------
class LuaParser:
    """
    A minimal recursive-descent parser for Lua table literals as written
    by WoW's SavedVariables serializer.  Supports:
      - { ... } table constructors (both array-style and key = value)
      - ["string key"] = value
      - [number] = value
      - identifier = value
      - strings (double-quoted, with basic escape handling)
      - numbers (int / float / negative)
      - booleans (true / false)
      - nil
    """

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    # -- helpers -------------------------------------------------------------

    def _skip_whitespace_and_comments(self):
        while self.pos < self.length:
            # whitespace
            if self.text[self.pos] in " \t\r\n":
                self.pos += 1
            # single-line comment
            elif self.text[self.pos:self.pos + 2] == "--":
                if self.text[self.pos + 2:self.pos + 4] == "[[":
                    # block comment --[[ ... ]]
                    end = self.text.find("]]", self.pos + 4)
                    self.pos = end + 2 if end != -1 else self.length
                else:
                    end = self.text.find("\n", self.pos)
                    self.pos = end + 1 if end != -1 else self.length
            else:
                break

    def _peek(self) -> str:
        self._skip_whitespace_and_comments()
        if self.pos < self.length:
            return self.text[self.pos]
        return ""

    def _advance(self, n: int = 1):
        self.pos += n

    def _match(self, s: str) -> bool:
        self._skip_whitespace_and_comments()
        if self.text[self.pos:self.pos + len(s)] == s:
            self.pos += len(s)
            return True
        return False

    def _expect(self, s: str):
        if not self._match(s):
            context = self.text[max(0, self.pos - 30):self.pos + 30]
            raise ValueError(
                f"Expected '{s}' at pos {self.pos}, got: ...{context}..."
            )

    # -- value parsers -------------------------------------------------------

    def parse_value(self):
        self._skip_whitespace_and_comments()
        ch = self._peek()

        if ch == "{":
            return self.parse_table()
        elif ch == '"':
            return self.parse_string()
        elif ch == "-" or ch.isdigit():
            return self.parse_number()
        elif self.text[self.pos:self.pos + 4] == "true":
            self.pos += 4
            return True
        elif self.text[self.pos:self.pos + 5] == "false":
            self.pos += 5
            return False
        elif self.text[self.pos:self.pos + 3] == "nil":
            self.pos += 3
            return None
        else:
            # Try to read as a bare identifier (enum value, etc.)
            m = re.match(r'[A-Za-z_]\w*', self.text[self.pos:])
            if m:
                self.pos += m.end()
                return m.group(0)
            context = self.text[max(0, self.pos - 20):self.pos + 20]
            raise ValueError(
                f"Unexpected character '{ch}' at pos {self.pos}: ...{context}..."
            )

    def parse_string(self) -> str:
        self._expect('"')
        parts = []
        while self.pos < self.length:
            ch = self.text[self.pos]
            if ch == "\\":
                self.pos += 1
                esc = self.text[self.pos]
                if esc == "n":
                    parts.append("\n")
                elif esc == "t":
                    parts.append("\t")
                elif esc == "\\":
                    parts.append("\\")
                elif esc == '"':
                    parts.append('"')
                elif esc == "'":
                    parts.append("'")
                elif esc.isdigit():
                    # \ddd decimal escape
                    digits = esc
                    for _ in range(2):
                        if self.pos + 1 < self.length and self.text[self.pos + 1].isdigit():
                            self.pos += 1
                            digits += self.text[self.pos]
                        else:
                            break
                    parts.append(chr(int(digits)))
                else:
                    parts.append(esc)
                self.pos += 1
            elif ch == '"':
                self.pos += 1
                return "".join(parts)
            else:
                parts.append(ch)
                self.pos += 1
        raise ValueError("Unterminated string")

    def parse_number(self):
        self._skip_whitespace_and_comments()
        m = re.match(r'-?(?:0x[0-9a-fA-F]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)',
                     self.text[self.pos:])
        if not m:
            raise ValueError(f"Expected number at pos {self.pos}")
        self.pos += m.end()
        s = m.group(0)
        if "." in s or ("e" in s.lower() and "x" not in s.lower()):
            return float(s)
        return int(s, 0)  # base 0 handles 0x prefix

    def parse_table(self):
        self._expect("{")
        # Determine if this is an array-like or dict-like table.
        # We'll build both; at the end choose the right representation.
        array_part = []
        dict_part = {}
        has_explicit_keys = False
        implicit_index = 1  # Lua 1-based

        while True:
            self._skip_whitespace_and_comments()
            if self._peek() == "}":
                self._advance()
                break
            if self._peek() == "":
                raise ValueError("Unexpected end of input inside table")

            # Check for explicit key
            saved_pos = self.pos
            key = None

            if self._peek() == "[":
                # [key] = value
                self._advance()
                key = self.parse_value()
                self._expect("]")
                self._expect("=")
                has_explicit_keys = True
            else:
                # Might be identifier = value
                m = re.match(r'([A-Za-z_]\w*)\s*=', self.text[self.pos:])
                if m:
                    key = m.group(1)
                    self.pos += m.end()
                    has_explicit_keys = True
                # else: implicit (array) entry

            value = self.parse_value()

            if key is not None:
                dict_part[key] = value
            else:
                array_part.append(value)
                implicit_index += 1

            # Optional trailing comma / semicolon
            self._skip_whitespace_and_comments()
            if self._peek() in (",", ";"):
                self._advance()

        # If there are only sequential integer keys, return a list.
        if array_part and not has_explicit_keys:
            return array_part
        if dict_part and not array_part:
            return dict_part
        # Mixed -- merge (integer keys go into dict too)
        for i, v in enumerate(array_part, start=1):
            dict_part[i] = v
        return dict_part


def parse_lua_saved_vars(text: str) -> dict:
    """
    Parse a WoW SavedVariables .lua file into a Python dict.
    Each top-level assignment like  HearthAndSeekDB = { ... }  becomes a key.
    """
    result = {}
    # Match top-level assignments:  VarName = { ... }
    # We find each one and hand it to LuaParser.
    pattern = re.compile(r'(\w+)\s*=\s*(?=\{)')
    for m in pattern.finditer(text):
        var_name = m.group(1)
        parser = LuaParser(text[m.end():])
        try:
            value = parser.parse_value()
            result[var_name] = value
        except ValueError:
            # Skip variables we can't parse
            continue
    return result


# ---------------------------------------------------------------------------
# Source text parsing
# ---------------------------------------------------------------------------
def strip_wow_formatting(text: str) -> str:
    """
    Strip WoW UI escape sequences from a string:
      |cXXXXXXXX  - color start (8 hex digits)
      |r           - color reset
      |n           - newline (convert to real newline)
      |Hlink|h     - hyperlink start/end
      |Tpath|t     - texture
    """
    if not text:
        return ""
    # Replace |n with real newline first
    text = text.replace("|n", "\n")
    # Strip texture tags:  |Tpath|t
    text = re.sub(r'\|T[^|]*\|t', '', text, flags=re.IGNORECASE)
    # Strip hyperlink tags: |Hdata|h ... |h  (just keep content between)
    text = re.sub(r'\|H[^|]*\|h', '', text)
    text = text.replace('|h', '')
    # Strip color codes: |cXXXXXXXX
    text = re.sub(r'\|c[0-9a-fA-F]{8}', '', text)
    # Strip color reset
    text = text.replace('|r', '')
    # Clean up extra whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    return text.strip()


def parse_source_text(source_text):
    """
    Parse the sourceText field from C_HousingCatalog.
    Raw format uses WoW escape sequences like:
        |cFFFFD200Quest: |rDecor Treasure Hunt|n|cFFFFD200Zone: |rFounder's Point
    After stripping:
        Quest: Decor Treasure Hunt
        Zone: Founder's Point
    Returns a dict with extracted fields.
    """
    if not source_text:
        return {}

    cleaned = strip_wow_formatting(source_text)

    parsed = {}
    sources = []

    for line in cleaned.split("\n"):
        line = line.strip()
        if not line:
            continue

        colon_idx = line.find(":")
        if colon_idx == -1:
            # No colon -- treat entire line as a generic source
            sources.append({"type": "Unknown", "value": line})
            continue

        key = line[:colon_idx].strip()
        value = line[colon_idx + 1:].strip()

        if not value:
            continue

        key_lower = key.lower()
        if key_lower == "zone":
            parsed["zone"] = value
        elif key_lower == "vendor":
            parsed.setdefault("vendor", value)  # keep first vendor
            sources.append({"type": "Vendor", "value": value})
        elif key_lower == "quest":
            parsed["quest"] = value
            sources.append({"type": "Quest", "value": value})
        elif key_lower == "achievement":
            parsed["achievement"] = value
            sources.append({"type": "Achievement", "value": value})
        elif key_lower == "profession":
            parsed["profession"] = value
            sources.append({"type": "Profession", "value": value})
        elif key_lower == "drop":
            parsed["drop"] = value
            sources.append({"type": "Drop", "value": value})
        elif key_lower == "treasure":
            parsed["treasure"] = value
            sources.append({"type": "Treasure", "value": value})
        elif key_lower == "cost":
            pass  # cost data is parsed from sourceTextRaw in output_catalog_lua.py
        elif key_lower in ("shop", "in-game shop"):
            sources.append({"type": "Shop", "value": value or "Blizzard Shop"})
        elif key_lower == "vendors":
            # "Vendors: Multiple Vendors" — treat like vendor
            parsed.setdefault("vendor", value)
            sources.append({"type": "Vendor", "value": value})
        else:
            sources.append({"type": key, "value": value})

    parsed["sources"] = sources
    return parsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def find_saved_vars_file(override: str | None = None) -> Path:
    """Locate the HearthAndSeek.lua SavedVariables file under the WTF account folder.

    If *override* is given, use that path directly (must exist).
    """
    if override:
        p = Path(override)
        if not p.is_file():
            print(f"ERROR: Specified input file does not exist: {p}")
            sys.exit(1)
        return p

    pattern = str(WTF_BASE / "*" / "SavedVariables" / SAVED_VARS_FILENAME)
    matches = glob.glob(pattern)
    if not matches:
        print(f"ERROR: No SavedVariables file found matching: {pattern}")
        print("Make sure you have run /hseek dump in-game and then /reload to save.")
        sys.exit(1)
    if len(matches) > 1:
        print(f"WARNING: Found multiple SavedVariables files, using first: {matches[0]}")
    return Path(matches[0])


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Parse HearthAndSeekDB.catalogDump from WoW SavedVariables into JSON.",
    )
    parser.add_argument(
        "--input", "-i",
        default=None,
        help="Path to a HearthAndSeek.lua SavedVariables file. "
             "If omitted, auto-discovers under the default WTF folder.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help=f"Output JSON path (default: {OUTPUT_FILE}).",
    )
    parser.add_argument(
        "--merge", action="store_true",
        help="Merge new items into existing catalog_dump.json instead of replacing it. "
             "Use after /hs dump new to add incremental items.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    output_file = Path(args.output) if args.output else OUTPUT_FILE

    # 1. Find and read the SavedVariables file
    sv_path = find_saved_vars_file(args.input)
    print(f"Reading: {sv_path}")
    sv_text = sv_path.read_text(encoding="utf-8", errors="replace")

    # 2. Parse the Lua table
    print("Parsing Lua tables...")
    data = parse_lua_saved_vars(sv_text)

    if "HearthAndSeekDB" not in data:
        print("ERROR: HearthAndSeekDB not found in SavedVariables file.")
        sys.exit(1)

    db = data["HearthAndSeekDB"]

    catalog_dump = db.get("catalogDump")
    if not catalog_dump:
        if args.merge:
            print("No new items in catalogDump (incremental dump found 0 new items).")
            print("Nothing to merge — catalog_dump.json is already up to date.")
            sys.exit(0)
        else:
            print("ERROR: HearthAndSeekDB.catalogDump is empty or missing.")
            print("Run /hs dump catalog in-game, then /reload to persist the data.")
            sys.exit(1)

    # catalogDump may be a list (array-keyed) or a dict with integer keys
    if isinstance(catalog_dump, dict):
        entries = list(catalog_dump.values())
    else:
        entries = list(catalog_dump)

    print(f"Found {len(entries)} entries in catalogDump.")

    # 3. Process each entry
    output_entries = []
    source_type_counter = Counter()
    quest_count = 0
    vendor_count = 0

    for entry in entries:
        if not isinstance(entry, dict):
            continue

        # Parse sourceText
        source_text = entry.get("sourceText", "")
        parsed_source = parse_source_text(source_text)

        # Determine primary source type
        source_types = [s["type"] for s in parsed_source.get("sources", [])]
        for st in source_types:
            source_type_counter[st] += 1
        if "Quest" in source_types:
            quest_count += 1
        if "Vendor" in source_types:
            vendor_count += 1

        output_entry = {
            "decorID": entry.get("decorID"),
            "name": entry.get("name"),
            "itemID": entry.get("itemID"),
            "quality": entry.get("quality"),
            "quantity": entry.get("quantity"),
            "numPlaced": entry.get("numPlaced"),
            "isAllowedIndoors": entry.get("isAllowedIndoors"),
            "isAllowedOutdoors": entry.get("isAllowedOutdoors"),
            "placementCost": entry.get("placementCost"),
            "iconTexture": entry.get("iconTexture"),
            "asset": entry.get("asset"),
            "uiModelSceneID": entry.get("uiModelSceneID"),
            "firstAcquisitionBonus": entry.get("firstAcquisitionBonus"),
            "categoryIDs": entry.get("categoryIDs"),
            "subcategoryIDs": entry.get("subcategoryIDs"),
            "size": entry.get("size"),
            "sourceTextRaw": source_text,
            "zone": parsed_source.get("zone"),
            "quest": parsed_source.get("quest"),
            "vendor": parsed_source.get("vendor"),
            "achievement": parsed_source.get("achievement"),
            "profession": parsed_source.get("profession"),
            "sources": parsed_source.get("sources", []),
        }
        output_entries.append(output_entry)

    # Sort by decorID for deterministic output
    output_entries.sort(key=lambda e: e.get("decorID") or 0)

    # 3b. Merge with existing catalog if --merge
    if args.merge:
        if output_file.exists():
            with open(output_file, encoding="utf-8") as fh:
                existing = json.load(fh)
            existing_by_id = {e["decorID"]: e for e in existing if e.get("decorID")}
            new_count = 0
            for entry in output_entries:
                did = entry.get("decorID")
                if did and did not in existing_by_id:
                    existing_by_id[did] = entry
                    new_count += 1
            output_entries = sorted(existing_by_id.values(), key=lambda e: e.get("decorID") or 0)
            print(f"Merged {new_count} new items into {len(existing)} existing ({len(output_entries)} total)")
        else:
            print(f"No existing {output_file.name} found, writing fresh file.")

    # 4. Write output JSON
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_entries, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(output_entries)} entries to {output_file}")

    # Stamp data directory metadata with dump info (non-fatal if it fails)
    try:
        from pipeline_metadata import update_metadata, get_game_version
        from datetime import datetime, timezone
        game = get_game_version()
        update_metadata(output_file.parent, {
            "gameVersion": game,
            "catalogDumpDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "catalogDumpItems": len(output_entries),
            "catalogDumpSource": "in-game /hs dump new --merge" if args.merge else "in-game /hs dump catalog",
        })
        print(f"Data metadata updated (game version: {game['expansion']} {game['interface']})")
    except Exception as exc:
        print(f"Warning: Failed to update data metadata: {exc}")

    # 5. Print summary stats
    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Total items:          {len(output_entries)}")
    print(f"Items with quest:     {quest_count}")
    print(f"Items with vendor:    {vendor_count}")
    print()
    print("Breakdown by source type:")
    for source_type, count in source_type_counter.most_common():
        print(f"  {source_type:20s}  {count}")


if __name__ == "__main__":
    main()
