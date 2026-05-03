"""
run_pipeline.py - Master script to run the full HearthAndSeek data pipeline.

Executes each stage in order:
  1a. parse_catalog_dump.py      - Parse in-game catalog SavedVariables dump
  1b. parse_boss_dump.py         - Parse in-game boss floor map dump
  2.  enrich_catalog.py          - Enrich with Wowhead (quest IDs, NPC coords, factions)
  3.  enrich_quest_chains.py     - Build quest prerequisite chains from Wowhead
  4.  enrich_quest_givers.py     - Extract quest-giver NPC coordinates from Wowhead
  5.  scrape_wowdb.py            - Scrape WoWDB community sets and item tags
  6.  compute_item_themes.py     - Compute aesthetic and culture theme scores
  7.  enrich_wowhead_extra.py    - Enrich with drop rates, profession skills, vendor costs
  8.  output_catalog_lua.py      - Generate Data/CatalogData.lua for the addon
  9.  output_quest_chains_lua.py - Generate Data/QuestChains.lua for the addon

Prerequisites:
  - Run /hs dump in WoW, then /reload to flush SavedVariables to disk
  - pip install -r requirements.txt

Usage:
    python run_pipeline.py                  # Full pipeline (parse + enrich + generate)
    python run_pipeline.py --skip-enrich    # Skip Wowhead enrichment (use cached data)
    python run_pipeline.py --generate-only  # Only regenerate Lua files from existing JSON
    python run_pipeline.py --deploy         # Auto-deploy to WoW AddOns after generation
    python run_pipeline.py --force          # Re-fetch all Wowhead data (ignore cache)
    python run_pipeline.py --from enrich_quest_chains  # Resume from a specific stage
    python run_pipeline.py --clear-cache    # Delete Wowhead cache before enrichment
"""

import argparse
import json
import logging
import shutil
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent.parent
DATA_DIR = SCRIPT_DIR / "data"
CACHE_DIR = DATA_DIR / "wowhead_cache"
WOWDB_CACHE_DIR = DATA_DIR / "wowdb_cache"

def _load_dev_config() -> dict:
    """Load dev.config.json from repo root."""
    cfg_path = REPO_ROOT / "dev.config.json"
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"dev.config.json not found at {cfg_path}. "
            "Copy dev.config.example.json to dev.config.json and set your WoW path."
        )
    return json.load(open(cfg_path, encoding="utf-8"))

_dev_config = _load_dev_config()
_addon_name = _dev_config.get("addonName", "HearthAndSeek")

# WoW deploy target (from dev.config.json)
WOW_ADDON_DIR = Path(_dev_config["wowRetailDir"]) / "Interface" / "AddOns" / _addon_name

# Addon folders to deploy
DEPLOY_FOLDERS = ["Core", "Data", "Modules", "UI", "Libs"]
DEPLOY_FILES = [f"{_addon_name}.toc"]

# Pipeline stages in execution order.
# Each entry: (stage_name, script_filename, description, timeout_seconds)
STAGES: list[tuple[str, str, str, int]] = [
    ("parse_catalog_dump", "parse_catalog_dump.py",
     "Parse in-game SavedVariables dump", 60),
    ("parse_boss_dump", "parse_boss_dump.py",
     "Parse boss floor map dump from SavedVariables", 60),
    ("enrich_catalog", "enrich_catalog.py",
     "Enrich with Wowhead (quest IDs, NPC coords, factions)", 3600),
    ("enrich_quest_chains", "enrich_quest_chains.py",
     "Build quest prerequisite chains from Wowhead", 1800),
    ("cleanup_quest_chains", "cleanup_quest_chains.py",
     "Rebuild prereqs from verified Series data + manual fixes", 120),
    ("enrich_quest_givers", "enrich_quest_givers.py",
     "Extract quest-giver NPC coordinates from Wowhead", 3600),
    ("scrape_wowdb", "scrape_wowdb.py",
     "Scrape WoWDB community sets and item tags", 3600),
    ("compute_item_themes", "compute_item_themes.py",
     "Compute aesthetic and culture theme scores", 120),
    ("enrich_wowhead_extra", "enrich_wowhead_extra.py",
     "Enrich with drop rates, profession skills, vendor costs", 3600),
    ("output_catalog_lua", "output_catalog_lua.py",
     "Generate Data/CatalogData.lua", 60),
    ("output_quest_chains_lua", "output_quest_chains_lua.py",
     "Generate Data/QuestChains.lua", 60),
]

STAGE_NAMES = [name for name, _, _, _ in STAGES]

# Stages that perform data enrichment or scraping (skipped with --skip-enrich).
# --force passes --force to stages that accept it, and --no-cache to scrape_wowdb.
ENRICH_STAGES = {
    "enrich_catalog", "enrich_quest_chains", "cleanup_quest_chains",
    "enrich_quest_givers",
    "scrape_wowdb", "compute_item_themes", "enrich_wowhead_extra",
}

