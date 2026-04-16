-------------------------------------------------------------------------------
-- HearthAndSeek: CatalogDumper.lua
-- Dev-only dump commands for the data pipeline:
--   /hseek dump catalog  - Scan housing catalog → HearthAndSeekDB.catalogDump
--   /hseek dump bosses   - Scan Encounter Journal → HearthAndSeekDB.bossDump
-- Both require NS.DEV_MODE = true in Constants.lua.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.CatalogDumper = NS.CatalogDumper or {}
local Dumper = NS.CatalogDumper

-------------------------------------------------------------------------------
-- Configuration
-------------------------------------------------------------------------------
local MAX_DECOR_ID         = 40000  -- Upper bound of decorID range to scan
local BATCH_SIZE           = 200    -- IDs to process per tick (catalog dump)
local TICK_INTERVAL        = 0.01   -- Seconds between batches (catalog dump)
local PROGRESS_EVERY       = 1000   -- Print progress every N IDs (catalog dump)
local BOSS_TICK_INTERVAL   = 0.02   -- Seconds between instance batches (boss dump)
local BOSS_PROGRESS_EVERY  = 10     -- Report progress every N instances (boss dump)

-------------------------------------------------------------------------------
-- Helper: safely fetch catalog entry for a decorID (entryType must be 1).
-- Wrapped in pcall because the API can throw on some invalid/edge IDs.
-------------------------------------------------------------------------------
local function TryGetCatalogEntry(decorID)
    local ok, info = pcall(C_HousingCatalog.GetCatalogEntryInfoByRecordID, 1, decorID, true)
    if ok and info and info.name and info.name ~= "" then
        return info
    end
    return nil
end

-------------------------------------------------------------------------------
-- Helper: build set of known decorIDs from baked CatalogData.
-------------------------------------------------------------------------------
local function GetKnownDecorIDs()
    local known = {}
    local items = NS.CatalogData and NS.CatalogData.Items
    if items then
        for id in pairs(items) do
            known[id] = true
        end
    end
    return known
end

-------------------------------------------------------------------------------
-- Helper: extract catalog entry fields into a dump table row.
-------------------------------------------------------------------------------
local function PackCatalogEntry(decorID, info)
    return {
        decorID             = decorID,
        name                = info.name,
        itemID              = info.itemID,
        sourceText          = info.sourceText,
        quality             = info.quality,
        quantity            = info.quantity,
        numPlaced           = info.numPlaced,
        isAllowedIndoors    = info.isAllowedIndoors,
        isAllowedOutdoors   = info.isAllowedOutdoors,
        placementCost       = info.placementCost,
        iconTexture         = info.iconTexture,
        asset               = info.asset,
        uiModelSceneID      = info.uiModelSceneID,
        firstAcquisitionBonus = info.firstAcquisitionBonus,
        categoryIDs         = info.categoryIDs,
        subcategoryIDs      = info.subcategoryIDs,
        size                = info.size,
    }
end

-------------------------------------------------------------------------------
-- DumpCatalog: Kicks off an async scan of all decorIDs 1..MAX_DECOR_ID.
-- Results are stored in HearthAndSeekDB.catalogDump.
-------------------------------------------------------------------------------
function Dumper.DumpCatalog()
    if Dumper._running then
        NS.Utils.PrintMessage("Catalog dump already in progress.")
        return
    end

    Dumper._running = true

    -- Ensure the storage table exists (wipe previous dump)
    HearthAndSeekDB.catalogDump = {}
    local results = HearthAndSeekDB.catalogDump

    local currentID   = 1
    local foundCount  = 0
    local lastReport  = 0  -- Track the last progress-report threshold

    NS.Utils.PrintMessage("Starting catalog dump (IDs 1.." .. MAX_DECOR_ID .. ")...")

    local ticker
    ticker = C_Timer.NewTicker(TICK_INTERVAL, function()
        local batchEnd = math.min(currentID + BATCH_SIZE - 1, MAX_DECOR_ID)

        for decorID = currentID, batchEnd do
            local info = TryGetCatalogEntry(decorID)
            if info then
                foundCount = foundCount + 1
                results[foundCount] = PackCatalogEntry(decorID, info)
            end
        end

        -- Progress reporting
        local progressThreshold = math.floor(batchEnd / PROGRESS_EVERY) * PROGRESS_EVERY
        if progressThreshold > lastReport and batchEnd < MAX_DECOR_ID then
            lastReport = progressThreshold
            NS.Utils.PrintMessage(
                string.format("Dump progress: %d/%d (%d found so far)",
                    progressThreshold, MAX_DECOR_ID, foundCount)
            )
        end

        currentID = batchEnd + 1

        -- Finished?
        if currentID > MAX_DECOR_ID then
            ticker:Cancel()
            Dumper._running = false
            NS.Utils.PrintMessage(
                string.format("Catalog dump complete: %d decorations found. /reload to save.",
                    foundCount)
            )
        end
    end)
end

