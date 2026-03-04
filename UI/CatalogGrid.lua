-------------------------------------------------------------------------------
-- HearthAndSeek: CatalogGrid.lua
-- Icon grid display, filtering engine (with dynamic counts), and scrollable grid.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.UI = NS.UI or {}

local CatSizing = nil
local gridButtons = {}
local gridParent = nil

-- Multi-select filter state (empty set = no filter = show all)
local filterState = {
    sources     = {},   -- { ["Vendor"] = true, ... }
    zones       = {},   -- { ["Stormwind City"] = true, ... }
    qualities   = {},   -- { [3] = true, [4] = true, ... }
    professions = {},   -- { ["Tailoring"] = true, ... }
    searchText  = "",
    -- Collection state: all true by default (show everything)
    collected       = true,
    notCollected    = true,
    -- Favorites filter
    onlyFavorites   = false,
}
local filteredItems = {}
local scrollOffset = 0   -- row offset (0 = top)
local totalRows = 0      -- total rows needed for all filtered items
local visibleRows = 5    -- rows visible at once (set from CatSizing.GridRows)
local scrollBarUpdating = false  -- guard against recursive OnValueChanged

-- Caches
local hyperlinkCache = {}
local ownershipCache = {}       -- decorID → { collected=bool }
local ownershipCacheBuilt = false
local achievementCache = nil
local achievementCacheReady = false
local achievementCacheNextID = 1
local ACHIEVEMENT_MAX_ID = 65000
local ACHIEVEMENT_BATCH_SIZE = 5000
local bossEncounterCache = nil

-------------------------------------------------------------------------------
-- Ownership cache (built from C_HousingCatalog runtime data)
-------------------------------------------------------------------------------
local iconCache = {}  -- decorID → runtime iconTexture (lazy backfill for missing static icons)

local function BuildOwnershipCache()
    if ownershipCacheBuilt then return end
    if not C_HousingCatalog or not C_HousingCatalog.GetCatalogEntryInfoByRecordID then return end
    if not NS.CatalogData or not NS.CatalogData.NameIndex then return end
    local entryType = Enum.HousingCatalogEntryType and Enum.HousingCatalogEntryType.Decor or 1

    for _, entry in ipairs(NS.CatalogData.NameIndex) do
        local id = entry[1]
        local ok, info = pcall(C_HousingCatalog.GetCatalogEntryInfoByRecordID, entryType, id, true)
        if ok and info then
            local stored = info.quantity or 0
            local placed = info.numPlaced or 0
            local rdm = info.remainingRedeemable or 0
            local hasItem = (stored + placed + rdm) > 0
            ownershipCache[id] = {
                collected  = hasItem,
            }
        end
    end
    ownershipCacheBuilt = true
end

--- Lazy icon lookup: query the housing catalog API at render time for items
--- whose static data has no iconTexture. Only caches hits so misses are retried
--- (the API may not have textures ready on the first call).
local function GetDecorIcon(decorID, itemID)
    if iconCache[decorID] then
        return iconCache[decorID]
    end
    -- Try housing catalog API first
    if C_HousingCatalog and C_HousingCatalog.GetCatalogEntryInfoByRecordID then
        local entryType = Enum.HousingCatalogEntryType
            and Enum.HousingCatalogEntryType.Decor or 1
        local ok, info = pcall(
            C_HousingCatalog.GetCatalogEntryInfoByRecordID,
            entryType, decorID, true)
        if ok and info and info.iconTexture then
            iconCache[decorID] = info.iconTexture
            return info.iconTexture
        end
    end
    -- Fallback: standard item icon from itemID
    if itemID and GetItemIcon then
        local tex = GetItemIcon(itemID)
        if tex then
            iconCache[decorID] = tex
            return tex
        end
    end
    return nil
end

function NS.UI.RefreshOwnershipCache()
    ownershipCacheBuilt = false
    ownershipCache = {}
    iconCache = {}
    BuildOwnershipCache()
end

-------------------------------------------------------------------------------
-- Event-driven collection refresh
-- Listens for decor collection, quest completion, and achievement events
-- to keep grid checkmarks and detail panel up-to-date in real time.
-------------------------------------------------------------------------------
local refreshFrame = CreateFrame("Frame")
refreshFrame:RegisterEvent("HOUSE_DECOR_ADDED_TO_CHEST")
refreshFrame:RegisterEvent("QUEST_TURNED_IN")
refreshFrame:RegisterEvent("ACHIEVEMENT_EARNED")

local refreshPending = false
local function ScheduleCollectionRefresh()
    if refreshPending then return end -- debounce
    refreshPending = true
    C_Timer.After(0.5, function()
        refreshPending = false
        ownershipCacheBuilt = false
        ownershipCache = {}
        BuildOwnershipCache()
        if NS.UI.CatalogGrid_ApplyFilters then
            NS.UI.CatalogGrid_ApplyFilters()
        end
        if NS.UI.RefreshDetailPanel then
            NS.UI.RefreshDetailPanel()
        end
    end)
end

refreshFrame:SetScript("OnEvent", function()
    if not ownershipCacheBuilt then return end
    ScheduleCollectionRefresh()
end)

-------------------------------------------------------------------------------
-- Resolve item hyperlink from C_HousingCatalog runtime API
-------------------------------------------------------------------------------
local function GetItemHyperlink(decorID)
    if hyperlinkCache[decorID] ~= nil then
        return hyperlinkCache[decorID]
    end
    if not C_HousingCatalog or not C_HousingCatalog.GetCatalogEntryInfoByRecordID then
        hyperlinkCache[decorID] = false
        return false
    end
    local ok, info = pcall(C_HousingCatalog.GetCatalogEntryInfoByRecordID,
        Enum.HousingCatalogEntryType and Enum.HousingCatalogEntryType.Decor or 1,
        decorID, true)
    if ok and info then
        if info.itemLink then
            hyperlinkCache[decorID] = info.itemLink
            return info.itemLink
        end
        if info.itemID and info.itemID > 0 then
            local link = "item:" .. info.itemID
            hyperlinkCache[decorID] = link
            return link
        end
    end
    hyperlinkCache[decorID] = false
    return false
