"""
output_lua.py - Convert route JSON files into Lua data files for the addon.

Reads from data/routes/*.json and outputs to ../../Data/Packs/*.lua
(relative to this script's location, which resolves to HearthAndSeek/Data/Packs/).

Generates valid Lua table syntax matching the HearthAndSeek data schema defined
in the project blueprint.

Output: HearthAndSeek/Data/Packs/<pack_id>.lua
"""

import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
ROUTES_DIR = SCRIPT_DIR / "data" / "routes"
LUA_OUTPUT_DIR = SCRIPT_DIR.parent.parent / "Data" / "Packs"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("output_lua")


# ---------------------------------------------------------------------------
# Lua serialization helpers
# ---------------------------------------------------------------------------

def lua_string(value: str | None) -> str:
    """Serialize a Python string to a Lua string literal, or 'nil'."""
    if value is None:
        return "nil"
    # Escape backslashes, double quotes, and newlines
    escaped = (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
        .replace("\r", "")
    )
    return f'"{escaped}"'


def lua_number(value: int | float | None) -> str:
    """Serialize a Python number to a Lua number literal, or 'nil'."""
    if value is None:
        return "nil"
    if isinstance(value, float):
        # Use a reasonable precision for coordinates
        if value == int(value):
            return str(int(value))
        return f"{value:.1f}"
    return str(value)


def lua_coords(coords: list | None) -> str:
    """Serialize a coordinate pair [x, y] to a Lua table {x, y}, or 'nil'."""
    if coords is None or len(coords) < 2:
        return "nil"
    x, y = coords[0], coords[1]
    if x is None or y is None:
        return "nil"
    return f"{{{lua_number(x)}, {lua_number(y)}}}"


def lua_value(value: Any) -> str:
    """Serialize a generic Python value to its Lua representation."""
    if value is None:
        return "nil"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return lua_number(value)
    if isinstance(value, str):
        return lua_string(value)
    if isinstance(value, list):
        return lua_coords(value)  # assumes coordinate pairs for now
    return lua_string(str(value))


# ---------------------------------------------------------------------------
# Step serialization
# ---------------------------------------------------------------------------

def serialize_step(step: dict[str, Any], indent: str = "        ") -> str:
    """
    Serialize a single step dict to a Lua table literal.

    Matches the blueprint schema:
      {
          stepIndex = 1,
          type = "VENDOR",
          label = "Buy Hooded Iron Lantern",
          decorName = "Hooded Iron Lantern",
          decorID = 12345,
          questID = nil,
          npc = "Captain Lancy Revshon",
          npcID = 49877,
          mapID = 37,
          coords = {67.6, 72.8},
          zone = "Elwynn Forest",
          note = nil,
      }
    """
    lines = [f"{indent}{{"]
    fields = [
        ("stepIndex", lua_number(step.get("stepIndex"))),
        ("type", lua_string(step.get("type"))),
        ("label", lua_string(step.get("label"))),
        ("decorName", lua_string(step.get("decorName"))),
        ("decorID", lua_number(step.get("decorID"))),
        ("questID", lua_number(step.get("questID"))),
        ("npc", lua_string(step.get("npc"))),
        ("npcID", lua_number(step.get("npcID"))),
        ("mapID", lua_number(step.get("mapID"))),
        ("coords", lua_coords(step.get("coords"))),
        ("zone", lua_string(step.get("zone"))),
        ("note", lua_string(step.get("note"))),
    ]

    for field_name, field_value in fields:
        lines.append(f"{indent}    {field_name} = {field_value},")

    lines.append(f"{indent}}},")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Pack serialization
# ---------------------------------------------------------------------------

def serialize_pack(pack: dict[str, Any]) -> str:
    """
    Serialize an entire pack dict to a complete Lua file string.

    Output format:
      local _, NS = ...
      NS.Data = NS.Data or {}
      NS.Data.Packs = NS.Data.Packs or {}

      NS.Data.Packs["pack_id"] = {
          packID = "pack_id",
          title = "...",
          description = "...",
          steps = {
              { ... },
              { ... },
          }
      }
    """
    pack_id = pack.get("packID", "unknown")
    title = pack.get("title", "Unknown Pack")
    description = pack.get("description", "")
    steps = pack.get("steps", [])

    lines: list[str] = []

    # Header
    lines.append(f"-- HearthAndSeek Pack: {title}")
    lines.append(f"-- Auto-generated by output_lua.py. DO NOT EDIT MANUALLY.")
    lines.append(f"-- Items: {len(steps)} steps")
    lines.append("")
    lines.append("local _, NS = ...")
    lines.append("NS.Data = NS.Data or {}")
    lines.append("NS.Data.Packs = NS.Data.Packs or {}")
    lines.append("")
    lines.append(f'NS.Data.Packs[{lua_string(pack_id)}] = {{')
    lines.append(f"    packID = {lua_string(pack_id)},")
    lines.append(f"    title = {lua_string(title)},")
    lines.append(f"    description = {lua_string(description)},")
    lines.append(f"    steps = {{")

    # Steps
    for step in steps:
        lines.append(serialize_step(step))

    lines.append("    }")
    lines.append("}")
    lines.append("")  # trailing newline

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def convert_route_file(json_path: Path, lua_dir: Path) -> Path | None:
    """
    Convert a single route JSON file to a Lua pack file.

    Returns the output Path on success, None on failure.
    """
    try:
        with open(json_path, "r", encoding="utf-8") as fh:
            pack = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to read %s: %s", json_path, exc)
        return None

    pack_id = pack.get("packID", json_path.stem)
    lua_content = serialize_pack(pack)

    # Sanitize filename: replace any non-alphanumeric/underscore with underscore
    safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", pack_id)
    output_path = lua_dir / f"{safe_name}.lua"

    try:
        with open(output_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(lua_content)
        logger.info("Generated: %s (%d steps)", output_path, len(pack.get("steps", [])))
        return output_path
    except OSError as exc:
        logger.error("Failed to write %s: %s", output_path, exc)
        return None


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    """Main entry point: convert all route JSON files to Lua pack files."""
    if not ROUTES_DIR.exists():
        logger.error("Routes directory not found: %s", ROUTES_DIR)
        logger.error("Run generate_routes.py first.")
        sys.exit(1)

    LUA_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    json_files = sorted(ROUTES_DIR.glob("*.json"))
    if not json_files:
        logger.warning("No route JSON files found in %s", ROUTES_DIR)
        return

    logger.info("Found %d route files to convert", len(json_files))

    success_count = 0
    for json_path in json_files:
        result = convert_route_file(json_path, LUA_OUTPUT_DIR)
        if result:
            success_count += 1

    logger.info(
        "Lua generation complete. %d/%d files converted successfully.",
        success_count,
        len(json_files),
    )
    logger.info("Output directory: %s", LUA_OUTPUT_DIR)


if __name__ == "__main__":
    main()