-------------------------------------------------------------------------------
-- DumpNewItems: Incremental scan — only decorIDs NOT in CatalogData.Items.
-- Much faster than a full dump when only a few items were added.
-- Results are stored in HearthAndSeekDB.catalogDump (replaces previous dump).
-------------------------------------------------------------------------------
function Dumper.DumpNewItems()
    if Dumper._running then
        NS.Utils.PrintMessage("Catalog dump already in progress.")
        return
    end

    local knownIDs = GetKnownDecorIDs()
    local knownCount = 0
    for _ in pairs(knownIDs) do knownCount = knownCount + 1 end

    if knownCount == 0 then
        NS.Utils.PrintMessage("No existing catalog data found. Run a full dump instead: /hs dump catalog")
        return
    end

    Dumper._running = true

    HearthAndSeekDB.catalogDump = {}
    local results = HearthAndSeekDB.catalogDump

    local currentID   = 1
    local foundCount  = 0
    local skippedCount = 0
    local lastReport  = 0

    NS.Utils.PrintMessage(string.format(
        "Starting incremental dump (skipping %d known items, scanning 1..%d)...",
        knownCount, MAX_DECOR_ID))

    local ticker
    ticker = C_Timer.NewTicker(TICK_INTERVAL, function()
        local batchEnd = math.min(currentID + BATCH_SIZE - 1, MAX_DECOR_ID)

        for decorID = currentID, batchEnd do
            if knownIDs[decorID] then
                skippedCount = skippedCount + 1
            else
                local info = TryGetCatalogEntry(decorID)
                if info then
                    foundCount = foundCount + 1
                    results[foundCount] = PackCatalogEntry(decorID, info)
                end
            end
        end

        -- Progress reporting
        local progressThreshold = math.floor(batchEnd / PROGRESS_EVERY) * PROGRESS_EVERY
        if progressThreshold > lastReport and batchEnd < MAX_DECOR_ID then
            lastReport = progressThreshold
            NS.Utils.PrintMessage(
                string.format("Scan progress: %d/%d (%d new found, %d skipped)",
                    progressThreshold, MAX_DECOR_ID, foundCount, skippedCount)
            )
        end

        currentID = batchEnd + 1

        if currentID > MAX_DECOR_ID then
            ticker:Cancel()
            Dumper._running = false
            NS.Utils.PrintMessage(
                string.format("Incremental dump complete: %d new items found (%d known skipped). /reload to save.",
                    foundCount, skippedCount)
            )
        end
    end)
end

-------------------------------------------------------------------------------
-- DumpCategories: Scans housing catalog categories and subcategories.
-- Results are stored in HearthAndSeekDB.categoryDump.
-------------------------------------------------------------------------------
function Dumper.DumpCategories()
    -- Ensure the storage table exists (wipe previous dump)
    HearthAndSeekDB.categoryDump = {
        categories = {},
        subcategories = {},
    }
    local results = HearthAndSeekDB.categoryDump

    local categoryCount = 0
    local subcategoryCount = 0

    -- Scan categories (IDs 1-10)
    for categoryID = 1, 10 do
        local success, info = pcall(C_HousingCatalog.GetCatalogCategoryInfo, categoryID)
        if success and info and info.name and info.name ~= "" then
            categoryCount = categoryCount + 1
            results.categories[categoryID] = {
                name = info.name,
                subcategoryIDs = info.subcategoryIDs or {},
            }
        end
    end

    -- Scan subcategories (IDs 1-60)
    for subcategoryID = 1, 60 do
        local success, info = pcall(C_HousingCatalog.GetCatalogSubcategoryInfo, subcategoryID)
        if success and info and info.name and info.name ~= "" then
            subcategoryCount = subcategoryCount + 1
            results.subcategories[subcategoryID] = {
                name = info.name,
            }
        end
    end

    NS.Utils.PrintMessage(
        string.format("Dumped %d categories, %d subcategories. /reload to save.",
            categoryCount, subcategoryCount)
    )
end

