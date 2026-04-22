# Wowhead extraction — design notes

This doc explains how the data pipeline pulls source/vendor attribution
from Wowhead, how it decides what to trust, and what to do when Wowhead
changes. It's the companion to `Tools/scraper/enrich_wowhead_extra.py`
and the NPC-fallback promotion in `Tools/scraper/output_catalog_lua.py`.

## Why we have this

The in-game scan (`/hs dump`) sometimes returns an empty `sourceText`
for a decor item. When that happens, the pipeline has nothing to go on
and the item ends up as `sourceType = "Other"` with no vendor name, no
navigate target. We use Wowhead as a secondary source to fill those
gaps — but we have to be careful because Wowhead's data for recently-
added items is sometimes stale or internally inconsistent.

## The two JSON shapes Wowhead emits

Each item page at `https://www.wowhead.com/item=<id>` contains vendor /
drop / quest data in **two different JSON shapes**. We parse both.

### 1. Legacy `sourcemore`

```
"sourcemore":[{"n":"Disguised Decor Duel Vendor","t":1,"ti":264056,"z":15969}]
```

- `n`: NPC name
- `t`: source type (mapped via `SOURCEMORE_TYPE_MAP`)
- `ti`: target ID (NPC id, quest id, etc.)
- `z`: Wowhead zone ID

This is the format Wowhead's **visible "Sold By" listview renders
from**. When sourcemore contains an NPC, the Wowhead UI shows it.
Reliable but thin on location info — no coords, no in-game `mapID`.

Parsed by `extract_sourcemore()`.

### 2. Modern decor-shape `sources`

```
"sources":[{"sourceType":5,"entityType":1,"entityId":264056,"reaction":{},
            "name":"Disguised Decor Duel Vendor",
            "area":{"id":15969,"name":"Silvermoon City","uiMap":2393,
                    "coords":[31.6,76.8]}}]
```

Strictly richer: carries the in-game `uiMap` (what we need for the
navigate button) and the zone name directly. But we've observed cases
(Dornogal Opals, Suramar Arcfruit Bowl, 2026-04-22) where Wowhead
embeds this block with a stale/datamined vendor that the UI **does NOT
display**.

Parsed by `extract_decor_sources()`.

## The trust model

We merge both extractors but only fully trust a decor-shape NPC entry
when Wowhead's page **actually renders it as a vendor**. Two signals,
either is sufficient:

### Primary signal: the sold-by Listview block

Rendered vendors appear inside:

```js
new Listview({id:'sold-by', ..., data:[{...,"name":"<NPC>",...}]})
```

`_extract_sold_by_block(html)` returns the raw text of this block (it's
JS-flavored JSON, so we don't try to parse it as JSON). The trust check
is then a simple substring match: `"id":<npcID>` or `"name":"<NPC>"`
inside the block.

This is **deterministic** — a block either exists with our NPC in it or
it doesn't. It doesn't shift when Wowhead tweaks breadcrumb counts or
sprinkles other JSON blobs.

### Secondary signal: word-bounded occurrence count

Fallback for NPCs rendered outside the sold-by listview (breadcrumbs,
related-item panels, etc.): `\b<NPC>\b` appears ≥ 3 times in the page.

We use a **word-bounded regex** (`re.compile(r'\b' + ...)`) — not a raw
`html.count(name)` — so a short NPC name isn't falsely corroborated by
being a substring of unrelated words. (A prior implementation using
plain `count()` would have over-counted "Ren" against "Rendering",
"Renown", etc.)

### Anything else → orphan JSON, dropped

Entries that don't clear either signal are logged and excluded:

```
itemID=272444: dropped 1 decor_sources entry(ies) that appear only in
isolated JSON (not rendered by Wowhead UI): ["Disguised Decor Duel Vendor
(occ=1)"]
```

## When in-game truth disagrees with Wowhead

If a human has verified in-game that an item IS sold by a specific
vendor but Wowhead's UI doesn't corroborate (orphan JSON rejected),
**do not** loosen the trust model. Instead, add a manual override in
`Tools/scraper/data/overrides.json` keyed by decorID. Include:

- `_note`: one-line explanation + verification date + user initials if
  multiple people maintain the catalog.
- `_reason`: one of `in_game_verified`, `zone_fixup`, `achievement_fix`,
  `wowhead_wrong`. Used for future grouping / audit.
- `_verified_date`: YYYY-MM-DD of the in-game verification.
- Fields to set: `sources`, `vendor`, `npcID`, `npcX`, `npcY`, `zone`,
  etc. Anything not prefixed with `_` overwrites the enriched item.

Example:

```json
"21602": {
  "_note": "Sin'dorei Garden Swing — sold by Disguised Decor Duel Vendor, not corroborated by Wowhead UI",
  "_reason": "in_game_verified",
  "_verified_date": "2026-04-22",
  "sources": [{"type": "Vendor", "value": "Disguised Decor Duel Vendor"}],
  "vendor": "Disguised Decor Duel Vendor",
  "npcID": 264056,
  "npcX": 31.6,
  "npcY": 76.8,
  "zone": "Silvermoon City"
}
```

The override is applied during `enrich_catalog.py`'s main merge loop
(`load_overrides()` + inline application). It runs **after** Wowhead
enrichment, so overrides always win.

## When Wowhead changes its HTML/JSON

Symptoms a Wowhead-side change has broken extraction:

1. Full pipeline run shows **sharply reduced** "With alt sources" count
   compared to the previous run (from ~1300 to dozens or zero).
2. New items have `sourceType = "Other"` despite having obvious vendors
   on the item page when viewed in a browser.
3. `enrich_wowhead_extra.py` log contains warnings like:

   ```
   itemID=XXXXX: Wowhead page contains a decor-shape sources block but
   extract_decor_sources() returned 0 entries — extractor may be out of
   sync with Wowhead's HTML structure.
   ```

Response:

1. Grab a fresh copy of a known-good item's HTML
   (`curl_cffi.requests.get(url, impersonate='chrome').text`).
2. Look for the vendor name in the HTML and identify the JSON shape.
3. If it's a new shape, add a new extractor alongside the existing ones
   (don't replace — keep backward compat for older items).
4. Keep `extract_sourcemore` running as the legacy fallback even if a
   new primary extractor lands. Wowhead keeps old blocks around for
   years.

## Related files

- `Tools/scraper/enrich_wowhead_extra.py` — extractors + trust filter
- `Tools/scraper/output_catalog_lua.py` — NPC-fallback promotion via
  `promote_wowhead_npc_fallback()` (importable)
- `Tools/scraper/enrich_catalog.py` — override application + primary
  enrichment (NPC coords via tooltip API / page scrape)
- `Tools/scraper/data/overrides.json` — manual attribution
- `Tools/scraper/tests/test_enrich_wowhead_extra.py` — 50+ tests
  covering extractors, promotion, orphan filter, targeted-run merge,
  overrides integration
