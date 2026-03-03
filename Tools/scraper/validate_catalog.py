#!/usr/bin/env python3
"""
validate_catalog.py — Cross-validate enriched_catalog.json against Wowhead.

Selects a stratified random sample of items and checks:
  1. Wowhead: vendor NPC coords, faction, quest data, item existence

Reports all failures and flags "interesting" items for manual review.
"""

import json
import logging
import math
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

from enrich_catalog import (
    _rate_limited_get,
    _react_to_faction,
    cache_get,
    cache_put,
    fetch_item_sold_by,
    fetch_npc_coords,
)

SCRIPT_DIR = Path(__file__).resolve().parent
CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("validate")


# ---------------------------------------------------------------------------
# Wowhead validators
# ---------------------------------------------------------------------------

def validate_npc_coords_wowhead(npc_id: int, our_x: float, our_y: float,
                                 tolerance: float = 5.0) -> Optional[dict]:
    """Check our NPC coords against Wowhead tooltip. Returns failure dict or None."""
    coords_data = fetch_npc_coords(npc_id)
    if not coords_data or not coords_data.get("coords"):
        return {"type": "wh_no_coords", "npcID": npc_id,
                "detail": "Wowhead has no coords for this NPC"}

    wh_x = coords_data["coords"]["x"]
    wh_y = coords_data["coords"]["y"]
    dist = math.sqrt((our_x - wh_x) ** 2 + (our_y - wh_y) ** 2)
    if dist > tolerance:
        return {
            "type": "wh_coord_mismatch",
            "npcID": npc_id,
            "ours": f"({our_x}, {our_y})",
            "wowhead": f"({wh_x}, {wh_y})",
            "distance": round(dist, 1),
        }
    return None


def validate_vendor_sold_by(item_id: int, expected_vendor: str,
                             expected_npc_id: int = None) -> Optional[dict]:
    """Check Wowhead sold-by matches our vendor. Returns failure dict or None."""
    sold_by = fetch_item_sold_by(item_id)
    if not sold_by:
        return {"type": "wh_no_soldby", "itemID": item_id,
                "detail": f"Wowhead has no sold-by for item {item_id}"}

    # Check if our vendor appears in the sold-by list
    vendor_names = [npc.get("name", "") for npc in sold_by]
    vendor_ids = [npc.get("id") for npc in sold_by]

    if expected_npc_id and expected_npc_id in vendor_ids:
        return None  # Found by ID
    if expected_vendor and any(expected_vendor.lower() in v.lower() for v in vendor_names):
        return None  # Found by name

    return {
        "type": "wh_vendor_mismatch",
        "itemID": item_id,
        "expected": f"{expected_vendor} (NPC {expected_npc_id})",
        "wowhead_vendors": ", ".join(f"{n} ({i})" for n, i in zip(vendor_names[:5], vendor_ids[:5])),
    }


def validate_faction_wowhead(item_id: int, our_faction: str,
                               our_npc_id: int = None) -> Optional[dict]:
    """Check if item faction is consistent with Wowhead NPC react data."""
    sold_by = fetch_item_sold_by(item_id)
    if not sold_by:
        return None  # Can't validate

    # Check react data of vendors
    factions_seen = set()
    for npc in sold_by:
        react = npc.get("react")
        f = _react_to_faction(react)
        if f:
            factions_seen.add(f)

    if not factions_seen:
        return None

    # If both alliance and horde vendors exist, item should be neutral
    if "alliance" in factions_seen and "horde" in factions_seen:
        if our_faction and our_faction not in ("neutral", ""):
            return {
                "type": "wh_faction_mismatch",
                "itemID": item_id,
                "ours": our_faction,
                "wowhead": "neutral (both faction vendors found)",
            }
    elif len(factions_seen) == 1:
        wh_faction = list(factions_seen)[0]
        if our_faction and our_faction != "neutral" and our_faction != wh_faction:
            return {
                "type": "wh_faction_mismatch",
                "itemID": item_id,
                "ours": our_faction,
                "wowhead": wh_faction,
            }

    return None


# ---------------------------------------------------------------------------
# Main validation
# ---------------------------------------------------------------------------