-------------------------------------------------------------------------------
-- DumpBossFloorMaps: Scans the Encounter Journal for every boss encounter
-- and resolves the correct dungeon floor mapID for each.
-- Results are stored in HearthAndSeekDB.bossDump.
-------------------------------------------------------------------------------
function Dumper.DumpBossFloorMaps()
    if Dumper._bossRunning then
        NS.Utils.PrintMessage("Boss dump already in progress.")
        return
    end
    Dumper._bossRunning = true

    -- Load Encounter Journal addon
    if C_AddOns and C_AddOns.LoadAddOn then
        pcall(C_AddOns.LoadAddOn, "Blizzard_EncounterJournal")
    end

    -- Verify required EJ APIs
    if not (EJ_GetNumTiers and EJ_SelectTier and EJ_GetInstanceByIndex
            and EJ_SelectInstance and EJ_GetEncounterInfoByIndex
            and EJ_GetInstanceInfo) then
        NS.Utils.PrintMessage("Encounter Journal API not available.")
        Dumper._bossRunning = false
        return
    end

    -- Save current EJ state for restoration
    local savedTier = EJ_GetCurrentTier and EJ_GetCurrentTier()

    -- Phase 1: Build work queue (synchronous — fast)
    local workQueue = {}
    for tier = 1, EJ_GetNumTiers() do
        EJ_SelectTier(tier)
        for isRaid = 0, 1 do
            local instIdx = 1
            while true do
                local instID = EJ_GetInstanceByIndex(instIdx, isRaid == 1)
                if not instID or instID == 0 then break end
                workQueue[#workQueue + 1] = {
                    tier   = tier,
                    instID = instID,
                    isRaid = (isRaid == 1),
                }
                instIdx = instIdx + 1
            end
        end
    end

    NS.Utils.PrintMessage(string.format("Boss dump: scanning %d instances...", #workQueue))

    -- Wipe previous dump
    HearthAndSeekDB.bossDump = {}
    local results = HearthAndSeekDB.bossDump
    local foundCount = 0
    local queueIdx = 1

    -- Helper: check if encounterID lives on a given map
    local function mapHasEncounter(mapID, encounterID)
        if not mapID or mapID <= 0 then return false end
        if not C_EncounterJournal or not C_EncounterJournal.GetEncountersOnMap then
            return false
        end
        local encs = C_EncounterJournal.GetEncountersOnMap(mapID)
        if encs then
            for _, enc in ipairs(encs) do
                if enc.encounterID == encounterID then return true end
            end
        end
        return false
    end

    -- Helper: resolve floor mapID by searching base map + children + grandchildren
    local function resolveFloorMap(baseMapID, encounterID)
        if not baseMapID or baseMapID <= 0 then return nil end
        if mapHasEncounter(baseMapID, encounterID) then return baseMapID end
        if not C_Map or not C_Map.GetMapChildrenInfo then return nil end
        local children = C_Map.GetMapChildrenInfo(baseMapID)
        if children then
            for _, child in ipairs(children) do
                if mapHasEncounter(child.mapID, encounterID) then
                    return child.mapID
                end
                local grandchildren = C_Map.GetMapChildrenInfo(child.mapID)
                if grandchildren then
                    for _, gc in ipairs(grandchildren) do
                        if mapHasEncounter(gc.mapID, encounterID) then
                            return gc.mapID
                        end
                    end
                end
            end
        end
        -- Brute-force: scan nearby mapIDs (base ± 20) for encounters the
        -- hierarchy walk missed (some floors aren't children of the base map)
        local lo = math.max(1, baseMapID - 20)
        local hi = math.min(3000, baseMapID + 20)
        for probe = lo, hi do
            if probe ~= baseMapID and mapHasEncounter(probe, encounterID) then
                return probe
            end
        end
        return nil
    end

    -- Phase 2: Async processing — one instance per tick
    local ticker
    ticker = C_Timer.NewTicker(BOSS_TICK_INTERVAL, function()
        if queueIdx > #workQueue then
            ticker:Cancel()
            Dumper._bossRunning = false
            if savedTier and EJ_SelectTier then
                pcall(EJ_SelectTier, savedTier)
            end
            NS.Utils.PrintMessage(string.format(
                "Boss dump complete: %d encounters across %d instances. /reload to save.",
                foundCount, #workQueue))
            return
        end

        local work = workQueue[queueIdx]
        EJ_SelectTier(work.tier)
        EJ_SelectInstance(work.instID)

        -- Get instance info: 7th = dungeonAreaMapID, 10th = mapID
        local instName, _, _, _, _, _, dungeonMapID, _, _, mapID10 =
            EJ_GetInstanceInfo(work.instID)

        -- Iterate encounters in this instance
        local encIdx = 1
        while true do
            local encName, _, encID = EJ_GetEncounterInfoByIndex(encIdx)
            if not encName then break end

            -- Try 7th value first, then 10th
            local floorMapID = nil
            local baseMapID = nil
            local baseSource = nil

            if dungeonMapID and dungeonMapID > 0 then
                floorMapID = resolveFloorMap(dungeonMapID, encID)
                if floorMapID then
                    baseMapID = dungeonMapID
                    baseSource = "7th"
                end
            end

            if not floorMapID and mapID10 and mapID10 > 0 then
                floorMapID = resolveFloorMap(mapID10, encID)
                if floorMapID then
                    baseMapID = mapID10
                    baseSource = "10th"
                end
            end

            foundCount = foundCount + 1
            results[foundCount] = {
                bossName     = encName,
                encounterID  = encID,
                instanceID   = work.instID,
                instanceName = instName or "",
                floorMapID   = floorMapID or 0,
                baseMapID    = baseMapID or 0,
                baseMapSource = baseSource or "none",
            }

            encIdx = encIdx + 1
        end

        -- Progress reporting
        if queueIdx % BOSS_PROGRESS_EVERY == 0 then
            NS.Utils.PrintMessage(string.format(
                "Boss dump: %d/%d instances (%d encounters)",
                queueIdx, #workQueue, foundCount))
        end

        queueIdx = queueIdx + 1
    end)
end
