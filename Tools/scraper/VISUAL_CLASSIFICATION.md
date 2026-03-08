# Visual Aesthetic Classification

How aesthetic categories are defined and items are classified for the
HearthAndSeek addon. This process runs **outside** the automated pipeline
and is needed whenever new decorations are added to the game.

---

## Overview

Items are classified into **15 aesthetic categories** using visual analysis
of item thumbnails from WoWDB. An AI model reviews montage grids of item
images and assigns each item to one or more categories based on its visual
appearance.

This approach replaced a text-based algorithm (name patterns + WoWDB tags +
community set voting) after a blind comparison showed visual classification
achieves 76.7% accuracy vs 54.7% for the algorithm.

### The 15 Aesthetic Categories

| Category | Description | Typical Items |
|----------|-------------|---------------|
| Arcane Sanctum | Purple-blue magical glow, mana crystals, enchanted filigree | Nightborne/Dalaran furniture, enchanting tables, glowing tomes |
| Cottage Hearth | Simple sturdy wood, woolen rugs, stone fireplaces | Rustic furniture, candles, woven baskets, farmhouse items |
| Enchanted Grove | Living moonlit wood, glowing vines, fae-touched surfaces | Night elf druidic items, Ardenweald, crescent moon motifs |
| Feast Hall | Food, drink, cooking, tavern signage | Mugs, cauldrons, platters, kegs, spice racks, market stalls |
| Fel Forge | Green felfire, jagged Legion architecture | Demonic containers, fel crystals, Burning Legion banners |
| Haunted Manor | Coffins, cobwebs, gothic decay, dark Victorian | Forsaken/Gilnean items, Revendreth, Ebon Blade, spiked iron |
| Royal Court | Gilded surfaces, ornamental wood, plush upholstery | Palace furniture, golden candelabras, elegant vases, trumpets |
| Sacred Temple | Golden altars, divine light, ceremonial bells | Temple braziers, religious candles, prayer cushions, Loa idols |
| Scholar's Archive | Bookcases, tomes, scrolls, writing desks | Books, maps, globes, filing cabinets, parchments |
| Seafarer's Haven | Rope, anchors, ship lanterns, maritime brass | Nautical equipment, fishing nets, treasure chests, dock pilings |
| Tinker's Workshop | Gears, cogs, pipes, rockets, riveted metal | Gnomish/goblin devices, engineering gadgets, anvils, toolboxes |
| Void Rift | Deep purple-black void energy, tentacles, cosmic horror | Void crystals, shadow-touched furniture, Old God motifs |
| War Room | Weapon racks, battle banners, planning tables | Military equipment, mounted trophies, faction heraldry |
| Wild Frontier | Hide, bone, antler, tusks, rough timber | War camp items, frontier lodge, tribal totems, hunting trophies |
| Wild Garden | Plants, trees, flowers, boulders, grass patches | Garden pots, stone ornaments, bird feeders, vines, landscaping |

### How Categories Were Originally Discovered

Four independent AI agents analyzed the full montage set (1,659 items across
81 sheets) with no predefined categories. All four converged on nearly
identical category sets:
- 11 categories had 4/4 agreement
- 3 categories had 3/4 agreement
- 1 category (Wild Garden) had 2/4 agreement

The categories map naturally to WoW's visual language — supernatural
categories (Fel green, Void purple, Arcane blue) have distinctive chromatic
signatures that make classification straightforward.

---

## Data Files

| File | Description |
|------|-------------|
| `data/montages/visual_classifications.json` | Primary source — all 1,659 items with aesthetic assignments |
| `data/manual_theme_annotations.json` | Human-reviewed overrides for ambiguous items (~50 entries) |
| `data/thumbnails/` | Cached WoWDB thumbnails (256x256 PNGs, ~67MB, gitignored) |

### visual_classifications.json Format

```json
{
  "metadata": { "total_items": 1659, "timestamp": "..." },
  "results": [
    {
      "decorID": 478,
      "name": "Wooden Coffin Lid",
      "aesthetics": ["Haunted Manor"],
      "confidence": "high",
      "notes": ""
    },
    {
      "decorID": 1161,
      "name": "Small Mask of Bwonsamdi",
      "aesthetics": ["Haunted Manor", "Sacred Temple"],
      "confidence": "high",
      "unclear": false,
      "notes": "Death loa — sacred but dark"
    }
  ]
}
```

Fields:
- `decorID`: Wowhead Gatherer Type 201 ID
- `name`: Item display name
- `aesthetics`: List of 0-3 category names (most items have 1-2)
- `confidence`: `"high"` (score 100) or `"medium"` (score 75)
- `unclear`: `true` if the classifier wasn't sure (flagged for human review)
- `notes`: Optional reasoning

### manual_theme_annotations.json Format

```json
{
  "487": { "aesthetics": ["Royal Court"] },
  "1161": { "aesthetics": ["Haunted Manor", "Sacred Temple"] }
}
```

