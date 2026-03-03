# DecorDrive: Project Blueprint
**Target Expansion:** World of Warcraft: Midnight (12.0)
**Goal:** A "RestedXP-style" in-game guide for the streamlined collection of Player Housing decorations.

## 1. Core Philosophy
* **The "Path of Least Resistance":** Unlike encyclopedic addons (e.g., *AllTheThings*), DecorDrive focuses on the *active route*. It tells the player where to go, which NPC to talk to, and what to do next.
* **Guaranteed Progress:** The primary "Storyline" guides focus on 100% drop-rate sources, such as Quests and Vendors.
* **Open Access:** The addon and all associated data guides will be free for the community.

---

## 2. Key Features & Decisions

### A. Regional Cluster Navigation
The guide is organized into **Regional Tours** (e.g., "The Eastern Kingdoms Collection Tour"). The logic prioritizes:
* Minimizing portal and flight-path usage.
* Grouping all quest-pickups and vendor-buys within the same sub-zone.

### B. Modular Guide Packs
The addon supports a modular pack system to keep memory usage low:
* **The Midnight Pack:** Focused on the new Sunwell, Silvermoon, and Void-themed assets.
* **The Legacy Library:** Retroactive packs for Classic/Cata, Outland, Northrend, etc.
* Packs are self-contained Lua data files dropped into `Data/Packs/`.

### C. The "RNG Detour" Logic
A proximity-based notification system that triggers when the player is near a dungeon or raid containing decor drops.
* **Pop-up:** *"Dungeon Nearby: Deadmines. Boss 'Cookie' drops [Rolling Pin] (15% chance). Attempt now?"*
* **Options:** `[Yes]`, `[Skip Once]`, `[Never show RNG for this zone]`.
* **Scope:** v1 focuses on QUEST + VENDOR steps. Dungeon/Raid detours are proximity-triggered optional side content.

### D. Smart Integration
* **Ownership Check:** Uses `C_HousingCatalog.GetCatalogEntryInfoByRecordID(entryType, decorID)` to check decor ownership (account-wide) and automatically skip already-owned items.
* **Navigation:** TomTom soft-dependency with fallback to `C_Map.SetUserWaypoint()`.
* **Zidormi Handling:** Time-phase transitions are encoded as explicit guide steps ("Talk to Zidormi at X,Y to switch to the correct phase").

### E. Stateless Step Resolution (Route Update Safety)
The guide engine is **stateless about individual step completion**. It never saves "step 5 done."
* On every pack load/resume, it iterates all steps and queries `C_HousingCatalog` for ownership.
* The first uncollected item becomes the current step.
* **Per-character persistence:** Only `activePackID` (which tour is active).
* **Route updates are seamless:** New steps slot in, removed steps vanish, reordered steps just work.

---

## 3. Technical Architecture

### Addon Stack (Retail 12.0)
* **Libraries:** LibStub, LibDBIcon-1.0, LibDataBroker-1.1, CallbackHandler-1.0, HereBeDragons-2.0
* **Settings:** Blizzard Settings panel via `Settings.RegisterCanvasLayoutCategory()`
* **UI:** Pure Lua frames (no XML). RestedXP-style freely draggable Step Tracker.
* **Item Previews:** `DressUpModel` / `Model` frame for 3D decor preview (same approach as ADT/Plumber).
* **Minimap Button:** LibDBIcon (standard Retail pattern).

### Guide Engine
* State machine: load pack → iterate steps → check ownership via `C_HousingCatalog` → find first uncollected → present step → set waypoint → wait for completion → advance.
* Pack selection via dropdown UI.

### Step Types (v1)
| Type | Description | Completion Signal |
|------|-------------|-------------------|
| `QUEST` | Accept/complete a quest that rewards decor | `C_HousingCatalog` ownership check |
| `VENDOR` | Buy decor from a vendor NPC | `C_HousingCatalog` ownership check |
| `TRAVEL` | Go to a location (Zidormi, flight master, portal) | Proximity check |
| `DUNGEON` | (RNG Detour) Kill a boss for a drop chance | `C_HousingCatalog` ownership check |

### Data Pipeline
```
[Wowhead Guides + WoWDB] → scrape_sources.py → raw_data.json
                                                      ↓
                                              generate_routes.py → optimized regional packs
                                                      ↓
                                              output_lua.py → Data/Packs/*.lua
                                                      ↓
                                              Drop into addon → deploy → test
```

**Pipeline is re-runnable:** Scrape new data → regenerate routes → replace Lua files → deploy. Addon handles it transparently via stateless step resolution.

### Data Sources
* **Primary catalog:** WoWDB Housing Decor DB (317+ quest items, paginated): `https://housing.wowdb.com/decor/?source_types=Quest`
* **Coordinates & zone grouping:** Wowhead Housing Guides: `https://www.wowhead.com/guide/player-housing/decor-farming-vanilla-cataclysm-quests-drops-achievements-vendors`
* **Cross-reference:** Questie open-source DB for NPC coordinate verification.