# Stages that accept --force. scrape_wowdb uses --no-cache instead;
# compute_item_themes has no caching (always recomputes).
FORCE_STAGES = {"enrich_catalog", "enrich_quest_chains", "enrich_quest_givers", "enrich_wowhead_extra"}

# Stages that only generate Lua (for --generate-only)
GENERATE_STAGES = {"output_catalog_lua", "output_quest_chains_lua"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Stage execution
# ---------------------------------------------------------------------------

def run_stage(
    stage_name: str,
    script_name: str,
    description: str,
    timeout: int,
    extra_args: list[str] | None = None,
) -> tuple[bool, float]:
    """
    Run a single pipeline stage as a subprocess.

    Returns (success: bool, elapsed_seconds: float).
    """
    script_path = SCRIPT_DIR / script_name
    if not script_path.exists():
        logger.error("Script not found: %s", script_path)
        return False, 0.0

    logger.info("")
    logger.info("=" * 60)
    logger.info("STAGE: %s", description)
    logger.info("Script: %s", script_name)
    logger.info("=" * 60)

    cmd = [sys.executable, str(script_path)]
    if extra_args:
        cmd.extend(extra_args)

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=str(SCRIPT_DIR),
            capture_output=False,
            timeout=timeout,
        )
        elapsed = time.monotonic() - start

        if result.returncode == 0:
            logger.info("[OK] %s completed (%.1fs)", stage_name, elapsed)
            return True, elapsed
        else:
            logger.error(
                "[FAIL] %s exited with code %d (%.1fs)",
                stage_name, result.returncode, elapsed,
            )
            return False, elapsed

    except subprocess.TimeoutExpired:
        elapsed = time.monotonic() - start
        logger.error("[TIMEOUT] %s after %.1fs (limit: %ds)", stage_name, elapsed, timeout)
        return False, elapsed
    except Exception as exc:
        elapsed = time.monotonic() - start
        logger.error("[ERROR] %s: %s (%.1fs)", stage_name, exc, elapsed)
        return False, elapsed


def deploy_to_wow() -> bool:
    """Copy addon files to WoW AddOns directory."""
    if not WOW_ADDON_DIR.parent.exists():
        logger.error("WoW AddOns directory not found: %s", WOW_ADDON_DIR.parent)
        logger.error("Check wowRetailDir in dev.config.json.")
        return False

    WOW_ADDON_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("")
    logger.info("=" * 60)
    logger.info("DEPLOY: Copying addon to %s", WOW_ADDON_DIR)
    logger.info("=" * 60)

    for folder in DEPLOY_FOLDERS:
        src = REPO_ROOT / folder
        dst = WOW_ADDON_DIR / folder
        if src.exists():
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            logger.info("  Copied %s/", folder)
        else:
            logger.warning("  Skipped %s/ (not found)", folder)

    for fname in DEPLOY_FILES:
        src = REPO_ROOT / fname
        dst = WOW_ADDON_DIR / fname
        if src.exists():
            shutil.copy2(src, dst)
            logger.info("  Copied %s", fname)
        else:
            logger.warning("  Skipped %s (not found)", fname)

    logger.info("[OK] Deploy complete. /reload in WoW to load changes.")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the HearthAndSeek data pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_pipeline.py                   # Full pipeline
  python run_pipeline.py --skip-enrich     # Use cached Wowhead data
  python run_pipeline.py --generate-only   # Only regenerate Lua from JSON
  python run_pipeline.py --deploy          # Deploy to WoW after generation
  python run_pipeline.py --force           # Re-fetch all Wowhead data
  python run_pipeline.py --clear-cache     # Nuke Wowhead cache first
  python run_pipeline.py --from enrich_quest_chains  # Resume from stage