end
NS.UI.GetItemHyperlink = GetItemHyperlink

-------------------------------------------------------------------------------
-- Achievement ID lookup by name (cached, built eagerly in background batches)
-------------------------------------------------------------------------------

-- Process one batch of achievement IDs into the cache.
-- Returns true when the full range has been scanned.
local function BuildAchievementCacheBatch()
    if achievementCacheReady then return true end
    if not GetAchievementInfo then
        achievementCacheReady = true
        return true
    end
    local endID = math.min(achievementCacheNextID + ACHIEVEMENT_BATCH_SIZE - 1,
                           ACHIEVEMENT_MAX_ID)
    for achID = achievementCacheNextID, endID do
        local _, achName = GetAchievementInfo(achID)
        if achName and achName ~= "" then
            achievementCache[achName:lower()] = achID
        end
    end
    achievementCacheNextID = endID + 1
    if achievementCacheNextID > ACHIEVEMENT_MAX_ID then
        achievementCacheReady = true
    end
    return achievementCacheReady
end

-- Kick off background cache build (called from InitCatalogGrid).
-- Processes ACHIEVEMENT_BATCH_SIZE IDs per frame via C_Timer.NewTicker.
local function StartAchievementCacheBuild()
    if achievementCache then return end
    achievementCache = {}
    C_Timer.NewTicker(0, function(ticker)
        if BuildAchievementCacheBatch() then
            ticker:Cancel()
        end
    end)
end

function NS.UI.FindAchievementIDByName(name)
    if not name or name == "" then return nil end
    -- If cache was never started, start and complete it now (safety fallback)
    if not achievementCache then
        achievementCache = {}
    end
    -- If cache is still building, complete it synchronously
    if not achievementCacheReady then
        repeat until BuildAchievementCacheBatch()
    end
    return achievementCache[name:lower()]
end

-------------------------------------------------------------------------------
-- Boss encounter lookup (for Drop items → correct dungeon floor)
-- Builds a name→{encounterID, instanceID} cache from Encounter Journal.
-------------------------------------------------------------------------------
local function FindBossEncounterID(bossName)
    if not bossName or bossName == "" then return nil end
    if not bossEncounterCache then
        bossEncounterCache = {}
        -- Ensure Encounter Journal addon is loaded (required for EJ_* API)
        if C_AddOns and C_AddOns.LoadAddOn then
            pcall(C_AddOns.LoadAddOn, "Blizzard_EncounterJournal")
        end
        if EJ_GetNumTiers and EJ_GetInstanceByIndex and EJ_GetEncounterInfoByIndex
            and EJ_SelectTier and EJ_SelectInstance then
            -- Save current state
            local savedTier = EJ_GetCurrentTier and EJ_GetCurrentTier()
            for tier = 1, EJ_GetNumTiers() do
                EJ_SelectTier(tier)
                for isRaid = 0, 1 do
                    local instIdx = 1
                    while true do
                        local instID = EJ_GetInstanceByIndex(instIdx, isRaid == 1)
                        if not instID or instID == 0 then break end
                        EJ_SelectInstance(instID)
                        local encIdx = 1
                        while true do
                            local eName, _, encID = EJ_GetEncounterInfoByIndex(encIdx)
                            if not eName then break end
                            bossEncounterCache[eName:lower()] = {
                                encounterID = encID,
                                instanceID  = instID,
                            }
                            encIdx = encIdx + 1
                        end
                        instIdx = instIdx + 1
                    end
                end
            end
            -- Restore tier
            if savedTier and EJ_SelectTier then
                pcall(EJ_SelectTier, savedTier)
            end
        end
    end
    return bossEncounterCache[bossName:lower()]
end

--- Find the specific dungeon floor mapID where a boss encounter lives.
--- Falls back to the root zoneMapID if not found.
local function FindBossFloorMap(zoneMapID, encounterID)
    if not zoneMapID or not encounterID then return zoneMapID end
    if not C_EncounterJournal or not C_EncounterJournal.GetEncountersOnMap then
        return zoneMapID
    end
    if not C_Map or not C_Map.GetMapChildrenInfo then return zoneMapID end

    -- Helper: check if encounterID is present on a map
    local function mapHasEncounter(mapID)
        local encs = C_EncounterJournal.GetEncountersOnMap(mapID)
        if encs then
            for _, enc in ipairs(encs) do
                if enc.encounterID == encounterID then
                    return true
                end
            end
        end
        return false
    end

    -- Check root map
    if mapHasEncounter(zoneMapID) then return zoneMapID end

    -- Check child floors
    local children = C_Map.GetMapChildrenInfo(zoneMapID)
    if children then
        for _, child in ipairs(children) do
            if mapHasEncounter(child.mapID) then
                return child.mapID
            end
            -- Check grandchildren (nested floors)
            local grandchildren = C_Map.GetMapChildrenInfo(child.mapID)
            if grandchildren then
                for _, gc in ipairs(grandchildren) do
                    if mapHasEncounter(gc.mapID) then
                        return gc.mapID
                    end
                end
            end
        end
    end

    return zoneMapID
end

