#!/usr/bin/env python3
"""Build an HTML validation page for reviewing visual aesthetic classifications.

Shows item thumbnails side-by-side with predicted and existing aesthetics.
User can confirm, override, or flag items. Exports overrides as JSON.

Usage:
    python build_validation_page.py [--unclear] [--disagreements] [--unthemed] [--all]
"""

import argparse
import base64
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
THUMB_DIR = SCRIPT_DIR / "data" / "thumbnails"
VISUAL_JSON = SCRIPT_DIR / "data" / "montages" / "visual_classifications.json"
THEMES_JSON = SCRIPT_DIR / "data" / "item_themes.json"
CATALOG_JSON = SCRIPT_DIR / "data" / "enriched_catalog.json"
OUTPUT_HTML = SCRIPT_DIR / "data" / "validation_review.html"

AESTHETICS = [
    "Arcane Sanctum", "Cottage Hearth", "Enchanted Grove", "Feast Hall",
    "Fel Forge", "Haunted Manor", "Royal Court", "Sacred Temple",
    "Scholar's Archive", "Seafarer's Haven", "Tinker's Workshop",
    "Void Rift", "War Room", "Primal Camp", "Wild Garden",
]

AESTHETIC_COLORS = {
    "Arcane Sanctum": "#9b59b6", "Cottage Hearth": "#d35400",
    "Enchanted Grove": "#2ecc71", "Feast Hall": "#e74c3c",
    "Fel Forge": "#27ae60", "Haunted Manor": "#2c3e50",
    "Royal Court": "#f1c40f", "Sacred Temple": "#f39c12",
    "Scholar's Archive": "#d4a574", "Seafarer's Haven": "#e67e22",
    "Tinker's Workshop": "#3498db", "Void Rift": "#8e44ad",
    "War Room": "#7f8c8d", "Primal Camp": "#795548",
    "Wild Garden": "#16a085",
}


def load_data():
    with open(VISUAL_JSON, "r", encoding="utf-8") as f:
        visual = json.load(f)
    visual_map = {str(r["decorID"]): r for r in visual["results"]}

    with open(THEMES_JSON, "r", encoding="utf-8") as f:
        themes = json.load(f)

    theme_names = themes["metadata"]["theme_names"]
    aesthetic_ids = set()
    for group in themes["metadata"]["theme_groups"]:
        if group["name"] == "Aesthetic":
            aesthetic_ids = set(str(t) for t in group["themes"])

    existing_map = {}
    for did, item_data in themes["items"].items():
        item_aesthetics = []
        for tid, score in item_data.get("themes", {}).items():
            if tid in aesthetic_ids:
                item_aesthetics.append(theme_names[tid])
        if item_aesthetics:
            existing_map[did] = sorted(item_aesthetics)

    with open(CATALOG_JSON, "r", encoding="utf-8") as f:
        catalog = json.load(f)
    catalog_map = {str(item["decorID"]): item for item in catalog}

    return visual_map, existing_map, catalog_map


def get_thumb_data_url(decor_id):
    thumb = THUMB_DIR / f"{decor_id}.png"
    if thumb.exists():
        data = thumb.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    return ""


