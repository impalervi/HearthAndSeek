"""
pipeline_metadata.py — Shared utility for reading/writing _metadata.json files.

Each data directory (data/, data/wowhead_cache/, data/wowdb_cache/) contains a
_metadata.json file that tracks when the data was collected/scraped and which
game version it corresponds to. This enables versioning of cached data so we
know how stale it is.

Usage from other scripts:

    from pipeline_metadata import update_metadata, read_metadata, get_game_version

    # Read current metadata
    meta = read_metadata(some_dir)

    # Update after a scraping run
    update_metadata(some_dir, {
        "last_scrape": datetime.now(timezone.utc).isoformat(),
        "items_scraped": 1667,
    })
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

METADATA_FILENAME = "_metadata.json"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TOC_FILE = REPO_ROOT / "HearthAndSeek.toc"


def get_game_version() -> dict:
    """
    Read the current game version from the TOC file.

    Returns dict with:
        interface: str — e.g. "120001"
        expansion: str — human-readable e.g. "Midnight 12.0"
    """
    interface = "unknown"
    if TOC_FILE.exists():
        for line in TOC_FILE.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^##\s*Interface:\s*(\d+)", line)
            if m:
                interface = m.group(1)
                break

    # Derive expansion name from interface number
    # 120001 = 12.0.001 → Midnight
    major = int(interface[:2]) if interface.isdigit() and len(interface) >= 2 else 0
    expansion_names = {
        1: "Classic",
        2: "The Burning Crusade",
        3: "Wrath of the Lich King",
        4: "Cataclysm",
        5: "Mists of Pandaria",
        6: "Warlords of Draenor",
        7: "Legion",
        8: "Battle for Azeroth",
        9: "Shadowlands",
        10: "Dragonflight",
        11: "The War Within",
        12: "Midnight",
    }
    expansion = expansion_names.get(major, f"Unknown ({major})")
    return {
        "interface": interface,
        "expansion": expansion,
    }


def read_metadata(directory: Path) -> dict:
    """Read _metadata.json from a directory. Returns empty dict if not found."""
    meta_file = directory / METADATA_FILENAME
    if meta_file.exists():
        try:
            return json.loads(meta_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def update_metadata(directory: Path, updates: dict) -> dict:
    """
    Merge updates into the directory's _metadata.json and write it back.

    Always sets 'lastModified' to current UTC timestamp.
    Returns the merged metadata dict.
    """
    meta = read_metadata(directory)
    meta.update(updates)
    meta["lastModified"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    meta_file = directory / METADATA_FILENAME
    meta_file.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return meta


def stamp_after_scrape(
    directory: Path,
    *,
    source: str,
    files_written: int | None = None,
    total_files: int | None = None,
) -> dict:
    """
    Convenience: stamp a cache directory after a scraping run.

    Args:
        directory: the cache directory
        source: e.g. "wowhead", "wowdb", "in-game dump"
        files_written: how many files were added/updated this run (omitted if None)
        total_files: total files in cache (if known)
    """
    game = get_game_version()
    updates = {
        "source": source,
        "gameVersion": game,
        "lastScrape": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if files_written is not None:
        updates["filesWrittenThisRun"] = files_written
    if total_files is not None:
        updates["totalFiles"] = total_files
    return update_metadata(directory, updates)