-- Get the correct instance mapID for a boss encounter.
-- First checks the hardcoded BossFloorMaps table (from in-game EJ dump),
-- then falls back to Encounter Journal dynamic lookup.
-- Returns: floorMapID (number|nil), encounterID (number|nil)
local function GetBossInstanceMapID(bossName)
    if not bossName or bossName == "" then return nil, nil end

    -- 1) Check baked pipeline data first (most reliable)
    local baked = NS.CatalogData and NS.CatalogData.BossFloorMaps
        and NS.CatalogData.BossFloorMaps[bossName:lower()]
    if baked then
        return baked, nil
    end

    -- 2) Fallback: dynamic EJ lookup
    local bossInfo = FindBossEncounterID(bossName)
    if not bossInfo then return nil, nil end

    if EJ_GetInstanceInfo and EJ_SelectInstance then
        EJ_SelectInstance(bossInfo.instanceID)
        -- dungeonAreaMapID is the 7th return value (instance interior map)
        local _, _, _, _, _, _, dungeonMapID, _, _, mapID10 =
            EJ_GetInstanceInfo(bossInfo.instanceID)
        -- Try the 7th value first, then 10th as fallback
        local instMapID = (dungeonMapID and dungeonMapID > 0) and dungeonMapID
            or (mapID10 and mapID10 > 0) and mapID10 or nil
        if instMapID then
            local floorMapID = FindBossFloorMap(instMapID, bossInfo.encounterID)
            return floorMapID, bossInfo.encounterID
        end
    end

    return nil, bossInfo.encounterID
end

--- Get the dungeon/raid root instance mapID (first floor) for a boss.
--- Unlike GetBossInstanceMapID which returns the boss-specific floor,
--- this returns the instance's base map (entrance level).
local function GetDungeonBaseMapID(bossName)
    if not bossName or bossName == "" then return nil end

    -- Use EJ to find the instance, then get its root map
    local bossInfo = FindBossEncounterID(bossName)
    if not bossInfo then return nil end

    if EJ_GetInstanceInfo and EJ_SelectInstance then
        EJ_SelectInstance(bossInfo.instanceID)
        local _, _, _, _, _, _, dungeonMapID, _, _, mapID10 =
            EJ_GetInstanceInfo(bossInfo.instanceID)
        local instMapID = (dungeonMapID and dungeonMapID > 0) and dungeonMapID
            or (mapID10 and mapID10 > 0) and mapID10 or nil
        return instMapID
    end
    return nil
end

-- Expose boss encounter helpers for CatalogDetail.lua
NS.UI.FindBossEncounterID = FindBossEncounterID
NS.UI.FindBossFloorMap = FindBossFloorMap
NS.UI.GetBossInstanceMapID = GetBossInstanceMapID
NS.UI.GetDungeonBaseMapID = GetDungeonBaseMapID

-------------------------------------------------------------------------------
-- Vendor accessibility check for dual-source items.
-- Pure Vendor items are always accessible. Achievement+Vendor requires the
-- achievement to be completed. Quest+Vendor requires the quest to be done.
-------------------------------------------------------------------------------
local function IsVendorAccessible(item)
    local hasVendor = (item.vendorName and item.vendorName ~= "") or item.factionVendors
    if not hasVendor then return false end
    if item.sourceType == "Vendor" then return true end
    if item.sourceType == "Achievement" and item.achievementName
        and item.achievementName ~= "" then
        local achID = NS.UI.FindAchievementIDByName(item.achievementName)
        if achID and GetAchievementInfo then
            return select(4, GetAchievementInfo(achID)) or false
        end
        return false
    end
    if item.sourceType == "Quest" and item.questID then
        if C_QuestLog and C_QuestLog.IsQuestFlaggedCompleted then
            return C_QuestLog.IsQuestFlaggedCompleted(item.questID)
        end
        return false
    end
    return false
end

-------------------------------------------------------------------------------
-- Standalone 3D Model Viewer (fallback when native preview unavailable)
-------------------------------------------------------------------------------
local modelViewer = nil