def build_html(items, title="Aesthetic Classification Review"):
    rows = []
    for item in items:
        did = str(item["decorID"])
        name = item.get("name", "?")
        visual_aes = item.get("visual_aesthetics", [])
        existing_aes = item.get("existing_aesthetics", [])
        confidence = item.get("confidence", "?")
        unclear = item.get("unclear", False)
        notes = item.get("notes", "")
        zone = item.get("zone", "")
        source = item.get("sourceType", "")
        thumb_url = get_thumb_data_url(did)

        visual_tags = " ".join(
            f'<span class="tag" style="background:{AESTHETIC_COLORS.get(a,"#555")}">{a}</span>'
            for a in visual_aes
        ) or '<span class="tag none">None</span>'

        existing_tags = " ".join(
            f'<span class="tag" style="background:{AESTHETIC_COLORS.get(a,"#555")}">{a}</span>'
            for a in existing_aes
        ) or '<span class="tag none">None</span>'

        checkboxes = "".join(
            f'<label><input type="checkbox" name="aes_{did}" value="{a}" '
            f'{"checked" if a in visual_aes else ""}> {a}</label>'
            for a in AESTHETICS
        )

        flag = " flagged" if unclear else ""
        rows.append(f"""
        <div class="item{flag}" data-id="{did}">
            <div class="thumb-col">
                <img src="{thumb_url}" alt="{name}" class="thumb">
                <div class="item-id">#{did}</div>
            </div>
            <div class="info-col">
                <div class="item-name">{name}</div>
                <div class="meta">{zone} | {source} | Confidence: {confidence}</div>
                {f'<div class="notes">Notes: {notes}</div>' if notes else ''}
                <div class="predictions">
                    <div><strong>Visual:</strong> {visual_tags}</div>
                    <div><strong>Algorithm:</strong> {existing_tags}</div>
                </div>
                <div class="overrides">
                    <strong>Your override:</strong>
                    <div class="checkbox-grid">{checkboxes}</div>
                    <div class="actions">
                        <button onclick="acceptVisual('{did}')">Accept Visual</button>
                        <button onclick="acceptAlgo('{did}')">Accept Algorithm</button>
                        <button onclick="acceptBoth('{did}')">Accept Union</button>
                        <button onclick="clearAll('{did}')">No Aesthetic</button>
                    </div>
                </div>
            </div>
        </div>""")

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #1a1a2e; color: #e0e0e0; margin: 0; padding: 20px; }}
h1 {{ color: #f1c40f; text-align: center; }}
.summary {{ text-align: center; margin-bottom: 20px; color: #aaa; }}
.item {{ display: flex; gap: 16px; padding: 16px; margin: 8px 0;
         background: #16213e; border-radius: 8px; border: 1px solid #333; }}
.item.flagged {{ border-color: #e74c3c; }}
.thumb-col {{ flex-shrink: 0; text-align: center; }}
.thumb {{ width: 128px; height: 128px; border-radius: 6px; background: #0a0a1a; }}
.item-id {{ color: #f1c40f; font-size: 12px; margin-top: 4px; }}
.info-col {{ flex: 1; }}
.item-name {{ font-size: 16px; font-weight: bold; color: #fff; }}
.meta {{ font-size: 12px; color: #888; margin: 4px 0; }}
.notes {{ font-size: 12px; color: #e74c3c; font-style: italic; }}
.predictions {{ margin: 8px 0; }}
.tag {{ display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 4px;
        font-size: 11px; color: #fff; font-weight: bold; }}
.tag.none {{ background: #333; color: #888; }}
.overrides {{ margin-top: 8px; padding-top: 8px; border-top: 1px solid #333; }}
.checkbox-grid {{ display: flex; flex-wrap: wrap; gap: 4px; margin: 4px 0; }}
.checkbox-grid label {{ font-size: 11px; min-width: 100px;
                        display: flex; align-items: center; gap: 3px; }}
.actions {{ margin-top: 6px; display: flex; gap: 6px; }}
.actions button {{ padding: 4px 10px; border: 1px solid #555; border-radius: 4px;
                   background: #2c3e50; color: #fff; cursor: pointer; font-size: 11px; }}
.actions button:hover {{ background: #3498db; }}
#export-bar {{ position: sticky; top: 0; background: #0a0a1a; padding: 12px;
               text-align: center; z-index: 100; border-bottom: 2px solid #f1c40f; }}
#export-bar button {{ padding: 8px 20px; background: #27ae60; color: #fff;
                      border: none; border-radius: 6px; font-size: 14px;
                      cursor: pointer; margin: 0 8px; }}
#export-bar button:hover {{ background: #2ecc71; }}
#count {{ color: #f1c40f; }}
</style>
</head>
<body>
<div id="export-bar">
    <button onclick="exportOverrides()">Export Overrides JSON</button>
    <button onclick="exportAll()">Export All Decisions</button>
    <span id="count">{len(items)} items to review</span>
</div>
<h1>{title}</h1>
<div class="summary">
    Check items that need your attention. Use "Accept Visual" / "Accept Algorithm" for quick approval.
    <br>Modify checkboxes for custom overrides. Export when done.
</div>
{"".join(rows)}
<script>
const AESTHETICS = {json.dumps(AESTHETICS)};
const VISUAL = {json.dumps({str(i['decorID']): i.get('visual_aesthetics', []) for i in items})};
const ALGO = {json.dumps({str(i['decorID']): i.get('existing_aesthetics', []) for i in items})};

function setChecked(did, aesthetics) {{
    AESTHETICS.forEach(a => {{
        const cb = document.querySelector(`input[name="aes_${{did}}"][value="${{a}}"]`);
        if (cb) cb.checked = aesthetics.includes(a);
    }});
}}
function acceptVisual(did) {{ setChecked(did, VISUAL[did] || []); }}
function acceptAlgo(did) {{ setChecked(did, ALGO[did] || []); }}
function acceptBoth(did) {{
    const union = [...new Set([...(VISUAL[did]||[]), ...(ALGO[did]||[])])];
    setChecked(did, union);
}}
function clearAll(did) {{ setChecked(did, []); }}

function collectDecisions() {{
    const decisions = {{}};
    document.querySelectorAll('.item').forEach(item => {{
        const did = item.dataset.id;
        const checked = [];
        item.querySelectorAll('input[type="checkbox"]:checked').forEach(cb => {{
            checked.push(cb.value);
        }});
        decisions[did] = checked;
    }});
    return decisions;
}}

function exportOverrides() {{
    const decisions = collectDecisions();
    const overrides = {{}};
    Object.entries(decisions).forEach(([did, aes]) => {{
        const v = (VISUAL[did] || []).sort().join(',');
        const d = aes.sort().join(',');
        if (v !== d) overrides[did] = aes;
    }});
    downloadJSON(overrides, 'aesthetic_overrides.json');
    alert(`Exported ${{Object.keys(overrides).length}} overrides`);
}}

function exportAll() {{
    const decisions = collectDecisions();
    downloadJSON(decisions, 'aesthetic_decisions.json');
    alert(`Exported ${{Object.keys(decisions).length}} decisions`);
}}

function downloadJSON(data, filename) {{
    const blob = new Blob([JSON.stringify(data, null, 2)], {{type: 'application/json'}});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = filename; a.click();
    URL.revokeObjectURL(url);
}}
</script>
</body>
</html>"""
    return html


def main():
    parser = argparse.ArgumentParser(description="Build validation review page")
    parser.add_argument("--unclear", action="store_true",
                        help="Show only unclear/low-confidence items")
    parser.add_argument("--disagreements", action="store_true",
                        help="Show items where visual and algorithm disagree")
    parser.add_argument("--unthemed", action="store_true",
                        help="Show only previously unthemed items")
    parser.add_argument("--all", action="store_true",
                        help="Show all items")
    args = parser.parse_args()

    visual_map, existing_map, catalog_map = load_data()

    items = []
    for did, r in visual_map.items():
        visual_aes = sorted(r.get("aesthetics", []))
        existing_aes = existing_map.get(did, [])
        cat = catalog_map.get(did, {})

        entry = {
            "decorID": int(did),
            "name": r.get("name", cat.get("name", "?")),
            "visual_aesthetics": visual_aes,
            "existing_aesthetics": existing_aes,
            "confidence": r.get("confidence", "?"),
            "unclear": r.get("unclear", False),
            "notes": r.get("notes", ""),
            "zone": cat.get("zone", "") or "",
            "sourceType": (cat.get("sources", [{}])[0].get("type", "")
                          if cat.get("sources") else ""),
        }

        include = False
        if args.all:
            include = True
        elif args.unclear and (r.get("unclear") or r.get("confidence") == "medium"):
            include = True
        elif args.disagreements:
            if set(visual_aes) != set(existing_aes):
                include = True
        elif args.unthemed:
            if did not in existing_map or not existing_map[did]:
                include = True
        else:
            # Default: unclear + unthemed + disagreements
            if r.get("unclear") or r.get("confidence") == "medium":
                include = True
            elif did not in existing_map or not existing_map[did]:
                include = True

        if include:
            items.append(entry)

    items.sort(key=lambda x: x["decorID"])

    title = "Aesthetic Classification Review"
    if args.unclear:
        title += " (Unclear Items)"
    elif args.disagreements:
        title += " (Disagreements)"
    elif args.unthemed:
        title += " (Previously Unthemed)"

    html = build_html(items, title)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    print(f"Built validation page with {len(items)} items: {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