### Data Schema
```lua
DecorDrive_Data["PackName"] = {
    packID = "vanilla_eastern_kingdoms",
    title = "Eastern Kingdoms: Vanilla Collection Tour",
    description = "Collect all quest and vendor decor across Eastern Kingdoms zones.",
    steps = {
        {
            stepIndex = 1,
            type = "VENDOR",
            label = "Buy Hooded Iron Lantern",
            decorName = "Hooded Iron Lantern",
            decorID = 12345,
            questID = nil,
            npc = "Captain Lancy Revshon",
            npcID = 49877,
            mapID = 37,           -- Elwynn Forest uiMapID
            coords = {67.6, 72.8},
            zone = "Elwynn Forest",
            note = nil,
        },
        {
            stepIndex = 2,
            type = "QUEST",
            label = "Complete 'Kobold Candles'",
            decorName = "Hooded Iron Lantern",
            decorID = 12346,
            questID = 60,
            npc = "Marshal Dughan",
            npcID = nil,
            mapID = 37,
            coords = {42.1, 65.7},
            zone = "Elwynn Forest",
            note = nil,
        },
        -- ...
    }
}
```

---

## 4. Project Structure
```
DecorDrive/
├── DecorDrive.toc
├── Core/
│   ├── Init.lua              -- Namespace, LibStub, event bootstrap
│   ├── Constants.lua          -- Enums, colors, sizing
│   └── Utils.lua              -- Shared helpers
├── Data/
│   ├── Schema.lua             -- Step/Route data structures
│   └── Packs/                 -- One file per regional tour (generated)
│       ├── ElwynnForest.lua   -- Mock data for PoC
│       └── ...
├── Modules/
│   ├── GuideEngine.lua        -- Step resolution, pack loading, advancement
│   ├── Navigation.lua         -- TomTom / C_Map waypoint integration
│   ├── DecorTracker.lua       -- C_HousingCatalog ownership queries
│   └── RNGDetour.lua          -- Proximity-based dungeon/raid notifications
├── UI/
│   ├── StepTracker.lua        -- Main RestedXP-style guide frame
│   ├── StepRow.lua            -- Individual step widget (icon, text, status)
│   ├── ItemPreview.lua        -- 3D model frame for decor preview
│   ├── DetourPopup.lua        -- RNG detour notification frame
│   └── MinimapButton.lua      -- LibDBIcon integration
├── Libs/                      -- Embedded libraries
│   ├── LibStub/
│   ├── CallbackHandler-1.0/
│   ├── LibDataBroker-1.1/
│   ├── LibDBIcon-1.0/
│   └── HereBeDragons-2.0/
├── Media/
│   └── Icons/
├── Docs/
│   ├── DecorDrive_Blueprint.md
│   └── Architecture_Decisions.md
├── Tools/
│   └── scraper/               -- Python data pipeline
│       ├── requirements.txt
│       ├── scrape_wowhead.py
│       ├── scrape_wowdb.py
│       ├── generate_routes.py
│       ├── output_lua.py
│       └── data/              -- Intermediate JSON files
├── scripts/
│   ├── deploy.ps1
│   ├── deploy.sh
│   └── deploy.cmd
├── deploy.config.example.json
├── README.md
└── .gitignore
```

---

## 5. Resolved Decisions
| Question | Decision | Rationale |
|----------|----------|-----------|
| Static vs. dynamic routes | Static, regenerated per data update | Simpler, predictable, no runtime algorithm cost |
| Route update handling | Stateless step resolution via catalog | Seamless updates, no migration needed |
| Progress persistence | Per-character (activePackID only) | Quest prerequisites are character-specific |
| Ownership checks | Account-wide via C_HousingCatalog | Housing collection is account-wide |
| Zidormi phase handling | Explicit guide steps | Simple, reliable, no fragile API detection |
| UI style | RestedXP-style, freely draggable | Familiar UX for guide addon users |
| Item previews | 3D model via DressUpModel | Matches existing housing addon patterns |
| v1 step types | QUEST + VENDOR, optional DUNGEON proximity | Achievements, professions, reputation come later |
| Data sources | WoWDB (catalog) + Wowhead (coordinates) | Complementary: WoWDB is complete, Wowhead has coords |
| Library stack | Retail standard: LibStub, LibDBIcon, HereBeDragons | Follows conventions from installed retail addons |

---

## 6. Phases
1. **Phase 1 (Current):** Addon scaffold + mock data PoC. Python scraper PoC targeting Vanilla/EK zones.
2. **Phase 2:** Full scraper pipeline across all expansions. Route optimizer.
3. **Phase 3:** Polish UI, RNG Detour system, Blizzard Settings integration.
4. **Phase 4:** Midnight (12.0) launch data + new API verification.