local function GetOrCreateModelViewer()
    if modelViewer then return modelViewer end

    local f = CreateFrame("Frame", "HearthAndSeekModelViewer", UIParent, "BackdropTemplate")
    f:SetSize(500, 580)
    f:SetPoint("CENTER", UIParent, "CENTER", 0, 50)
    f:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8X8",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        edgeSize = 12,
        insets   = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    f:SetBackdropColor(0.05, 0.05, 0.07, 0.97)
    f:SetBackdropBorderColor(0.30, 0.30, 0.35, 1)
    f:SetFrameStrata("DIALOG")
    f:SetToplevel(true)
    f:SetClampedToScreen(true)
    f:SetMovable(true)
    f:SetResizable(true)
    if f.SetResizeBounds then
        f:SetResizeBounds(350, 400, 1000, 1000)
    end
    f:EnableMouse(true)

    tinsert(UISpecialFrames, "HearthAndSeekModelViewer")

    -- Title bar
    local titleBar = CreateFrame("Frame", nil, f)
    titleBar:SetHeight(28)
    titleBar:SetPoint("TOPLEFT", 3, -3)
    titleBar:SetPoint("TOPRIGHT", -3, -3)
    titleBar:EnableMouse(true)
    titleBar:RegisterForDrag("LeftButton")
    titleBar:SetScript("OnDragStart", function() f:StartMoving() end)
    titleBar:SetScript("OnDragStop", function() f:StopMovingOrSizing() end)

    local titleBg = titleBar:CreateTexture(nil, "BACKGROUND")
    titleBg:SetAllPoints()
    titleBg:SetColorTexture(0.10, 0.10, 0.12, 1)

    local titleSep = titleBar:CreateTexture(nil, "ARTWORK")
    titleSep:SetHeight(1)
    titleSep:SetPoint("BOTTOMLEFT", titleBar, "BOTTOMLEFT")
    titleSep:SetPoint("BOTTOMRIGHT", titleBar, "BOTTOMRIGHT")
    titleSep:SetColorTexture(0.25, 0.25, 0.28, 1)

    f._titleText = titleBar:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    f._titleText:SetPoint("LEFT", titleBar, "LEFT", 8, 0)
    f._titleText:SetTextColor(1, 0.82, 0, 1)
    f._titleText:SetText("3D Preview")

    local closeBtn = CreateFrame("Button", nil, titleBar, "UIPanelCloseButton")
    closeBtn:SetPoint("TOPRIGHT", f, "TOPRIGHT", -1, -1)
    closeBtn:SetScript("OnClick", function() f:Hide() end)

    -- Info panel at bottom
    local infoPanel = CreateFrame("Frame", nil, f, "BackdropTemplate")
    infoPanel:SetHeight(80)
    infoPanel:SetPoint("BOTTOMLEFT", 3, 3)
    infoPanel:SetPoint("BOTTOMRIGHT", -3, 3)
    infoPanel:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    infoPanel:SetBackdropColor(0.04, 0.04, 0.06, 1)

    local infoSep = infoPanel:CreateTexture(nil, "ARTWORK")
    infoSep:SetHeight(1)
    infoSep:SetPoint("TOPLEFT", infoPanel, "TOPLEFT")
    infoSep:SetPoint("TOPRIGHT", infoPanel, "TOPRIGHT")
    infoSep:SetColorTexture(0.25, 0.25, 0.28, 1)

    f._infoSource = infoPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    f._infoSource:SetPoint("TOPLEFT", infoPanel, "TOPLEFT", 8, -8)
    f._infoSource:SetPoint("RIGHT", infoPanel, "RIGHT", -8, 0)
    f._infoSource:SetJustifyH("LEFT")

    f._infoZone = infoPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    f._infoZone:SetPoint("TOPLEFT", f._infoSource, "BOTTOMLEFT", 0, -2)
    f._infoZone:SetJustifyH("LEFT")

    f._infoStorage = infoPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    f._infoStorage:SetPoint("TOPLEFT", f._infoZone, "BOTTOMLEFT", 0, -2)
    f._infoStorage:SetJustifyH("LEFT")

    f._infoPanel = infoPanel

    -- ModelScene frame (above info panel) with built-in drag/zoom/pan
    local modelScene = CreateFrame("ModelScene", nil, f,
        "PanningModelSceneMixinTemplate")
    modelScene:SetPoint("TOPLEFT", 6, -31)
    modelScene:SetPoint("BOTTOMRIGHT", infoPanel, "TOPRIGHT", -6, 4)

    -- ModelScene control buttons (zoom, rotate, reset)
    local ctrl = CreateFrame("Frame", nil, f, "ModelSceneControlFrameTemplate")
    ctrl:SetPoint("BOTTOM", modelScene, "BOTTOM", 0, 8)
    ctrl:SetModelScene(modelScene)

    -- Drag-to-rotate: left-drag horizontal = yaw, vertical = pitch (full 360°)
    local dragLastX, dragLastY = nil, nil
    modelScene:HookScript("OnMouseDown", function(self, button)
        if button == "LeftButton" then
            local x, y = GetCursorPosition()
            dragLastX, dragLastY = x, y
        end
    end)
    modelScene:HookScript("OnMouseUp", function(self, button)
        if button == "LeftButton" then dragLastX, dragLastY = nil, nil end
    end)
    modelScene:HookScript("OnUpdate", function(self)
        if dragLastX and dragLastY then
            local x, y = GetCursorPosition()
            local dx = (x - dragLastX) * 0.02
            local dy = (y - dragLastY) * 0.02
            dragLastX, dragLastY = x, y
            local actor = self:GetActorByTag("decor")
            if actor then
                actor:SetYaw((actor:GetYaw() or 0) + dx)
                actor:SetPitch((actor:GetPitch() or 0) - dy)
            end
        end
    end)

    -- Resize grip
    local resizeGrip = CreateFrame("Button", nil, f)
    resizeGrip:SetSize(16, 16)
    resizeGrip:SetPoint("BOTTOMRIGHT", -4, 4)
    resizeGrip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
    resizeGrip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
    resizeGrip:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
    resizeGrip:SetScript("OnMouseDown", function()
        f:StartSizing("BOTTOMRIGHT")
    end)
    resizeGrip:SetScript("OnMouseUp", function()
        f:StopMovingOrSizing()
    end)

    f._modelScene = modelScene
    f:Hide()
    modelViewer = f
    return f
end

local function OpenModelViewer(item)
    if not item or not item.asset or item.asset <= 0 then
        if NS.Utils and NS.Utils.PrintMessage then
            NS.Utils.PrintMessage("No 3D model available for this decoration.")
        end
        return
    end

    local viewer = GetOrCreateModelViewer()

    -- Title: quality-colored item name
    local qc = NS.QualityColors[item.quality] or NS.QualityColors[1]
    local colorHex = string.format("%02x%02x%02x",
        math.floor(qc[1] * 255), math.floor(qc[2] * 255), math.floor(qc[3] * 255))
    viewer._titleText:SetText("|cff" .. colorHex .. (item.name or "3D Preview") .. "|r")

    -- Source info
    local srcColor = NS.SourceColors and NS.SourceColors[item.sourceType]
        or { 0.6, 0.6, 0.6, 1 }
    local srcText = item.sourceType or "Unknown"
    if item.sourceDetail and item.sourceDetail ~= "" then
        srcText = srcText .. ": " .. item.sourceDetail
    end
    viewer._infoSource:SetText(srcText)
    viewer._infoSource:SetTextColor(srcColor[1], srcColor[2], srcColor[3], 1)

    -- Zone
    if item.zone and item.zone ~= "" then
        viewer._infoZone:SetText("|cff888888Zone:|r " .. item.zone)
        viewer._infoZone:Show()
    else
        viewer._infoZone:SetText("")
        viewer._infoZone:Hide()
    end

    -- Runtime housing data
    local storageText = ""
    local ownInfo = ownershipCache[item.decorID]
    if ownInfo then
        if ownInfo.collected then
            storageText = "|cff1eff00Owned|r"
        else
            storageText = "|cffff4444Not Collected|r"
        end
    end
    viewer._infoStorage:SetText(storageText)

    -- Load model via ModelScene
    local sceneID = item.uiModelSceneID or 859
    pcall(function()
        viewer._modelScene:TransitionToModelSceneID(
            sceneID,
            CAMERA_TRANSITION_TYPE_IMMEDIATE,
            CAMERA_MODIFICATION_TYPE_DISCARD,
            true)
    end)
    local actor = viewer._modelScene:GetActorByTag("decor")
    if actor then
        actor:SetPreferModelCollisionBounds(true)
        actor:SetModelByFileID(item.asset)
    end
    viewer:Show()