""",
    )
    parser.add_argument(
        "--skip-enrich", action="store_true",
        help="Skip Wowhead enrichment stages (use cached/existing JSON data).",
    )
    parser.add_argument(
        "--generate-only", action="store_true",
        help="Only run Lua generation stages (output_catalog_lua + output_quest_chains_lua).",
    )
    parser.add_argument(
        "--deploy", action="store_true",
        help="Deploy addon files to WoW AddOns directory after generation.",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Re-fetch all enrichment data (Wowhead + WoWDB caches bypassed).",
    )
    parser.add_argument(
        "--clear-cache", action="store_true",
        help="Delete Wowhead and WoWDB cache directories before running enrichment.",
    )
    parser.add_argument(
        "--from", dest="from_stage", choices=STAGE_NAMES, default=None,
        help="Start the pipeline from this stage (skip earlier stages).",
    )
    parser.add_argument(
        "--only", choices=STAGE_NAMES, default=None,
        help="Run only this single stage.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print stages that would run without executing them.",
    )
    return parser.parse_args()


def get_stages_to_run(args: argparse.Namespace) -> list[tuple[str, str, str, int]]:
    """Determine which stages to run based on CLI arguments."""
    if args.only:
        return [(n, s, d, t) for n, s, d, t in STAGES if n == args.only]

    if args.generate_only:
        return [(n, s, d, t) for n, s, d, t in STAGES if n in GENERATE_STAGES]

    stages = list(STAGES)

    if args.skip_enrich:
        stages = [(n, s, d, t) for n, s, d, t in stages if n not in ENRICH_STAGES]

    if args.from_stage:
        start_idx = STAGE_NAMES.index(args.from_stage)
        stages = [(n, s, d, t) for n, s, d, t in stages if STAGE_NAMES.index(n) >= start_idx]

    return stages


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    stages = get_stages_to_run(args)

    if not stages:
        logger.warning("No stages to run.")
        return

    logger.info("HearthAndSeek Data Pipeline")
    logger.info("Stages: %s", " -> ".join(name for name, _, _, _ in stages))
    if args.deploy:
        logger.info("Deploy: enabled (will copy to WoW after generation)")

    if args.dry_run:
        logger.info("")
        for name, script, desc, timeout in stages:
            logger.info("  [DRY RUN] %s (%s) - %s [timeout: %ds]", name, script, desc, timeout)
        if args.deploy:
            logger.info("  [DRY RUN] Deploy to %s", WOW_ADDON_DIR)
        return

    # Remind about backups when cache-destructive operations are requested
    if args.clear_cache or args.force:
        logger.warning("")
        logger.warning("*** REMINDER: Have you backed up the cache? ***")
        logger.warning("Run: bash scripts/backup_cache.sh")
        logger.warning("This creates a timestamped archive you can restore from.")
        logger.warning("")

    # Clear cache if requested
    if args.clear_cache:
        for cache_dir, label in [(CACHE_DIR, "Wowhead"), (WOWDB_CACHE_DIR, "WoWDB")]:
            if cache_dir.exists():
                count = len(list(cache_dir.glob("*")))
                shutil.rmtree(cache_dir)
                cache_dir.mkdir(parents=True, exist_ok=True)
                logger.info("Cleared %s cache (%d files)", label, count)

    # Validate inputs exist
    if "parse_catalog_dump" not in [n for n, _, _, _ in stages]:
        # If we're skipping parse, make sure the JSON inputs exist
        if not (DATA_DIR / "catalog_dump.json").exists() and \
           any(n in ("enrich_catalog",) for n, _, _, _ in stages):
            logger.error("data/catalog_dump.json not found. Run parse_catalog_dump first.")
            sys.exit(1)
        if not (DATA_DIR / "enriched_catalog.json").exists() and \
           any(n in GENERATE_STAGES for n, _, _, _ in stages):
            logger.error("data/enriched_catalog.json not found. Run enrich_catalog first.")
            sys.exit(1)

    # Run stages
    results: list[tuple[str, bool, float]] = []
    total_start = time.monotonic()

    for stage_name, script_name, description, timeout in stages:
        extra_args = []
        if args.force and stage_name in FORCE_STAGES:
            extra_args.append("--force")
        # SAFETY: Always use --merge when catalog_dump.json already exists to
        # prevent overwriting the full catalog with only new items from SavedVariables.
        if stage_name == "parse_catalog_dump" and (DATA_DIR / "catalog_dump.json").exists():
            existing_count = 0
            try:
                import json as _json
                with open(DATA_DIR / "catalog_dump.json", encoding="utf-8") as fh:
                    existing_count = len(_json.load(fh))
            except Exception:
                pass
            if existing_count > 0:
                logger.info("  catalog_dump.json exists with %d items — using --merge to preserve data", existing_count)
                extra_args.append("--merge")
        if stage_name == "scrape_wowdb":
            extra_args.append("--all")
            if args.force:
                extra_args.append("--no-cache")
            else:
                # Incremental: only scrape items not already in wowdb_item_tags.json
                extra_args.append("--new-only")

        success, elapsed = run_stage(stage_name, script_name, description, timeout, extra_args)
        results.append((stage_name, success, elapsed))

        if not success:
            logger.error("")
            logger.error("Pipeline halted at '%s'.", stage_name)
            logger.error("Fix the issue and resume with: python run_pipeline.py --from %s", stage_name)
            break

    total_elapsed = time.monotonic() - total_start

    # Summary
    logger.info("")
    logger.info("=" * 60)
    logger.info("PIPELINE SUMMARY")
    logger.info("=" * 60)
    for stage_name, success, elapsed in results:
        status = "OK" if success else "FAILED"
        logger.info("  %-28s %s  (%.1fs)", stage_name, status, elapsed)
    logger.info("-" * 60)
    logger.info("Total: %.1fs", total_elapsed)

    all_success = all(s for _, s, _ in results)

    # Deploy if requested and all stages succeeded
    if all_success and args.deploy:
        deploy_to_wow()

    if all_success:
        logger.info("")
        logger.info("Pipeline completed successfully!")
        if not args.deploy:
            logger.info("Run with --deploy to copy files to WoW, or deploy manually:")
            logger.info("  cp -r %s %s \\", " ".join(f + "/" for f in DEPLOY_FOLDERS), " ".join(DEPLOY_FILES))
            logger.info('    "%s"', WOW_ADDON_DIR)
    else:
        logger.error("Pipeline completed with errors.")
        sys.exit(1)


if __name__ == "__main__":
    main()