def main():
    random.seed(42)

    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)

    # Stratified sample
    by_source: dict[str, list] = {}
    for it in catalog:
        srcs = it.get("sources", [])
        primary = srcs[0]["type"] if srcs else "Unknown"
        by_source.setdefault(primary, []).append(it)

    sample_counts = {
        "Vendor": 25, "Quest": 10, "Achievement": 8,
        "Profession": 5, "Drop": 5, "Unknown": 3, "Treasure": 2,
    }

    sample = []
    for src_type, count in sample_counts.items():
        pool = by_source.get(src_type, [])
        n = min(count, len(pool))
        picked = random.sample(pool, n)
        for p in picked:
            p["_src"] = src_type
        sample.extend(picked)

    logger.info("Validating %d items across %d source types", len(sample), len(sample_counts))

    all_failures: list[dict] = []
    interesting: list[dict] = []
    validated = 0

    for item in sample:
        decor_id = item.get("decorID", 0)
        name = item.get("name", "?")
        src = item.get("_src", "?")
        item_id = item.get("itemID")
        npc_id = item.get("npcID")
        faction = item.get("faction", "")
        vendor_name = item.get("vendor", "") or item.get("vendorName", "")
        npc_x = item.get("npcX")
        npc_y = item.get("npcY")
        fv = item.get("factionVendors")
        quest_name = item.get("quest", "")

        item_failures = []

        # --- Wowhead checks ---

        # 1. Vendor items: check sold-by
        if src == "Vendor" and item_id:
            if vendor_name:
                f = validate_vendor_sold_by(item_id, vendor_name, npc_id)
                if f:
                    f["decorID"] = decor_id
                    f["name"] = name
                    item_failures.append(f)

            # Check faction vs sold-by react
            f = validate_faction_wowhead(item_id, faction, npc_id)
            if f:
                f["decorID"] = decor_id
                f["name"] = name
                item_failures.append(f)

        # 2. NPC coords check (for items with flat vendor coords)
        if npc_id and npc_x is not None and npc_y is not None:
            f = validate_npc_coords_wowhead(npc_id, npc_x, npc_y, tolerance=5.0)
            if f:
                f["decorID"] = decor_id
                f["name"] = name
                item_failures.append(f)

        # 3. factionVendors coord checks
        if fv:
            for faction_key in ("Alliance", "Horde"):
                fv_data = fv.get(faction_key, {})
                fv_npc = fv_data.get("npcID")
                fv_x = fv_data.get("x")
                fv_y = fv_data.get("y")
                if fv_npc and fv_x is not None:
                    f = validate_npc_coords_wowhead(fv_npc, fv_x, fv_y, tolerance=5.0)
                    if f:
                        f["decorID"] = decor_id
                        f["name"] = name
                        f["factionVendor"] = faction_key
                        item_failures.append(f)

        # --- Flag interesting items ---
        reasons = []
        if fv and len(fv) >= 2:
            reasons.append("dual-faction vendors")
        if item.get("factionQuestChains"):
            reasons.append("faction quest chains")
        if item.get("isRotatingVendor"):
            reasons.append("rotating vendor")
        if item.get("coordsMismatch"):
            reasons.append("coord mismatch flag")
        if src == "Drop" and item.get("npcID"):
            reasons.append("boss drop with NPC")
        if quest_name and faction and faction != "neutral":
            reasons.append(f"faction-locked quest ({faction})")
        if vendor_name and ("Brawl" in vendor_name or "PvP" in name
                            or "Empowerment" in name or "Tome" in name):
            reasons.append("PvP/special vendor")
        if item.get("covenantID"):
            reasons.append("covenant vendor")
        if reasons:
            interesting.append({
                "decorID": decor_id,
                "name": name,
                "source": src,
                "reasons": reasons,
                "failures": len(item_failures),
            })

        all_failures.extend(item_failures)
        validated += 1

        if validated % 10 == 0:
            logger.info("  Validated %d/%d items, %d failures so far",
                        validated, len(sample), len(all_failures))

    # --- Report ---
    print("\n" + "=" * 70)
    print("VALIDATION REPORT")
    print("=" * 70)
    print(f"Items validated: {validated}")
    print(f"Total failures:  {len(all_failures)}")
    print()

    if all_failures:
        print("ALL FAILURES:")
        print("-" * 70)
        # Group by type
        by_type: dict[str, list] = {}
        for f in all_failures:
            by_type.setdefault(f["type"], []).append(f)

        for ftype, fails in sorted(by_type.items()):
            print(f"\n  [{ftype}] ({len(fails)} failures)")
            for f in fails:
                did = f.get("decorID", "?")
                nm = f.get("name", "?")
                detail_parts = []
                for k, v in f.items():
                    if k not in ("type", "decorID", "name"):
                        detail_parts.append(f"{k}={v}")
                print(f"    [{did}] {nm}: {', '.join(detail_parts)}")
    else:
        print("NO FAILURES! All items passed validation.")

    print()
    if interesting:
        print(f"INTERESTING ITEMS FOR MANUAL REVIEW ({len(interesting)}):")
        print("-" * 70)
        for it in interesting:
            status = "FAIL" if it["failures"] > 0 else "PASS"
            print(f"  [{it['decorID']:5d}] {it['name']}")
            print(f"         Source: {it['source']}, Status: {status}")
            print(f"         Reasons: {', '.join(it['reasons'])}")

    # Save detailed results
    results = {
        "validated": validated,
        "failures": all_failures,
        "interesting": interesting,
    }
    out_path = SCRIPT_DIR / "data" / "_validation_results.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    logger.info("Detailed results saved to %s", out_path)


if __name__ == "__main__":
    main()