end

local RefreshGridButtons  -- forward declaration (defined below OnLoad)

-------------------------------------------------------------------------------
-- Grid button OnLoad (called from XML template)
-- Atlas bg, TexCoord crop, hover overlay
-------------------------------------------------------------------------------
function HearthAndSeek_CatalogItem_OnLoad(self)
    -- Atlas background (Blizzard housing catalog card)
    local slotBg = self:CreateTexture(nil, "BACKGROUND")
    slotBg:SetAllPoints()
    slotBg:SetAtlas("house-chest-list-Item-default")
    self.SlotBg = slotBg

    -- Hover overlay (atlas with additive blend)
    local hoverBg = self:CreateTexture(nil, "BACKGROUND", nil, 1)
    hoverBg:SetAllPoints()
    hoverBg:SetAtlas("house-chest-list-Item-default")
    hoverBg:SetAlpha(0.75)
    hoverBg:SetBlendMode("ADD")
    hoverBg:Hide()
    self.HoverBg = hoverBg

    -- Icon texture with TexCoord crop (10px inset + tighter crop for clean edges)
    local icon = self:CreateTexture(nil, "ARTWORK")
    icon:SetPoint("TOPLEFT", 10, -10)
    icon:SetPoint("BOTTOMRIGHT", -10, 10)
    icon:SetTexCoord(0.10, 0.90, 0.10, 0.90)
    self.Icon = icon

    -- Collected checkmark
    local coll = self:CreateTexture(nil, "OVERLAY")
    coll:SetSize(20, 20)
    coll:SetPoint("BOTTOMRIGHT", -4, 4)
    coll:SetTexture("Interface\\RaidFrame\\ReadyCheck-Ready")
    coll:Hide()
    self.Collected = coll

    -- Favorite star (display-only, not clickable from grid)
    local favStar = self:CreateTexture(nil, "OVERLAY")
    favStar:SetSize(21, 21)
    favStar:SetPoint("TOPLEFT", 5, -5)
    favStar:SetAtlas("PetJournal-FavoritesIcon")
    favStar:SetVertexColor(1, 0.82, 0, 1)
    favStar:Hide()
    self.FavoriteStar = favStar

    -- Click handlers
    self:RegisterForClicks("LeftButtonUp", "RightButtonUp")

    self:SetScript("OnClick", function(btn, button)
        if not btn.itemData then return end
        local item = btn.itemData

        if button == "RightButton" then
            if IsControlKeyDown() then
                -- CTRL+Right Click: open dungeon map (correct floor) for Drop items
                if item.sourceType == "Drop" and item.sourceDetail
                    and item.sourceDetail ~= "" then
                    -- Use EJ to get the correct instance floor mapID
                    local targetMapID = GetBossInstanceMapID(item.sourceDetail)
                    if not targetMapID and item.zone and item.zone ~= ""
                        and NS.UI.GetZoneMapID then
                        -- Fallback: zone name lookup
                        targetMapID = NS.UI.GetZoneMapID(item.zone)
                    end
                    if targetMapID and NS.UI.ForceOpenWorldMap then
                        NS.UI.ForceOpenWorldMap(targetMapID)
                    end
                end
            else
                -- Right click: open achievement panel for Achievement/Prey items
                if item.achievementName and item.achievementName ~= "" then
                    if not AchievementFrame then
                        if not C_AddOns.IsAddOnLoaded("Blizzard_AchievementUI") then
                            pcall(C_AddOns.LoadAddOn, "Blizzard_AchievementUI")
                        end
                        if not AchievementFrame and ToggleAchievementFrame then
                            ToggleAchievementFrame()
                            if AchievementFrame and AchievementFrame:IsShown() then
                                AchievementFrame:Hide()
                            end
                        end
                    end
                    if NS.UI.FindAchievementIDByName then
                        local achID = NS.UI.FindAchievementIDByName(item.achievementName)
                        if achID then
                            if OpenAchievementFrameToAchievement then
                                OpenAchievementFrameToAchievement(achID)
                            elseif AchievementFrame and AchievementFrame.SelectAchievement then
                                AchievementFrame:SelectAchievement(achID)
                                if not AchievementFrame:IsShown() then AchievementFrame:Show() end
                            end
                        end
                    end
                end
            end
            return
        end

        -- Left click
        if IsControlKeyDown() then
            local link = GetItemHyperlink(item.decorID)
            if link then
                DressUpItemLink(link)
            else
                OpenModelViewer(item)
            end
            return
        end
        if IsShiftKeyDown() then
            NS.UI.CatalogGrid_ToggleFavorite(item.decorID)
            -- Refresh detail panel star if this item is currently shown
            if NS.UI._currentDetailItem
                    and NS.UI._currentDetailItem.decorID == item.decorID
                    and NS.UI.CatalogDetail_ShowItem then
                NS.UI.CatalogDetail_ShowItem(NS.UI._currentDetailItem)
            end
            return
        end
        if NS.UI.CatalogDetail_ShowItem then
            NS.UI.CatalogDetail_ShowItem(item)
            RefreshGridButtons()
        end
    end)

    -- Hover: native WoW tooltip + interaction hints + magnifying glass cursor
    self:SetScript("OnEnter", function(btn)
        if not btn.itemData then return end
        btn.HoverBg:Show()
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(btn, "ANCHOR_RIGHT")
        local link = GetItemHyperlink(btn.itemData.decorID)
        if link then
            GameTooltip:SetHyperlink(link)
        else
            -- Fallback: manual tooltip when no hyperlink available
            local item = btn.itemData
            local qc = NS.QualityColors[item.quality] or NS.QualityColors[1]
            GameTooltip:AddLine(item.name, qc[1], qc[2], qc[3])
            if item.sourceType then
                local detail = item.sourceDetail or ""
                if detail ~= "" then
                    GameTooltip:AddLine(item.sourceType .. ": " .. detail, 0.7, 0.7, 0.7)
                else
                    GameTooltip:AddLine(item.sourceType, 0.7, 0.7, 0.7)
                end
            end
        end

        -- Interaction hint lines
        GameTooltip:AddLine(" ")
        GameTooltip:AddDoubleLine("|cff00ff00CTRL+Left Click|r", "Preview", nil, nil, nil, 0.7, 0.7, 0.7)
        GameTooltip:AddDoubleLine("|cff00ff00SHIFT+Left Click|r",
            NS.UI.CatalogGrid_IsFavorite(btn.itemData.decorID) and "Unfavorite" or "Favorite",
            nil, nil, nil, 0.7, 0.7, 0.7)
        local item = btn.itemData
        if item.achievementName and item.achievementName ~= "" then
            GameTooltip:AddDoubleLine("|cff00ff00Right-Click|r", "Open Achievement", nil, nil, nil, 0.7, 0.7, 0.7)
        end
        if item.sourceType == "Drop" then
            GameTooltip:AddDoubleLine("|cff00ff00CTRL+Right-Click|r", "Open Dungeon Map", nil, nil, nil, 0.7, 0.7, 0.7)
        end
        GameTooltip:Show()
    end)

    self:SetScript("OnLeave", function(btn)
        btn.HoverBg:Hide()
        ResetCursor()
        GameTooltip:Hide()
    end)