Manual annotations **override** visual classifications. They are used for
items where the visual classifier was uncertain or made an error, as
verified by a human reviewer.

---

## Pipeline Integration

`compute_item_themes.py` applies themes in this priority order:

1. **Algorithm** computes culture themes (racial/faction) from WoWDB tags,
   community sets, and name patterns
2. **Visual classifications** replace all algorithm-computed aesthetic themes
   (culture themes are preserved)
3. **Manual annotations** override both visual and algorithm aesthetics for
   specific items
4. **Cross-aesthetic conflict exclusions** remove contradictory assignments
   (e.g., an item named "Forsaken X" won't keep a "Sacred Temple" tag if
   it has no Sacred keywords in its name)

If `visual_classifications.json` is missing, the algorithm falls back to
text-based aesthetic scoring (lower accuracy but functional).

---

## Classifying New Items (Future Patches)

When a new WoW patch adds decorations, follow these steps to classify them.

### Prerequisites

```bash
pip install Pillow requests beautifulsoup4 lxml
```

### Step 1: Run the Main Pipeline First

Complete the standard pipeline through WoWDB scraping so that thumbnails
are available:

```bash
cd Tools/scraper
python run_pipeline.py --deploy     # or at minimum through scrape_wowdb.py
```

This populates `data/wowdb_cache/` with HTML pages containing thumbnail URLs.

### Step 2: Download Thumbnails

```bash
python download_thumbnails.py
```

Downloads and resizes thumbnails from WoWDB to `data/thumbnails/`. Only
fetches items not already cached. Requires the WoWDB cache from Step 1.

Options:
- `--size 256` — thumbnail size in pixels (default: 256)
- `--workers 4` — parallel download threads (default: 4)

### Step 3: Build Montage Grids

```bash
# All items (for full reclassification):
python build_montages.py --all --cols 4 --rows 4

# Only unthemed items (for incremental updates):
python build_montages.py --unthemed --cols 4 --rows 4
```

Creates labeled contact sheets in `data/montages/`. Each sheet shows a grid
of item thumbnails with decorID and name labels. Metadata JSON files are
created alongside for reference.

Output: `data/montages/all_001.png`, `all_002.png`, ..., plus
`all_metadata.json` with per-sheet item lists.

### Step 4: Visual Classification with AI

Feed the montage images to an AI model (Claude with vision) along with the
metadata. Use this prompt template:

```
You are classifying World of Warcraft housing decorations into aesthetic
categories. For each item in the montage grid, assign one or more
categories from this list:

CATEGORIES:
- Arcane Sanctum: Purple-blue magical glow, mana crystals, enchanted filigree
- Cottage Hearth: Simple sturdy wood, woolen rugs, stone fireplaces
- Enchanted Grove: Moonlit wood, glowing vines, fae-touched, druidic
- Feast Hall: Food, drink, cooking equipment, tavern items
- Fel Forge: Green felfire, demonic, Legion architecture
- Haunted Manor: Coffins, cobwebs, gothic decay, dark Victorian
- Royal Court: Gilded, ornamental, plush upholstery, aristocratic
- Sacred Temple: Golden altars, divine light, ceremonial, devotional
- Scholar's Archive: Books, scrolls, maps, writing desks, academic
- Seafarer's Haven: Nautical, rope, anchors, ship equipment, maritime
- Tinker's Workshop: Gears, cogs, pipes, rockets, engineering
- Void Rift: Deep purple-black void energy, tentacles, cosmic horror
- War Room: Weapon racks, battle banners, military equipment
- Wild Frontier: Hide, bone, antler, rough timber, tribal, rugged
- Wild Garden: Plants, flowers, trees, garden items, landscaping

For each item, provide:
- decorID (from the label)
- name (from the label)
- aesthetics: array of 1-3 category names
- confidence: "high" or "medium"
- unclear: true if you're not sure
- notes: brief reasoning for non-obvious choices

The metadata JSON file provides additional context (zone, source type,
expansion) for each item.

IMPORTANT GUIDELINES:
- Most items should have exactly 1 category. Use 2-3 only when the item
  genuinely belongs in multiple categories (e.g., a Forsaken war banner
  is both Haunted Manor and War Room).
- Prefer the most specific category. A bookcase in a Val'sharah style is
  "Enchanted Grove" + "Scholar's Archive", not just "Scholar's Archive".
- Items with no clear aesthetic (generic crates, plain objects) can have
  an empty aesthetics array.
- Focus on VISUAL appearance, not just the name. A "Sacred" item that
  looks dark and spooky should be Haunted Manor.
```

Return format (JSON):
```json
[
  {
    "decorID": 478,
    "name": "Wooden Coffin Lid",
    "aesthetics": ["Haunted Manor"],
    "confidence": "high",
    "unclear": false,
    "notes": ""
  }
]
```

### Step 5: Merge Results

Combine all batch results into a single `visual_classifications.json`:

```python
import json
from pathlib import Path

results = []
for f in sorted(Path("data/montages").glob("results_*.json")):
    results.extend(json.load(open(f)))

# Deduplicate by decorID (later batches win)
seen = {}
for r in results:
    seen[r["decorID"]] = r

output = {
    "metadata": {"total_items": len(seen)},
    "results": sorted(seen.values(), key=lambda x: x["decorID"])
}
with open("data/montages/visual_classifications.json", "w") as f:
    json.dump(output, f, indent=2)
```

### Step 6: Build Validation Page (Optional)

```bash
# Review items where visual and algorithm disagree:
python build_validation_page.py --disagreements

# Review only unclear/low-confidence items:
python build_validation_page.py --unclear

# Review all items:
python build_validation_page.py --all
```

Opens an interactive HTML page where you can:
- See the item thumbnail alongside visual and algorithm predictions
- Accept visual, accept algorithm, or override with custom selections
- Export your decisions as JSON

Export format: `aesthetic_decisions.json` — `{decorID: [category1, ...]}`

### Step 7: Import Overrides

Convert exported decisions to annotation format and save:

```python
import json

with open("aesthetic_decisions.json") as f:
    decisions = json.load(f)

annotations = {}
for did, aesthetics in decisions.items():
    annotations[did] = {"aesthetics": aesthetics}

with open("data/manual_theme_annotations.json", "w") as f:
    json.dump(annotations, f, indent=2)
```

### Step 8: Regenerate Pipeline Output

```bash
python compute_item_themes.py
python output_catalog_lua.py
# or:
python run_pipeline.py --generate-only --deploy
```

### Incremental Updates (Recommended for Patches)

For small batches of new items (10-100), you don't need to reclassify
everything:

1. Run `python build_montages.py --unthemed` to get only new items
2. Classify those items (Step 4)
3. **Merge** new results into the existing `visual_classifications.json`
   (don't overwrite — append and deduplicate)
4. Run Step 8 to regenerate

---

## Quality Assurance

### Per-Category Montages

To spot-check category consistency, build per-category montages:

```python
import json, random
from pathlib import Path
from build_montages import build_montage, load_items, MONTAGE_DIR

items, themes = load_items()
visual = json.load(open("data/montages/visual_classifications.json"))

# Group items by category
by_cat = {}
for r in visual["results"]:
    for aes in r.get("aesthetics", []):
        by_cat.setdefault(aes, []).append(str(r["decorID"]))

# Build a montage for each category (24 random items)
random.seed(42)
for cat, dids in sorted(by_cat.items()):
    sample = random.sample(dids, min(24, len(dids)))
    slug = cat.lower().replace("'", "").replace(" ", "_")
    out = MONTAGE_DIR / "per_category" / f"{slug}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    build_montage(sample, items, 6, 4, out)
    print(f"{cat}: {len(dids)} items, montage: {out.name}")
```

### Coherence Scores (from initial audit)

| Category | Score | Verdict |
|----------|-------|---------|
| Fel Forge | 5/5 | Consistent |
| Void Rift | 5/5 | Consistent |
| Seafarer's Haven | 5/5 | Consistent |
| Feast Hall | 5/5 | Consistent |
| Cottage Hearth | 5/5 | Consistent |
| Scholar's Archive | 5/5 | Consistent |
| Arcane Sanctum | 4/5 | Mostly consistent |
| Royal Court | 4/5 | Mostly consistent |
| Sacred Temple | 4/5 | Mostly consistent |
| Tinker's Workshop | 4/5 | Mostly consistent |
| Wild Frontier | 4/5 | Mostly consistent |
| Wild Garden | 4/5 | Mostly consistent |
| Haunted Manor | 3/5 | Acceptable (broad scope) |
| War Room | 3/5 | Acceptable (catch-all risk) |
| Enchanted Grove | 3/5 | Acceptable (boundary overlap with Wild Garden) |

**Overall average: 4.2/5.0**

Known boundary challenges:
- **Enchanted Grove vs Wild Garden**: Enchanted = magical nature (glowing,
  moonlit). Wild Garden = normal nature (plants, garden items).
- **War Room catch-all risk**: Ensure items are genuinely military, not just
  "tough-looking".
- **Haunted Manor breadth**: Gilnean items are fine (dark Victorian). Items
  that are merely dark-colored but not gothic/spooky should be elsewhere.

---

## Troubleshooting

### Missing thumbnails
Re-run `python download_thumbnails.py`. If WoWDB cache is missing, run
`python scrape_wowdb.py --all` first.

### visual_classifications.json not found
The pipeline falls back to algorithm-based aesthetics (lower accuracy).
Follow the full classification process above to create it.

### New categories needed
If a future expansion introduces a distinctly new aesthetic (e.g., "Titan
Forge" for Titan-themed items), add it to:
1. `AESTHETIC_THEMES` in `compute_item_themes.py`
2. `themeColors` in `UI/CatalogFrame.lua`
3. `AESTHETICS` and `AESTHETIC_COLORS` in `build_validation_page.py`
4. The classification prompt (Step 4 above)
5. This document's category table
