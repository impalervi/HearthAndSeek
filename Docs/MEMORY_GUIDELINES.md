# Memory Guidelines

Developer reference for keeping HearthAndSeek's memory footprint low.

## Memory Budget

- Current: ~13-14 MB reported by WoW (includes Lua overhead; actual heap ~2-3 MB)
- Largest contributor: `CatalogData.lua` (1,677 items, ~2 MB on disk)
- Second: `QuestChains.lua` (4,978 quests, ~812 KB)

## Rules for Pipeline Output (output_catalog_lua.py)

1. **Never emit `nil` fields** -- Lua tables don't store nil keys, so `field = nil,` wastes file size with zero runtime benefit.
2. **Never emit empty strings** -- Runtime code already handles nil gracefully for all optional fields.
3. **Required fields** that MUST always be emitted: `decorID`, `name`, `sourceType`. Runtime assumes these exist.
4. All other fields are **optional** -- only emit when they carry meaningful values.
5. New per-item fields must be optional (emit only when non-nil) and runtime must nil-check them.

## Rules for Runtime Lua Code

1. Always check catalog fields for nil before use (except `decorID`, `name`, `sourceType`).
2. Use guard patterns: `item.field and item.field ~= ""` or `item.field or fallback`.
3. Never assume a field exists just because some items have it -- source types vary widely.
4. Avoid large lookup tables at startup -- prefer lazy initialization.
5. Caches should be populated on-demand rather than all-at-once when feasible.

## Per-Item Table Overhead

- Every Lua table allocation costs ~40-56 bytes (hash part + array part).
- Nested tables (`categoryIDs`, `themeIDs`, `additionalSources`, etc.) each cost this overhead **per item**.
- Prefer flat scalar fields over nested tables when possible.
- Only add new nested table fields when truly necessary.

## What NOT to Do

- Don't emit `= {},` for empty tables (table overhead for zero data).
- Don't duplicate data across items (e.g., zone name is already in the ByZone index).
- Don't store computed values that can be derived at runtime.
- Don't add string fields with repetitive values (Lua interns strings, but table slots still cost memory).