end

-------------------------------------------------------------------------------
-- Refresh grid buttons for current page
-------------------------------------------------------------------------------
RefreshGridButtons = function()
    if not CatSizing then return end
    local cols = CatSizing.GridColumns
    local startIdx = scrollOffset * cols + 1

    for i = 1, visibleRows * cols do
        local btn = gridButtons[i]
        if not btn then break end

        local decorID = filteredItems[startIdx + i - 1]

        if decorID then
            local item = NS.CatalogData and NS.CatalogData.Items
                and NS.CatalogData.Items[decorID]
            btn.decorID = decorID
            btn.itemData = item

            if item then
                -- Selected state: active atlas tinted golden for currently shown item
                local selItem = NS.UI._currentDetailItem
                if selItem and selItem.decorID == decorID then
                    btn.SlotBg:SetAtlas("house-chest-list-Item-active")
                    btn.SlotBg:SetDesaturated(true)
                    btn.SlotBg:SetVertexColor(1.0, 0.82, 0.2, 1)
                else
                    btn.SlotBg:SetAtlas("house-chest-list-Item-default")
                    btn.SlotBg:SetDesaturated(false)
                    btn.SlotBg:SetVertexColor(1, 1, 1, 1)
                end

                -- Icon texture (lazy runtime lookup for items missing static icons)
                local icon = item.iconTexture
                if not icon or icon == 0 then
                    icon = GetDecorIcon(decorID, item.itemID)
                end
                if icon then
                    btn.Icon:SetTexture(icon)
                else
                    btn.Icon:SetTexture("Interface\\Icons\\INV_Misc_QuestionMark")
                end

                -- Owned checkmark (from ownership cache)
                local ownInfo = ownershipCache[decorID]
                local owned = ownInfo and ownInfo.collected or false
                btn.Collected:SetShown(owned)

                -- Favorite star
                local favDB = NS.favorites or (NS.db and NS.db.favorites)
                btn.FavoriteStar:SetShown(favDB and favDB[decorID] and true or false)

                btn.Icon:SetDesaturated(false)
                btn.Icon:SetAlpha(1.0)
            else
                btn.Icon:SetTexture("Interface\\Icons\\INV_Misc_QuestionMark")
                btn.Collected:Hide()
                btn.FavoriteStar:Hide()
            end

            btn:Show()
        else
            btn.decorID = nil
            btn.itemData = nil
            btn:Hide()
        end
    end
end

-------------------------------------------------------------------------------
-- Update scroll indicator
-------------------------------------------------------------------------------
local function UpdateScrollIndicator()
    if not gridParent or not gridParent._scrollBar then return end
    local maxScroll = math.max(0, totalRows - visibleRows)
    if maxScroll > 0 then
        scrollBarUpdating = true
        gridParent._scrollBar:SetMinMaxValues(0, maxScroll)
        gridParent._scrollBar:SetValue(scrollOffset)
        scrollBarUpdating = false
        gridParent._scrollBar:Show()
    else
        gridParent._scrollBar:Hide()
    end
end

-------------------------------------------------------------------------------
-- Update count text
-------------------------------------------------------------------------------
local function UpdateCountText()
    if not gridParent or not gridParent._countText then return end

    local totalCount = #filteredItems
    local ownedCount = 0
    for _, decorID in ipairs(filteredItems) do
        local ownInfo = ownershipCache[decorID]
        if ownInfo and ownInfo.collected then
            ownedCount = ownedCount + 1
        end
    end

    local countText = string.format("Showing %d items  |cff888888(%d collected)|r", totalCount, ownedCount)
    gridParent._countText:SetText(countText)
end

