# DecorDrive: Architecture Decisions Log

## ADR-001: Stateless Step Resolution
**Decision:** The guide engine does not persist per-step completion state. On every load, it queries `C_HousingCatalog` for each step's `decorID` and finds the first uncollected item.
**Rationale:** Makes route data updates seamless. No migration logic needed when packs change.
**Trade-off:** Slightly more work on load (N catalog queries per pack), but N is small (typically <100 steps per pack).

## ADR-002: Per-Character Active Pack, Account-Wide Ownership
**Decision:** `activePackID` is saved per-character. Decor ownership is checked account-wide via Blizzard's `C_HousingCatalog` API.
**Rationale:** Quest prerequisites are character-specific (can't skip quest chains). But housing collection is account-wide, so if char A collects an item, char B's guide skips it.

## ADR-003: Retail Library Stack
**Decision:** Use LibStub, LibDBIcon-1.0, LibDataBroker-1.1, CallbackHandler-1.0. No Ace3 framework.
**Rationale:** Follows patterns from well-maintained retail addons (ADT, Plumber, HomeBound). Ace3 is overkill for our use case. Zone-to-mapID resolution is pipeline-emitted (no runtime library needed).

## ADR-004: Lua + XML Templates for UI
**Decision:** Use XML for reusable widget templates (StepRow, DetourPopup). Use Lua for frame construction, layout logic, and dynamic behavior.
**Rationale:** Modern retail addons (Plumber, BigWigs, Baganator, Details) commonly use XML templates for repeating widgets. DecorDrive's Step Tracker has repeating row widgets (icon + label + coords + status) — defining a `StepRowTemplate` in XML and instantiating in Lua is cleaner than repeating CreateFrame() boilerplate. One-off frames and dynamic layout remain in Lua.
**Supersedes:** Original decision was "Pure Lua, no XML" (carried from CogwheelRecruiter Classic pattern).

## ADR-005: TomTom Soft-Dependency
**Decision:** Use TomTom for waypoints if available, fall back to `C_Map.SetUserWaypoint()`.
**Rationale:** TomTom is widely installed and offers better UX (arrow, distance). But we don't want to require it.

## ADR-006: Data Pipeline Generates Lua Files
**Decision:** Python scraper outputs `.lua` files directly into `Data/Packs/`. These are committed to the repo and shipped with the addon.
**Rationale:** No runtime parsing needed. Lua tables load instantly. Pipeline is re-runnable: scrape → generate → replace files → deploy.

## ADR-007: v1 Step Types
**Decision:** QUEST, VENDOR, TRAVEL, DUNGEON (proximity-triggered only).
**Rationale:** Quest and Vendor cover guaranteed-progress items. Travel handles Zidormi and navigation. Dungeon is opt-in RNG. Achievements, professions, reputation deferred to v2.

## ADR-008: Scraper Data Sources
**Decision:** WoWDB as primary item catalog (317+ quest items, structured). Wowhead guides for coordinates and zone grouping. Questie DB for coordinate cross-reference.
**Rationale:** WoWDB has the most complete structured data. Wowhead has coordinates. Together they cover both dimensions.

## ADR-009: Faction Flags on Steps (Not Separate Packs)
**Decision:** Single pack per region with per-step `faction` field (`nil` = both, `"Alliance"`, `"Horde"`). Guide engine skips steps where `step.faction ~= nil and step.faction ~= playerFaction`.
**Rationale:** Avoids duplicating shared content across faction-specific packs. Most decor sources are faction-neutral; only a minority are gated.

## ADR-010: Quest Chain Resolution
**Decision:** Each QUEST step includes the full prerequisite chain (`questChain` = ordered list of quest IDs leading to the decor-rewarding quest). The Guide Engine checks `C_QuestLog.IsQuestFlaggedCompleted(questID)` for each quest in the chain and presents the first incomplete prerequisite as the current sub-step.
**Rationale:** Without chain tracking, the guide tells players "complete quest X" when they can't pick it up due to incomplete prerequisites. This is the core "guide" value — walking the player through every step, not just pointing at the final reward.
**Data impact:** Scraper must resolve quest prerequisites from Wowhead (each quest page lists prereqs). Recursive crawl adds complexity but is essential for usability.
**UI impact:** Step Tracker shows chain progress: "Morbent's Bane (Quest 3/4): Complete 'Sven's Camp'" with final decor reward preview visible.

## ADR-011: Verified C_HousingCatalog API Signature
**Decision:** Use `C_HousingCatalog.GetCatalogEntryInfoByRecordID(1, decorID, true)` for all ownership checks and decor info retrieval. Ownership is determined by `info.quantity > 0 or info.numPlaced > 0`.
**Rationale:** Verified in-game on 12.0 PTR. Key findings: `entryType` must be `1` (not `0`); `decorID` matches Wowhead Gatherer Type 201 IDs; the return table has `quantity`, `numPlaced`, `name`, `itemID`, `sourceText`, `iconTexture`, `asset`, `uiModelSceneID`, and 20+ other fields. `GetCatalogEntryInfoByItem` exists but returned empty results in all tests — avoid it.
**Reference:** Full field documentation in `Docs/Housing_API_Reference.md`.
