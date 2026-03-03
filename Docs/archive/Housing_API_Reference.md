# C_HousingCatalog API Reference (Retail 12.0 — Midnight)

> Tested in-game on the 12.0 PTR. This document captures verified API behavior
> so we don't have to re-run exploratory `/run` commands in future sessions.

---

## Overview

`C_HousingCatalog` is the primary Blizzard API namespace for Player Housing
decoration data. It provides catalog browsing, ownership queries, and search
functionality.

---

## Functions (Verified)

### `C_HousingCatalog.GetCatalogEntryInfoByRecordID(entryType, decorID, tryGetOwnedInfo)`

**This is the primary function DecorDrive uses for ownership checks.**

| Parameter | Type | Description |
|-----------|------|-------------|
| `entryType` | `number` | **Must be `1`** for standard decor items. `0` returns empty/nil. |
| `decorID` | `number` | The decoration record ID (matches Wowhead Gatherer Type 201 IDs). |
| `tryGetOwnedInfo` | `boolean` | Set to `true` to populate ownership fields (`quantity`, `numPlaced`, etc.). |

**Returns:** A table with the following fields (verified with decorID `4815` — "Northshire Barrel"):

| Field | Type | Example | Notes |
|-------|------|---------|-------|
| `name` | `string` | `"Northshire Barrel"` | Display name |
| `quantity` | `number` | `0` | **Owned count.** `> 0` means the player has collected this. |
| `remainingRedeemable` | `number` | `0` | How many more the player can still acquire. |
| `numPlaced` | `number` | `0` | How many are currently placed in the player's house. |
| `itemID` | `number` | `248798` | The WoW item ID (NOT the decor record ID). |
| `sourceText` | `string` | `"Quest: Report to Goldshire\nZone: Elwynn Forest\n..."` | Multi-line, describes how to obtain. |
| `quality` | `number` | `2` | Item quality (2 = Uncommon/green). |
| `isAllowedIndoors` | `boolean` | `true` | Can be placed inside. |
| `isAllowedOutdoors` | `boolean` | `true` | Can be placed outside. |
| `placementCost` | `number` | `1` | Cost to place (budget points). |
| `size` | `number` | `66` | Physical size metric. |
| `iconTexture` | `number` | `7423915` | FileID for the icon texture. |
| `asset` | `number` | `950755` | 3D model asset ID (for DressUpModel preview). |
| `uiModelSceneID` | `number` | `1334` | Scene ID for model framing. |
| `entryID` | `table` | *(nested)* | Internal entry identifier. |
| `categoryIDs` | `table` | *(nested)* | Which categories this belongs to. |
| `subcategoryIDs` | `table` | *(nested)* | Which subcategories this belongs to. |
| `dyeIDs` | `table` | *(nested)* | Available dye options. |
| `dataTagsByID` | `table` | *(nested)* | Internal data tags. |
| `customizations` | `table` | *(nested)* | Customization options. |
| `canCustomize` | `boolean` | `false` | Whether dyes/customizations are available. |
| `isUniqueTrophy` | `boolean` | `false` | Trophy-type decoration flag. |
| `isPrefab` | `boolean` | `false` | Prefab (multi-piece) decoration flag. |
| `showQuantity` | `boolean` | `false` | Whether UI should show owned count. |
| `firstAcquisitionBonus` | `number` | `10` | Bonus awarded on first acquisition. |

#### Ownership Check Logic

```lua
local info = C_HousingCatalog.GetCatalogEntryInfoByRecordID(1, decorID, true)
if info then
    local isOwned = (info.quantity > 0) or (info.numPlaced > 0)
end
```

- `quantity > 0` — player has at least one in inventory/collection
- `numPlaced > 0` — player has placed it (still counts as owned)
- `remainingRedeemable` — how many more can be acquired; 0 doesn't mean owned

---

### `C_HousingCatalog.GetCatalogEntryInfoByItem(itemInfo, tryGetOwnedInfo)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `itemInfo` | `number` | **The WoW item ID** (e.g., `228937`), NOT the decor record ID. |
| `tryGetOwnedInfo` | `boolean` | Same as above. |

**Status:** Requires 2 arguments (errors with 1). Returns empty results in our testing
even with valid item IDs. **Not recommended for use.** Prefer `GetCatalogEntryInfoByRecordID`.

---

### `C_HousingCatalog.GetDecorTotalOwnedCount()`

**Returns:** Two numbers.

| Return | Type | Example | Notes |
|--------|------|---------|-------|
| `totalOwned` | `number` | `778` | Total decorations the account owns. |
| `uniqueOwned` | `number` | `57` | Unique decoration types owned. |

Useful for progress display (e.g., "57 unique decorations collected").

---

### `C_HousingCatalog.GetDecorMaxOwnedCount()`

**Returns:** One number.

| Return | Type | Example | Notes |
|--------|------|---------|-------|
| `maxCount` | `number` | `5000` | Maximum decorations an account can own. |

---

### `C_HousingCatalog.GetCatalogCategoryInfo(categoryID)`

Returns category metadata for the housing catalog UI.

| categoryID | Name | Subcategory IDs |
|------------|------|-----------------|
| `1` | Furnishings | `{1, 5, 6, 2, 7}` |
| `2` | Structural | `{3, 4, 8, 9, 10}` |
| `3` | Accents | `{11, 12, 13, 14, 15}` |

**Return structure:** Table with `name` (string) and subcategory ID list.

---

### `C_HousingCatalog.GetCatalogSubcategoryInfo(subcategoryID)`

Returns subcategory metadata.

| subcategoryID | Name |
|---------------|------|
| `1` | Seating |
| `5` | Tables and Desks |
| *(others not yet tested)* | |

---

### `C_HousingCatalog.CreateCatalogSearcher()`

Returns a userdata searcher object. `getmetatable()` returns `false` — methods
are not discoverable via standard Lua metatable inspection. **Not yet usable by
DecorDrive.** May be useful for future catalog browsing features.

---

## Key Findings

### DecorID Mapping
- The `decorID` used in `GetCatalogEntryInfoByRecordID` matches the IDs from
  **Wowhead's WH.Gatherer.addData() Type 201** data blocks.
- Example: Wowhead Gatherer ID `4815` → `GetCatalogEntryInfoByRecordID(1, 4815, true)`
  → returns "Northshire Barrel".

### entryType Parameter
- `entryType = 0` → returns nil/empty for all tested decorIDs.
- **`entryType = 1` → works** for standard decoration items.
- Other entryType values (2+) not yet tested. May exist for prefabs, trophies, etc.

### Item ID vs Decor ID
- `decorID` (record ID) ≠ `itemID` (WoW item ID).
- Example: Northshire Barrel has `decorID = 4815` but `itemID = 248798`.
- The scraper captures Wowhead Gatherer IDs, which ARE the decorIDs.

### sourceText Parsing
The `sourceText` field contains human-readable acquisition info:
```
Quest: Report to Goldshire
Zone: Elwynn Forest
...
```
This could be used as a fallback data source for quest/zone mapping if scraper
data is incomplete.

---

## Functions Not Yet Tested

These exist in the `C_HousingCatalog` namespace but haven't been explored:

- `GetCatalogEntryInfoByRecordID` with entryType values > 1
- `CreateCatalogSearcher` methods (searcher:SetQuery, etc.)
- Any pagination or filtering APIs
- Events related to housing catalog changes

---

## Revision History

| Date | Notes |
|------|-------|
| 2026-02-23 | Initial documentation from PTR testing session. |