-------------------------------------------------------------------------------
-- Apply filters (main filtering function — single pass with dynamic counts)
--
-- Computes filteredItems AND dynamic counts for all sidebar dimensions.
-- For each dimension, the count reflects "items passing all OTHER filters".
-- This lets the sidebar show how many items would show for each option.
-------------------------------------------------------------------------------
function NS.UI.CatalogGrid_ApplyFilters()
    CatSizing = CatSizing or NS.CatalogSizing
    if not NS.CatalogData or not NS.CatalogData.Items then return end

    -- Ensure ownership cache is built
    BuildOwnershipCache()

    -- Read search text
    if NS.UI._catalogSearchBox then
        filterState.searchText = NS.UI._catalogSearchBox:GetText() or ""
    end

    local allItems = NS.CatalogData.NameIndex or {}

    -- Pre-compute which dimension filters are active
    local hasSrcFilter  = next(filterState.sources) ~= nil
    local hasZoneFilter = next(filterState.zones) ~= nil
    local hasQualFilter = next(filterState.qualities) ~= nil
    local hasProfFilter = next(filterState.professions) ~= nil

    -- Collection filter: active only when partially checked
    local collAll  = filterState.collected and filterState.notCollected
    local collNone = not filterState.collected and not filterState.notCollected
    local hasCollFilter = not collAll and not collNone

    -- Favorites filter
    local favoritesDB = NS.favorites or (NS.db and NS.db.favorites) or {}
    local hasFavFilter = filterState.onlyFavorites

    -- Text search
    local query = strtrim(filterState.searchText):lower()
    local hasQuery = query ~= ""

    -- Dynamic count accumulators
    local dynCounts = {
        sources     = {},
        zones       = {},
        qualities   = {},
        professions = {},
        collection  = { collected = 0, notCollected = 0 },
        favorites   = 0,
    }

    -- Single-pass: filter items and compute dynamic counts simultaneously
    filteredItems = {}

    for _, entry in ipairs(allItems) do repeat -- repeat/break/until true = continue
        local id   = entry[1]
        local name = entry[2]   -- lowercase
        local item = NS.CatalogData.Items[id]
        if not item then break end

        -- Text search applies to all dimensions
        if hasQuery and (not name or not name:find(query, 1, true)) then
            break
        end

        -- Determine pass/fail for each dimension
        -- Source: check primary sourceType AND secondary sources (dual-source items).
        -- Items may match multiple source filters (e.g. Quest item with vendor
        -- matches both Quest and Vendor filters, even after quest completion).
        local vendorReady = IsVendorAccessible(item)
        local pSrc
        if not hasSrcFilter then
            pSrc = true
        else
            pSrc = filterState.sources[item.sourceType]
                or (filterState.sources["Vendor"] and vendorReady)
                or (item.achievementName and item.achievementName ~= ""
                    and filterState.sources["Achievement"])
                or (item.vendorUnlockAchievement and item.vendorUnlockAchievement ~= ""
                    and filterState.sources["Achievement"])
                or (item.questID and item.questID > 0 and not item.skipQuestChain
                    and filterState.sources["Quest"])
            pSrc = pSrc and true or false
        end
        local pZone = not hasZoneFilter or (item.zone and filterState.zones[item.zone] ~= nil)
        local pQual = not hasQualFilter or (filterState.qualities[item.quality] ~= nil)
        local pProf = not hasProfFilter or (item.professionName and item.professionName ~= ""
                                            and filterState.professions[item.professionName] ~= nil)

        -- Collection check
        local ownInfo = ownershipCache[id]
        local isCollected = ownInfo and ownInfo.collected or false
        local isNotColl   = not isCollected

        local pColl = true
        if hasCollFilter then
            pColl = false
            if isCollected and filterState.collected then pColl = true end
            if isNotColl and filterState.notCollected then pColl = true end
        end

        -- Favorites check
        local isFav = favoritesDB[id] and true or false
        local pFav = not hasFavFilter or isFav

        -- Item passes ALL filters -> add to filtered list
        if pSrc and pZone and pQual and pProf and pColl and pFav then
            filteredItems[#filteredItems + 1] = id
        end

        -----------------------------------------------------------------------
        -- Dynamic counts: for each dimension, count items passing all OTHER
        -----------------------------------------------------------------------

        -- Source counts (exclude source filter, include secondary sources).
        -- Items count in all applicable source buckets simultaneously.
        if pZone and pQual and pProf and pColl and pFav then
            local st = item.sourceType or "Other"
            dynCounts.sources[st] = (dynCounts.sources[st] or 0) + 1
            -- Secondary sources
            if st ~= "Vendor" and vendorReady then
                dynCounts.sources["Vendor"] = (dynCounts.sources["Vendor"] or 0) + 1
            end
            if st ~= "Achievement" and item.achievementName and item.achievementName ~= "" then
                dynCounts.sources["Achievement"] = (dynCounts.sources["Achievement"] or 0) + 1
            elseif st ~= "Achievement" and item.vendorUnlockAchievement and item.vendorUnlockAchievement ~= "" then
                dynCounts.sources["Achievement"] = (dynCounts.sources["Achievement"] or 0) + 1
            end
            if st ~= "Quest" and item.questID and item.questID > 0 and not item.skipQuestChain then
                dynCounts.sources["Quest"] = (dynCounts.sources["Quest"] or 0) + 1
            end
        end

        -- Zone counts (exclude zone filter)
        if pSrc and pQual and pProf and pColl and pFav then
            local z = item.zone or ""
            if z ~= "" then
                dynCounts.zones[z] = (dynCounts.zones[z] or 0) + 1
            end
        end

        -- Quality counts (exclude quality filter)
        if pSrc and pZone and pProf and pColl and pFav then
            local q = item.quality
            if q then
                dynCounts.qualities[q] = (dynCounts.qualities[q] or 0) + 1
            end
        end

        -- Profession counts (exclude profession filter)
        if pSrc and pZone and pQual and pColl and pFav then
            if item.professionName and item.professionName ~= "" then
                local pn = item.professionName
                dynCounts.professions[pn] = (dynCounts.professions[pn] or 0) + 1
            end
        end

        -- Collection counts (exclude collection filter)
        if pSrc and pZone and pQual and pProf and pFav then
            if isCollected then
                dynCounts.collection.collected = dynCounts.collection.collected + 1
            end
            if isNotColl then
                dynCounts.collection.notCollected = dynCounts.collection.notCollected + 1
            end
        end

        -- Favorites count (exclude favorites filter, include all other filters)
        if pSrc and pZone and pQual and pProf and pColl and isFav then
            dynCounts.favorites = dynCounts.favorites + 1
        end

    until true end

    -- Scroll state
    local cols = CatSizing.GridColumns
    totalRows = math.max(0, math.ceil(#filteredItems / cols))
    local maxScroll = math.max(0, totalRows - visibleRows)
    scrollOffset = math.min(math.max(scrollOffset, 0), maxScroll)

    -- Refresh grid, scroll indicator, count text
    RefreshGridButtons()
    UpdateScrollIndicator()
    UpdateCountText()

    -- Broadcast dynamic counts to sidebar
    if NS.UI.UpdateSidebarCounts then
        NS.UI.UpdateSidebarCounts(dynCounts)
    end
end

-------------------------------------------------------------------------------
-- Multi-select toggle functions (called from sidebar checkboxes)
-------------------------------------------------------------------------------
function NS.UI.CatalogGrid_ToggleSource(sourceType, checked)
    if checked then
        filterState.sources[sourceType] = true
    else
        filterState.sources[sourceType] = nil
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleZone(zone, checked)
    if checked then
        filterState.zones[zone] = true
    else
        filterState.zones[zone] = nil
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleExpansion(expansion, checked)
    local zoneMap = NS.CatalogData and NS.CatalogData.ZoneToExpansionMap or {}
    local byZone = NS.CatalogData and NS.CatalogData.ByZone or {}
    for zone, exp in pairs(zoneMap) do
        if exp == expansion and byZone[zone] then
            if checked then
                filterState.zones[zone] = true
            else
                filterState.zones[zone] = nil
            end
        end
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleQuality(quality, checked)
    if checked then
        filterState.qualities[quality] = true
    else
        filterState.qualities[quality] = nil
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleProfession(profession, checked)
    if checked then
        filterState.professions[profession] = true
    else
        filterState.professions[profession] = nil
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleAllProfessions(checked)
    local profOrder = NS.CatalogData and NS.CatalogData.ProfessionOrder or {}
    local byProf = NS.CatalogData and NS.CatalogData.ByProfession or {}
    for _, profName in ipairs(profOrder) do
        if byProf[profName] then
            if checked then
                filterState.professions[profName] = true
            else
                filterState.professions[profName] = nil
            end
        end
    end
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleCollection(stateKey, checked)
    filterState[stateKey] = checked
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleFavorites(checked)
    filterState.onlyFavorites = checked
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_ToggleFavorite(decorID)
    local favDB = NS.favorites or (NS.db and NS.db.favorites)
    if not favDB then return end
    if favDB[decorID] then
        favDB[decorID] = nil
    else
        favDB[decorID] = true
    end
    NS.UI.CatalogGrid_ApplyFilters()
end

function NS.UI.CatalogGrid_IsFavorite(decorID)
    local favDB = NS.favorites or (NS.db and NS.db.favorites)
    if not favDB then return false end
    return favDB[decorID] and true or false
end

function NS.UI.CatalogGrid_ResetFilters()
    filterState.sources = {}
    filterState.zones = {}
    filterState.qualities = {}
    filterState.professions = {}
    filterState.searchText = ""
    filterState.collected = true
    filterState.notCollected = true
    filterState.onlyFavorites = false
    scrollOffset = 0
    NS.UI.CatalogGrid_ApplyFilters()
end

-------------------------------------------------------------------------------
-- InitCatalogGrid
-------------------------------------------------------------------------------
function NS.UI.InitCatalogGrid(parent)
    CatSizing = NS.CatalogSizing
    gridParent = parent
    visibleRows = CatSizing.GridRows

    -- Start building achievement name→ID cache in background batches
    StartAchievementCacheBuild()

    local cols = CatSizing.GridColumns
    local size = CatSizing.GridItemSize
    local gap  = CatSizing.GridItemSpacing
    local numButtons = visibleRows * cols

    -- Calculate total grid dimensions to center it
    local gridWidth  = cols * size + (cols - 1) * gap

    for i = 1, numButtons do
        local btn = CreateFrame("Button", nil, parent,
                                "HearthAndSeekCatalogItemTemplate")
        btn:SetSize(size, size)
        local col = (i - 1) % cols
        local row = math.floor((i - 1) / cols)
        btn:SetPoint("CENTER", parent, "TOP",
            -gridWidth / 2 + col * (size + gap) + size / 2,
            -(40 + row * (size + gap) + size / 2))
        gridButtons[i] = btn
    end

    -- Count text (centered below grid)
    parent._countText = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    parent._countText:SetPoint("BOTTOM", parent, "BOTTOM", 0, 10)
    parent._countText:SetTextColor(0.55, 0.55, 0.55, 1)

    -- Scroll bar (simple Slider with Blizzard thumb art)
    local gridHeight = visibleRows * size + (visibleRows - 1) * gap
    local scrollBar = CreateFrame("Slider", nil, parent)
    scrollBar:SetSize(6, gridHeight)
    scrollBar:SetPoint("TOPLEFT", parent, "TOP",
        gridWidth / 2 + 6, -40)
    scrollBar:SetOrientation("VERTICAL")
    scrollBar:SetMinMaxValues(0, 0)
    scrollBar:SetValue(0)
    scrollBar:SetValueStep(1)
    scrollBar:SetObeyStepOnDrag(true)

    local trackBg = scrollBar:CreateTexture(nil, "BACKGROUND")
    trackBg:SetAllPoints()
    trackBg:SetColorTexture(0.15, 0.15, 0.15, 0.5)

    scrollBar:SetThumbTexture("Interface\\Buttons\\WHITE8X8")
    local thumb = scrollBar:GetThumbTexture()
    thumb:SetAtlas("minimal-scrollbar-small-thumb-middle")
    thumb:SetSize(6, 40)

    scrollBar:SetScript("OnValueChanged", function(_, value)
        if scrollBarUpdating then return end
        local newOffset = math.floor(value + 0.5)
        if newOffset ~= scrollOffset then
            scrollOffset = newOffset
            RefreshGridButtons()
        end
    end)
    parent._scrollBar = scrollBar

    -- Mouse wheel scrolling on the grid area
    parent:EnableMouseWheel(true)
    parent:SetScript("OnMouseWheel", function(_, delta)
        local maxScroll = math.max(0, totalRows - visibleRows)
        local newOffset = scrollOffset - delta
        newOffset = math.max(0, math.min(newOffset, maxScroll))
        if newOffset ~= scrollOffset then
            scrollOffset = newOffset
            RefreshGridButtons()
            UpdateScrollIndicator()
        end
    end)
end
