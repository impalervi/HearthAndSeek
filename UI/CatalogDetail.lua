-------------------------------------------------------------------------------
-- HearthAndSeek: CatalogDetail.lua
-- Detail panel with 3D model viewer, acquisition info, and waypoint button.
-- Model supports drag-to-rotate and scroll-to-zoom.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.UI = NS.UI or {}

local detailPanel = nil

-------------------------------------------------------------------------------
-- Map navigation helper.
-- Uses C_Map.OpenWorldMap (added 11.1.5) which routes through a secure
-- execution path — no taint on GameTooltip / QuestMapFrame / MoneyFrame.
-------------------------------------------------------------------------------
local function ForceOpenWorldMap(mapID)
    if InCombatLockdown() or not mapID then return end
    C_Map.OpenWorldMap(mapID)
end

-------------------------------------------------------------------------------
-- Zone name → uiMapID resolver (pipeline-emitted via ZONE_TO_MAPID)
-- All mapIDs are pre-computed in CatalogData.lua — no HereBeDragons needed.
-------------------------------------------------------------------------------
local function GetZoneMapID(zoneName)
    if not zoneName or zoneName == "" then return nil end
    local data = NS.CatalogData and NS.CatalogData.ZoneToMapID
    if not data then return nil end
    return data[zoneName]
end

-- Expose for use by CatalogGrid (CTRL+Right Click → open map)
NS.UI.GetZoneMapID = GetZoneMapID
NS.UI.ForceOpenWorldMap = ForceOpenWorldMap

--- Walk up the map parent chain to find a navigable parent zone.
-- If the given mapID is a micro-zone, dungeon floor, or orphan that doesn't
-- support waypoints, returns the first ancestor that can be opened in the
-- world map (Zone, Micro, or other navigable types — NOT Continent/Dungeon).
-- Returns: fallbackMapID, fallbackZoneName (or nil if none found)
local function GetNavigableParentZone(mapID)
    if not mapID then return nil, nil end
    local info = C_Map.GetMapInfo(mapID)
    if not info or not info.parentMapID or info.parentMapID <= 0 then
        return nil, nil
    end
    -- Walk up from the parent (skip the original zone itself)
    local parentInfo = C_Map.GetMapInfo(info.parentMapID)
    while parentInfo do
        -- Accept Zone (3), Micro (4), and other navigable types (e.g., 5 for
        -- city/orphan zones like Dalaran). Skip Cosmic (0), World (1),
        -- Continent (2), and Dungeon (6) — these are not useful targets.
        if parentInfo.mapType then
            local t = parentInfo.mapType
            if t == Enum.UIMapType.Zone or t == 4 or t == 5 then
                return parentInfo.mapID, parentInfo.name
            end
            -- Stop if we've reached Continent level — no useful parent above
            if t == Enum.UIMapType.Continent
                or t == Enum.UIMapType.World
                or t == Enum.UIMapType.Cosmic then
                break
            end
        end
        if parentInfo.parentMapID and parentInfo.parentMapID > 0 then
            parentInfo = C_Map.GetMapInfo(parentInfo.parentMapID)
        else
            break
        end
    end
    return nil, nil
end

--- Midnight zone mapIDs that are real navigable zones despite being nested
--- under a parent Zone (not directly under a Continent).
--- Emitted from pipeline (TRUSTED_ZONE_IDS in output_catalog_lua.py).
local TRUSTED_ZONE_IDS = NS.CatalogData and NS.CatalogData.TrustedZoneIDs or {}

--- Shadowlands covenant data for covenant-locked vendors.
--- covenantID: 1=Kyrian, 2=Venthyr, 3=Night Fae, 4=Necrolord
local COVENANT_DATA = {
    [1] = { name = "Kyrian",    color = "68ccef" },
    [2] = { name = "Venthyr",   color = "ff4040" },
    [3] = { name = "Night Fae", color = "8b5cf6" },
    [4] = { name = "Necrolord", color = "40bf40" },
}
local ORIBOS_ENCLAVE = { zone = "Oribos", mapID = 1670, x = 39.0, y = 65.6 }

--- Resolve a zone name to a navigable mapID, falling back to a parent zone
--- when the zone is non-navigable (dungeon, micro-map, sub-zone, or orphan).
--- Returns: mapID (or nil), coordsTrusted (boolean)
--- coordsTrusted = false when the zone is an instance/sub-zone, meaning
--- the NPC coordinates are in the zone's own coordinate space and
--- cannot be accurately placed on the parent zone's world map.
local function ResolveNavigableMap(zoneName)
    local mapID = GetZoneMapID(zoneName)
    if not mapID then return nil, false end
    local mi = C_Map.GetMapInfo(mapID)
    if not mi or not mi.mapType then return mapID, true end

    -- Continent: always navigable
    if mi.mapType == Enum.UIMapType.Continent then
        return mapID, true
    end

    -- Zone type: check if it's a real world zone or a sub-zone (class hall, etc.)
    if mi.mapType == Enum.UIMapType.Zone then
        -- Explicitly trusted zones (Midnight Quel'Thalas)
        if TRUSTED_ZONE_IDS[mapID] then
            return mapID, true
        end
        -- Walk up parent chain — if we reach a Continent through Zone
        -- parents, this is a real navigable world zone.  Handles nested
        -- zones like Valdrakken → Thaldraszus → Dragon Isles (2 levels)
        -- and Dazar'alor → Zuldazar → Zandalar.
        local walkID = mi.parentMapID
        for _ = 1, 4 do
            if not walkID or walkID <= 0 then break end
            local parentInfo = C_Map.GetMapInfo(walkID)
            if not parentInfo or not parentInfo.mapType then break end
            if parentInfo.mapType == Enum.UIMapType.Continent then
                return mapID, true
            end
            -- Stop if we leave Zone territory (Dungeon, Micro, etc.)
            if parentInfo.mapType ~= Enum.UIMapType.Zone then break end
            walkID = parentInfo.parentMapID
        end
        -- No Continent found in parent chain — treat as sub-zone
        local parentID = GetNavigableParentZone(mapID)
        if parentID then
            return parentID, false
        end
        -- No parent found — return as-is (best effort)
        return mapID, true
    end

    -- Non-navigable zone (Dungeon, Micro, etc.) — try parent
    local isInstance = (mi.mapType == Enum.UIMapType.Dungeon)
    local parentID = GetNavigableParentZone(mapID)
    if parentID then
        return parentID, not isInstance
    end
    return mapID, not isInstance
end

--- Resolve a zone name to a mapID suitable for OpenWorldMap.
--- For dungeon/instance zones with a known entrance, returns the entrance zone's
--- map so the world map opens to a navigable outdoor region.
--- Sub-zones (class halls, etc.) return their raw mapID so the zone click opens
--- the actual zone map rather than the parent.
local function GetOpenableMapID(zoneName)
    -- Check DungeonEntrances first — these zones are instances whose map IDs
    -- open to non-navigable dungeon floors in the world map UI.
    local entrance = NS.CatalogData and NS.CatalogData.DungeonEntrances
        and NS.CatalogData.DungeonEntrances[zoneName]
    if entrance then
        -- Prefer pre-computed mapID; fall back to zone name lookup
        if entrance.mapID then return entrance.mapID end
        if entrance.zone then return GetZoneMapID(entrance.zone) end
    end
    -- Return raw mapID — sub-zones (class halls) have viewable maps in the
    -- world map UI and should open directly when clicking the zone name.
    return GetZoneMapID(zoneName)
end

-------------------------------------------------------------------------------
-- Continent resolver: walk up the map parent chain to find the continent.
-- Returns the continent-level uiMapID, or nil if unresolvable.
-------------------------------------------------------------------------------
-- Quel'Thalas zones have a broken map hierarchy (historically connected to
-- Outland infrastructure) and don't resolve to Eastern Kingdoms via parent walk.
-- We use BOTH mapID overrides (for known zones) and name-based continent
-- redirects (to catch ALL sub-zones under pseudo-continents like Quel'Thalas).
local CONTINENT_OVERRIDES = {
    -- Quel'Thalas outdoor zones (old TBC versions → Eastern Kingdoms)
    [94]  = { 13, "Eastern Kingdoms" },  -- Eversong Woods
    [95]  = { 13, "Eastern Kingdoms" },  -- Ghostlands
    [110] = { 13, "Eastern Kingdoms" },  -- Silvermoon City
    [122] = { 13, "Eastern Kingdoms" },  -- Isle of Quel'Danas
    -- Mechagon — separate travel region (Blizzard navigation only works within)
    [1462] = { 1462, "Mechagon" },       -- Mechagon Island
    -- Midnight zones — treated as separate continents for navigation
    [2405] = { 2405, "The Voidstorm" },  -- The Voidstorm
    [2413] = { 2413, "Harandar" },       -- Harandar
}

-- Name-based redirects for pseudo-continents. When the parent walk reaches a
-- Continent-type map whose name matches one of these, redirect to the real
-- continent. This catches ALL sub-zones (dungeons, raids, micro-zones) without
-- needing to enumerate every individual mapID.
local CONTINENT_NAME_REDIRECTS = {
    ["quel'thalas"]    = { 13, "Eastern Kingdoms" },
    ["mechagon"]       = { 1462, "Mechagon" },
    ["the voidstorm"]  = { 2405, "The Voidstorm" },
    ["harandar"]       = { 2413, "Harandar" },
}

local function GetContinentMapID(mapID)
    if not mapID then return nil, nil end
    local info = C_Map.GetMapInfo(mapID)
    while info do
        local override = CONTINENT_OVERRIDES[info.mapID]
        if override then return override[1], override[2] end
        if info.mapType == Enum.UIMapType.Continent then
            -- Check name-based redirect for pseudo-continents
            local nameKey = info.name and info.name:lower() or ""
            local redirect = CONTINENT_NAME_REDIRECTS[nameKey]
            if redirect then return redirect[1], redirect[2] end
            return info.mapID, info.name
        end
        if info.parentMapID and info.parentMapID > 0 then
            info = C_Map.GetMapInfo(info.parentMapID)
        else
            break
        end
    end
    return nil, nil
end

local function GetCrossContinentInfo(destMapID)
    if not destMapID then return false, nil end
    local playerMapID = C_Map.GetBestMapForUnit("player")
    if not playerMapID then return false, nil end
    local destContinent, destName = GetContinentMapID(destMapID)
    local playerContinent = GetContinentMapID(playerMapID)
    if not destContinent or not playerContinent then return false, nil end
    if destContinent ~= playerContinent then
        return true, destName or "another continent"
    end
    return false, nil
end

-- (Cross-continent popup is a custom frame, created in InitCatalogDetail)

-------------------------------------------------------------------------------
-- Floating copy popup: appears near cursor with URL pre-selected
-------------------------------------------------------------------------------
local copyPopup

local function GetOrCreateCopyPopup()
    if copyPopup then return copyPopup end

    local f = CreateFrame("Frame", nil, UIParent, "BackdropTemplate")
    f:SetSize(340, 58)
    f:SetFrameStrata("TOOLTIP")
    f:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    f:SetBackdropColor(0.08, 0.08, 0.10, 0.95)
    f:SetBackdropBorderColor(0.4, 0.4, 0.45, 1)
    f:EnableMouse(true)
    f:Hide()

    local hint = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    hint:SetPoint("TOP", f, "TOP", 0, -8)
    hint:SetText("|cff55aaeeCTRL+C to copy|r  |cff666666ESC to close|r")

    local eb = CreateFrame("EditBox", nil, f, "InputBoxTemplate")
    eb:SetHeight(20)
    eb:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 10, 8)
    eb:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -10, 8)
    eb:SetFontObject(GameFontHighlightSmall)
    eb:SetAutoFocus(false)
    eb:SetJustifyH("CENTER")
    eb:SetScript("OnEscapePressed", function(self)
        self:ClearFocus()
        f:Hide()
    end)
    eb:SetScript("OnEditFocusGained", function(self) self:HighlightText() end)
    eb:SetScript("OnEditFocusLost", function()
        C_Timer.After(0.1, function()
            if not eb:HasFocus() then f:Hide() end
        end)
    end)
    eb:SetScript("OnTextChanged", function(self)
        if self._url and self:GetText() ~= self._url then
            self:SetText(self._url)
            self:HighlightText()
        end
    end)

    f._editBox = eb
    f:SetScript("OnShow", function(self)
        if self._timer then self._timer:Cancel() end
        self._timer = C_Timer.NewTimer(10, function() self:Hide() end)
    end)
    f:SetScript("OnHide", function(self)
        if self._timer then self._timer:Cancel() end
        self._editBox:ClearFocus()
    end)

    copyPopup = f
    return f
end

local function ShowCopyableURL(url)
    local popup = GetOrCreateCopyPopup()
    popup._editBox._url = url
    popup._editBox:SetText(url)

    -- Position near cursor, clamped to screen
    local x, y = GetCursorPosition()
    local scale = UIParent:GetEffectiveScale()
    local cx, cy = x / scale, y / scale
    local pw = popup:GetWidth()
    local screenW = UIParent:GetWidth()
    cx = math.max(pw / 2, math.min(cx, screenW - pw / 2))
    popup:ClearAllPoints()
    popup:SetPoint("BOTTOM", UIParent, "BOTTOMLEFT", cx, cy + 15)

    popup:Show()
    popup._editBox:SetFocus()
    popup._editBox:HighlightText()
end

-------------------------------------------------------------------------------
-- Text truncation helper for fixed-width UI elements
-------------------------------------------------------------------------------
local function TruncateButtonText(button, text)
    if not button or not text then return end
    button:SetText(text)
    local fs = button:GetFontString()
    if not fs then return end
    local maxW = button:GetWidth() - 16 -- padding inside button
    if maxW <= 0 or fs:GetStringWidth() <= maxW then return end
    -- Binary search for the longest prefix that fits with ellipsis
    local len = #text
    local lo, hi = 1, len
    while lo < hi do
        local mid = math.ceil((lo + hi) / 2)
        button:SetText(text:sub(1, mid) .. "...")
        if fs:GetStringWidth() <= maxW then
            lo = mid
        else
            hi = mid - 1
        end
    end
    button:SetText(text:sub(1, lo) .. "...")
end

-------------------------------------------------------------------------------
-- Section header: gray label + amber-gold 2px delimiter (reusable)
-------------------------------------------------------------------------------
local function CreateDetailSectionHeader(parent, labelText)
    local container = CreateFrame("Frame", nil, parent)
    container:SetHeight(22)
    local label = container:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("TOPLEFT", 0, 0)
    label:SetText(labelText)
    label:SetTextColor(0.50, 0.50, 0.50, 1)
    local line = container:CreateTexture(nil, "ARTWORK")
    line:SetHeight(2)
    line:SetPoint("TOPLEFT", label, "BOTTOMLEFT", 0, -3)
    line:SetPoint("RIGHT", container, "RIGHT", 0, 0)
    line:SetColorTexture(0.72, 0.58, 0.25, 0.5)
    container._label = label
    container._line = line
    return container
end

-------------------------------------------------------------------------------
-- Info row: N evenly-spaced cells (uses OnSizeChanged for proportional widths)
-------------------------------------------------------------------------------
local function CreateInfoRow(parent, cellCount)
    local row = CreateFrame("Frame", nil, parent)
    row:SetHeight(18)
    row._cells = {}
    for i = 1, cellCount do
        local cell = CreateFrame("Frame", nil, row)
        cell:SetHeight(18)
        local text = cell:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        text:SetPoint("CENTER")
        text:SetJustifyH("CENTER")
        cell._text = text
        row._cells[i] = cell
    end
    row:SetScript("OnSizeChanged", function(self, width)
        if width <= 0 then return end
        local cellW = width / cellCount
        for i, cell in ipairs(self._cells) do
            cell:ClearAllPoints()
            cell:SetPoint("TOPLEFT", self, "TOPLEFT", (i - 1) * cellW, 0)
            cell:SetSize(cellW, 18)
        end
    end)
    return row
end

-------------------------------------------------------------------------------
-- Dismiss all popups (cross-continent, zidormi, copy URL, success)
-------------------------------------------------------------------------------
local function DismissAllPopups()
    if not detailPanel then return end
    if detailPanel._crossPopup then
        detailPanel._crossPopup:Hide()
        detailPanel._crossPopup._pendingMapID = nil
        detailPanel._crossPopup._pendingSuccess = nil
    end
    if detailPanel._zidormiPopup then
        detailPanel._zidormiPopup:Hide()
        detailPanel._zidormiPopup._pendingMapID = nil
        detailPanel._zidormiPopup._pendingSuccess = nil
    end
    if detailPanel._successPopup then
        detailPanel._successPopup:Hide()
        if detailPanel._successPopup._timer then
            detailPanel._successPopup._timer:Cancel()
            detailPanel._successPopup._timer = nil
        end
    end
    if copyPopup and copyPopup:IsShown() then copyPopup:Hide() end
    if detailPanel._detailsFlyout then
        if detailPanel._detailsFlyout._hideTimer then
            detailPanel._detailsFlyout._hideTimer:Cancel()
            detailPanel._detailsFlyout._hideTimer = nil
        end
        detailPanel._detailsFlyout:Hide()
        if detailPanel._detailsLabel then
            detailPanel._detailsLabel:SetTextColor(0.4, 0.7, 1.0, 0.7)
        end
    end
end

-------------------------------------------------------------------------------
-- Show green "Map ping created" success popup
-------------------------------------------------------------------------------
local function ShowWaypointSuccess(zoneName, x, y)
    if not detailPanel or not detailPanel._successPopup then return end
    DismissAllPopups()
    local popup = detailPanel._successPopup
    local continent = NS.CatalogData and NS.CatalogData.ZoneToContinentMap
        and NS.CatalogData.ZoneToContinentMap[zoneName] or ""
    local coordStr = ""
    if x and y then
        coordStr = string.format(" (%.1f, %.1f)",
            math.floor(x * 10 + 0.5) / 10,
            math.floor(y * 10 + 0.5) / 10)
    end
    local locationStr = zoneName or ""
    if continent ~= "" then
        locationStr = locationStr .. ", " .. continent
    end
    popup._text:SetText("|cff40e040Map ping created|r\n" .. locationStr .. coordStr)
    popup:Show()
    if popup._timer then popup._timer:Cancel() end
    popup._timer = C_Timer.NewTimer(4, function() popup:Hide() end)
end

-------------------------------------------------------------------------------
-- Open achievement panel by name (reusable helper)
-------------------------------------------------------------------------------
local function OpenAchievementByName(achievementName)
    if not achievementName or achievementName == "" then return end
    if InCombatLockdown() then return end
    if not AchievementFrame then
        if C_AddOns then pcall(C_AddOns.LoadAddOn, "Blizzard_AchievementUI") end
        if not AchievementFrame and ToggleAchievementFrame then
            ToggleAchievementFrame()
            if AchievementFrame and AchievementFrame:IsShown() then
                AchievementFrame:Hide()
            end
        end
    end
    local achID = NS.UI.FindAchievementIDByName
        and NS.UI.FindAchievementIDByName(achievementName)
    if achID then
        if OpenAchievementFrameToAchievement then
            OpenAchievementFrameToAchievement(achID)
        elseif AchievementFrame and AchievementFrame.SelectAchievement then
            ShowUIPanel(AchievementFrame)
            AchievementFrame:SelectAchievement(achID)
        end
    elseif NS.Utils and NS.Utils.PrintMessage then
        NS.Utils.PrintMessage("Could not find achievement: " .. achievementName)
    end
end

-------------------------------------------------------------------------------
-- Quest Chain Helpers
-------------------------------------------------------------------------------

--- Walk prereqs from a decor quest back to the chain root, return ordered list.
-- Each entry: { questID = int, name = str, isDecorQuest = bool }
-- Returns nil only if questID is nil. Always returns a chain (even single-entry)
-- so that quest completion status is shown for ALL items with a questID.
local function BuildQuestChainList(questID, fallbackName)
    if not questID then return nil end

    if NS.QuestChains and NS.QuestChains[questID] then
        -- Walk backwards from the decor quest following first prereq
        local chain = {}
        local current = questID
        local seen = {}
        while current and NS.QuestChains[current] and not seen[current] do
            seen[current] = true
            local entry = NS.QuestChains[current]
            table.insert(chain, 1, {
                questID = current,
                name = entry.name or fallbackName
                    or C_QuestLog.GetTitleForQuestID(current)
                    or ("Quest " .. current),
                isDecorQuest = entry.isDecorQuest or false,
            })
            local prereqs = entry.prereqs
            if prereqs and #prereqs > 0 then
                current = prereqs[1]  -- follow first prereq (primary chain)
            else
                current = nil
            end
        end
        return chain  -- includes single-entry chains
    end

    -- Not in QuestChains: synthetic single-entry chain
    local questName = fallbackName
        or C_QuestLog.GetTitleForQuestID(questID)
        or ("Quest " .. questID)
    return {{ questID = questID, name = questName, isDecorQuest = false }}
end

--- Find the first incomplete quest in a chain (for waypointing).
-- Returns questID or nil.
local function GetFirstIncompleteQuestID(questID)
    local chain = BuildQuestChainList(questID)
    if not chain then return nil end
    for _, entry in ipairs(chain) do
        if not C_QuestLog.IsQuestFlaggedCompleted(entry.questID) then
            return entry.questID
        end
    end
    return nil  -- all complete
end

-- Expose for external use (CatalogGrid waypoint logic)
NS.UI.GetFirstIncompleteQuestID = GetFirstIncompleteQuestID

-------------------------------------------------------------------------------
-- Build acquisition instructions based on source type
-------------------------------------------------------------------------------
local function GetAcquisitionText(item)
    if not item then return nil end
    local st = item.sourceType

    if st == "Vendor" then
        -- Show achievement prerequisite line for vendor items requiring achievements
        if item.vendorUnlockAchievement and item.vendorUnlockAchievement ~= "" then
            local achCompleted = false
            local achID = NS.UI.FindAchievementIDByName
                and NS.UI.FindAchievementIDByName(item.vendorUnlockAchievement)
            if achID and GetAchievementInfo then
                achCompleted = select(4, GetAchievementInfo(achID)) or false
            end
            if achCompleted then
                return "|cffe6cc80Earn achievement:|r\n"
                    .. "|TInterface\\RaidFrame\\ReadyCheck-Ready:14:14|t "
                    .. "|cff1eff00" .. item.vendorUnlockAchievement .. "|r"
            else
                return "|cffe6cc80Earn achievement:|r\n"
                    .. "|cffffcc00" .. item.vendorUnlockAchievement .. "|r"
            end
        end
        -- Regular vendor: handled by _vendorLine + _vendorZonePart
        return nil

    elseif st == "Quest" then
        local text = "|cffffd200Complete quest:|r |cffff8800" .. (item.sourceDetail or "Unknown") .. "|r"
        if item.questID then
            text = text .. " |cff888888(ID: " .. item.questID .. ")|r"
        end
        -- Zone displayed in separate _zoneLine (clickable)
        return text

    elseif st == "Achievement" then
        local achCompleted = false
        local achID = NS.UI.FindAchievementIDByName
            and NS.UI.FindAchievementIDByName(item.sourceDetail or "")
        if achID and GetAchievementInfo then
            achCompleted = select(4, GetAchievementInfo(achID)) or false
        end
        if achCompleted then
            return "|cffe6cc80Earn achievement:|r\n"
                .. "|TInterface\\RaidFrame\\ReadyCheck-Ready:14:14|t "
                .. "|cff1eff00" .. (item.sourceDetail or "Unknown") .. "|r"
        else
            return "|cffe6cc80Earn achievement:|r\n"
                .. "|cffffcc00" .. (item.sourceDetail or "Unknown") .. "|r"
        end

    elseif st == "Prey" then
        -- Vendor info is shown via the interactive vendor display line below
        return "|cffd93030Prey challenge:|r\n" .. (item.sourceDetail or "Unknown")

    elseif st == "Profession" then
        local text = "|cff996633Crafted via:|r " .. (item.sourceDetail or "a profession")
        if item.professionName and item.professionName ~= "" then
            local profIcon = NS.ProfessionIcons and NS.ProfessionIcons[item.professionName]
            if profIcon then
                text = "|T" .. profIcon .. ":14:14|t " .. text
            end
        end
        return text

    elseif st == "Drop" then
        -- Multi-mob items show header only; individual mobs displayed in pool lines
        local dropMobs = NS.CatalogData and NS.CatalogData.DropMobs
            and NS.CatalogData.DropMobs[item.decorID]
        if dropMobs and dropMobs.mobs and #dropMobs.mobs > 1 then
            return "|cffcc66ccDrops from:|r"
        end
        return "|cffcc66ccDrops from:|r " .. (item.sourceDetail or "Unknown")

    elseif st == "Treasure" then
        -- Treasure info shown via interactive _treasureLine + _treasureZonePart below
        return nil
    end

    return nil
end

-------------------------------------------------------------------------------
-- Get runtime housing data from C_HousingCatalog
-------------------------------------------------------------------------------
local function GetHousingInfo(decorID)
    if not C_HousingCatalog or not C_HousingCatalog.GetCatalogEntryInfoByRecordID then
        return nil
    end
    local ok, info = pcall(C_HousingCatalog.GetCatalogEntryInfoByRecordID,
        Enum.HousingCatalogEntryType and Enum.HousingCatalogEntryType.Decor or 1,
        decorID, true)
    if ok and info then
        return info
    end
    return nil
end

-------------------------------------------------------------------------------
-- Faction helpers (needed by both InitCatalogDetail click handlers and ShowItem)
-------------------------------------------------------------------------------
local function GetPlayerFaction()
    return NS.DebugFaction
        or (UnitFactionGroup and UnitFactionGroup("player"))
        or nil
end

-- Neighborhood zones and their owning faction
local NEIGHBORHOOD_FACTIONS = {
    ["Founder's Point"]  = "Alliance",
    ["Razorwind Shores"] = "Horde",
}

-- Faction-colored zone names for dual-vendor items
local FACTION_ZONE_COLORS = {
    -- Alliance cities
    ["Stormwind City"] = "3399FF",
    ["Ironforge"]      = "3399FF",
    ["Darnassus"]      = "3399FF",
    ["The Exodar"]     = "3399FF",
    ["Stormshield"]    = "3399FF",
    ["Boralus"]        = "3399FF",
    ["Deeprun Tram"]   = "3399FF",
    -- Horde cities
    ["Orgrimmar"]      = "FF3333",
    ["Thunder Bluff"]  = "FF3333",
    ["Undercity"]      = "FF3333",
    -- Silvermoon City is neutral since Midnight (player hub)
    ["Warspear"]       = "FF3333",
    ["Dazar'alor"]     = "FF3333",
    -- Neighborhoods
    ["Founder's Point"]  = "3399FF",
    ["Razorwind Shores"] = "FF3333",
}

local FACTION_ICONS = {
    Alliance = "|TInterface\\FriendsFrame\\PlusManz-Alliance:14:14:0:0|t",
    Horde    = "|TInterface\\FriendsFrame\\PlusManz-Horde:14:14:0:0|t",
}

-- Portal room redirects for zones that can't accept waypoint pins directly
local PORTAL_REDIRECTS = {
    Dalaran = {
        Alliance = { zone = "Stormwind City", x = 49.0, y = 87.0 },
        Horde    = { zone = "Orgrimmar",      x = 56.0, y = 88.0 },
    },
    ["Founder's Point"] = {
        Alliance = { zone = "Stormwind City", x = 49.5, y = 86.7 },
        Horde    = { zone = "Orgrimmar",      x = 57.1, y = 89.8 },
    },
    ["Razorwind Shores"] = {
        Alliance = { zone = "Stormwind City", x = 49.5, y = 86.7 },
        Horde    = { zone = "Orgrimmar",      x = 57.1, y = 89.8 },
    },
}

local function GetPortalRedirect(zoneName)
    local entry = PORTAL_REDIRECTS[zoneName]
    if not entry then return nil end
    local faction = GetPlayerFaction()
    return faction and entry[faction] or nil
end

--- Status text for the navigation hint below the Navigate button.
--- Neighborhood zones get "Via <capital> portal room or\nHouse Plot Teleport
--- from Housing Dashboard".  Dalaran gets the plain portal room text.
local function GetPortalStatusText(zoneName, redirect)
    if not redirect then return nil end
    if NEIGHBORHOOD_FACTIONS[zoneName] then
        return "Via " .. redirect.zone .. " portal room or\n"
            .. "House Plot Teleport from Housing Dashboard"
    end
    return "Via " .. redirect.zone .. " portal room"
end

-------------------------------------------------------------------------------
-- UpdateFavoriteStar: sync the detail panel star icon with favorite state
-------------------------------------------------------------------------------
local function UpdateFavoriteStar(panel)
    if not panel or not panel._favBtn then return end
    local icon = panel._favBtn._icon
    local item = panel._currentItem
    if not item then
        icon:SetDesaturated(true)
        icon:SetAlpha(0.3)
        icon:SetVertexColor(1, 1, 1, 1)
        return
    end
    local isFav = NS.UI.CatalogGrid_IsFavorite
        and NS.UI.CatalogGrid_IsFavorite(item.decorID)
    if isFav then
        icon:SetDesaturated(false)
        icon:SetAlpha(1.0)
        icon:SetVertexColor(1, 0.82, 0, 1)
    else
        icon:SetDesaturated(true)
        icon:SetAlpha(0.3)
        icon:SetVertexColor(1, 1, 1, 1)
    end
end

-------------------------------------------------------------------------------
-- Big model viewer (Alt+Click on 3D viewer)
-------------------------------------------------------------------------------
local bigViewerFrame = nil

function NS.UI.ShowBigModelViewer(item)
    if not item or not item.asset or item.asset <= 0 then return end

    -- Lazy-create the viewer frame
    if not bigViewerFrame then
        local f = CreateFrame("Frame", "HearthAndSeekBigViewer", UIParent, "BackdropTemplate")
        f:SetFrameStrata("FULLSCREEN")
        f:SetSize(1024, 768)
        f:SetPoint("CENTER")
        f:SetBackdrop({
            bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            tile = true, tileSize = 16, edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 },
        })
        f:SetBackdropColor(0.05, 0.05, 0.05, 0.99)
        f:SetBackdropBorderColor(0.90, 0.76, 0.25, 0.9)
        f:EnableMouse(true)
        f:SetMovable(true)
        f:RegisterForDrag("LeftButton")
        f:SetScript("OnDragStart", f.StartMoving)
        f:SetScript("OnDragStop", f.StopMovingOrSizing)
        f:SetClampedToScreen(true)
        f:Hide()

        -- Register for Escape to close
        tinsert(UISpecialFrames, "HearthAndSeekBigViewer")

        -- Dim backdrop behind the frame
        local dimmer = CreateFrame("Frame", nil, f)
        dimmer:SetFrameStrata("FULLSCREEN")
        dimmer:SetFrameLevel(f:GetFrameLevel() - 1)
        dimmer:SetAllPoints(UIParent)
        dimmer:EnableMouse(true)
        local dimTex = dimmer:CreateTexture(nil, "BACKGROUND")
        dimTex:SetAllPoints()
        dimTex:SetColorTexture(0.02, 0.005, 0.04, 0.65)
        dimmer:SetScript("OnMouseDown", function() f:Hide() end)
        f._dimmer = dimmer

        -- Atlas background (same as detail panel)
        local bgTex = f:CreateTexture(nil, "BACKGROUND", nil, 1)
        bgTex:SetPoint("TOPLEFT", 6, -30)
        bgTex:SetPoint("BOTTOMRIGHT", -6, 6)
        bgTex:SetAtlas("catalog-list-preview-bg")
        bgTex:SetVertexColor(1, 1, 1, 1)

        -- Title bar
        local title = f:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
        title:SetPoint("TOP", f, "TOP", 0, -8)
        title:SetTextColor(0.90, 0.76, 0.25, 1)
        f._title = title

        -- Close button
        local closeBtn = CreateFrame("Button", nil, f, "UIPanelCloseButton")
        closeBtn:SetPoint("TOPRIGHT", f, "TOPRIGHT", -2, -2)

        -- ModelScene
        local bigScene = CreateFrame("ModelScene", nil, f,
            "PanningModelSceneMixinTemplate")
        bigScene:SetPoint("TOPLEFT", 12, -30)
        bigScene:SetPoint("BOTTOMRIGHT", -12, 12)

        -- Drag-to-rotate
        local bDragX, bDragY = nil, nil
        bigScene:HookScript("OnMouseDown", function(self, button)
            if button == "LeftButton" then
                bDragX, bDragY = GetCursorPosition()
            end
        end)
        bigScene:HookScript("OnMouseUp", function(self, button)
            if button == "LeftButton" then
                bDragX, bDragY = nil, nil
            end
        end)
        bigScene:HookScript("OnUpdate", function(self)
            if bDragX and bDragY then
                local x, y = GetCursorPosition()
                local dx = (x - bDragX) * 0.015
                local dy = (y - bDragY) * 0.015
                bDragX, bDragY = x, y
                local actor = self:GetActorByTag("decor")
                if actor then
                    actor:SetYaw((actor:GetYaw() or 0) + dx)
                    actor:SetPitch((actor:GetPitch() or 0) - dy)
                end
            end
        end)

        -- ModelScene control buttons
        local bigControls = CreateFrame("Frame", nil, f,
            "ModelSceneControlFrameTemplate")
        bigControls:SetPoint("BOTTOM", f, "BOTTOM", 0, 14)
        bigControls:SetModelScene(bigScene)

        -- Decorative corbels (bottom corners only)
        local corbelBL = f:CreateTexture(nil, "OVERLAY")
        corbelBL:SetAtlas("catalog-corbel-bottom-left")
        corbelBL:SetSize(80, 60)
        corbelBL:SetPoint("BOTTOMLEFT", f, "BOTTOMLEFT", 0, 0)

        local corbelBR = f:CreateTexture(nil, "OVERLAY")
        corbelBR:SetAtlas("catalog-corbel-bottom-right")
        corbelBR:SetSize(80, 60)
        corbelBR:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", 0, 0)

        f._modelScene = bigScene

        bigViewerFrame = f
    end

    -- Set up the model
    local f = bigViewerFrame
    f._title:SetText(item.name or "")
    local sceneID = item.uiModelSceneID or 859
    local ok = pcall(function()
        f._modelScene:TransitionToModelSceneID(
            sceneID,
            CAMERA_TRANSITION_TYPE_IMMEDIATE,
            CAMERA_MODIFICATION_TYPE_DISCARD,
            true)
    end)
    if ok then
        local actor = f._modelScene:GetActorByTag("decor")
        if actor then
            actor:SetPreferModelCollisionBounds(true)
            actor:SetModelByFileID(item.asset)
        end
    end
    f:Show()
end

-------------------------------------------------------------------------------
-- InitCatalogDetail
-------------------------------------------------------------------------------
function NS.UI.InitCatalogDetail(parent)
    detailPanel = parent
    local CatSizing = NS.CatalogSizing

    -- Model container with Blizzard catalog atlas background
    local modelBg = CreateFrame("Frame", nil, parent)
    modelBg:SetHeight(CatSizing.ModelViewerHeight)
    modelBg:SetPoint("TOPLEFT", parent, "TOPLEFT", 4, -4)
    modelBg:SetPoint("TOPRIGHT", parent, "TOPRIGHT", -4, -4)
    local bgTex = modelBg:CreateTexture(nil, "BACKGROUND")
    bgTex:SetAllPoints()
    bgTex:SetAtlas("catalog-list-preview-bg")

    -- ModelScene with built-in drag-rotate, scroll-zoom, right-drag-pan
    local modelScene = CreateFrame("ModelScene", nil, modelBg,
        "PanningModelSceneMixinTemplate")
    modelScene:SetPoint("TOPLEFT", 6, -6)
    modelScene:SetPoint("BOTTOMRIGHT", -6, 6)

    -- Drag-to-rotate: left-drag horizontal = yaw, vertical = pitch (full 360°)
    local dragLastX, dragLastY = nil, nil
    local ctrlClickStart = nil
    local altClickStart = nil
    modelScene:HookScript("OnMouseDown", function(self, button)
        if button == "LeftButton" then
            local x, y = GetCursorPosition()
            dragLastX, dragLastY = x, y
            ctrlClickStart = GetTime()
            if IsAltKeyDown() and not IsControlKeyDown() then
                altClickStart = GetTime()
            else
                altClickStart = nil
            end
        end
    end)
    modelScene:HookScript("OnMouseUp", function(self, button)
        if button == "LeftButton" then
            -- ALT+Click: open big model viewer (short click, not drag)
            if altClickStart and IsAltKeyDown() then
                local elapsed = GetTime() - altClickStart
                if elapsed < 0.3 then
                    NS.UI.ShowBigModelViewer(parent._currentItem)
                end
            -- CTRL+Click: open larger preview (short click, not drag)
            elseif IsControlKeyDown() then
                local elapsed = GetTime() - (ctrlClickStart or 0)
                if elapsed < 0.3 then
                    local item = parent._currentItem
                    if item then
                        local link = NS.UI.GetItemHyperlink
                            and NS.UI.GetItemHyperlink(item.decorID)
                        if link then DressUpItemLink(link) end
                    end
                end
            end
            dragLastX, dragLastY = nil, nil
            ctrlClickStart = nil
            altClickStart = nil
        end
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

    -- ModelScene control buttons (zoom, rotate, reset)
    local controls = CreateFrame("Frame", nil, modelBg,
        "ModelSceneControlFrameTemplate")
    controls:SetPoint("BOTTOM", modelBg, "BOTTOM", 0, 8)
    controls:SetModelScene(modelScene)

    -- Decorative corbels
    local corbelL = modelBg:CreateTexture(nil, "OVERLAY")
    corbelL:SetAtlas("catalog-corbel-bottom-left")
    corbelL:SetSize(66, 50)
    corbelL:SetPoint("BOTTOMLEFT", modelBg, "BOTTOMLEFT", 0, 0)

    local corbelR = modelBg:CreateTexture(nil, "OVERLAY")
    corbelR:SetAtlas("catalog-corbel-bottom-right")
    corbelR:SetSize(66, 50)
    corbelR:SetPoint("BOTTOMRIGHT", modelBg, "BOTTOMRIGHT", 0, 0)

    parent._modelScene = modelScene
    parent._modelBg = modelBg
    parent._modelControls = controls

    -- Watermark icon (shown before any item is selected)
    -- Child of parent (not modelBg) so it stays visible when modelBg is hidden.
    -- HIGH strata required to render above ModelScene 3D content.
    local wmFrame = CreateFrame("Frame", nil, parent)
    wmFrame:SetPoint("TOPLEFT", modelBg, "TOPLEFT", 0, 0)
    wmFrame:SetPoint("BOTTOMRIGHT", modelBg, "BOTTOMRIGHT", 0, 0)
    wmFrame:SetFrameStrata("HIGH")
    local watermark = wmFrame:CreateTexture(nil, "ARTWORK")
    watermark:SetSize(180, 180)
    watermark:SetPoint("CENTER", wmFrame, "CENTER", 0, 0)
    watermark:SetTexture(
        "Interface\\AddOns\\HearthAndSeek\\Media\\Icons\\HearthAndSeek")
    watermark:SetAlpha(0.30)
    parent._watermark = wmFrame

    -- No model placeholder
    parent._noModelText = modelBg:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._noModelText:SetPoint("CENTER")
    parent._noModelText:SetText("No 3D Preview")
    parent._noModelText:SetTextColor(0.4, 0.4, 0.4, 1)
    parent._noModelText:Hide()

    -- Alt+Click hint (centered between corbels at bottom of model frame)
    local ctrlHint = modelBg:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    ctrlHint:SetPoint("BOTTOM", modelBg, "BOTTOM", 0, 6)
    ctrlHint:SetText("|cff8a7340ALT+Left Click for full screen preview|r")
    parent._ctrlHint = ctrlHint

    ---------------------------------------------------------------------------
    -- NAVIGATION section: fixed bottom (buttons, warnings, always visible)
    ---------------------------------------------------------------------------
    local bottomSection = CreateFrame("Frame", nil, parent)
    bottomSection:SetPoint("BOTTOMLEFT", parent, "BOTTOMLEFT", 0, 0)
    bottomSection:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", 0, 0)
    bottomSection:SetHeight(100) -- recalculated in ShowItem
    parent._bottomSection = bottomSection

    -- NAVIGATION section header
    local navHeader = CreateDetailSectionHeader(bottomSection, "NAVIGATION")
    navHeader:SetPoint("TOPLEFT", bottomSection, "TOPLEFT", 4, -2)
    navHeader:SetPoint("TOPRIGHT", bottomSection, "TOPRIGHT", -4, -2)
    parent._navHeader = navHeader

    -- Zidormi timeline warning (shown when zone requires Zidormi)
    parent._zidormiWarning = bottomSection:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._zidormiWarning:SetPoint("TOPLEFT", navHeader, "BOTTOMLEFT", 0, -4)
    parent._zidormiWarning:SetPoint("RIGHT", bottomSection, "RIGHT", -4, 0)
    parent._zidormiWarning:SetJustifyH("CENTER")
    parent._zidormiWarning:SetWordWrap(true)
    parent._zidormiWarning:Hide()

    -- Achievement button (at very bottom of bottomSection)
    parent._achieveBtn = CreateFrame("Button", nil, bottomSection, "UIPanelButtonTemplate")
    parent._achieveBtn:SetSize(CatSizing.DetailPanelWidth - 16, 26)
    parent._achieveBtn:SetPoint("BOTTOMLEFT", bottomSection, "BOTTOMLEFT", 4, 8)
    parent._achieveBtn:SetPoint("BOTTOMRIGHT", bottomSection, "BOTTOMRIGHT", -4, 8)
    parent._achieveBtn:SetText("Open Achievement")
    parent._achieveBtn:SetScript("OnClick", function()
        DismissAllPopups()
        local item = parent._currentItem
        if not item then return end
        local achName = item.achievementName
        if not achName or achName == "" then
            achName = item.vendorUnlockAchievement
        end
        if not achName or achName == "" then return end
        OpenAchievementByName(achName)
    end)
    parent._achieveBtn:Hide()

    -- Zidormi button (above achievement button, shown for Zidormi zones)
    parent._zidormiBtn = CreateFrame("Button", nil, bottomSection, "UIPanelButtonTemplate")
    parent._zidormiBtn:SetSize(CatSizing.DetailPanelWidth - 16, 26)
    parent._zidormiBtn:SetPoint("BOTTOMLEFT", parent._achieveBtn, "TOPLEFT", 0, 4)
    parent._zidormiBtn:SetPoint("BOTTOMRIGHT", parent._achieveBtn, "TOPRIGHT", 0, 4)
    parent._zidormiBtn:SetText("Visit Zidormi (Timeline)")
    parent._zidormiBtn:SetScript("OnEnter", function(self)
        if not self._zidormiInfo then return end
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:AddLine("Timeline Zone", 1, 0.82, 0)
        GameTooltip:AddLine("This decoration is in a zone with multiple timelines. You may need to talk to Zidormi to switch to the correct time period.", 0.8, 0.8, 0.8, true)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeClick|r to set waypoint to Zidormi", 0.6, 0.6, 0.6)
        GameTooltip:Show()
    end)
    parent._zidormiBtn:SetScript("OnLeave", function() GameTooltip:Hide() end)
    parent._zidormiBtn:SetScript("OnClick", function()
        DismissAllPopups()
        local zInfo = parent._zidormiBtn._zidormiInfo
        if not zInfo then return end
        local mapID = NS.UI.GetZoneMapID and NS.UI.GetZoneMapID(zInfo.npcZone)
        if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
            NS.Navigation.SetWaypoint(mapID, zInfo.x, zInfo.y,
                "Zidormi (" .. zInfo.npcZone .. ")")
        end

        -- Show timeline popup
        local item = parent._currentItem
        local zoneName = item and item.zone or zInfo.npcZone
        local timelineHint = "the correct timeline"
        -- For known zones, give specific timeline hints
        if zoneName == "Eversong Woods" or zoneName == "Silvermoon City"
                or zoneName == "Ghostlands" or zoneName == "Isle of Quel'Danas" then
            if item and item.expansion == "Midnight" then
                timelineHint = "the |cff9955CCMidnight|r (current) timeline"
            else
                timelineHint = "the |cff1EFF00Burning Crusade|r (past) timeline"
            end
        elseif zoneName == "Darkshore" then
            timelineHint = "the |cff668FD6Battle for Azeroth|r (current) timeline"
        elseif zoneName == "Vale of Eternal Blossoms" then
            timelineHint = "the |cff00FF96Mists of Pandaria|r (past) timeline"
        end

        if parent._zidormiPopup then
            parent._zidormiPopup._text:SetText(
                "This item requires " .. timelineHint .. ".\n"
                .. "Talk to |cffffcc00Zidormi|r in " .. zInfo.npcZone
                .. " to switch.")
            parent._zidormiPopup._pendingMapID = mapID
            parent._zidormiPopup._pendingSuccess = {
                zone = zInfo.npcZone, x = zInfo.x, y = zInfo.y,
            }
            parent._zidormiPopup:Show()
        end
    end)
    parent._zidormiBtn:Hide()

    -- Waypoint button (above zidormi button)
    parent._waypointBtn = CreateFrame("Button", nil, bottomSection, "UIPanelButtonTemplate")
    parent._waypointBtn:SetSize(CatSizing.DetailPanelWidth - 16, 28)
    parent._waypointBtn:SetPoint("BOTTOMLEFT", parent._zidormiBtn, "TOPLEFT", 0, 4)
    parent._waypointBtn:SetPoint("BOTTOMRIGHT", parent._zidormiBtn, "TOPRIGHT", 0, 4)
    parent._waypointBtn:SetText("Set Waypoint")
    parent._waypointBtn:SetScript("OnClick", function()
        DismissAllPopups()
        local item = parent._currentItem
        if not item then return end

        -- Dungeon entrance navigation (Drop items with entrance data)
        local entranceData = parent._waypointBtn._entranceData
        if entranceData then
            -- Use explicit mapID when available (Midnight zones); fall back to resolution
            local mapID = entranceData.mapID
            local coordsTrusted = true
            if not mapID then
                mapID, coordsTrusted = ResolveNavigableMap(entranceData.zone)
            end
            if mapID and coordsTrusted and NS.Navigation and NS.Navigation.SetWaypoint then
                local navLabel = (item.zone or "Dungeon")
                    .. " (" .. (entranceData.zone or "") .. ")"
                NS.Navigation.SetWaypoint(mapID, entranceData.x, entranceData.y, navLabel)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage(
                        "Waypoint set: " .. (item.zone or "Dungeon")
                        .. " entrance in " .. entranceData.zone
                        .. string.format(" (%.1f, %.1f)", entranceData.x, entranceData.y))
                end
            end

            -- Open outdoor map or show cross-continent popup
            if mapID then
                local isCross, continentName = GetCrossContinentInfo(mapID)
                if isCross and parent._crossPopup then
                    local cExp = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                    local cHex = cExp and NS.ExpansionColors and NS.ExpansionColors[cExp] or "00CCFF"
                    parent._crossPopup._text:SetText(
                        "Different travel region!\n"
                        .. "Navigate to |cff" .. cHex .. continentName .. "|r.\n"
                        .. "The map ping navigation system activates once you arrive.")
                    parent._crossPopup._pendingMapID = mapID
                    parent._crossPopup._pendingSuccess = {
                        zone = entranceData.zone, x = entranceData.x, y = entranceData.y,
                    }
                    parent._crossPopup:Show()
                else
                    ForceOpenWorldMap(mapID)
                    ShowWaypointSuccess(entranceData.zone, entranceData.x, entranceData.y)
                end
            end
            return
        end

        -- Hub navigation (multi-mob outdoor rares with a gathering point)
        local hubData = parent._waypointBtn._hubData
        if hubData then
            local mapID = GetZoneMapID(hubData.zone)
            if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
                NS.Navigation.SetWaypoint(mapID, hubData.x, hubData.y,
                    (hubData.label or item.zone) .. " (" .. hubData.zone .. ")")
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage(
                        "Waypoint set: " .. (hubData.label or item.zone)
                        .. string.format(" (%.1f, %.1f)", hubData.x, hubData.y))
                end
            end
            if mapID then
                local isCross, continentName = GetCrossContinentInfo(mapID)
                if isCross and parent._crossPopup then
                    local cExp = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                    local cHex = cExp and NS.ExpansionColors and NS.ExpansionColors[cExp] or "00CCFF"
                    parent._crossPopup._text:SetText(
                        "Different travel region!\n"
                        .. "Navigate to |cff" .. cHex .. continentName .. "|r.\n"
                        .. "The map ping navigation system activates once you arrive.")
                    parent._crossPopup._pendingMapID = mapID
                    parent._crossPopup._pendingSuccess = {
                        zone = hubData.zone, x = hubData.x, y = hubData.y,
                    }
                    parent._crossPopup:Show()
                else
                    ForceOpenWorldMap(mapID)
                    ShowWaypointSuccess(hubData.zone, hubData.x, hubData.y)
                end
            end
            return
        end

        -- Additional treasure source navigation (Treasure is not primary source)
        local tCoords = parent._waypointBtn._treasureCoords
        if tCoords then
            local mapID = tCoords.mapID or GetZoneMapID(tCoords.zone)
            if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
                local navLabel = (tCoords.zone or "Treasure")
                NS.Navigation.SetWaypoint(mapID, tCoords.x, tCoords.y, navLabel)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage(
                        "Waypoint set: Treasure in " .. (tCoords.zone or "")
                        .. string.format(" (%.1f, %.1f)", tCoords.x, tCoords.y))
                end
            end
            if mapID then
                local isCross, continentName = GetCrossContinentInfo(mapID)
                if isCross and parent._crossPopup then
                    local cExp = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                    local cHex = cExp and NS.ExpansionColors and NS.ExpansionColors[cExp] or "00CCFF"
                    parent._crossPopup._text:SetText(
                        "Different travel region!\n"
                        .. "Navigate to |cff" .. cHex .. continentName .. "|r.\n"
                        .. "The map ping navigation system activates once you arrive.")
                    parent._crossPopup._pendingMapID = mapID
                    parent._crossPopup._pendingSuccess = {
                        zone = tCoords.zone, x = tCoords.x, y = tCoords.y,
                    }
                    parent._crossPopup:Show()
                else
                    ForceOpenWorldMap(mapID)
                    ShowWaypointSuccess(tCoords.zone, tCoords.x, tCoords.y)
                end
            end
            return
        end

        local isQuestVendor = item.sourceType == "Quest"
            and item.vendorName and item.vendorName ~= ""
        local chainComplete = false
        local firstIncomplete = nil

        -- For Quest items with chain data, determine chain status
        if item.sourceType == "Quest" and item.questID then
            firstIncomplete = GetFirstIncompleteQuestID(item.questID)
            if not firstIncomplete then
                chainComplete = true
            end
        end

        -- Determine navigation target: quest giver or item/vendor coords
        local navX, navY, navZone, navLabel, navMsg

        if firstIncomplete and not chainComplete then
            -- Try quest-giver coordinates for the next incomplete quest
            local giverEntry = NS.QuestChains and NS.QuestChains[firstIncomplete]
            if giverEntry and giverEntry.giverX and giverEntry.giverY and giverEntry.giverZone then
                navX = giverEntry.giverX
                navY = giverEntry.giverY
                navZone = giverEntry.giverZone
                navLabel = (giverEntry.giverName or giverEntry.name)
                    .. " (" .. giverEntry.giverZone .. ")"
                navMsg = "Waypoint set: " .. (giverEntry.giverName or "quest giver")
                    .. " for \"" .. (giverEntry.name or "") .. "\""
                    .. " in " .. giverEntry.giverZone

                if NS.Utils and NS.Utils.PrintMessage and firstIncomplete ~= item.questID then
                    NS.Utils.PrintMessage(
                        "Next quest in chain: " .. (giverEntry.name or item.sourceDetail or ("Quest " .. firstIncomplete)))
                end
            end
        end

        -- Fall back to item/vendor coordinates
        if not navX and item.npcX and item.npcY then
            navX = item.npcX
            navY = item.npcY
            navZone = item.zone

            if (isQuestVendor and chainComplete)
                or item.sourceType == "Achievement"
                or item.sourceType == "Prey" then
                navLabel = (item.vendorName or item.name) .. " (" .. (item.zone or "") .. ")"
            else
                navLabel = item.name .. " (" .. (item.zone or "") .. ")"
            end

            if isQuestVendor and chainComplete then
                navMsg = "Waypoint set: Visit " .. item.vendorName
            elseif item.sourceType == "Vendor" then
                local vendorLabel = (item.vendorName and item.vendorName ~= "")
                    and item.vendorName or item.sourceDetail
                navMsg = "Waypoint set: " .. item.name .. " — Visit " .. (vendorLabel or "")
            elseif (item.sourceType == "Achievement" or item.sourceType == "Prey")
                and item.vendorName then
                navMsg = "Waypoint set: Visit " .. item.vendorName
            elseif item.sourceType == "Quest" and item.sourceDetail then
                navMsg = "Waypoint set: " .. item.name .. " — Complete quest: " .. item.sourceDetail
            else
                navMsg = "Waypoint set: " .. item.name
            end

            if item.zone then navMsg = navMsg .. " in " .. item.zone end
        end

        -- Neighborhood items without vendor coords: use the portal redirect
        -- coords directly (capital portal room).  Only for neighborhoods —
        -- other zones may genuinely have no coords.
        if not navZone and NEIGHBORHOOD_FACTIONS[item.zone] then
            local redirect = GetPortalRedirect(item.zone)
            if redirect then
                navX = redirect.x
                navY = redirect.y
                navZone = redirect.zone
                local vendorLabel = (item.vendorName and item.vendorName ~= "")
                    and item.vendorName or item.sourceDetail or item.name
                navLabel = vendorLabel
                    .. " (via " .. redirect.zone .. " portal room)"
                navMsg = "Waypoint set: Portal room in " .. redirect.zone
            end
        end
        if navZone then
            -- For neighborhood zones: only redirect to portal if player is NOT
            -- already inside a neighborhood. If player is in the neighborhood,
            -- navigate directly to vendor coords.
            local skipRedirect = false
            if NEIGHBORHOOD_FACTIONS[navZone] then
                local playerMapID = C_Map and C_Map.GetBestMapForUnit
                    and C_Map.GetBestMapForUnit("player")
                if playerMapID == 2352 or playerMapID == 2351 then
                    skipRedirect = true
                end
            end
            if not skipRedirect then
                local redirect = GetPortalRedirect(navZone)
                if redirect then
                    navX = redirect.x
                    navY = redirect.y
                    navZone = redirect.zone
                    local vendorLabel = (item.vendorName and item.vendorName ~= "")
                        and item.vendorName or item.sourceDetail or item.name
                    navLabel = vendorLabel
                        .. " (via " .. redirect.zone .. " portal room)"
                    navMsg = "Waypoint set: Portal room in " .. redirect.zone
                end
            end
        end

        -- Set waypoint if we have coordinates
        if navX and navY and navZone then
            local mapID, coordsTrusted = ResolveNavigableMap(navZone)
            if mapID then
                if coordsTrusted and NS.Navigation and NS.Navigation.SetWaypoint then
                    -- Coords can be pinned accurately on the resolved map
                    NS.Navigation.SetWaypoint(mapID, navX, navY, navLabel)
                    if NS.Utils and NS.Utils.PrintMessage then
                        navMsg = navMsg .. string.format(
                            " (%.1f, %.1f)",
                            math.floor(navX * 10 + 0.5) / 10,
                            math.floor(navY * 10 + 0.5) / 10)
                        NS.Utils.PrintMessage(navMsg)
                    end
                elseif NS.Utils and NS.Utils.PrintMessage then
                    -- Instance zone: coords are in the instance's own space
                    NS.Utils.PrintMessage(
                        (navLabel or "Vendor") .. " is inside an instance accessible from this zone.")
                end

                -- Check cross-continent: show popup instead of opening map
                local isCross, continentName = GetCrossContinentInfo(mapID)
                if isCross and parent._crossPopup then
                    local cExp = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                    local cHex = cExp and NS.ExpansionColors and NS.ExpansionColors[cExp] or "00CCFF"
                    parent._crossPopup._text:SetText(
                        "Different travel region!\n"
                        .. "Navigate to |cff" .. cHex .. continentName .. "|r.\n"
                        .. "The map ping navigation system activates once you arrive.")
                    parent._crossPopup._pendingMapID = mapID
                    parent._crossPopup._pendingSuccess = {
                        zone = navZone, x = navX, y = navY,
                    }
                    parent._crossPopup:Show()
                else
                    -- Same continent: open map to waypoint zone
                    ForceOpenWorldMap(mapID)
                    ShowWaypointSuccess(navZone, navX, navY)
                end
            elseif NS.Utils and NS.Utils.PrintMessage then
                NS.Utils.PrintMessage("Could not resolve zone: " .. (navZone or "unknown"))
            end
        end

        -- Fallback: "Open Map" mode (zone only, no coords)
        if not navX and not navY then
            local btn = parent._waypointBtn
            if btn._openMapID then
                local targetMapID = btn._openMapID
                -- For Drop items: resolve to correct boss floor if possible
                if btn._openMapDrop then
                    local bossInfo = NS.UI.FindBossEncounterID
                        and NS.UI.FindBossEncounterID(btn._openMapDrop)
                    if bossInfo then
                        targetMapID = NS.UI.FindBossFloorMap(targetMapID, bossInfo.encounterID)
                    end
                end
                ForceOpenWorldMap(targetMapID)
            end
        end
    end)
    parent._waypointBtn:Disable()

    -- Open Map button (above waypoint button — only shown for Drop items with entrance data)
    parent._openMapBtn = CreateFrame("Button", nil, bottomSection, "UIPanelButtonTemplate")
    parent._openMapBtn:SetSize(CatSizing.DetailPanelWidth - 16, 28)
    parent._openMapBtn:SetPoint("BOTTOMLEFT", parent._waypointBtn, "TOPLEFT", 0, 4)
    parent._openMapBtn:SetPoint("BOTTOMRIGHT", parent._waypointBtn, "TOPRIGHT", 0, 4)
    parent._openMapBtn:SetText("Open Map")
    parent._openMapBtn:SetScript("OnClick", function()
        DismissAllPopups()
        local btn = parent._openMapBtn
        -- Use EJ-based instance mapID resolution (correct dungeon floor)
        local targetMapID = nil
        if btn._bossName and NS.UI.GetBossInstanceMapID then
            targetMapID = NS.UI.GetBossInstanceMapID(btn._bossName)
        end
        -- Fallback to stored zone-based mapID
        if not targetMapID then
            targetMapID = btn._dungeonMapID
        end
        if targetMapID then
            ForceOpenWorldMap(targetMapID)
            if NS.Utils and NS.Utils.PrintMessage then
                NS.Utils.PrintMessage("Opening map: " .. (btn._dungeonName or "dungeon"))
            end
        end
    end)
    parent._openMapBtn:Hide()

    -- Alternate Navigate button (Treasure+Vendor dual navigation)
    parent._altNavBtn = CreateFrame("Button", nil, bottomSection, "UIPanelButtonTemplate")
    parent._altNavBtn:SetSize(CatSizing.DetailPanelWidth - 16, 28)
    parent._altNavBtn:SetText("Navigate")
    parent._altNavBtn:SetScript("OnClick", function()
        DismissAllPopups()
        local navData = parent._altNavBtn._navData
        if not navData then return end
        local mapID = navData.mapID or GetZoneMapID(navData.zone)
        if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
            NS.Navigation.SetWaypoint(mapID, navData.x, navData.y,
                (navData.label or navData.zone or ""))
            if NS.Utils and NS.Utils.PrintMessage then
                NS.Utils.PrintMessage(
                    "Waypoint set: " .. (navData.label or "")
                    .. " in " .. (navData.zone or "")
                    .. string.format(" (%.1f, %.1f)", navData.x, navData.y))
            end
        end
        if mapID then
            local isCross, continentName = GetCrossContinentInfo(mapID)
            if isCross and parent._crossPopup then
                local cExp = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                local cHex = cExp and NS.ExpansionColors and NS.ExpansionColors[cExp] or "00CCFF"
                parent._crossPopup._text:SetText(
                    "Different travel region!\n"
                    .. "Navigate to |cff" .. cHex .. continentName .. "|r.\n"
                    .. "The map ping navigation system activates once you arrive.")
                parent._crossPopup._pendingMapID = mapID
                parent._crossPopup._pendingSuccess = {
                    zone = navData.zone, x = navData.x, y = navData.y,
                }
                parent._crossPopup:Show()
            else
                ForceOpenWorldMap(mapID)
                ShowWaypointSuccess(navData.zone, navData.x, navData.y)
            end
        end
    end)
    parent._altNavBtn:Hide()

    -- Waypoint status text (above open-map button or waypoint button)
    parent._waypointStatus = bottomSection:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._waypointStatus:SetPoint("BOTTOMLEFT", parent._openMapBtn, "TOPLEFT", 0, 4)
    parent._waypointStatus:SetPoint("BOTTOMRIGHT", parent._openMapBtn, "TOPRIGHT", 0, 4)
    parent._waypointStatus:SetTextColor(0.5, 0.5, 0.5, 1)
    parent._waypointStatus:SetWordWrap(true)
    parent._waypointStatus:Hide()

    -- Tooltip overlay for waypoint status (cross-continent hint)
    local statusHit = CreateFrame("Frame", nil, bottomSection)
    statusHit:SetAllPoints(parent._waypointStatus)
    statusHit:EnableMouse(true)
    statusHit:SetScript("OnEnter", function(self)
        if not self._tooltip then return end
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:AddLine("Cross-Region Destination", 1, 0.82, 0)
        GameTooltip:AddLine(self._tooltip, 0.8, 0.8, 0.8, true)
        if self._portalCoords then
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint to portal room")
        end
        GameTooltip:Show()
    end)
    statusHit:SetScript("OnLeave", function() GameTooltip:Hide() end)
    statusHit:SetScript("OnMouseUp", function(self, button)
        if button == "RightButton" and IsControlKeyDown() and self._portalCoords then
            local pc = self._portalCoords
            local mapID = GetZoneMapID(pc.zone)
            if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
                NS.Navigation.SetWaypoint(mapID, pc.x, pc.y, pc.zone .. " Portal Room")
                ForceOpenWorldMap(mapID)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage("Waypoint set: " .. pc.zone .. " portal room"
                        .. string.format(" (%.1f, %.1f)", pc.x, pc.y))
                end
            end
        end
    end)
    statusHit:Hide()
    parent._waypointStatusHit = statusHit

    -- Covenant requirement warning (Shadowlands covenant-locked vendors)
    parent._covenantStatus = bottomSection:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._covenantStatus:SetTextColor(0.5, 0.5, 0.5, 1)
    parent._covenantStatus:SetWordWrap(true)
    parent._covenantStatus:Hide()

    local covHit = CreateFrame("Frame", nil, bottomSection)
    covHit:SetAllPoints(parent._covenantStatus)
    covHit:EnableMouse(true)
    covHit:SetScript("OnEnter", function(self)
        if not self._tooltip then return end
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:AddLine("Covenant Requirement", 1, 0.82, 0)
        GameTooltip:AddLine(self._tooltip, 0.8, 0.8, 0.8, true)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint to Oribos Enclave")
        GameTooltip:Show()
    end)
    covHit:SetScript("OnLeave", function() GameTooltip:Hide() end)
    covHit:SetScript("OnMouseUp", function(self, button)
        if button == "RightButton" and IsControlKeyDown() and self._enclave then
            local enc = self._enclave
            local mapID = GetZoneMapID(enc.zone)
            if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
                NS.Navigation.SetWaypoint(mapID, enc.x, enc.y, "Oribos Enclave")
                ForceOpenWorldMap(mapID)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage(string.format(
                        "Waypoint set: Oribos Enclave (%.1f, %.1f)", enc.x, enc.y))
                end
            end
        end
    end)
    covHit:Hide()
    parent._covenantStatusHit = covHit

    -- Arcantina toy hint: "Use [Personal Key to the Arcantina]"
    -- Shown below the cross-region status text for Arcantina items.
    local arcHint = CreateFrame("Frame", nil, bottomSection)
    arcHint:SetSize(CatSizing.DetailPanelWidth - 16, 16)
    arcHint:EnableMouse(true)
    local arcHintText = arcHint:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    arcHintText:SetAllPoints()
    arcHintText:SetText("Use |cff0070dd[Personal Key to the Arcantina]|r")
    arcHintText:SetJustifyH("CENTER")
    arcHint:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:SetItemByID(253629)
        GameTooltip:Show()
    end)
    arcHint:SetScript("OnLeave", function() GameTooltip:Hide() end)
    arcHint:Hide()
    parent._arcHint = arcHint

    -- Tooltip overlay for disabled waypoint button (quest requirement hint)
    local btnHit = CreateFrame("Frame", nil, bottomSection)
    btnHit:SetAllPoints(parent._waypointBtn)
    btnHit:EnableMouse(true)
    btnHit:SetFrameLevel(parent._waypointBtn:GetFrameLevel() + 5)
    btnHit:SetScript("OnEnter", function(self)
        if not self._tooltip then return end
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:AddLine("Quest Required", 1, 0.82, 0)
        GameTooltip:AddLine(self._tooltip, 0.8, 0.8, 0.8, true)
        GameTooltip:Show()
    end)
    btnHit:SetScript("OnLeave", function() GameTooltip:Hide() end)
    btnHit:Hide()
    parent._waypointBtnHit = btnHit

    -- Wowhead link box (copyable URL, shown on CTRL+Click or when coords missing)
    local linkLabel = bottomSection:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    linkLabel:SetPoint("BOTTOMLEFT", parent._waypointStatus, "TOPLEFT", 0, 4)
    linkLabel:SetPoint("BOTTOMRIGHT", parent._waypointStatus, "TOPRIGHT", 0, 4)
    linkLabel:SetJustifyH("CENTER")
    linkLabel:SetText("|cff888888Look up on Wowhead:|r")
    linkLabel:Hide()
    parent._wowheadLabel = linkLabel

    local linkBox = CreateFrame("EditBox", nil, bottomSection, "InputBoxTemplate")
    linkBox:SetHeight(20)
    linkBox:SetPoint("BOTTOMLEFT", linkLabel, "TOPLEFT", 0, 2)
    linkBox:SetPoint("BOTTOMRIGHT", linkLabel, "TOPRIGHT", 0, 2)
    linkBox:SetFontObject(GameFontHighlightSmall)
    linkBox:SetAutoFocus(false)
    linkBox:SetJustifyH("CENTER")
    linkBox:SetScript("OnEscapePressed", function(self) self:ClearFocus() end)
    linkBox:SetScript("OnEditFocusGained", function(self) self:HighlightText() end)
    linkBox:SetScript("OnTextChanged", function(self)
        if self._url and self:GetText() ~= self._url then
            self:SetText(self._url)
            self:HighlightText()
        end
    end)
    linkBox:Hide()
    parent._wowheadBox = linkBox

    -- Cross-continent popup (DIALOG strata, anchored to parent)
    local crossPopup = CreateFrame("Frame", nil, parent, "BackdropTemplate")
    crossPopup:SetPoint("BOTTOMLEFT", parent, "BOTTOMLEFT", 8, 8)
    crossPopup:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", -8, 8)
    crossPopup:SetHeight(124)
    crossPopup:SetFrameStrata("DIALOG")
    crossPopup:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    crossPopup:SetBackdropColor(0.08, 0.05, 0.02, 0.97)
    crossPopup:SetBackdropBorderColor(0.8, 0.6, 0.0, 1)
    crossPopup:Hide()

    local crossIcon = crossPopup:CreateTexture(nil, "OVERLAY")
    crossIcon:SetSize(24, 24)
    crossIcon:SetPoint("TOP", crossPopup, "TOP", 0, -8)
    crossIcon:SetTexture("Interface\\DialogFrame\\UI-Dialog-Icon-AlertNew")

    local crossText = crossPopup:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    crossText:SetPoint("TOP", crossIcon, "BOTTOM", 0, -4)
    crossText:SetPoint("LEFT", crossPopup, "LEFT", 10, 0)
    crossText:SetPoint("RIGHT", crossPopup, "RIGHT", -10, 0)
    crossText:SetJustifyH("CENTER")
    crossText:SetWordWrap(true)

    local crossBtn = CreateFrame("Button", nil, crossPopup, "UIPanelButtonTemplate")
    crossBtn:SetSize(100, 22)
    crossBtn:SetPoint("BOTTOM", crossPopup, "BOTTOM", 0, 8)
    crossBtn:SetText("Got it")
    crossBtn:SetScript("OnClick", function()
        crossPopup:Hide()
        local pendingMap = crossPopup._pendingMapID
        crossPopup._pendingMapID = nil
        ForceOpenWorldMap(pendingMap)
        -- Show green success popup with stored waypoint info
        local ps = crossPopup._pendingSuccess
        crossPopup._pendingSuccess = nil
        if ps then
            ShowWaypointSuccess(ps.zone, ps.x, ps.y)
        end
    end)

    crossPopup._text = crossText
    parent._crossPopup = crossPopup

    -- Zidormi timeline popup (DIALOG strata, anchored to parent)
    local zidormiPopup = CreateFrame("Frame", nil, parent, "BackdropTemplate")
    zidormiPopup:SetPoint("BOTTOMLEFT", parent, "BOTTOMLEFT", 8, 8)
    zidormiPopup:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", -8, 8)
    zidormiPopup:SetHeight(120)
    zidormiPopup:SetFrameStrata("DIALOG")
    zidormiPopup:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    zidormiPopup:SetBackdropColor(0.05, 0.05, 0.10, 0.97)
    zidormiPopup:SetBackdropBorderColor(0.4, 0.6, 0.9, 1)
    zidormiPopup:Hide()

    local zidIcon = zidormiPopup:CreateTexture(nil, "OVERLAY")
    zidIcon:SetSize(24, 24)
    zidIcon:SetPoint("TOP", zidormiPopup, "TOP", 0, -8)
    zidIcon:SetTexture("Interface\\Icons\\Spell_Holy_BorrowedTime")

    local zidText = zidormiPopup:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    zidText:SetPoint("TOP", zidIcon, "BOTTOM", 0, -4)
    zidText:SetPoint("LEFT", zidormiPopup, "LEFT", 10, 0)
    zidText:SetPoint("RIGHT", zidormiPopup, "RIGHT", -10, 0)
    zidText:SetJustifyH("CENTER")
    zidText:SetWordWrap(true)

    local zidBtn = CreateFrame("Button", nil, zidormiPopup, "UIPanelButtonTemplate")
    zidBtn:SetSize(100, 22)
    zidBtn:SetPoint("BOTTOM", zidormiPopup, "BOTTOM", 0, 8)
    zidBtn:SetText("Got it")
    zidBtn:SetScript("OnClick", function()
        zidormiPopup:Hide()
        local pendingMap = zidormiPopup._pendingMapID
        zidormiPopup._pendingMapID = nil
        ForceOpenWorldMap(pendingMap)
        -- Show green success popup with stored waypoint info
        local ps = zidormiPopup._pendingSuccess
        zidormiPopup._pendingSuccess = nil
        if ps then
            ShowWaypointSuccess(ps.zone, ps.x, ps.y)
        end
    end)

    zidormiPopup._text = zidText
    parent._zidormiPopup = zidormiPopup

    -- Waypoint success popup (green, auto-hides after 4 seconds)
    local successPopup = CreateFrame("Frame", nil, parent, "BackdropTemplate")
    successPopup:SetPoint("BOTTOMLEFT", parent, "BOTTOMLEFT", 8, 8)
    successPopup:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", -8, 8)
    successPopup:SetHeight(56)
    successPopup:SetFrameStrata("DIALOG")
    successPopup:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    successPopup:SetBackdropColor(0.02, 0.08, 0.02, 0.97)
    successPopup:SetBackdropBorderColor(0.2, 0.8, 0.2, 1)
    successPopup:Hide()

    local successText = successPopup:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    successText:SetPoint("TOPLEFT", successPopup, "TOPLEFT", 10, -10)
    successText:SetPoint("BOTTOMRIGHT", successPopup, "BOTTOMRIGHT", -10, 10)
    successText:SetJustifyH("CENTER")
    successText:SetJustifyV("MIDDLE")
    successText:SetWordWrap(true)
    successText:SetTextColor(0.4, 0.9, 0.4, 1)

    successPopup._text = successText
    parent._successPopup = successPopup

    ---------------------------------------------------------------------------
    -- Fixed INFO section (between model and scrollable source area)
    ---------------------------------------------------------------------------
    local infoSection = CreateFrame("Frame", nil, parent)
    infoSection:SetPoint("TOPLEFT", modelBg, "BOTTOMLEFT", 0, -2)
    infoSection:SetPoint("TOPRIGHT", modelBg, "BOTTOMRIGHT", 0, -2)
    infoSection:SetHeight(118) -- recalculated in ShowItem
    parent._infoSection = infoSection

    -- Favorite star button (top-right of info section, above name hit frame)
    local favBtn = CreateFrame("Button", nil, infoSection)
    favBtn:SetSize(25, 25)
    favBtn:SetPoint("TOPRIGHT", infoSection, "TOPRIGHT", -9, -2)
    favBtn:SetFrameLevel(infoSection:GetFrameLevel() + 5)
    local favIcon = favBtn:CreateTexture(nil, "ARTWORK")
    favIcon:SetAllPoints()
    favIcon:SetAtlas("PetJournal-FavoritesIcon")
    favIcon:SetDesaturated(true)
    favIcon:SetAlpha(0.3)
    favBtn._icon = favIcon
    favBtn:RegisterForClicks("LeftButtonUp", "RightButtonUp")

    local function RefreshFavTooltip(btn)
        if not parent._currentItem then return end
        if not GameTooltip:IsOwned(btn) then return end
        GameTooltip:ClearLines()
        if NS.UI.CatalogGrid_IsFavorite
            and NS.UI.CatalogGrid_IsFavorite(parent._currentItem.decorID) then
            GameTooltip:AddLine("Remove from favorites", 1, 0.82, 0, true)
            GameTooltip:AddLine("Shift+Right Click to clear all favorites", 0.5, 0.5, 0.5, true)
        else
            GameTooltip:AddLine("Favorite this decor item", 1, 0.82, 0, true)
        end
        GameTooltip:Show()
    end

    favBtn:SetScript("OnClick", function(self, button)
        if not parent._currentItem then return end
        local decorID = parent._currentItem.decorID
        if button == "RightButton" and IsShiftKeyDown() then
            -- Shift+Right Click: clear ALL favorites
            if NS.UI.CatalogGrid_IsFavorite and NS.UI.CatalogGrid_IsFavorite(decorID) then
                local favDB = NS.favorites or (NS.db and NS.db.favorites)
                if favDB then
                    wipe(favDB)
                end
                if NS.UI.CatalogGrid_ApplyFilters then
                    NS.UI.CatalogGrid_ApplyFilters()
                end
                UpdateFavoriteStar(parent)
                RefreshFavTooltip(self)
            end
            return
        end
        if button == "LeftButton" then
            if NS.UI.CatalogGrid_ToggleFavorite then
                NS.UI.CatalogGrid_ToggleFavorite(decorID)
            end
            UpdateFavoriteStar(parent)
            RefreshFavTooltip(self)
        end
    end)

    favBtn:SetScript("OnEnter", function(self)
        if not parent._currentItem then return end
        GameTooltip:SetOwner(self, "ANCHOR_LEFT")
        if NS.UI.CatalogGrid_IsFavorite
            and NS.UI.CatalogGrid_IsFavorite(parent._currentItem.decorID) then
            GameTooltip:AddLine("Remove from favorites", 1, 0.82, 0, true)
            GameTooltip:AddLine("Shift+Right Click to clear all favorites", 0.5, 0.5, 0.5, true)
        else
            GameTooltip:AddLine("Favorite this decor item", 1, 0.82, 0, true)
        end
        GameTooltip:Show()
    end)

    favBtn:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)

    parent._favBtn = favBtn
    favBtn:Hide()  -- hidden until an item is selected

    -- Item name (centered, quality-colored, leaves room for fav star at right)
    parent._itemName = infoSection:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    parent._itemName:SetPoint("TOPLEFT", infoSection, "TOPLEFT", 4, -6)
    parent._itemName:SetPoint("RIGHT", infoSection, "RIGHT", -38, 0)
    parent._itemName:SetJustifyH("CENTER")
    parent._itemName:SetWordWrap(true)

    -- Item name hit frame: tooltip on hover, CTRL+Left Click → Wowhead URL
    local nameHit = CreateFrame("Frame", nil, infoSection)
    nameHit:SetAllPoints(parent._itemName)
    nameHit:EnableMouse(true)
    nameHit:SetFrameLevel(infoSection:GetFrameLevel() + 2)

    nameHit:SetScript("OnEnter", function(self)
        if not self._decorID then return end
        GameTooltip:SetOwner(self, "ANCHOR_BOTTOM")
        local link = NS.UI.GetItemHyperlink and NS.UI.GetItemHyperlink(self._decorID)
        if link then
            GameTooltip:SetHyperlink(link)
        else
            GameTooltip:AddLine(self._itemName or "Unknown", 1, 1, 1)
        end
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link", 0.5, 0.5, 0.5)
        local chatOpen = ChatEdit_GetActiveWindow and ChatEdit_GetActiveWindow()
        if chatOpen then
            GameTooltip:AddLine("|cff55aaeeSHIFT+Left Click|r to link in chat", 0.5, 0.5, 0.5)
        end
        GameTooltip:Show()
    end)
    nameHit:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)
    nameHit:SetScript("OnMouseUp", function(self, button)
        if button == "LeftButton" then
            if IsShiftKeyDown() then
                -- Shift+Click: link item in chat
                local editBox = ChatEdit_GetActiveWindow and ChatEdit_GetActiveWindow()
                if editBox and self._itemID then
                    local _, chatLink = GetItemInfo(self._itemID)
                    if chatLink then
                        ChatEdit_InsertLink(chatLink)
                    end
                end
            elseif IsControlKeyDown() and self._itemID then
                ShowCopyableURL("https://www.wowhead.com/item=" .. self._itemID)
            end
        end
    end)
    parent._itemNameHit = nameHit
    nameHit:Hide()  -- hidden until an item is selected

    -- INFO section header
    local infoHeader = CreateDetailSectionHeader(infoSection, "INFO")
    infoHeader:SetPoint("TOPLEFT", parent._itemName, "BOTTOMLEFT", 0, -6)
    infoHeader:SetPoint("RIGHT", infoSection, "RIGHT", -4, 0)
    parent._infoHeader = infoHeader

    -- DETAILS hover trigger (right side of INFO header row)
    local detailsLabel = infoHeader:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    detailsLabel:SetPoint("TOPRIGHT", infoHeader, "TOPRIGHT", -2, 0)
    detailsLabel:SetText("DETAILS")
    detailsLabel:SetTextColor(0.4, 0.7, 1.0, 0.7)
    detailsLabel:Hide()
    parent._detailsLabel = detailsLabel

    local detailsHit = CreateFrame("Frame", nil, infoHeader)
    detailsHit:SetSize(60, 22)
    detailsHit:SetPoint("TOPRIGHT", infoHeader, "TOPRIGHT", 0, 0)
    detailsHit:EnableMouse(true)
    detailsHit:SetFrameLevel(infoHeader:GetFrameLevel() + 5)
    detailsHit:Hide()
    parent._detailsHit = detailsHit

    ---------------------------------------------------------------------------
    -- DETAILS flyout panel (extends right beyond catalog frame on hover)
    ---------------------------------------------------------------------------
    local FLYOUT_W = 300
    local FLYOUT_MAX_ROWS = 16
    local FLYOUT_ROW_H = 18
    local FLYOUT_TOP_PAD = 32  -- 8 top + 16 header + 4 sep + 4 gap

    local catalogParent = parent:GetParent()
    local detailsFlyout = CreateFrame("Frame", nil, catalogParent, "BackdropTemplate")
    detailsFlyout:SetWidth(FLYOUT_W)
    detailsFlyout:SetPoint("TOPLEFT", infoSection, "TOPRIGHT", 4, 0)
    detailsFlyout:SetFrameStrata("DIALOG")
    detailsFlyout:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    detailsFlyout:SetBackdropColor(0.06, 0.06, 0.09, 0.97)
    detailsFlyout:SetBackdropBorderColor(0.4, 0.7, 1.0, 0.8)
    detailsFlyout:EnableMouse(true)
    detailsFlyout:Hide()

    -- Flyout header
    local flyoutHeader = detailsFlyout:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    flyoutHeader:SetPoint("TOPLEFT", detailsFlyout, "TOPLEFT", 10, -8)
    flyoutHeader:SetText("ITEM DETAILS")
    flyoutHeader:SetTextColor(0.90, 0.76, 0.25, 1)
    detailsFlyout._header = flyoutHeader

    local flyoutSep1 = detailsFlyout:CreateTexture(nil, "ARTWORK")
    flyoutSep1:SetHeight(1)
    flyoutSep1:SetPoint("TOPLEFT", flyoutHeader, "BOTTOMLEFT", 0, -4)
    flyoutSep1:SetPoint("RIGHT", detailsFlyout, "RIGHT", -10, 0)
    flyoutSep1:SetColorTexture(0.72, 0.58, 0.25, 0.5)
    detailsFlyout._sep1 = flyoutSep1

    -- Pool of label+value row pairs
    local flyoutRows = {}
    for i = 1, FLYOUT_MAX_ROWS do
        local lbl = detailsFlyout:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
        lbl:SetJustifyH("LEFT")
        lbl:SetTextColor(0.90, 0.76, 0.25, 1)
        local val = detailsFlyout:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
        val:SetJustifyH("LEFT")
        val:SetTextColor(0.88, 0.88, 0.88, 1)
        flyoutRows[i] = { lbl = lbl, val = val }
    end
    detailsFlyout._rows = flyoutRows

    -- Optional separator between fixed and conditional sections
    local flyoutSep2 = detailsFlyout:CreateTexture(nil, "ARTWORK")
    flyoutSep2:SetHeight(1)
    flyoutSep2:SetColorTexture(0.25, 0.25, 0.30, 0.6)
    flyoutSep2:Hide()
    detailsFlyout._sep2 = flyoutSep2
    detailsFlyout._maxRows = FLYOUT_MAX_ROWS
    detailsFlyout._rowH = FLYOUT_ROW_H
    detailsFlyout._topPad = FLYOUT_TOP_PAD

    -- Hover bridge: DETAILS label → flyout
    detailsHit:SetScript("OnEnter", function()
        if detailsFlyout._hideTimer then
            detailsFlyout._hideTimer:Cancel()
            detailsFlyout._hideTimer = nil
        end
        detailsLabel:SetTextColor(0.6, 0.85, 1.0, 1.0)
        detailsFlyout:Show()
    end)
    detailsHit:SetScript("OnLeave", function()
        detailsFlyout._hideTimer = C_Timer.NewTimer(0.08, function()
            detailsFlyout._hideTimer = nil
            if not detailsFlyout:IsMouseOver() then
                detailsFlyout:Hide()
                detailsLabel:SetTextColor(0.4, 0.7, 1.0, 0.7)
            end
        end)
    end)

    detailsFlyout:SetScript("OnEnter", function(self)
        if self._hideTimer then
            self._hideTimer:Cancel()
            self._hideTimer = nil
        end
    end)
    detailsFlyout:SetScript("OnLeave", function(self)
        self._hideTimer = C_Timer.NewTimer(0.08, function()
            self._hideTimer = nil
            if not self:IsMouseOver() and not detailsHit:IsMouseOver() then
                self:Hide()
                detailsLabel:SetTextColor(0.4, 0.7, 1.0, 0.7)
            end
        end)
    end)

    parent._detailsFlyout = detailsFlyout

    -- Info row 1: Rarity | Indoor | Outdoor | Faction
    local infoRow1 = CreateInfoRow(infoSection, 4)
    infoRow1:SetPoint("TOPLEFT", infoHeader, "BOTTOMLEFT", 0, -2)
    infoRow1:SetPoint("RIGHT", infoSection, "RIGHT", -4, 0)
    parent._infoRow1 = infoRow1

    -- Info row 2: Stored | Placed | Redeemable
    local infoRow2 = CreateInfoRow(infoSection, 3)
    infoRow2:SetPoint("TOPLEFT", infoRow1, "BOTTOMLEFT", 0, -2)
    infoRow2:SetPoint("RIGHT", infoSection, "RIGHT", -4, 0)
    parent._infoRow2 = infoRow2

    -- Pool of per-cost segments: FontString + hit frame (supports up to 3 currencies)
    local MAX_COST_SEGS = 3
    local costSegs = {}
    for i = 1, MAX_COST_SEGS do
        local segText = infoSection:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        segText:SetJustifyH("LEFT")
        segText:SetTextColor(0.75, 0.75, 0.75, 1)
        segText:Hide()

        local segHit = CreateFrame("Frame", nil, infoSection)
        segHit:EnableMouse(true)
        segHit:SetFrameLevel(infoSection:GetFrameLevel() + 3)
        segHit:Hide()
        segHit:SetScript("OnEnter", function(self)
            local cid = self._currencyID
            if not cid or cid == 0 then return end
            GameTooltip:SetOwner(self, "ANCHOR_BOTTOM")
            GameTooltip:SetCurrencyByID(cid)
            GameTooltip:Show()
        end)
        segHit:SetScript("OnLeave", function() GameTooltip:Hide() end)

        costSegs[i] = { text = segText, hit = segHit }
    end
    -- Separator FontStrings between cost segments
    local costSeps = {}
    for i = 1, MAX_COST_SEGS - 1 do
        local sep = infoSection:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        sep:SetText("+")
        sep:SetTextColor(0.5, 0.5, 0.5, 1)
        sep:Hide()
        costSeps[i] = sep
    end
    parent._costSegs = costSegs
    parent._costSeps = costSeps

    -- Collected status: full-width banner bar
    local collectedBanner = CreateFrame("Frame", nil, infoSection, "BackdropTemplate")
    collectedBanner:SetHeight(22)
    collectedBanner:SetPoint("TOPLEFT", infoRow2, "BOTTOMLEFT", 0, -4)
    collectedBanner:SetPoint("RIGHT", infoSection, "RIGHT", -4, 0)
    collectedBanner:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    collectedBanner:SetBackdropColor(0, 0, 0, 0)
    local collectedText = collectedBanner:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    collectedText:SetPoint("CENTER")
    collectedText:SetJustifyH("CENTER")
    collectedBanner._text = collectedText
    parent._collectedBanner = collectedBanner

    ---------------------------------------------------------------------------
    -- Scrollable SOURCE section (with visible scrollbar)
    ---------------------------------------------------------------------------
    local SCROLLBAR_WIDTH = 3

    local middleScroll = CreateFrame("ScrollFrame", nil, parent)
    middleScroll:SetPoint("TOPLEFT", infoSection, "BOTTOMLEFT", 0, 0)
    middleScroll:SetPoint("TOPRIGHT", infoSection, "BOTTOMRIGHT", -(SCROLLBAR_WIDTH + 2), 0)
    middleScroll:SetPoint("BOTTOMLEFT", bottomSection, "TOPLEFT", 0, 0)
    middleScroll:SetPoint("BOTTOMRIGHT", bottomSection, "TOPRIGHT", -(SCROLLBAR_WIDTH + 2), 0)
    middleScroll:EnableMouseWheel(true)

    local middleChild = CreateFrame("Frame", nil, middleScroll)
    middleChild:SetWidth(1) -- set via OnSizeChanged
    middleScroll:SetScrollChild(middleChild)

    -- Manual scrollbar: track (thin bar on the right) + thumb (draggable)
    local scrollTrack = CreateFrame("Frame", nil, parent)
    scrollTrack:SetPoint("TOPLEFT", middleScroll, "TOPRIGHT", 1, -1)
    scrollTrack:SetPoint("BOTTOMLEFT", middleScroll, "BOTTOMRIGHT", 1, 1)
    scrollTrack:SetWidth(SCROLLBAR_WIDTH)
    local trackBg = scrollTrack:CreateTexture(nil, "BACKGROUND")
    trackBg:SetAllPoints()
    trackBg:SetColorTexture(0.12, 0.12, 0.14, 0.4)
    scrollTrack:Hide()

    local scrollThumb = CreateFrame("Frame", nil, scrollTrack)
    scrollThumb:SetWidth(SCROLLBAR_WIDTH)
    scrollThumb:SetHeight(30) -- recalculated dynamically
    scrollThumb:SetPoint("TOP", scrollTrack, "TOP", 0, 0)
    local thumbTex = scrollThumb:CreateTexture(nil, "OVERLAY")
    thumbTex:SetAllPoints()
    thumbTex:SetColorTexture(0.55, 0.48, 0.28, 0.7)
    scrollThumb:EnableMouse(true)

    -- Thumb drag support
    local thumbDragging = false
    local thumbDragStartY = 0
    local thumbDragStartScroll = 0

    scrollThumb:SetScript("OnMouseDown", function(self, button)
        if button ~= "LeftButton" then return end
        thumbDragging = true
        thumbDragStartY = select(2, GetCursorPosition()) / (self:GetEffectiveScale() or 1)
        thumbDragStartScroll = middleScroll:GetVerticalScroll()
    end)
    scrollThumb:SetScript("OnMouseUp", function()
        thumbDragging = false
    end)
    scrollThumb:SetScript("OnUpdate", function(self)
        if not thumbDragging then return end
        local curY = select(2, GetCursorPosition()) / (self:GetEffectiveScale() or 1)
        local deltaY = thumbDragStartY - curY -- positive = dragged down
        local trackH = scrollTrack:GetHeight() - self:GetHeight()
        if trackH <= 0 then return end
        local maxScroll = math.max(0, middleChild:GetHeight() - middleScroll:GetHeight())
        local scrollDelta = (deltaY / trackH) * maxScroll
        local newScroll = math.max(0, math.min(maxScroll, thumbDragStartScroll + scrollDelta))
        middleScroll:SetVerticalScroll(newScroll)
    end)

    -- Track click: jump thumb to click position
    scrollTrack:EnableMouse(true)
    scrollTrack:SetScript("OnMouseDown", function(self, button)
        if button ~= "LeftButton" then return end
        local curY = select(2, GetCursorPosition()) / (self:GetEffectiveScale() or 1)
        local trackTop = select(2, self:GetCenter()) + self:GetHeight() / 2
        local clickRatio = (trackTop - curY) / self:GetHeight()
        clickRatio = math.max(0, math.min(1, clickRatio))
        local maxScroll = math.max(0, middleChild:GetHeight() - middleScroll:GetHeight())
        middleScroll:SetVerticalScroll(clickRatio * maxScroll)
    end)

    -- Position thumb based on scroll state
    local function UpdateSourceScrollBar()
        local contentH = middleChild:GetHeight()
        local viewH = middleScroll:GetHeight()
        local maxScroll = math.max(0, contentH - viewH)
        if maxScroll <= 0 then
            scrollTrack:Hide()
            middleScroll:SetVerticalScroll(0)
        else
            scrollTrack:Show()
            -- Thumb height proportional to visible ratio
            local trackH = scrollTrack:GetHeight()
            local thumbH = math.max(16, trackH * (viewH / contentH))
            scrollThumb:SetHeight(thumbH)
            -- Thumb position
            local scrollPos = middleScroll:GetVerticalScroll()
            local ratio = scrollPos / maxScroll
            local travel = trackH - thumbH
            scrollThumb:ClearAllPoints()
            scrollThumb:SetPoint("TOP", scrollTrack, "TOP", 0, -(ratio * travel))
        end
    end

    middleScroll:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, middleChild:GetHeight() - self:GetHeight())
        local current = self:GetVerticalScroll()
        local newScroll = math.max(0, math.min(maxScroll, current - delta * 20))
        self:SetVerticalScroll(newScroll)
        UpdateSourceScrollBar()
    end)
    middleScroll:SetScript("OnScrollRangeChanged", function()
        UpdateSourceScrollBar()
    end)
    middleScroll:SetScript("OnSizeChanged", function(self, width)
        middleChild:SetWidth(width)
        UpdateSourceScrollBar()
    end)

    -- Mouse wheel on the scrollbar track/thumb area
    scrollTrack:EnableMouseWheel(true)
    scrollTrack:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, middleChild:GetHeight() - middleScroll:GetHeight())
        local current = middleScroll:GetVerticalScroll()
        local newScroll = math.max(0, math.min(maxScroll, current - delta * 20))
        middleScroll:SetVerticalScroll(newScroll)
        UpdateSourceScrollBar()
    end)

    parent._middleScroll = middleScroll
    parent._middleChild = middleChild
    parent._scrollBar = scrollTrack
    parent._UpdateSourceScrollBar = UpdateSourceScrollBar

    ---------------------------------------------------------------------------
    -- SOURCE section content (in scrollable area)
    ---------------------------------------------------------------------------
    local sourceHeader = CreateDetailSectionHeader(middleChild, "SOURCE")
    sourceHeader:SetPoint("TOPLEFT", middleChild, "TOPLEFT", 4, -4)
    sourceHeader:SetPoint("RIGHT", middleChild, "RIGHT", -4, 0)
    parent._sourceHeader = sourceHeader

    -- Source type
    parent._sourceLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    parent._sourceLine:SetPoint("TOPLEFT", sourceHeader, "BOTTOMLEFT", 0, -4)
    parent._sourceLine:SetPoint("RIGHT", middleChild, "RIGHT", -4, 0)
    parent._sourceLine:SetJustifyH("LEFT")

    -- Acquisition instructions
    parent._acquireLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._acquireLine:SetPoint("TOPLEFT", parent._sourceLine, "BOTTOMLEFT", 0, -4)
    parent._acquireLine:SetPoint("RIGHT", middleChild, "RIGHT", -4, 0)
    parent._acquireLine:SetJustifyH("LEFT")
    parent._acquireLine:SetWordWrap(true)
    parent._acquireLine:SetSpacing(2)

    -- Acquisition text hit frame (CTRL+Click to copy Wowhead URL)
    local acquireHit = CreateFrame("Frame", nil, middleChild)
    acquireHit:SetAllPoints(parent._acquireLine)
    acquireHit:EnableMouse(true)
    acquireHit:SetScript("OnEnter", function(self)
        if not self._active then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        if self._noQuestData then
            GameTooltip:AddLine(self._tipTitle or "", 1, 0.82, 0)
            GameTooltip:AddLine("No quest data available for this quest", 0.5, 0.5, 0.5, true)
        elseif self._achievementName then
            GameTooltip:AddLine(self._tipTitle or "", 0.9, 0.8, 0.2)
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to open achievement")
        elseif self._dropBoss or self._dropZone then
            GameTooltip:AddLine(self._tipTitle or "", 0.8, 0.4, 0.8)
            GameTooltip:AddLine(" ")
            if self._npcID then
                GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            end
            if self._dropZone and self._dropZone ~= "" then
                local mapHint = self._dropBoss and "dungeon map" or "map"
                GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view " .. mapHint)
            end
        elseif self._giverX then
            GameTooltip:AddLine(self._tipTitle or "", 1, 0.82, 0)
            if self._giverName then
                local giverLine = "Quest Giver: " .. self._giverName
                if self._giverZone then
                    giverLine = giverLine .. " (" .. self._giverZone .. ")"
                end
                GameTooltip:AddLine(giverLine, 0.53, 0.67, 0.53)
            end
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to navigate to quest giver")
        else
            GameTooltip:AddLine(self._tipTitle or "", 1, 0.82, 0)
            if self._giverName then
                local giverLine = "Quest Giver: " .. self._giverName
                if self._giverZone then
                    giverLine = giverLine .. " (" .. self._giverZone .. ")"
                end
                GameTooltip:AddLine(giverLine, 0.53, 0.67, 0.53)
            end
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            if self._vendorX and self._vendorZone ~= "Arcantina" then
                GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint & view map")
            end
        end
        GameTooltip:Show()
    end)
    acquireHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    acquireHit:SetScript("OnMouseUp", function(self, button)
        if not self._active or not IsControlKeyDown() then return end
        if button == "LeftButton" and self._achievementName then
            OpenAchievementByName(self._achievementName)
        elseif button == "LeftButton" and self._dropBoss and self._npcID then
            ShowCopyableURL("https://www.wowhead.com/npc=" .. self._npcID)
        elseif button == "LeftButton" and self._url then
            ShowCopyableURL(self._url)
        elseif button == "RightButton" and self._dropZone then
            -- Drop item: open dungeon map via EJ (correct instance floor)
            local targetMapID = nil
            if self._dropBoss and NS.UI.GetBossInstanceMapID then
                targetMapID = NS.UI.GetBossInstanceMapID(self._dropBoss)
            end
            if not targetMapID and self._dropZone then
                targetMapID = GetZoneMapID(self._dropZone)
            end
            ForceOpenWorldMap(targetMapID)
        elseif button == "RightButton" and self._giverX then
            -- Quest item: navigate to quest giver
            local mapID, coordsTrusted = ResolveNavigableMap(self._giverZone)
            if mapID and coordsTrusted and NS.Navigation and NS.Navigation.SetWaypoint then
                NS.Navigation.SetWaypoint(mapID, self._giverX, self._giverY,
                    (self._giverName or "Quest Giver") .. " (" .. self._giverZone .. ")")
                ForceOpenWorldMap(mapID)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage("Waypoint set: " .. (self._giverName or "Quest Giver")
                        .. " in " .. self._giverZone)
                end
            elseif mapID then
                ForceOpenWorldMap(mapID)
            end
        elseif button == "RightButton" and self._vendorZone and self._vendorZone ~= "Arcantina" then
            local mapID, coordsTrusted = ResolveNavigableMap(self._vendorZone)
            if mapID and coordsTrusted and self._vendorX and self._vendorY then
                if NS.Navigation and NS.Navigation.SetWaypoint then
                    NS.Navigation.SetWaypoint(mapID, self._vendorX, self._vendorY,
                        (self._vendorName or "Vendor") .. " (" .. self._vendorZone .. ")")
                    ForceOpenWorldMap(mapID)
                    if NS.Utils and NS.Utils.PrintMessage then
                        NS.Utils.PrintMessage("Waypoint set: " .. (self._vendorName or "Vendor")
                            .. " in " .. self._vendorZone)
                    end
                end
            else
                -- Sub-zone or no coords: open the actual zone map (not parent)
                local openMapID = GetOpenableMapID(self._vendorZone) or mapID
                ForceOpenWorldMap(openMapID)
                if NS.Utils and NS.Utils.PrintMessage then
                    NS.Utils.PrintMessage((self._vendorName or "Vendor")
                        .. " is in " .. self._vendorZone)
                end
            end
        end
    end)
    acquireHit:Hide()
    parent._acquireHit = acquireHit

    -- Drop mob line pool (for items that drop from multiple mobs)
    parent._dropMobPool = {}
    parent._dropMobCount = 0
    local MAX_DROP_MOBS = 12
    for poolIdx = 1, MAX_DROP_MOBS do
        local mobLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
        mobLine:SetJustifyH("LEFT")
        mobLine:SetWordWrap(false)
        mobLine:Hide()

        local mobHit = CreateFrame("Frame", nil, middleChild)
        mobHit:SetAllPoints(mobLine)
        mobHit:EnableMouse(true)
        mobHit:SetScript("OnEnter", function(self)
            if not self._active then return end
            SetCursor("INSPECT_CURSOR")
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:AddLine(self._mobName or "", 0.8, 0.4, 0.8)
            if self._mobX and self._mobY then
                GameTooltip:AddLine(string.format("%.1f, %.1f in %s",
                    self._mobX, self._mobY, self._mobZone or ""), 0.7, 0.7, 0.7)
            end
            GameTooltip:AddLine(" ")
            if self._npcID then
                GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            end
            if self._mobX and self._mobY then
                GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint & view map")
            end
            GameTooltip:Show()
        end)
        mobHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
        mobHit:SetScript("OnMouseUp", function(self, button)
            if not self._active or not IsControlKeyDown() then return end
            if button == "LeftButton" and self._npcID then
                ShowCopyableURL("https://www.wowhead.com/npc=" .. self._npcID)
            elseif button == "RightButton" and self._mobX and self._mobY and self._mobZone then
                local mapID = GetZoneMapID(self._mobZone)
                if mapID and NS.Navigation and NS.Navigation.SetWaypoint then
                    NS.Navigation.SetWaypoint(mapID, self._mobX, self._mobY,
                        self._mobName .. " (" .. self._mobZone .. ")")
                    ForceOpenWorldMap(mapID)
                    if NS.Utils and NS.Utils.PrintMessage then
                        NS.Utils.PrintMessage("Waypoint set: " .. self._mobName
                            .. " in " .. self._mobZone)
                    end
                end
            end
        end)
        mobHit:Hide()

        parent._dropMobPool[poolIdx] = { line = mobLine, hit = mobHit }
    end

    -- Vendor line (separate from acquisition text, clickable)
    -- Zone line (separate clickable element for Quest items)
    parent._zoneLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._zoneLine:SetPoint("TOPLEFT", parent._acquireLine, "BOTTOMLEFT", 0, -2)
    parent._zoneLine:SetPoint("RIGHT", middleChild, "RIGHT", -4, 0)
    parent._zoneLine:SetJustifyH("LEFT")
    parent._zoneLine:SetWordWrap(false)
    parent._zoneLine:Hide()

    local zoneHit = CreateFrame("Frame", nil, middleChild)
    zoneHit:SetAllPoints(parent._zoneLine)
    zoneHit:EnableMouse(true)
    zoneHit:SetScript("OnEnter", function(self)
        if not self._zoneName then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(self._zoneName, 1, 1, 1)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view zone map")
        GameTooltip:Show()
    end)
    zoneHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    zoneHit:SetScript("OnMouseUp", function(self, button)
        if button ~= "RightButton" or not IsControlKeyDown() then return end
        if not self._zoneName then return end
        local mapID = GetOpenableMapID(self._zoneName)
        ForceOpenWorldMap(mapID)
    end)
    zoneHit:Hide()
    parent._zoneHit = zoneHit

    -- Vendor NPC part: "Purchase from <NPC>" (auto-width, no right anchor)
    parent._vendorLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    -- TOPLEFT set dynamically in ShowItem; no RIGHT anchor for inline zone
    parent._vendorLine:SetJustifyH("LEFT")
    parent._vendorLine:SetWordWrap(false)
    parent._vendorLine:Hide()

    -- Vendor NPC hit frame (CTRL+Left = Wowhead URL, CTRL+Right = waypoint)
    local vendorHit = CreateFrame("Frame", nil, middleChild)
    vendorHit:SetAllPoints(parent._vendorLine)
    vendorHit:EnableMouse(true)
    vendorHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
    vendorHit:SetScript("OnEnter", function(self)
        if not self._active then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        if self._isDropLocation then
            GameTooltip:AddLine(self._vendorName or "Location", 0.53, 0.53, 0.53)
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view dungeon map")
        elseif self._isRotatingVendor then
            GameTooltip:AddLine(self._vendorName or "Vendor", 0.25, 0.69, 1)
            GameTooltip:AddLine("Their location rotates based on event scheduling.", 0.53, 0.53, 0.53, true)
            if self._npcID then
                GameTooltip:AddLine(" ")
                GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            end
        else
            GameTooltip:AddLine(self._vendorName or "Vendor", 0.25, 0.69, 1)
            GameTooltip:AddLine(" ")
            if self._npcID then
                GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
            end
            if self._vendorX and self._vendorZone ~= "Arcantina" then
                GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint & view map")
            end
        end
        GameTooltip:Show()
    end)
    vendorHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    vendorHit:SetScript("OnMouseUp", function(self, button)
        if not self._active or not IsControlKeyDown() then return end
        if self._isDropLocation and button == "RightButton" then
            -- Open dungeon map at the first floor (entrance level)
            local targetMapID = nil
            if self._dropBossName and NS.UI.GetDungeonBaseMapID then
                targetMapID = NS.UI.GetDungeonBaseMapID(self._dropBossName)
            end
            if not targetMapID then
                targetMapID = GetZoneMapID(self._dropZoneName)
            end
            ForceOpenWorldMap(targetMapID)
        elseif button == "LeftButton" and self._npcID then
            ShowCopyableURL("https://www.wowhead.com/npc=" .. self._npcID)
        elseif button == "RightButton" and self._vendorZone
                and self._vendorZone ~= "Arcantina" then
            local mapID, coordsTrusted = ResolveNavigableMap(self._vendorZone)
            if mapID and coordsTrusted and self._vendorX and self._vendorY then
                if NS.Navigation and NS.Navigation.SetWaypoint then
                    NS.Navigation.SetWaypoint(mapID, self._vendorX, self._vendorY,
                        (self._vendorName or "Vendor") .. " (" .. self._vendorZone .. ")")
                    ForceOpenWorldMap(mapID)
                    if NS.Utils and NS.Utils.PrintMessage then
                        NS.Utils.PrintMessage("Waypoint set: " .. (self._vendorName or "Vendor")
                            .. " in " .. self._vendorZone)
                    end
                end
            else
                -- Sub-zone or no coords: open the actual zone map (not parent)
                local openMapID = GetOpenableMapID(self._vendorZone) or mapID
                ForceOpenWorldMap(openMapID)
            end
        end
    end)
    vendorHit:Hide()
    parent._vendorHit = vendorHit

    -- Vendor zone part: " in <Zone>" (inline after _vendorLine)
    parent._vendorZonePart = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._vendorZonePart:SetJustifyH("LEFT")
    parent._vendorZonePart:SetWordWrap(false)
    parent._vendorZonePart:Hide()

    -- Vendor zone hit frame (CTRL+Right Click = open zone map)
    local vendorZoneHit = CreateFrame("Frame", nil, middleChild)
    vendorZoneHit:SetAllPoints(parent._vendorZonePart)
    vendorZoneHit:EnableMouse(true)
    vendorZoneHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
    vendorZoneHit:SetScript("OnEnter", function(self)
        if not self._zoneName or self._zoneName == "Arcantina" then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(self._zoneName, 1, 1, 1)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view zone map")
        GameTooltip:Show()
    end)
    vendorZoneHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    vendorZoneHit:SetScript("OnMouseUp", function(self, button)
        if button ~= "RightButton" or not IsControlKeyDown() then return end
        if not self._zoneName or self._zoneName == "Arcantina" then return end
        local mapID = GetOpenableMapID(self._zoneName)
        ForceOpenWorldMap(mapID)
    end)
    vendorZoneHit:Hide()
    parent._vendorZoneHit = vendorZoneHit

    -- Alternate faction vendor: "Purchase from <NPC> in <Zone>"
    -- Shown for items available in both neighborhoods (factionVendors).
    parent._altVendorLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._altVendorLine:SetJustifyH("LEFT")
    parent._altVendorLine:SetWordWrap(true)
    parent._altVendorLine:Hide()

    -- Hit frame for alternate vendor line (CTRL+Right Click → open zone map)
    local altVendorHit = CreateFrame("Frame", nil, middleChild)
    altVendorHit:SetAllPoints(parent._altVendorLine)
    altVendorHit:EnableMouse(true)
    altVendorHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
    altVendorHit:SetScript("OnEnter", function(self)
        if not self._zoneName then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(self._vendorName or "Vendor", 0.25, 0.69, 1)
        GameTooltip:AddLine(self._zoneName, 1, 1, 1)
        GameTooltip:AddLine(" ")
        if self._npcID then
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to copy Wowhead link")
        end
        GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view zone map")
        GameTooltip:Show()
    end)
    altVendorHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    altVendorHit:SetScript("OnMouseUp", function(self, button)
        if not IsControlKeyDown() then return end
        if button == "LeftButton" and self._npcID then
            ShowCopyableURL("https://www.wowhead.com/npc=" .. self._npcID)
        elseif button == "RightButton" and self._zoneName then
            local mapID = GetOpenableMapID(self._zoneName)
            ForceOpenWorldMap(mapID)
        end
    end)
    altVendorHit:Hide()
    parent._altVendorHit = altVendorHit

    -- Vendor note: "(available after completing the quest)" etc.
    parent._vendorNote = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._vendorNote:SetJustifyH("LEFT")
    parent._vendorNote:SetWordWrap(true)
    parent._vendorNote:SetSpacing(2)
    parent._vendorNote:Hide()

    ---------------------------------------------------------------------------
    -- Treasure source: "Found in: <treasure>" with interactive name + zone
    ---------------------------------------------------------------------------
    parent._treasureLine = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._treasureLine:SetJustifyH("LEFT")
    parent._treasureLine:SetWordWrap(false)
    parent._treasureLine:Hide()

    local treasureHit = CreateFrame("Frame", nil, middleChild)
    treasureHit:SetAllPoints(parent._treasureLine)
    treasureHit:EnableMouse(true)
    treasureHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
    treasureHit:SetScript("OnEnter", function(self)
        if not self._active then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(self._treasureName or "Treasure", 0.38, 0.88, 0.38)
        if self._treasureZone then
            GameTooltip:AddLine(self._treasureZone, 0.53, 0.53, 0.53)
        end
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to search on Wowhead")
        if self._treasureX then
            GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to set waypoint & view map")
        end
        GameTooltip:Show()
    end)
    treasureHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    treasureHit:SetScript("OnMouseUp", function(self, button)
        if not self._active or not IsControlKeyDown() then return end
        if button == "LeftButton" and self._treasureName then
            ShowCopyableURL("https://www.wowhead.com/search?q=" .. self._treasureName)
        elseif button == "RightButton" and self._treasureX then
            local mapID, coordsTrusted = ResolveNavigableMap(self._treasureZone)
            if mapID and coordsTrusted then
                if NS.Navigation and NS.Navigation.SetWaypoint then
                    NS.Navigation.SetWaypoint(mapID, self._treasureX, self._treasureY,
                        (self._treasureName or "Treasure") .. " (" .. self._treasureZone .. ")")
                    ForceOpenWorldMap(mapID)
                    if NS.Utils and NS.Utils.PrintMessage then
                        NS.Utils.PrintMessage("Waypoint set: " .. (self._treasureName or "Treasure")
                            .. " in " .. self._treasureZone)
                    end
                end
            else
                local openMapID = GetOpenableMapID(self._treasureZone) or mapID
                ForceOpenWorldMap(openMapID)
            end
        end
    end)
    treasureHit:Hide()
    parent._treasureHit = treasureHit

    -- Treasure zone part: " in <Zone>" (inline after _treasureLine)
    parent._treasureZonePart = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    parent._treasureZonePart:SetJustifyH("LEFT")
    parent._treasureZonePart:SetWordWrap(false)
    parent._treasureZonePart:Hide()

    local treasureZoneHit = CreateFrame("Frame", nil, middleChild)
    treasureZoneHit:SetAllPoints(parent._treasureZonePart)
    treasureZoneHit:EnableMouse(true)
    treasureZoneHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
    treasureZoneHit:SetScript("OnEnter", function(self)
        if not self._zoneName then return end
        SetCursor("INSPECT_CURSOR")
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        GameTooltip:AddLine(self._zoneName, 1, 1, 1)
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine("|cff55aaeeCTRL+Right Click|r to view zone map")
        GameTooltip:Show()
    end)
    treasureZoneHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
    treasureZoneHit:SetScript("OnMouseUp", function(self, button)
        if button ~= "RightButton" or not IsControlKeyDown() then return end
        if not self._zoneName then return end
        local mapID = GetOpenableMapID(self._zoneName)
        ForceOpenWorldMap(mapID)
    end)
    treasureZoneHit:Hide()
    parent._treasureZoneHit = treasureZoneHit

    -- Treasure hint lines pool: quest/NPC links for guided treasures (max 4)
    local MAX_TREASURE_HINTS = 4
    local treasureHintLines = {}
    for i = 1, MAX_TREASURE_HINTS do
        local hintText = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        hintText:SetJustifyH("LEFT")
        hintText:SetTextColor(0.7, 0.7, 0.7, 1)
        hintText:Hide()

        local hintHit = CreateFrame("Frame", nil, middleChild)
        hintHit:EnableMouse(true)
        hintHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
        hintHit:Hide()
        hintHit:SetScript("OnEnter", function(self)
            if not self._hintText then return end
            SetCursor("INSPECT_CURSOR")
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:AddLine(self._hintText, 1, 1, 1)
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to view on Wowhead")
            GameTooltip:Show()
        end)
        hintHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
        hintHit:SetScript("OnMouseUp", function(self, button)
            if button ~= "LeftButton" or not IsControlKeyDown() then return end
            if self._url then ShowCopyableURL(self._url) end
        end)

        treasureHintLines[i] = { text = hintText, hit = hintHit }
    end
    parent._treasureHintLines = treasureHintLines

    -- Treasure container lines pool: multi-spawn container names (max 6)
    local MAX_CONTAINER_LINES = 6
    local containerLines = {}
    for i = 1, MAX_CONTAINER_LINES do
        local cText = middleChild:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        cText:SetJustifyH("LEFT")
        cText:SetTextColor(0.7, 0.7, 0.7, 1)
        cText:Hide()

        local cHit = CreateFrame("Frame", nil, middleChild)
        cHit:EnableMouse(true)
        cHit:SetFrameLevel(middleChild:GetFrameLevel() + 3)
        cHit:Hide()
        cHit:SetScript("OnEnter", function(self)
            if not self._containerName then return end
            SetCursor("INSPECT_CURSOR")
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:AddLine(self._containerName, 1, 1, 1)
            GameTooltip:AddLine(" ")
            GameTooltip:AddLine("|cff55aaeeCTRL+Left Click|r to search on Wowhead")
            GameTooltip:Show()
        end)
        cHit:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
        cHit:SetScript("OnMouseUp", function(self, button)
            if button ~= "LeftButton" or not IsControlKeyDown() then return end
            if self._containerName then
                ShowCopyableURL("https://www.wowhead.com/search?q=" .. self._containerName)
            end
        end)

        containerLines[i] = { text = cText, hit = cHit }
    end
    parent._containerLines = containerLines

    ---------------------------------------------------------------------------
    -- Quest Chain scroll frame (shown only for Quest items with chain data)
    ---------------------------------------------------------------------------
    local CHAIN_LINE_HEIGHT = 16
    -- Separator before quest chain (re-anchored in ShowItem)
    parent._sepBeforeChain = middleChild:CreateTexture(nil, "ARTWORK")
    parent._sepBeforeChain:SetHeight(1)
    parent._sepBeforeChain:SetPoint("TOPLEFT", parent._acquireLine, "BOTTOMLEFT", 0, -6)
    parent._sepBeforeChain:SetPoint("RIGHT", middleChild, "RIGHT", -8, 0)
    parent._sepBeforeChain:SetColorTexture(0.25, 0.25, 0.28, 0.5)
    parent._sepBeforeChain:Hide()

    local CHAIN_MAX_VISIBLE = 7
    local CHAIN_TRUNCATION_THRESHOLD = 8  -- chains > this get truncated display
    local CHAIN_HEAD_COUNT = 2            -- show first N incomplete quests in head
    local CHAIN_TAIL_COUNT = 3            -- show last N entries (incl. decor reward)

    local chainContainer = CreateFrame("Frame", nil, middleChild)
    -- Chain anchors below sepBeforeChain (set in ShowItem)
    chainContainer:SetPoint("TOPLEFT", parent._sepBeforeChain, "BOTTOMLEFT", 0, -6)
    chainContainer:SetPoint("RIGHT", middleChild, "RIGHT", -4, 0)
    chainContainer:SetHeight(10) -- will be resized dynamically
    chainContainer:Hide()

    -- Chain header: "Quest Chain: X/Y completed"
    local chainHeader = chainContainer:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    chainHeader:SetPoint("TOPLEFT", 0, 0)
    chainHeader:SetPoint("RIGHT", 0, 0)
    chainHeader:SetJustifyH("LEFT")
    chainContainer._header = chainHeader

    -- Scroll area for quest entries
    local scrollFrame = CreateFrame("ScrollFrame", nil, chainContainer)
    scrollFrame:SetPoint("TOPLEFT", chainHeader, "BOTTOMLEFT", 0, -2)
    scrollFrame:SetPoint("RIGHT", chainContainer, "RIGHT", -10, 0)

    local scrollChild = CreateFrame("Frame", nil, scrollFrame)
    scrollChild:SetWidth(1) -- will be set from scroll width
    scrollFrame:SetScrollChild(scrollChild)

    -- Propagate width when the scroll frame gets its actual size from layout
    scrollFrame:SetScript("OnSizeChanged", function(self, width)
        if width and width > 0 then
            scrollChild:SetWidth(width)
        end
    end)

    scrollFrame:EnableMouseWheel(true)
    scrollFrame:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, scrollChild:GetHeight() - scrollFrame:GetHeight())
        local current = self:GetVerticalScroll()
        local newScroll = math.max(0, math.min(maxScroll, current - delta * CHAIN_LINE_HEIGHT * 2))
        self:SetVerticalScroll(newScroll)
        if chainContainer._scrollThumb and maxScroll > 0 then
            local pct = newScroll / maxScroll
            local trackH = chainContainer._scrollTrack:GetHeight() - chainContainer._scrollThumb:GetHeight()
            chainContainer._scrollThumb:SetPoint("TOP", chainContainer._scrollTrack, "TOP", 0, -pct * trackH)
        end
    end)

    -- Minimal scrollbar track + thumb
    local scrollTrack = CreateFrame("Frame", nil, chainContainer)
    scrollTrack:SetWidth(4)
    scrollTrack:SetPoint("TOPRIGHT", chainContainer, "TOPRIGHT", 0, -18)
    scrollTrack:SetPoint("BOTTOMRIGHT", chainContainer, "BOTTOMRIGHT")

    local trackBg = scrollTrack:CreateTexture(nil, "BACKGROUND")
    trackBg:SetAllPoints()
    trackBg:SetColorTexture(0.15, 0.15, 0.18, 0.5)

    local scrollThumb = CreateFrame("Frame", nil, scrollTrack)
    scrollThumb:SetWidth(4)
    scrollThumb:SetHeight(20)
    scrollThumb:SetPoint("TOP", scrollTrack, "TOP")

    local thumbTex = scrollThumb:CreateTexture(nil, "OVERLAY")
    thumbTex:SetAllPoints()
    thumbTex:SetColorTexture(0.4, 0.4, 0.45, 0.8)

    chainContainer._scrollFrame = scrollFrame
    chainContainer._scrollChild = scrollChild
    chainContainer._scrollTrack = scrollTrack
    chainContainer._scrollThumb = scrollThumb
    chainContainer._entries = {}
    chainContainer._hitFrames = {}

    parent._chainContainer = chainContainer
    parent._chainLineHeight = CHAIN_LINE_HEIGHT
    parent._chainMaxVisible = CHAIN_MAX_VISIBLE
    parent._chainTruncThreshold = CHAIN_TRUNCATION_THRESHOLD
    parent._chainHeadCount = CHAIN_HEAD_COUNT
    parent._chainTailCount = CHAIN_TAIL_COUNT

    -- Centered placeholder shown when no item is selected
    local placeholder = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    placeholder:SetPoint("CENTER", parent, "CENTER", 0, 0)
    placeholder:SetText("|cff666666No Decor Selected|r")
    parent._placeholder = placeholder

    -- Initial state: hide all content sections
    parent._modelBg:Hide()
    parent._middleScroll:Hide()
    parent._bottomSection:Hide()
end

-------------------------------------------------------------------------------
-- Show item in detail panel
-------------------------------------------------------------------------------
local function ResolveFactionVendor(item)
    if not item or not item.factionVendors then return end
    local faction = GetPlayerFaction()
    if not faction then return end
    local fv = item.factionVendors[faction]
    if not fv then return end
    item.vendorName = fv.name or ""
    item.npcID = fv.npcID
    item.npcX = fv.x
    item.npcY = fv.y
    item.zone = fv.zone or ""
end

local function ResolveFactionQuestChain(item)
    if not item or not item.factionQuestChains then return end
    local faction = GetPlayerFaction()
    if not faction then return end
    local fqc = item.factionQuestChains[faction]
    if not fqc then return end
    item.questID = fqc.questID
    -- Update sourceDetail to the resolved quest's name from QuestChains data
    local qcEntry = NS.QuestChains and NS.QuestChains[fqc.questID]
    if qcEntry and qcEntry.name then
        item.sourceDetail = qcEntry.name
    end
end

-------------------------------------------------------------------------------
-- Populate DETAILS flyout panel content for the given item.
-------------------------------------------------------------------------------
local function PopulateDetailsFlyout(item)
    local flyout = detailPanel._detailsFlyout
    if not flyout then return end
    local rows = flyout._rows
    local ROW_H = flyout._rowH
    local TOP_PAD = flyout._topPad

    -- Hide all rows and reset value colors
    for i = 1, flyout._maxRows do
        rows[i].lbl:Hide()
        rows[i].val:Hide()
        rows[i].val:SetTextColor(0.88, 0.88, 0.88, 1)
    end
    flyout._sep2:Hide()

    local rowIdx = 0
    local function AddRow(labelText, valueText)
        rowIdx = rowIdx + 1
        if rowIdx > flyout._maxRows then return end
        local r = rows[rowIdx]
        local yOff = -(TOP_PAD + (rowIdx - 1) * ROW_H)
        r.lbl:ClearAllPoints()
        r.lbl:SetPoint("TOPLEFT", flyout, "TOPLEFT", 10, yOff)
        r.lbl:SetWidth(100)
        r.lbl:SetText(labelText)
        r.lbl:Show()
        r.val:ClearAllPoints()
        r.val:SetPoint("TOPLEFT", flyout, "TOPLEFT", 114, yOff)
        r.val:SetPoint("RIGHT", flyout, "RIGHT", -10, 0)
        r.val:SetText(valueText)
        r.val:Show()
        return r
    end

    -- Expansion (colored by expansion)
    local expRow = AddRow("Expansion:", item.expansion or "Unknown")
    if expRow then
        local expHex = NS.ExpansionColors and NS.ExpansionColors[item.expansion]
        if expHex then
            local r = tonumber(expHex:sub(1, 2), 16) / 255
            local g = tonumber(expHex:sub(3, 4), 16) / 255
            local b = tonumber(expHex:sub(5, 6), 16) / 255
            expRow.val:SetTextColor(r, g, b, 1)
        end
    end

    -- Patch (if known)
    if item.patchAdded and item.patchAdded ~= "" then
        AddRow("Patch:", item.patchAdded)
    end

    -- Placement cost with Blizzard's housing icon format
    local placeCostStr
    if HOUSING_DECOR_PLACEMENT_COST_FORMAT then
        placeCostStr = HOUSING_DECOR_PLACEMENT_COST_FORMAT:format(item.placementCost or 0)
    else
        placeCostStr = tostring(item.placementCost or 0)
    end
    AddRow("Place Cost:", placeCostStr)

    -- Check for conditional rows
    local fixedRowIdx = rowIdx
    local hasConditional = false
    if item.dropRate then hasConditional = true end
    if item.professionSkill or (item.professionName and item.professionName ~= "") then
        hasConditional = true
    end
    if item.additionalSources and #item.additionalSources > 0 then
        hasConditional = true
    end

    if hasConditional then
        -- Separator between fixed and conditional sections
        local sepY = -(TOP_PAD + fixedRowIdx * ROW_H - ROW_H / 2 + 2)
        flyout._sep2:ClearAllPoints()
        flyout._sep2:SetPoint("TOPLEFT", flyout, "TOPLEFT", 10, sepY)
        flyout._sep2:SetPoint("RIGHT", flyout, "RIGHT", -10, 0)
        flyout._sep2:Show()

        if item.dropRate then
            AddRow("Drop Rate:", string.format("%.1f%%", item.dropRate))
        end
        if item.professionSkill or (item.professionName and item.professionName ~= "") then
            local profStr = item.professionName or ""
            if item.professionSkill and item.professionSkill ~= "" then
                profStr = profStr ~= ""
                    and (profStr .. " (" .. item.professionSkill .. ")")
                    or item.professionSkill
            end
            if profStr ~= "" then
                AddRow("Profession:", profStr)
            end
        end
        if item.additionalSources then
            for _, src in ipairs(item.additionalSources) do
                local srcStr = src.sourceType or ""
                if src.sourceDetail and src.sourceDetail ~= "" then
                    srcStr = srcStr .. ": " .. src.sourceDetail
                end
                AddRow("Also from:", srcStr)
            end
        end
    end

    -- Dynamic height (cap rowIdx to max pool size)
    local contentH = TOP_PAD + math.min(rowIdx, flyout._maxRows) * ROW_H + 12
    flyout:SetHeight(contentH)
end

function NS.UI.CatalogDetail_ShowItem(item)
    if not detailPanel or not item then return end
    detailPanel._currentItem = item
    NS.UI._currentDetailItem = item  -- for external refresh (e.g. faction debug)

    -- Resolve faction-specific fields before any display logic
    ResolveFactionVendor(item)
    ResolveFactionQuestChain(item)

    -- Show content sections, hide placeholder
    if detailPanel._placeholder then detailPanel._placeholder:Hide() end
    detailPanel._modelBg:Show()
    detailPanel._middleScroll:Show()
    detailPanel._bottomSection:Show()
    detailPanel._favBtn:Show()

    -- Name with quality color
    local qc = NS.QualityColors[item.quality] or NS.QualityColors[1]
    detailPanel._itemName:SetText(item.name)
    detailPanel._itemName:SetTextColor(qc[1], qc[2], qc[3], 1)

    -- Wire up item name hit frame (tooltip + Wowhead link)
    detailPanel._itemNameHit._decorID = item.decorID
    detailPanel._itemNameHit._itemID = item.itemID
    detailPanel._itemNameHit._itemName = item.name
    detailPanel._itemNameHit:Show()

    -- Update favorite star
    UpdateFavoriteStar(detailPanel)

    -- Rarity color hex (used in infoRow1)
    local qName = NS.QualityNames and NS.QualityNames[item.quality] or "Unknown"
    local colorHex = string.format("%02x%02x%02x",
        math.floor(qc[1] * 255), math.floor(qc[2] * 255), math.floor(qc[3] * 255))

    -- 3D model (ModelScene)
    if detailPanel._watermark then detailPanel._watermark:Hide() end
    if item.asset and item.asset > 0 then
        local sceneID = item.uiModelSceneID or 859
        local ok = pcall(function()
            detailPanel._modelScene:TransitionToModelSceneID(
                sceneID,
                CAMERA_TRANSITION_TYPE_IMMEDIATE,
                CAMERA_MODIFICATION_TYPE_DISCARD,
                true)
        end)
        if ok then
            local actor = detailPanel._modelScene:GetActorByTag("decor")
            if actor then
                actor:SetPreferModelCollisionBounds(true)
                actor:SetModelByFileID(item.asset)
            end
            detailPanel._modelScene:Show()
        end
        detailPanel._noModelText:Hide()
    else
        detailPanel._modelScene:Hide()
        detailPanel._noModelText:Show()
    end

    -- Source line (show secondary source for dual-source items)
    local srcColor = NS.SourceColors and NS.SourceColors[item.sourceType]
                  or (NS.SourceColors and NS.SourceColors.Other)
                  or { 0.6, 0.6, 0.6, 1 }
    local srcText = item.sourceType or "Unknown"
    -- Indicate dual-source
    if item.sourceType == "Quest" and item.vendorName and item.vendorName ~= "" then
        srcText = srcText .. " |cff40b0ff+ Vendor|r"
    elseif item.sourceType == "Achievement" and item.vendorName and item.vendorName ~= "" then
        srcText = srcText .. " |cff40b0ff+ Vendor|r"
    elseif item.sourceType == "Vendor" and item.vendorUnlockAchievement
        and item.vendorUnlockAchievement ~= "" then
        srcText = srcText .. " |cffe6cc80+ Achievement|r"
    end
    -- Indicate additional Treasure source (for non-Treasure primary items)
    if item.sourceType ~= "Treasure" and item.treasureX and item.treasureY then
        srcText = srcText .. " |cff60e060+ Treasure|r"
    end
    detailPanel._sourceLine:SetText(srcText)
    detailPanel._sourceLine:SetTextColor(srcColor[1], srcColor[2], srcColor[3], 1)

    -- Acquisition instructions
    local acquireText = GetAcquisitionText(item)
    if acquireText then
        detailPanel._acquireLine:SetText(acquireText)
        detailPanel._acquireLine:Show()
    else
        detailPanel._acquireLine:SetText("")
        detailPanel._acquireLine:Hide()
    end

    -- Activate acquisition hit frame for CTRL+Click interactions
    local ah = detailPanel._acquireHit
    ah._active = false
    ah._url = nil
    ah._tipTitle = nil
    ah._vendorName = nil
    ah._vendorZone = nil
    ah._vendorX = nil
    ah._vendorY = nil
    ah._achievementName = nil
    ah._dropZone = nil
    ah._dropBoss = nil
    ah._npcID = nil
    ah._giverX = nil
    ah._giverY = nil
    ah._giverZone = nil
    ah._giverName = nil
    ah._noQuestData = nil
    if item.sourceType == "Quest" and item.questID then
        ah._active = true
        ah._url = "https://www.wowhead.com/quest=" .. item.questID
        ah._tipTitle = item.sourceDetail or "Quest"
        -- Quest giver data for tooltip + CTRL+Right Click navigation
        local qcEntry = NS.QuestChains and NS.QuestChains[item.questID]
        if qcEntry then
            if qcEntry.giverName then
                ah._giverName = qcEntry.giverName
            end
            if qcEntry.giverZone then
                ah._giverZone = qcEntry.giverZone
            end
            if qcEntry.giverX and qcEntry.giverY and qcEntry.giverZone then
                ah._giverX = qcEntry.giverX
                ah._giverY = qcEntry.giverY
            end
        end
        ah:Show()
    elseif item.sourceType == "Quest" and not item.questID then
        ah._active = true
        ah._noQuestData = true
        ah._tipTitle = item.sourceDetail or "Quest"
        ah:Show()
    -- Vendor items: NPC interactions handled by _vendorHit (not _acquireHit)
    elseif (item.sourceType == "Achievement" or item.sourceType == "Prey")
        and item.achievementName and item.achievementName ~= "" then
        ah._active = true
        ah._achievementName = item.achievementName
        ah._tipTitle = item.sourceDetail or item.sourceType
        ah:Show()
    -- Vendor items with achievement prerequisite: same interaction as Achievement items
    elseif item.sourceType == "Vendor" and item.vendorUnlockAchievement
        and item.vendorUnlockAchievement ~= "" then
        ah._active = true
        ah._achievementName = item.vendorUnlockAchievement
        ah._tipTitle = item.vendorUnlockAchievement
        ah:Show()
    elseif item.sourceType == "Drop" then
        ah._active = true
        ah._tipTitle = item.sourceDetail or "Unknown"
        ah._dropZone = item.zone
        -- Only set _dropBoss for actual dungeon/raid drops (triggers EJ scan);
        -- catch-all zones like "Midnight Delves" have no boss to look up
        local isDungeonDrop = NS.CatalogData and NS.CatalogData.DungeonEntrances
            and NS.CatalogData.DungeonEntrances[item.zone]
        ah._dropBoss = isDungeonDrop and item.sourceDetail or nil
        ah._npcID = item.npcID
        ah:Show()
    else
        ah:Hide()
    end

    -- Zone line (separate clickable element — shown for Quest items WITHOUT
    -- a vendor; Quest+Vendor and Achievement+Vendor show zone inline via
    -- _vendorZonePart instead, and pure Vendor items also use _vendorZonePart)
    local zh = detailPanel._zoneHit
    zh._zoneName = nil
    local hasVendor = item.vendorName and item.vendorName ~= ""
    local showZoneLine = item.zone and item.zone ~= ""
        and item.sourceType == "Quest" and not hasVendor
    if showZoneLine then
        detailPanel._zoneLine:SetText("|cff888888Zone:|r " .. item.zone)
        detailPanel._zoneLine:Show()
        zh._zoneName = item.zone
        zh:Show()
    else
        detailPanel._zoneLine:SetText("")
        detailPanel._zoneLine:Hide()
        zh:Hide()
    end

    -- Multi-mob drop lines (pool of per-mob interactive rows)
    local dropMobsData = nil
    local visibleMobs = 0
    if item.sourceType == "Drop" then
        dropMobsData = NS.CatalogData and NS.CatalogData.DropMobs
            and NS.CatalogData.DropMobs[item.decorID]
    end
    -- Hide all mob lines from previous item
    for poolIdx = 1, #detailPanel._dropMobPool do
        detailPanel._dropMobPool[poolIdx].line:Hide()
        detailPanel._dropMobPool[poolIdx].hit._active = false
        detailPanel._dropMobPool[poolIdx].hit:Hide()
    end
    if dropMobsData and dropMobsData.mobs and #dropMobsData.mobs > 1 then
        local prevAnchor = detailPanel._acquireLine
        for mi, mob in ipairs(dropMobsData.mobs) do
            if mi > #detailPanel._dropMobPool then break end
            local pool = detailPanel._dropMobPool[mi]
            local mobName, npcID, mobX, mobY = mob[1], mob[2], mob[3], mob[4]
            local coordStr = ""
            if mobX and mobY then
                coordStr = string.format(" |cff888888(%.1f, %.1f)|r", mobX, mobY)
            end
            pool.line:SetText("  |cffcc99ff" .. mobName .. "|r" .. coordStr)
            pool.line:ClearAllPoints()
            pool.line:SetPoint("TOPLEFT", prevAnchor, "BOTTOMLEFT", 0, -2)
            pool.line:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
            pool.line:Show()
            pool.hit:ClearAllPoints()
            pool.hit:SetAllPoints(pool.line)
            pool.hit._active = true
            pool.hit._mobName = mobName
            pool.hit._npcID = npcID
            pool.hit._mobX = mobX
            pool.hit._mobY = mobY
            pool.hit._mobZone = item.zone
            pool.hit:Show()
            prevAnchor = pool.line
            visibleMobs = mi
        end
        -- Hide single _acquireHit (individual mob hits handle interactions)
        ah:Hide()
        ah._active = false
    end
    detailPanel._dropMobCount = visibleMobs

    -- Determine the last element in the acquire section (for anchoring below)
    -- For Vendor items, _acquireLine is hidden; fall back to _sourceLine
    local lastAcquireElem = detailPanel._acquireLine:IsShown()
        and detailPanel._acquireLine or detailPanel._sourceLine
    if detailPanel._zoneLine:IsShown() then
        lastAcquireElem = detailPanel._zoneLine
    end
    if visibleMobs > 0 then
        lastAcquireElem = detailPanel._dropMobPool[visibleMobs].line
    end

    -- Vendor display: "Purchase from <NPC> in <Zone>" with separate hit frames
    -- Used for: Vendor, Quest+Vendor, Achievement+Vendor, and Drop location
    local showVendorLine = false
    local showTreasureLine = false
    local treasureZoneWrapped = false
    local vendorNoteText = nil
    detailPanel._vendorHit._isDropLocation = false

    -- Reset treasure elements (re-shown if Treasure item)
    detailPanel._treasureLine:Hide()
    detailPanel._treasureHit._active = false
    detailPanel._treasureHit:Hide()
    detailPanel._treasureZonePart:Hide()
    detailPanel._treasureZoneHit._zoneName = nil
    detailPanel._treasureZoneHit:Hide()
    detailPanel._hintRowCount = nil
    detailPanel._hintLineAnchor = nil
    for i = 1, #detailPanel._treasureHintLines do
        detailPanel._treasureHintLines[i].text:Hide()
        detailPanel._treasureHintLines[i].text:SetTextColor(0.7, 0.7, 0.7, 1)
        detailPanel._treasureHintLines[i].hit:Hide()
        detailPanel._treasureHintLines[i].hit._hintText = nil
        detailPanel._treasureHintLines[i].hit._url = nil
    end
    for i = 1, #detailPanel._containerLines do
        detailPanel._containerLines[i].text:Hide()
        detailPanel._containerLines[i].hit:Hide()
        detailPanel._containerLines[i].hit._containerName = nil
    end

    -- Determine vendor name for the "Purchase from" line
    local vendorName = nil
    if item.sourceType == "Vendor" then
        -- Prefer resolved vendorName (e.g. faction vendor) over sourceDetail
        if item.vendorName and item.vendorName ~= "" then
            vendorName = item.vendorName
        elseif item.sourceDetail and item.sourceDetail ~= "" then
            vendorName = item.sourceDetail
        end
        -- Check for vendor-unlock requirements
        -- Note: vendorUnlockAchievement is shown as a proper acquisition line
        -- (same format as Achievement source items), not as a vendor note.
        if item.unlockQuestID then
            local completed = C_QuestLog and C_QuestLog.IsQuestFlaggedCompleted
                and C_QuestLog.IsQuestFlaggedCompleted(item.unlockQuestID)
            if not completed then
                if item.vendorUnlockQuest then
                    vendorNoteText = "|cff888888(requires quest: " .. item.vendorUnlockQuest .. ")|r"
                else
                    vendorNoteText = "|cff888888(requires completing a quest first)|r"
                end
            end
        elseif item.vendorUnlockQuest then
            vendorNoteText = "|cff888888(requires quest: " .. item.vendorUnlockQuest .. ")|r"
        end
    elseif item.sourceType == "Quest" and item.vendorName and item.vendorName ~= "" then
        vendorName = item.vendorName
        vendorNoteText = "|cff888888(available after completing the quest)|r"
    elseif (item.sourceType == "Achievement" or item.sourceType == "Prey")
        and item.vendorName and item.vendorName ~= "" then
        vendorName = item.vendorName
        local achCompleted = false
        local achID = NS.UI.FindAchievementIDByName
            and NS.UI.FindAchievementIDByName(item.achievementName or "")
        if achID and GetAchievementInfo then
            achCompleted = select(4, GetAchievementInfo(achID)) or false
        end
        if not achCompleted then
            vendorNoteText = "|cff888888(available after earning the achievement)|r"
        end
    end

    -- Shop prerequisite note (items originally from the In-Game Shop)
    if item.isShopItem and not vendorNoteText then
        vendorNoteText = "|cff9955CC(Requires purchase of Midnight Epic Edition upgrade)|r"
    end

    local vendorZoneWrapped = false
    if vendorName then
        -- "Purchase from <faction icon> <NPC>" (auto-width for separate hit region)
        local factionIcon = ""
        if item.factionVendors then
            local pf = GetPlayerFaction()
            factionIcon = (FACTION_ICONS[pf] or "") .. " "
        end
        local vendorNpcText = "|cff40b0ffPurchase from|r " .. factionIcon .. vendorName
        detailPanel._vendorLine:ClearAllPoints()
        detailPanel._vendorLine:SetPoint("TOPLEFT", lastAcquireElem, "BOTTOMLEFT", 0, -6)
        detailPanel._vendorLine:SetWordWrap(false)
        detailPanel._vendorLine:SetText(vendorNpcText)
        detailPanel._vendorLine:Show()
        showVendorLine = true

        -- NPC hit frame covers just the NPC text
        detailPanel._vendorHit._active = true
        detailPanel._vendorHit._vendorName = vendorName
        detailPanel._vendorHit._npcID = item.npcID
        detailPanel._vendorHit._vendorZone = item.zone
        detailPanel._vendorHit._vendorX = item.npcX
        detailPanel._vendorHit._vendorY = item.npcY
        detailPanel._vendorHit._isRotatingVendor = item.isRotatingVendor
        detailPanel._vendorHit:ClearAllPoints()
        detailPanel._vendorHit:SetAllPoints(detailPanel._vendorLine)
        detailPanel._vendorHit:Show()

        -- Zone part: " in <Zone>" inline or wrapped below
        if item.zone and item.zone ~= "" then
            local primaryZoneHex = FACTION_ZONE_COLORS[item.zone]
            local zoneDisplay = primaryZoneHex
                and ("|cff" .. primaryZoneHex .. item.zone .. "|r")
                or item.zone
            detailPanel._vendorZonePart:SetText(" |cff888888in|r " .. zoneDisplay)
            detailPanel._vendorZonePart:ClearAllPoints()
            local availableW = detailPanel._middleChild:GetWidth() - 4
            local vendorW = detailPanel._vendorLine:GetStringWidth() or 0
            local zoneW = detailPanel._vendorZonePart:GetStringWidth() or 0
            if (vendorW + zoneW) > availableW then
                detailPanel._vendorZonePart:SetText("|cff888888in|r " .. zoneDisplay)
                detailPanel._vendorZonePart:SetPoint("TOPLEFT", detailPanel._vendorLine, "BOTTOMLEFT", 0, -1)
                vendorZoneWrapped = true
            else
                detailPanel._vendorZonePart:SetPoint("LEFT", detailPanel._vendorLine, "RIGHT", 0, 0)
            end
            detailPanel._vendorZonePart:Show()
            detailPanel._vendorZoneHit._zoneName = item.zone
            detailPanel._vendorZoneHit:ClearAllPoints()
            detailPanel._vendorZoneHit:SetAllPoints(detailPanel._vendorZonePart)
            detailPanel._vendorZoneHit:Show()
        else
            detailPanel._vendorZonePart:Hide()
            detailPanel._vendorZoneHit._zoneName = nil
            detailPanel._vendorZoneHit:Hide()
        end

        -- Alternate faction vendor (shown for items in both neighborhoods)
        local altAnchor = vendorZoneWrapped
            and detailPanel._vendorZonePart or detailPanel._vendorLine
        if item.factionVendors then
            local playerFaction = GetPlayerFaction()
            local altFaction = (playerFaction == "Alliance") and "Horde" or "Alliance"
            local altFv = item.factionVendors[altFaction]
            if altFv and altFv.name and altFv.name ~= "" then
                local altZone = altFv.zone or ""
                local zoneHex = FACTION_ZONE_COLORS[altZone] or "AAAAAA"
                local altIcon = (FACTION_ICONS[altFaction] or "") .. " "
                local altText = "|cff40b0ffPurchase from|r " .. altIcon .. altFv.name
                if altZone ~= "" then
                    altText = altText .. " |cff888888in|r |cff" .. zoneHex .. altZone .. "|r"
                end
                detailPanel._altVendorLine:SetText(altText)
                detailPanel._altVendorLine:ClearAllPoints()
                detailPanel._altVendorLine:SetPoint("TOPLEFT", altAnchor, "BOTTOMLEFT", 0, -1)
                detailPanel._altVendorLine:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
                detailPanel._altVendorLine:Show()
                -- Wire up hit frame for Ctrl+Right Click → zone map
                detailPanel._altVendorHit._vendorName = altFv.name
                detailPanel._altVendorHit._npcID = altFv.npcID
                detailPanel._altVendorHit._zoneName = altZone ~= "" and altZone or nil
                detailPanel._altVendorHit:ClearAllPoints()
                detailPanel._altVendorHit:SetAllPoints(detailPanel._altVendorLine)
                detailPanel._altVendorHit:Show()
                altAnchor = detailPanel._altVendorLine
            else
                detailPanel._altVendorLine:Hide()
                detailPanel._altVendorHit:Hide()
            end
        elseif item.altVendorName and item.altVendorName ~= "" then
            -- Non-faction alt vendor (e.g. item sold by two vendors in same zone)
            local altZone = item.altVendorZone or item.zone or ""
            local altText = "|cff40b0ffPurchase from|r " .. item.altVendorName
            if altZone ~= "" then
                altText = altText .. " |cff888888in|r " .. altZone
            end
            detailPanel._altVendorLine:SetText(altText)
            detailPanel._altVendorLine:ClearAllPoints()
            detailPanel._altVendorLine:SetPoint("TOPLEFT", altAnchor, "BOTTOMLEFT", 0, -1)
            detailPanel._altVendorLine:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
            detailPanel._altVendorLine:Show()
            detailPanel._altVendorHit._vendorName = item.altVendorName
            detailPanel._altVendorHit._npcID = item.altNpcID
            detailPanel._altVendorHit._zoneName = altZone ~= "" and altZone or nil
            detailPanel._altVendorHit:ClearAllPoints()
            detailPanel._altVendorHit:SetAllPoints(detailPanel._altVendorLine)
            detailPanel._altVendorHit:Show()
            altAnchor = detailPanel._altVendorLine
        else
            detailPanel._altVendorLine:Hide()
            detailPanel._altVendorHit:Hide()
        end

        -- Note below vendor line (e.g. "available after completing the quest")
        if vendorNoteText then
            detailPanel._vendorNote:ClearAllPoints()
            detailPanel._vendorNote:SetPoint("TOPLEFT", altAnchor, "BOTTOMLEFT", 0, -2)
            detailPanel._vendorNote:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
            detailPanel._vendorNote:SetWordWrap(true)
            detailPanel._vendorNote:SetText(vendorNoteText)
            detailPanel._vendorNote:Show()
        else
            detailPanel._vendorNote:Hide()
        end

    elseif item.sourceType == "Treasure" and item.sourceDetail
            and item.sourceDetail ~= "" then
        -- Treasure interactive display: "Found in: <treasure> in <zone>"
        detailPanel._treasureLine:SetText("|cff60e060Found in:|r " .. item.sourceDetail)
        detailPanel._treasureLine:ClearAllPoints()
        detailPanel._treasureLine:SetPoint("TOPLEFT", lastAcquireElem, "BOTTOMLEFT", 0, -6)
        detailPanel._treasureLine:Show()
        showTreasureLine = true

        detailPanel._treasureHit._active = true
        detailPanel._treasureHit._treasureName = item.sourceDetail
        detailPanel._treasureHit._treasureZone = item.zone
        detailPanel._treasureHit._treasureX = item.npcX
        detailPanel._treasureHit._treasureY = item.npcY
        detailPanel._treasureHit:ClearAllPoints()
        detailPanel._treasureHit:SetAllPoints(detailPanel._treasureLine)
        detailPanel._treasureHit:Show()

        if item.zone and item.zone ~= "" then
            detailPanel._treasureZonePart:SetText(" |cff888888in|r " .. item.zone)
            detailPanel._treasureZonePart:ClearAllPoints()

            local availableW = detailPanel._middleChild:GetWidth() - 4
            local treasureW = detailPanel._treasureLine:GetStringWidth() or 0
            local zoneW = detailPanel._treasureZonePart:GetStringWidth() or 0
            if (treasureW + zoneW) > availableW then
                detailPanel._treasureZonePart:SetText("|cff888888In|r " .. item.zone)
                detailPanel._treasureZonePart:SetPoint("TOPLEFT", detailPanel._treasureLine, "BOTTOMLEFT", 0, -1)
                treasureZoneWrapped = true
            else
                detailPanel._treasureZonePart:SetPoint("LEFT", detailPanel._treasureLine, "RIGHT", 0, 0)
            end
            detailPanel._treasureZonePart:Show()
            detailPanel._treasureZoneHit._zoneName = item.zone
            detailPanel._treasureZoneHit:ClearAllPoints()
            detailPanel._treasureZoneHit:SetAllPoints(detailPanel._treasureZonePart)
            detailPanel._treasureZoneHit:Show()
        else
            detailPanel._treasureZonePart:Hide()
            detailPanel._treasureZoneHit._zoneName = nil
            detailPanel._treasureZoneHit:Hide()
        end

        -- Treasure hint line: inline segments with wrapping (e.g. "Complete scenario X from Y")
        local lastTreasureSubElem = treasureZoneWrapped
            and detailPanel._treasureZonePart or detailPanel._treasureLine
        if item.treasureHintLine and #item.treasureHintLine > 0 then
            local numSegs = math.min(#item.treasureHintLine, #detailPanel._treasureHintLines)
            local availableW = detailPanel._middleChild:GetWidth() - 4
            local accWidth = 0
            local lineAnchor = nil
            local hintRows = 1
            local prevSeg = nil
            for i = 1, numSegs do
                local seg = item.treasureHintLine[i]
                local line = detailPanel._treasureHintLines[i]
                line.text:ClearAllPoints()
                if i == 1 then
                    line.text:SetText("  " .. seg.text)
                    line.text:SetPoint("TOPLEFT", lastTreasureSubElem, "BOTTOMLEFT", 0, -2)
                    lineAnchor = line.text
                    accWidth = line.text:GetStringWidth() or 0
                else
                    line.text:SetText(seg.text)
                    local segW = line.text:GetStringWidth() or 0
                    if accWidth + segW > availableW then
                        line.text:SetText("  " .. seg.text)
                        line.text:SetPoint("TOPLEFT", lineAnchor, "BOTTOMLEFT", 0, -1)
                        lineAnchor = line.text
                        accWidth = line.text:GetStringWidth() or 0
                        hintRows = hintRows + 1
                    else
                        line.text:SetPoint("LEFT", prevSeg, "RIGHT", 0, 0)
                        accWidth = accWidth + segW
                    end
                end
                if seg.url then
                    line.text:SetTextColor(0.85, 0.85, 0.55, 1)
                    line.hit._hintText = seg.text
                    line.hit._url = seg.url
                    line.hit:ClearAllPoints()
                    line.hit:SetAllPoints(line.text)
                    line.hit:Show()
                else
                    line.text:SetTextColor(0.7, 0.7, 0.7, 1)
                end
                line.text:Show()
                prevSeg = line.text
            end
            detailPanel._hintRowCount = hintRows
            detailPanel._hintLineAnchor = lineAnchor
            lastTreasureSubElem = lineAnchor
        end

        -- Treasure container lines (multi-spawn container names)
        if item.treasureContainers then
            local numContainers = math.min(#item.treasureContainers, #detailPanel._containerLines)
            for i = 1, numContainers do
                local containerName = item.treasureContainers[i]
                local line = detailPanel._containerLines[i]
                line.text:SetText("  " .. containerName)
                line.text:ClearAllPoints()
                line.text:SetPoint("TOPLEFT", lastTreasureSubElem, "BOTTOMLEFT", 0, -2)
                line.text:Show()
                line.hit._containerName = containerName
                line.hit:ClearAllPoints()
                line.hit:SetAllPoints(line.text)
                line.hit:Show()
                lastTreasureSubElem = line.text
            end
        end

        -- Vendor line for Treasure items with a secondary vendor
        local tvName = item.vendorName and item.vendorName ~= "" and item.vendorName or nil
        local tvNpcID = item.treasureVendorNpcID
        local tvX = item.treasureVendorX
        local tvY = item.treasureVendorY
        local tvZone = item.treasureVendorZone or item.zone or ""
        if tvName then
            local treasureAnchor = lastTreasureSubElem
            local tvNpcText = "|cff40b0ffPurchase from|r " .. tvName
            detailPanel._vendorLine:ClearAllPoints()
            detailPanel._vendorLine:SetPoint("TOPLEFT", treasureAnchor, "BOTTOMLEFT", 0, -6)
            detailPanel._vendorLine:SetWordWrap(false)
            detailPanel._vendorLine:SetText(tvNpcText)
            detailPanel._vendorLine:Show()
            showVendorLine = true

            detailPanel._vendorHit._active = true
            detailPanel._vendorHit._vendorName = tvName
            detailPanel._vendorHit._npcID = tvNpcID
            detailPanel._vendorHit._vendorZone = tvZone
            detailPanel._vendorHit._vendorX = tvX
            detailPanel._vendorHit._vendorY = tvY
            detailPanel._vendorHit._isRotatingVendor = false
            detailPanel._vendorHit._isDropLocation = false
            detailPanel._vendorHit:ClearAllPoints()
            detailPanel._vendorHit:SetAllPoints(detailPanel._vendorLine)
            detailPanel._vendorHit:Show()

            if tvZone ~= "" then
                detailPanel._vendorZonePart:SetText(" |cff888888in|r " .. tvZone)
                detailPanel._vendorZonePart:ClearAllPoints()
                local tvAvailW = detailPanel._middleChild:GetWidth() - 4
                local tvVendorW = detailPanel._vendorLine:GetStringWidth() or 0
                local tvZoneW = detailPanel._vendorZonePart:GetStringWidth() or 0
                if (tvVendorW + tvZoneW) > tvAvailW then
                    detailPanel._vendorZonePart:SetText("|cff888888in|r " .. tvZone)
                    detailPanel._vendorZonePart:SetPoint("TOPLEFT", detailPanel._vendorLine, "BOTTOMLEFT", 0, -1)
                    vendorZoneWrapped = true
                else
                    detailPanel._vendorZonePart:SetPoint("LEFT", detailPanel._vendorLine, "RIGHT", 0, 0)
                end
                detailPanel._vendorZonePart:Show()
                detailPanel._vendorZoneHit._zoneName = tvZone
                detailPanel._vendorZoneHit:ClearAllPoints()
                detailPanel._vendorZoneHit:SetAllPoints(detailPanel._vendorZonePart)
                detailPanel._vendorZoneHit:Show()
            else
                detailPanel._vendorZonePart:Hide()
                detailPanel._vendorZoneHit._zoneName = nil
                detailPanel._vendorZoneHit:Hide()
            end

            -- Note: available after finding the treasure
            detailPanel._vendorNote:SetText("|cff888888(available after finding the treasure)|r")
            detailPanel._vendorNote:ClearAllPoints()
            local tvNoteAnchor = vendorZoneWrapped
                and detailPanel._vendorZonePart or detailPanel._vendorLine
            detailPanel._vendorNote:SetPoint("TOPLEFT", tvNoteAnchor, "BOTTOMLEFT", 0, -2)
            detailPanel._vendorNote:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
            detailPanel._vendorNote:Show()
        else
            detailPanel._vendorLine:Hide()
            detailPanel._vendorHit._active = false
            detailPanel._vendorHit:Hide()
            detailPanel._vendorZonePart:Hide()
            detailPanel._vendorZoneHit:Hide()
            detailPanel._vendorNote:Hide()
        end
        detailPanel._altVendorLine:Hide()
        detailPanel._altVendorHit:Hide()

    elseif item.sourceType == "Drop" and item.zone and item.zone ~= ""
            and item.zone ~= "Midnight Delves" then
        -- Drop items: "Location" link navigates to dungeon entrance
        -- (Midnight Delves items skip this — "Drops from" already covers it)
        detailPanel._vendorLine:SetText("|cff888888Location:|r " .. item.zone)
        detailPanel._vendorLine:ClearAllPoints()
        detailPanel._vendorLine:SetPoint("TOPLEFT", lastAcquireElem, "BOTTOMLEFT", 0, -6)
        detailPanel._vendorLine:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)
        detailPanel._vendorLine:Show()
        showVendorLine = true
        detailPanel._vendorHit._isDropLocation = true
        detailPanel._vendorHit._isRotatingVendor = false
        detailPanel._vendorHit._active = true
        detailPanel._vendorHit._vendorName = item.zone
        detailPanel._vendorHit._npcID = nil
        detailPanel._vendorHit._vendorZone = nil
        detailPanel._vendorHit._vendorX = nil
        detailPanel._vendorHit._vendorY = nil
        local entrance = NS.CatalogData and NS.CatalogData.DungeonEntrances
            and NS.CatalogData.DungeonEntrances[item.zone]
        detailPanel._vendorHit._entranceData = entrance
        detailPanel._vendorHit._dropZoneName = item.zone
        detailPanel._vendorHit._dropBossName = item.sourceDetail
        detailPanel._vendorHit:ClearAllPoints()
        detailPanel._vendorHit:SetAllPoints(detailPanel._vendorLine)
        detailPanel._vendorHit:Show()
        detailPanel._vendorZonePart:Hide()
        detailPanel._vendorZoneHit:Hide()
        detailPanel._altVendorLine:Hide()
        detailPanel._altVendorHit:Hide()
        detailPanel._vendorNote:Hide()
    else
        detailPanel._vendorLine:Hide()
        detailPanel._vendorHit._active = false
        detailPanel._vendorHit._isDropLocation = false
        detailPanel._vendorHit._isRotatingVendor = false
        detailPanel._vendorHit._entranceData = nil
        detailPanel._vendorHit._dropBossName = nil
        detailPanel._vendorHit._dropZoneName = nil
        detailPanel._vendorHit:Hide()
        detailPanel._vendorZonePart:Hide()
        detailPanel._vendorZoneHit._zoneName = nil
        detailPanel._vendorZoneHit:Hide()
        detailPanel._altVendorLine:Hide()
        detailPanel._altVendorHit:Hide()
        detailPanel._vendorNote:Hide()

        -- Additional Treasure source: show treasure info for non-Treasure primary items
        -- (e.g. Profession items with a treasure alternative)
        if item.treasureX and item.treasureY
                and item.treasureZone and item.treasureZone ~= "" then
            local treasureName = ""
            if item.additionalSources then
                for _, src in ipairs(item.additionalSources) do
                    if src.sourceType == "Treasure" and src.sourceDetail then
                        treasureName = src.sourceDetail
                        break
                    end
                end
            end
            if treasureName ~= "" then
                detailPanel._treasureLine:SetText("|cff60e060Also found in:|r " .. treasureName)
                detailPanel._treasureLine:ClearAllPoints()
                detailPanel._treasureLine:SetPoint("TOPLEFT", lastAcquireElem, "BOTTOMLEFT", 0, -6)
                detailPanel._treasureLine:Show()
                showTreasureLine = true

                detailPanel._treasureHit._active = true
                detailPanel._treasureHit._treasureName = treasureName
                detailPanel._treasureHit._treasureZone = item.treasureZone
                detailPanel._treasureHit._treasureX = item.treasureX
                detailPanel._treasureHit._treasureY = item.treasureY
                detailPanel._treasureHit:ClearAllPoints()
                detailPanel._treasureHit:SetAllPoints(detailPanel._treasureLine)
                detailPanel._treasureHit:Show()

                detailPanel._treasureZonePart:SetText(" |cff888888in|r " .. item.treasureZone)
                detailPanel._treasureZonePart:ClearAllPoints()
                local availableW = detailPanel._middleChild:GetWidth() - 4
                local treasureW = detailPanel._treasureLine:GetStringWidth() or 0
                local zoneW = detailPanel._treasureZonePart:GetStringWidth() or 0
                if (treasureW + zoneW) > availableW then
                    detailPanel._treasureZonePart:SetText("|cff888888In|r " .. item.treasureZone)
                    detailPanel._treasureZonePart:SetPoint("TOPLEFT", detailPanel._treasureLine, "BOTTOMLEFT", 0, -1)
                    treasureZoneWrapped = true
                else
                    detailPanel._treasureZonePart:SetPoint("LEFT", detailPanel._treasureLine, "RIGHT", 0, 0)
                end
                detailPanel._treasureZonePart:Show()
                detailPanel._treasureZoneHit._zoneName = item.treasureZone
                detailPanel._treasureZoneHit:ClearAllPoints()
                detailPanel._treasureZoneHit:SetAllPoints(detailPanel._treasureZonePart)
                detailPanel._treasureZoneHit:Show()
            end
        end
    end

    -- Determine anchor for chain area (below vendor note/alt vendor/zone/line, else last acquire)
    local chainAnchor = lastAcquireElem
    if showVendorLine then
        if detailPanel._vendorNote:IsShown() then
            chainAnchor = detailPanel._vendorNote
        elseif detailPanel._altVendorLine:IsShown() then
            chainAnchor = detailPanel._altVendorLine
        elseif vendorZoneWrapped then
            chainAnchor = detailPanel._vendorZonePart
        else
            chainAnchor = detailPanel._vendorLine
        end
    elseif showTreasureLine then
        -- Start with the basic treasure anchor
        if treasureZoneWrapped then
            chainAnchor = detailPanel._treasureZonePart
        else
            chainAnchor = detailPanel._treasureLine
        end
        -- Walk down through visible hint line / container lines
        if detailPanel._treasureHintLines[1].text:IsShown() then
            chainAnchor = detailPanel._hintLineAnchor or detailPanel._treasureHintLines[1].text
        end
        for i = #detailPanel._containerLines, 1, -1 do
            if detailPanel._containerLines[i].text:IsShown() then
                chainAnchor = detailPanel._containerLines[i].text
                break
            end
        end
    end

    -- Re-anchor pre-chain separator
    detailPanel._sepBeforeChain:ClearAllPoints()
    detailPanel._sepBeforeChain:SetPoint("TOPLEFT", chainAnchor, "BOTTOMLEFT", 0, -6)
    detailPanel._sepBeforeChain:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -8, 0)

    ---------------------------------------------------------------------------
    -- Quest Chain scroll frame population
    ---------------------------------------------------------------------------
    local chainContainer = detailPanel._chainContainer
    -- Re-anchor chain container below pre-chain separator
    chainContainer:ClearAllPoints()
    chainContainer:SetPoint("TOPLEFT", detailPanel._sepBeforeChain, "BOTTOMLEFT", 0, -6)
    chainContainer:SetPoint("RIGHT", detailPanel._middleChild, "RIGHT", -4, 0)

    local chain = nil
    if item.questID and not item.skipQuestChain then
        chain = BuildQuestChainList(item.questID, item.sourceDetail)
    elseif item.sourceType == "Quest" and item.sourceDetail and not item.skipQuestChain then
        -- No questID but has quest name — create a name-only chain entry
        chain = {{ questID = nil, name = item.sourceDetail, isDecorQuest = false }}
    end

    if chain and #chain >= 1 then
        -- Count completed quests
        local completed = 0
        local firstIncompleteIdx = nil
        for idx, entry in ipairs(chain) do
            if entry.questID and C_QuestLog.IsQuestFlaggedCompleted(entry.questID) then
                completed = completed + 1
            elseif not firstIncompleteIdx then
                firstIncompleteIdx = idx
            end
        end

        -- Header: "Quest:" for single, "Quest Chain:" for multi-step
        -- Always uses full chain length for truth
        local headerLabel = (#chain > 1) and "Quest Chain:" or "Quest:"
        local completedColor = (completed == #chain) and "|cff1eff00" or "|cffffcc00"
        chainContainer._header:SetText(string.format(
            "|cffffd200%s|r %d/%d %scompleted|r",
            headerLabel, completed, #chain, completedColor))

        -- Build display list (truncated for long chains)
        -- Each entry: {type="quest", chainIdx=N} or {type="skip", count=N}
        local displayList = {}
        local truncThreshold = detailPanel._chainTruncThreshold
        local headCount = detailPanel._chainHeadCount
        local tailCount = detailPanel._chainTailCount

        if #chain > truncThreshold then
            -- Head segment: last completed + next N incomplete quests
            local headIndices = {}
            if not firstIncompleteIdx then
                -- All completed: show chain start
                headIndices[1] = 1
            elseif firstIncompleteIdx == 1 then
                -- None completed: show first N incomplete
                for hi = 1, math.min(headCount, #chain) do
                    headIndices[#headIndices + 1] = hi
                end
            else
                -- Show last completed + next N incomplete
                headIndices[1] = firstIncompleteIdx - 1
                for hi = 0, headCount - 1 do
                    local ci = firstIncompleteIdx + hi
                    if ci <= #chain then
                        headIndices[#headIndices + 1] = ci
                    end
                end
            end

            -- Tail segment: last tailCount entries
            local tailStart = #chain - tailCount + 1
            local lastHeadIdx = headIndices[#headIndices]

            if lastHeadIdx >= tailStart then
                -- Head overlaps tail: show everything from first head to end
                for ci = headIndices[1], #chain do
                    displayList[#displayList + 1] = { type = "quest", chainIdx = ci }
                end
            else
                -- Head entries
                for _, ci in ipairs(headIndices) do
                    displayList[#displayList + 1] = { type = "quest", chainIdx = ci }
                end
                -- Skip line (omit if head is directly adjacent to tail)
                local skipCount = tailStart - lastHeadIdx - 1
                if skipCount > 0 then
                    displayList[#displayList + 1] = { type = "skip", count = skipCount }
                end
                -- Tail entries
                for ci = tailStart, #chain do
                    displayList[#displayList + 1] = { type = "quest", chainIdx = ci }
                end
            end
        else
            -- Short chain: show all entries
            for ci = 1, #chain do
                displayList[#displayList + 1] = { type = "quest", chainIdx = ci }
            end
        end

        local displayCount = #displayList

        -- Ensure enough FontString + hit frame entries exist in the pool
        local entries = chainContainer._entries
        local hitFrames = chainContainer._hitFrames
        local scrollChild = chainContainer._scrollChild
        local lineH = detailPanel._chainLineHeight

        for i = #entries + 1, displayCount do
            local fs = scrollChild:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            fs:SetPoint("TOPLEFT", 0, -(i - 1) * lineH)
            fs:SetPoint("RIGHT", scrollChild, "RIGHT", 0, 0)
            fs:SetJustifyH("LEFT")
            fs:SetWordWrap(false)
            entries[i] = fs

            -- Invisible overlay frame for hover tooltip
            local hf = CreateFrame("Frame", nil, scrollChild)
            hf:SetPoint("TOPLEFT", 0, -(i - 1) * lineH)
            hf:SetPoint("RIGHT", scrollChild, "RIGHT", 0, 0)
            hf:SetHeight(lineH)
            hf:EnableMouse(true)
            hf:SetScript("OnEnter", function(self)
                if not self._tipTitle then return end
                SetCursor("INSPECT_CURSOR")
                GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
                GameTooltip:AddLine(self._tipTitle, 1, 0.82, 0)
                if self._tipLines then
                    for _, line in ipairs(self._tipLines) do
                        GameTooltip:AddLine(line, 0.8, 0.8, 0.8, true)
                    end
                end
                GameTooltip:AddLine(" ")
                GameTooltip:AddLine("|cff55aaeeCTRL+Left Click to copy Wowhead link|r")
                if self._isDecorReward then
                    GameTooltip:AddLine("|cff55aaeeCTRL+Right Click for Blizzard preview|r")
                end
                GameTooltip:Show()
            end)
            hf:SetScript("OnLeave", function() ResetCursor(); GameTooltip:Hide() end)
            hf:SetScript("OnMouseUp", function(self, button)
                if not self._questID or not IsControlKeyDown() then return end
                if button == "LeftButton" then
                    ShowCopyableURL("https://www.wowhead.com/quest=" .. self._questID)
                elseif button == "RightButton" and self._decorID then
                    local link = NS.UI.GetItemHyperlink
                        and NS.UI.GetItemHyperlink(self._decorID)
                    if link and DressUpItemLink then
                        DressUpItemLink(link)
                    end
                end
            end)
            hitFrames[i] = hf
        end

        -- Populate entries from display list
        for slot, displayEntry in ipairs(displayList) do
            local fs = entries[slot]
            local hf = hitFrames[slot]

            if displayEntry.type == "skip" then
                -- Skip line: dimmed text, no tooltip
                local word = displayEntry.count == 1 and "quest" or "quests"
                fs:SetText("  |cff666666... " .. displayEntry.count .. " more " .. word .. " ...|r")
                fs:Show()
                hf._tipTitle = nil
                hf._tipLines = nil
                hf._questID = nil
                hf._isDecorReward = false
                hf._decorID = nil
                hf:Hide()
            else
                -- Quest entry from original chain
                local ci = displayEntry.chainIdx
                local entry = chain[ci]
                local isComplete = entry.questID
                    and C_QuestLog.IsQuestFlaggedCompleted(entry.questID)
                local prefix, color

                if not entry.questID then
                    prefix = "|cff888888?|r "
                    color = "|cff888888"
                elseif isComplete then
                    prefix = "|TInterface\\RaidFrame\\ReadyCheck-Ready:0|t "
                    color = "|cff1eff00"
                elseif ci == firstIncompleteIdx then
                    prefix = "|cffffcc00>|r "
                    color = "|cffffcc00"
                else
                    prefix = "  "
                    color = "|cff666666"
                end

                -- Highlight decor reward quests with a gold star icon
                local suffix = ""
                if entry.questID and entry.questID == item.questID then
                    suffix = " |TInterface\\GossipFrame\\ActiveQuestIcon:0|t|cffff8800 DECOR REWARD|r"
                elseif entry.isDecorQuest then
                    suffix = " |TInterface\\GossipFrame\\ActiveQuestIcon:0|t|cff888888 DECOR REWARD (other)|r"
                end

                fs:SetText(prefix .. color .. entry.name .. "|r" .. suffix)
                fs:Show()

                -- Build tooltip content for hover
                hf._tipTitle = entry.name
                local tipLines = {}
                if entry.questID then
                    tipLines[#tipLines + 1] = "|cff888888Quest ID: " .. entry.questID .. "|r"
                else
                    tipLines[#tipLines + 1] = "|cff888888Quest ID unknown|r"
                end
                if isComplete then
                    tipLines[#tipLines + 1] = "|cff1eff00Completed|r"
                elseif ci == firstIncompleteIdx then
                    tipLines[#tipLines + 1] = "|cffffcc00Next in chain|r"
                else
                    tipLines[#tipLines + 1] = "|cff666666Not yet available|r"
                end
                if entry.questID == item.questID then
                    tipLines[#tipLines + 1] = "|cffff8800Decor Reward|r"
                elseif entry.isDecorQuest then
                    tipLines[#tipLines + 1] = "|cff888888Decor Reward (other item)|r"
                end
                local qcEntry = entry.questID
                    and NS.QuestChains and NS.QuestChains[entry.questID]
                if qcEntry and qcEntry.giverName then
                    local giverLine = "Quest Giver: " .. qcEntry.giverName
                    if qcEntry.giverZone then
                        giverLine = giverLine .. " (" .. qcEntry.giverZone .. ")"
                    end
                    tipLines[#tipLines + 1] = giverLine
                end
                hf._tipLines = tipLines
                hf._questID = entry.questID
                if entry.questID and entry.questID == item.questID then
                    hf._isDecorReward = true
                    hf._decorID = item.decorID
                elseif entry.isDecorQuest and NS.QuestChains and NS.QuestChains[entry.questID] then
                    hf._isDecorReward = true
                    hf._decorID = NS.QuestChains[entry.questID].decorID
                else
                    hf._isDecorReward = false
                    hf._decorID = nil
                end
                hf:Show()
            end
        end

        -- Hide unused entries
        for i = displayCount + 1, #entries do
            entries[i]:Hide()
        end
        for i = displayCount + 1, #hitFrames do
            hitFrames[i]:Hide()
        end

        -- Size the scroll child to fit display entries (not full chain)
        local totalHeight = displayCount * lineH
        scrollChild:SetHeight(totalHeight)

        -- Size the scroll viewport (max visible lines)
        local maxVisible = detailPanel._chainMaxVisible
        local visibleCount = math.min(displayCount, maxVisible)
        local viewportH = visibleCount * lineH
        chainContainer._scrollFrame:SetHeight(viewportH)

        -- Set scroll child width to match scroll frame
        scrollChild:SetWidth(chainContainer._scrollFrame:GetWidth())

        -- Show/hide scrollbar
        local needsScroll = displayCount > maxVisible
        chainContainer._scrollTrack:SetShown(needsScroll)
        chainContainer._scrollThumb:SetShown(needsScroll)
        if needsScroll then
            -- Size thumb proportionally
            local thumbH = math.max(12, viewportH * (viewportH / totalHeight))
            chainContainer._scrollThumb:SetHeight(thumbH)
        end

        -- Reset scroll position
        chainContainer._scrollFrame:SetVerticalScroll(0)
        if needsScroll then
            chainContainer._scrollThumb:ClearAllPoints()
            chainContainer._scrollThumb:SetPoint("TOP", chainContainer._scrollTrack, "TOP")
        end

        -- Total container height: header + gap + viewport
        local containerH = chainContainer._header:GetStringHeight() + 2 + viewportH
        chainContainer:SetHeight(containerH)
        chainContainer:Show()
        detailPanel._sepBeforeChain:Show()

    else
        -- No chain: hide chain + its separators
        chainContainer:Hide()
        detailPanel._sepBeforeChain:Hide()
    end

    ---------------------------------------------------------------------------
    -- INFO section: infoRow1, infoRow2, collected banner
    ---------------------------------------------------------------------------
    -- infoRow1: Rarity | Indoor | Outdoor | Faction
    detailPanel._infoRow1._cells[1]._text:SetText("|cff" .. colorHex .. qName .. "|r")
    if item.isAllowedIndoors then
        detailPanel._infoRow1._cells[2]._text:SetText(
            "|TInterface\\Icons\\Spell_Nature_RemoveCurse:14:14|t |cffaaee88Indoor|r")
    else
        detailPanel._infoRow1._cells[2]._text:SetText("|cff444444Indoor|r")
    end
    if item.isAllowedOutdoors then
        detailPanel._infoRow1._cells[3]._text:SetText(
            "|TInterface\\Icons\\INV_Misc_Map_01:14:14|t |cffaaee88Outdoor|r")
    else
        detailPanel._infoRow1._cells[3]._text:SetText("|cff444444Outdoor|r")
    end
    if item.faction and item.faction ~= "" then
        if item.faction == "alliance" then
            detailPanel._infoRow1._cells[4]._text:SetText(
                "|TInterface\\Icons\\Inv_Misc_Tournaments_banner_Human:14:14|t |cff3399ffAlliance|r")
        elseif item.faction == "horde" then
            detailPanel._infoRow1._cells[4]._text:SetText(
                "|TInterface\\Icons\\Inv_Misc_Tournaments_banner_Orc:14:14|t |cffff3333Horde|r")
        else
            detailPanel._infoRow1._cells[4]._text:SetText(
                "|TInterface\\RAIDFRAME\\ReadyCheck-Waiting:14:14|t |cffffffccNeutral|r")
        end
    else
        detailPanel._infoRow1._cells[4]._text:SetText("")
    end

    -- infoRow2: Storage | Placed
    -- Blizzard UI: "In Storage" = quantity + remainingRedeemable
    -- (remainingRedeemable = lazily-instantiated items awaiting redemption)
    local housingInfo = GetHousingInfo(item.decorID)
    local stored = housingInfo and (housingInfo.quantity or 0) or 0
    local placed = housingInfo and (housingInfo.numPlaced or 0) or 0
    local redeemable = housingInfo and (housingInfo.remainingRedeemable or 0) or 0
    local inStorage = stored + redeemable
    detailPanel._infoRow2._cells[1]._text:SetText(inStorage .. " Storage")
    detailPanel._infoRow2._cells[1]._text:SetTextColor(0.75, 0.75, 0.75, 1)
    detailPanel._infoRow2._cells[2]._text:SetText(placed .. " Placed")
    detailPanel._infoRow2._cells[2]._text:SetTextColor(0.75, 0.75, 0.75, 1)
    -- Cell 3: Vendor cost — per-cost segments with individual tooltips
    local cell3 = detailPanel._infoRow2._cells[3]
    cell3._text:SetText("")
    -- Reset all cost segments
    for i = 1, #detailPanel._costSegs do
        detailPanel._costSegs[i].text:Hide()
        detailPanel._costSegs[i].hit:Hide()
        detailPanel._costSegs[i].hit._currencyID = nil
    end
    for i = 1, #detailPanel._costSeps do
        detailPanel._costSeps[i]:Hide()
    end

    if item.vendorCosts and #item.vendorCosts > 0 then
        local numCosts = math.min(#item.vendorCosts, #detailPanel._costSegs)

        -- Build each segment text and measure width
        local segWidths = {}
        local totalW = 0
        for idx = 1, numCosts do
            local cost = item.vendorCosts[idx]
            local seg = detailPanel._costSegs[idx]
            local costStr
            if cost.currencyID == 0 then
                costStr = cost.amount .. " |TINTERFACE\\MONEYFRAME\\UI-GOLDICON.BLP:14:14|t"
            else
                local iconPath = cost.iconPath
                    or (NS.CatalogData.CurrencyInfo and NS.CatalogData.CurrencyInfo[cost.currencyID])
                    or "Interface\\Icons\\INV_Misc_QuestionMark"
                costStr = cost.amount .. " |T" .. iconPath .. ":14:14|t"
            end
            seg.text:SetText(costStr)
            seg.hit._currencyID = cost.currencyID
            local w = seg.text:GetStringWidth() or 40
            segWidths[idx] = w
            totalW = totalW + w
        end

        -- Account for separator widths
        local sepW = 0
        if numCosts > 1 then
            sepW = (detailPanel._costSeps[1]:GetStringWidth() or 8) + 4
            totalW = totalW + (numCosts - 1) * sepW
        end

        -- Position segments centered within cell3
        local cellW = cell3:GetWidth()
        local curX = (cellW - totalW) / 2
        for idx = 1, numCosts do
            if idx > 1 then
                local sep = detailPanel._costSeps[idx - 1]
                sep:ClearAllPoints()
                sep:SetPoint("LEFT", cell3, "LEFT", curX + 2, 0)
                sep:Show()
                curX = curX + sepW
            end
            local seg = detailPanel._costSegs[idx]
            seg.text:ClearAllPoints()
            seg.text:SetPoint("LEFT", cell3, "LEFT", curX, 0)
            seg.text:Show()
            seg.hit:ClearAllPoints()
            seg.hit:SetAllPoints(seg.text)
            seg.hit:Show()
            curX = curX + segWidths[idx]
        end
    end

    -- Populate DETAILS flyout content and show the trigger label
    PopulateDetailsFlyout(item)
    detailPanel._detailsLabel:Show()
    detailPanel._detailsHit:Show()

    -- Collected status banner
    local hasItem = (stored + placed + redeemable) > 0
    if hasItem then
        detailPanel._collectedBanner._text:SetText(
            "|TInterface\\RaidFrame\\ReadyCheck-Ready:16:16|t |cff1eff00COLLECTED|r")
        detailPanel._collectedBanner:SetBackdropColor(0.08, 0.25, 0.08, 0.6)
    else
        detailPanel._collectedBanner._text:SetText(
            "|TInterface\\RaidFrame\\ReadyCheck-NotReady:16:16|t |cffff4444NOT COLLECTED|r")
        detailPanel._collectedBanner:SetBackdropColor(0.25, 0.05, 0.05, 0.6)
    end

    -- Hide Wowhead link (use CTRL+Left Click on quest name instead)
    detailPanel._wowheadBox:Hide()
    detailPanel._wowheadLabel:Hide()

    -- Hide popups on item change
    detailPanel._crossPopup:Hide()
    detailPanel._crossPopup._pendingMapID = nil
    detailPanel._zidormiPopup:Hide()
    detailPanel._zidormiPopup._pendingMapID = nil
    if detailPanel._detailsFlyout then
        if detailPanel._detailsFlyout._hideTimer then
            detailPanel._detailsFlyout._hideTimer:Cancel()
            detailPanel._detailsFlyout._hideTimer = nil
        end
        detailPanel._detailsFlyout:Hide()
        detailPanel._detailsLabel:SetTextColor(0.4, 0.7, 1.0, 0.7)
    end
    GameTooltip:Hide()

    -- Reset "Open Map" fallback state and dungeon entrance data
    detailPanel._waypointBtn._openMapID = nil
    detailPanel._waypointBtn._openMapDrop = nil
    detailPanel._waypointBtn._entranceData = nil
    detailPanel._waypointBtn._hubData = nil
    detailPanel._waypointBtn._treasureCoords = nil
    detailPanel._altNavBtn._navData = nil
    detailPanel._altNavBtn:Hide()
    detailPanel._openMapBtn._dungeonMapID = nil
    detailPanel._openMapBtn._bossName = nil
    detailPanel._openMapBtn._dungeonName = nil
    detailPanel._openMapBtn:Hide()
    -- Note: _waypointStatus anchoring is handled by the top-down stacking
    -- loop at the end of this function. Do NOT re-anchor here — it causes
    -- circular dependencies on subsequent item loads.

    -- Achievement button: show only when achievement is NOT completed
    local showAchBtn = false
    -- Resolve achievement name: primary achievementName for Achievement/Prey,
    -- or vendorUnlockAchievement for Vendor items with achievement prerequisite
    local achBtnName = nil
    if (item.sourceType == "Achievement" or item.sourceType == "Prey")
        and item.achievementName and item.achievementName ~= "" then
        achBtnName = item.achievementName
    elseif item.vendorUnlockAchievement and item.vendorUnlockAchievement ~= "" then
        achBtnName = item.vendorUnlockAchievement
    end
    if achBtnName then
        local achID = NS.UI.FindAchievementIDByName
            and NS.UI.FindAchievementIDByName(achBtnName)
        local achCompleted = achID and GetAchievementInfo
            and select(4, GetAchievementInfo(achID)) or false
        if not achCompleted then
            showAchBtn = true
            TruncateButtonText(detailPanel._achieveBtn, "Open Achievement: " .. achBtnName)
            detailPanel._achieveBtn:Show()
        else
            detailPanel._achieveBtn:Hide()
        end
    else
        detailPanel._achieveBtn:Hide()
    end

    -- Waypoint button logic
    -- Dual-source awareness: Quest+Vendor, Achievement+Vendor
    local hasChain = item.questID
        and not item.skipQuestChain
        and BuildQuestChainList(item.questID, item.sourceDetail)
    -- Only Quest-source items use chain status for navigation;
    -- other items (e.g. Vendor with quest reward) show the chain for info only
    local chainAffectsNav = hasChain and item.sourceType == "Quest"
    local firstIncomplete = chainAffectsNav and GetFirstIncompleteQuestID(item.questID)
    local chainComplete = chainAffectsNav and not firstIncomplete

    local isQuestVendor = item.sourceType == "Quest"
        and item.vendorName and item.vendorName ~= ""
    local isAchVendor = (item.sourceType == "Achievement" or item.sourceType == "Prey")
        and item.vendorName and item.vendorName ~= ""

    local hasCoords = item.npcX and item.npcY and item.zone and item.zone ~= ""
    local hasTreasureCoords = item.treasureX and item.treasureY
        and item.treasureZone and item.treasureZone ~= ""

    -- Check if coords can actually be pinned on the world map. Instance/dungeon
    -- zones have their own coordinate space that doesn't translate to the parent
    -- zone, so treat them as "no usable coords" for button display purposes.
    -- Exception: zones with a portal redirect (e.g. Dalaran, neighborhoods) get
    -- treated as having coords because the click handler redirects to the portal room.
    local isInstanceZone = false
    local portalRedirect = GetPortalRedirect(item.zone)
    if hasCoords then
        local _, coordsTrusted = ResolveNavigableMap(item.zone)
        if not coordsTrusted then
            if portalRedirect then
                -- Portal redirect available — keep hasCoords true so the
                -- Navigate button works; the click handler handles the redirect
                isInstanceZone = false
            else
                isInstanceZone = true
                hasCoords = false  -- can't pin on world map
            end
        end
    elseif not hasCoords and NEIGHBORHOOD_FACTIONS[item.zone] and portalRedirect then
        -- Neighborhood zones without vendor coords: the click handler will use
        -- the portal redirect coords directly (capital portal room).
        -- Only for neighborhoods — other portal-redirected zones (e.g. Dalaran)
        -- may genuinely have no coords.
        hasCoords = true
    end

    -- Opposite-faction neighborhood: vendor is in a faction-specific housing zone
    -- that the player cannot access (e.g. Alliance player, Razorwind Shores item)
    local neighborhoodFaction = NEIGHBORHOOD_FACTIONS[item.zone]
    local isOppositeFaction = false
    if neighborhoodFaction then
        local pf = GetPlayerFaction()
        isOppositeFaction = pf and pf ~= neighborhoodFaction
    end

    -- Reset tooltip overlays
    detailPanel._waypointStatusHit:Hide()
    detailPanel._waypointStatusHit._tooltip = nil
    detailPanel._waypointStatusHit._portalCoords = nil
    detailPanel._covenantStatus:Hide()
    detailPanel._covenantStatusHit:Hide()
    detailPanel._covenantStatusHit._tooltip = nil
    detailPanel._covenantStatusHit._enclave = nil
    detailPanel._waypointBtnHit:Hide()
    detailPanel._waypointBtnHit._tooltip = nil
    detailPanel._arcHint:Hide()

    -- Arcantina: flag for showing teleport hint (checked at end of button logic)
    -- Check item zone AND quest giver zone for the first incomplete quest
    local showArcHint = (item.zone == "Arcantina")
    if not showArcHint and firstIncomplete then
        local ge = NS.QuestChains and NS.QuestChains[firstIncomplete]
        if ge and ge.giverZone == "Arcantina" then
            showArcHint = true
        end
    end

    -- Check achievement completion for Achievement+Vendor items
    local achCompleted = false
    if isAchVendor and item.achievementName and item.achievementName ~= "" then
        local achID = NS.UI.FindAchievementIDByName
            and NS.UI.FindAchievementIDByName(item.achievementName)
        if achID and GetAchievementInfo then
            achCompleted = select(4, GetAchievementInfo(achID)) or false
        end
    end

    -- Check if player is currently inside a neighborhood zone
    local isInNeighborhood = false
    if NEIGHBORHOOD_FACTIONS[item.zone] then
        local playerMapID = C_Map and C_Map.GetBestMapForUnit
            and C_Map.GetBestMapForUnit("player")
        isInNeighborhood = (playerMapID == 2352 or playerMapID == 2351)
    end

    -- Treasure+Vendor: item has both treasure coords and a secondary vendor
    local isTreasureVendor = item.sourceType == "Treasure"
        and item.vendorName and item.vendorName ~= ""
        and item.treasureVendorX and item.treasureVendorY

    if isOppositeFaction then
        -- Item is in a faction-specific neighborhood the player cannot access
        local pf = GetPlayerFaction() or "your faction"
        detailPanel._waypointBtn:Disable()
        detailPanel._waypointBtn:SetText("Not available for " .. pf)
        detailPanel._waypointStatus:Hide()

    elseif item.isRotatingVendor then
        -- Neutral vendor that rotates between neighborhoods
        detailPanel._waypointBtn:Disable()
        detailPanel._waypointBtn:SetText("Vendor rotates between Neighborhoods")
        detailPanel._waypointStatus:Hide()

    elseif NEIGHBORHOOD_FACTIONS[item.zone] and hasCoords and isInNeighborhood then
        -- Player IS in a neighborhood — navigate directly to vendor coords
        detailPanel._waypointBtn:Enable()
        local vendorLabel = (item.vendorName and item.vendorName ~= "")
            and item.vendorName or item.sourceDetail
        TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. vendorLabel .. ")")
        detailPanel._waypointStatus:Hide()

    elseif isAchVendor then
        -- Achievement + Vendor: button always points to vendor
        if achCompleted and hasCoords then
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. item.vendorName .. ")")
            local portalText = GetPortalStatusText(item.zone, portalRedirect)
            if portalText then
                detailPanel._waypointStatus:SetText(portalText)
                detailPanel._waypointStatus:Show()
            else
                detailPanel._waypointStatus:Hide()
            end
        elseif achCompleted and isInstanceZone then
            -- Vendor is inside a sub-zone (e.g. class hall) — navigate to parent zone
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (nearby " .. item.vendorName .. ")")
            detailPanel._waypointStatus:SetText("Vendor inside: " .. item.zone)
            detailPanel._waypointStatus:Show()
        elseif achCompleted and not hasCoords then
            if item.zone and item.zone ~= "" then
                local zoneMapID = GetZoneMapID(item.zone)
                if zoneMapID then
                    detailPanel._waypointBtn:Enable()
                    TruncateButtonText(detailPanel._waypointBtn, "Open Map: " .. item.zone)
                    detailPanel._waypointBtn._openMapID = zoneMapID
                    detailPanel._waypointStatus:Hide()
                else
                    detailPanel._waypointBtn:Disable()
                    TruncateButtonText(detailPanel._waypointBtn, "Vendor: " .. item.vendorName)
                    detailPanel._waypointStatus:SetText("Located in " .. item.zone)
                    detailPanel._waypointStatus:Show()
                end
            else
                detailPanel._waypointBtn:Disable()
                TruncateButtonText(detailPanel._waypointBtn, "Vendor: " .. item.vendorName)
                detailPanel._waypointStatus:SetText("No coordinates available")
                detailPanel._waypointStatus:Show()
            end
        else
            -- Achievement not completed
            detailPanel._waypointBtn:Disable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. item.vendorName .. ")")
            detailPanel._waypointStatus:SetText(
                "Complete \"" .. (item.achievementName or "achievement") .. "\" first")
            detailPanel._waypointStatus:Show()
        end

    elseif isTreasureVendor then
        -- Treasure + Vendor: always show both Navigate buttons
        -- Primary button → Navigate to treasure
        if hasCoords then
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. item.sourceDetail .. ")")
        else
            detailPanel._waypointBtn:Disable()
            detailPanel._waypointBtn:SetText("Navigate (Treasure)")
            detailPanel._waypointStatus:SetText("No treasure coordinates available")
            detailPanel._waypointStatus:Show()
        end
        -- Secondary button → Navigate to vendor
        local tvZone = item.treasureVendorZone or ""
        local tvMapID, tvCoordsTrusted
        if tvZone ~= "" then
            tvMapID, tvCoordsTrusted = ResolveNavigableMap(tvZone)
        end
        if tvMapID and tvCoordsTrusted and item.treasureVendorX and item.treasureVendorY then
            detailPanel._altNavBtn:Enable()
            TruncateButtonText(detailPanel._altNavBtn, "Navigate (" .. item.vendorName .. ")")
            detailPanel._altNavBtn._navData = {
                label = item.vendorName,
                x = item.treasureVendorX,
                y = item.treasureVendorY,
                zone = tvZone,
                mapID = tvMapID,
            }
            detailPanel._altNavBtn:Show()
        end

    elseif isQuestVendor then
        -- Quest + Vendor: chain logic determines button behavior
        if chainComplete and hasCoords then
            -- Chain done → navigate to vendor
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. item.vendorName .. ")")
            detailPanel._waypointStatus:Hide()
        elseif chainComplete and isInstanceZone then
            -- Vendor is inside a sub-zone (e.g. class hall) — navigate to parent zone
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (nearby " .. item.vendorName .. ")")
            detailPanel._waypointStatus:SetText("Vendor inside: " .. item.zone)
            detailPanel._waypointStatus:Show()
        elseif chainComplete and not hasCoords then
            if item.zone and item.zone ~= "" then
                local zoneMapID = GetZoneMapID(item.zone)
                if zoneMapID then
                    detailPanel._waypointBtn:Enable()
                    TruncateButtonText(detailPanel._waypointBtn, "Open Map: " .. item.zone)
                    detailPanel._waypointBtn._openMapID = zoneMapID
                    detailPanel._waypointStatus:Hide()
                else
                    detailPanel._waypointBtn:Disable()
                    TruncateButtonText(detailPanel._waypointBtn, "Vendor: " .. item.vendorName)
                    detailPanel._waypointStatus:SetText("Located in " .. item.zone)
                    detailPanel._waypointStatus:Show()
                end
            else
                detailPanel._waypointBtn:Disable()
                TruncateButtonText(detailPanel._waypointBtn, "Vendor: " .. item.vendorName)
                detailPanel._waypointStatus:SetText("No coordinates available")
                detailPanel._waypointStatus:Show()
            end
        elseif firstIncomplete then
            -- Chain not complete → navigate to quest giver or gray out vendor
            local giverEntry = NS.QuestChains and NS.QuestChains[firstIncomplete]
            local targetName = giverEntry and giverEntry.name
                or item.sourceDetail
                or C_QuestLog.GetTitleForQuestID(firstIncomplete)
                or ("Quest " .. firstIncomplete)
            local hasGiverCoords = giverEntry and giverEntry.giverX
                and giverEntry.giverY and giverEntry.giverZone
            if hasGiverCoords then
                detailPanel._waypointBtn:Enable()
                TruncateButtonText(detailPanel._waypointBtn, "Navigate (Next: " .. targetName .. ")")
                detailPanel._waypointStatus:Hide()
                detailPanel._waypointBtnHit:Hide()
            else
                -- No quest giver coords — gray out vendor button, show quest requirement.
                -- Don't fall back to vendor coords (item.npcX/npcY) — the vendor is the
                -- destination AFTER the quest chain, not before.
                detailPanel._waypointBtn:Disable()
                TruncateButtonText(detailPanel._waypointBtn,
                    "Navigate (" .. item.vendorName .. ")")
                detailPanel._waypointStatus:SetText(
                    "Complete \"" .. targetName .. "\" first")
                detailPanel._waypointStatus:Show()
                -- Show tooltip on hover over disabled button
                detailPanel._waypointBtnHit._tooltip =
                    "You first need to complete quest \""
                    .. targetName
                    .. "\" before being able to purchase from "
                    .. item.vendorName .. "."
                detailPanel._waypointBtnHit:Show()
            end
        else
            -- No chain data (questID missing) — navigate to vendor if coords available
            if hasCoords then
                detailPanel._waypointBtn:Enable()
                TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. item.vendorName .. ")")
                local portalText = GetPortalStatusText(item.zone, portalRedirect)
                if portalText then
                    detailPanel._waypointStatus:SetText(portalText)
                    detailPanel._waypointStatus:Show()
                else
                    detailPanel._waypointStatus:Hide()
                end
            else
                detailPanel._waypointBtn:Disable()
                TruncateButtonText(detailPanel._waypointBtn, "Vendor: " .. item.vendorName)
                detailPanel._waypointStatus:SetText("No coordinates available")
                detailPanel._waypointStatus:Show()
            end
        end

    elseif chainAffectsNav then
        -- Pure Quest with chain (no vendor)
        if firstIncomplete then
            local giverEntry = NS.QuestChains and NS.QuestChains[firstIncomplete]
            local targetName = giverEntry and giverEntry.name or ""
            local hasGiverCoords = giverEntry and giverEntry.giverX
                and giverEntry.giverY and giverEntry.giverZone
            if hasGiverCoords then
                detailPanel._waypointBtn:Enable()
                if firstIncomplete ~= item.questID then
                    TruncateButtonText(detailPanel._waypointBtn, "Navigate (Next: " .. targetName .. ")")
                else
                    detailPanel._waypointBtn:SetText("Navigate (Quest Giver)")
                end
                detailPanel._waypointStatus:Hide()
            elseif hasCoords then
                -- Fall back to item coords (vendor/NPC)
                detailPanel._waypointBtn:Enable()
                detailPanel._waypointBtn:SetText("Navigate (approx.)")
                detailPanel._waypointStatus:SetText("Quest giver location unknown")
                detailPanel._waypointStatus:Show()
            else
                detailPanel._waypointBtn:Disable()
                TruncateButtonText(detailPanel._waypointBtn, "Next: " .. targetName)
                detailPanel._waypointStatus:SetText("No coordinates for quest giver")
                detailPanel._waypointStatus:Show()
            end
        else
            -- Chain complete → decor already collected
            detailPanel._waypointBtn:Disable()
            detailPanel._waypointBtn:SetText("Chain Complete")
            detailPanel._waypointStatus:SetText("You already collected this decor!")
            detailPanel._waypointStatus:Show()
        end

    elseif hasCoords then
        -- Standard items with coordinates
        detailPanel._waypointBtn:Enable()
        local navVendor = (item.vendorName and item.vendorName ~= "")
            and item.vendorName
            or (item.sourceDetail and item.sourceDetail ~= "" and item.sourceDetail)
            or nil
        if navVendor then
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (" .. navVendor .. ")")
        else
            detailPanel._waypointBtn:SetText("Navigate")
        end
        local portalText = GetPortalStatusText(item.zone, portalRedirect)
        if portalText then
            detailPanel._waypointStatus:SetText(portalText)
            detailPanel._waypointStatus:Show()
        else
            detailPanel._waypointStatus:Hide()
        end
        -- Also has additional Treasure source → show second Navigate button
        if hasTreasureCoords then
            local treasureName = ""
            if item.additionalSources then
                for _, src in ipairs(item.additionalSources) do
                    if src.sourceType == "Treasure" and src.sourceDetail then
                        treasureName = src.sourceDetail
                        break
                    end
                end
            end
            detailPanel._altNavBtn:Enable()
            TruncateButtonText(detailPanel._altNavBtn, "Navigate (Treasure)")
            detailPanel._altNavBtn._navData = {
                label = treasureName ~= "" and treasureName or "Treasure",
                x = item.treasureX,
                y = item.treasureY,
                zone = item.treasureZone,
                mapID = item.treasureMapID,
            }
            detailPanel._altNavBtn:Show()
        end

    elseif isInstanceZone then
        -- Instance/sub-zone: check for a known entrance in the outdoor world
        local instanceEntrance = NS.CatalogData and NS.CatalogData.DungeonEntrances
            and NS.CatalogData.DungeonEntrances[item.zone]
        if instanceEntrance then
            -- Navigate to the outdoor entrance (like dungeon drops)
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn,
                "Navigate (" .. item.zone .. ")")
            detailPanel._waypointBtn._entranceData = instanceEntrance
            detailPanel._waypointStatus:SetText(
                "Entrance in " .. (instanceEntrance.zone or ""))
            detailPanel._waypointStatus:Show()
        else
            -- No entrance data — navigate to parent zone
            detailPanel._waypointBtn:Enable()
            local vendorLabel = item.vendorName or item.sourceDetail or item.name
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (nearby " .. vendorLabel .. ")")
            detailPanel._waypointStatus:SetText("Vendor inside: " .. item.zone)
            detailPanel._waypointStatus:Show()
        end

    elseif hasTreasureCoords then
        -- Treasure is an additional source with known coords
        local treasureName = ""
        if item.additionalSources then
            for _, src in ipairs(item.additionalSources) do
                if src.sourceType == "Treasure" and src.sourceDetail then
                    treasureName = src.sourceDetail
                    break
                end
            end
        end
        detailPanel._waypointBtn:Enable()
        if treasureName ~= "" then
            TruncateButtonText(detailPanel._waypointBtn, "Navigate (Treasure)")
        else
            detailPanel._waypointBtn:SetText("Navigate (Treasure)")
        end
        detailPanel._waypointBtn._treasureCoords = {
            x = item.treasureX,
            y = item.treasureY,
            zone = item.treasureZone,
            mapID = item.treasureMapID,
        }
        if treasureName ~= "" then
            detailPanel._waypointStatus:SetText(treasureName .. " in " .. item.treasureZone)
        else
            detailPanel._waypointStatus:SetText("Treasure in " .. item.treasureZone)
        end
        detailPanel._waypointStatus:Show()

    else
        -- No coordinates available
        -- Check for multi-mob hub navigation (outdoor rares with a gathering point)
        local hubData = dropMobsData and dropMobsData.hub
        -- Check for dungeon entrance data (Drop items from dungeons/raids)
        local entrance = item.sourceType == "Drop"
            and item.zone and item.zone ~= ""
            and NS.CatalogData and NS.CatalogData.DungeonEntrances
            and NS.CatalogData.DungeonEntrances[item.zone]

        if hubData and hubData.zone then
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn,
                "Navigate (" .. (hubData.label or item.zone) .. ")")
            detailPanel._waypointBtn._hubData = hubData
            detailPanel._waypointStatus:Hide()

        elseif entrance then
            -- Two-button navigation: Navigate to entrance + Open dungeon map
            -- Navigate button → waypoint to dungeon entrance on outdoor map
            detailPanel._waypointBtn:Enable()
            TruncateButtonText(detailPanel._waypointBtn,
                "Navigate (" .. item.zone .. ")")
            detailPanel._waypointBtn._entranceData = entrance

            -- Open Map button → opens dungeon interior to boss floor via EJ
            local bossLabel = item.sourceDetail or ""
            local mapLabel = item.zone
            if bossLabel ~= "" then
                mapLabel = item.zone .. " (" .. bossLabel .. ")"
            end
            detailPanel._openMapBtn._dungeonMapID = GetZoneMapID(item.zone) -- fallback
            detailPanel._openMapBtn._bossName = item.sourceDetail
            detailPanel._openMapBtn._dungeonName = item.zone
            TruncateButtonText(detailPanel._openMapBtn, "Open Map: " .. mapLabel)
            detailPanel._openMapBtn:Show()
            detailPanel._waypointStatus:Hide()

        elseif item.sourceType == "Profession" then
            detailPanel._waypointBtn:Disable()
            detailPanel._waypointBtn:SetText("Crafted Item")
            detailPanel._waypointStatus:SetText("Learn the recipe to craft this")
            detailPanel._waypointStatus:Show()
        elseif item.zone and item.zone ~= "" then
            local zoneMapID = GetZoneMapID(item.zone)
            if zoneMapID then
                detailPanel._waypointBtn:Enable()
                TruncateButtonText(detailPanel._waypointBtn, "Open Map: " .. item.zone)
                detailPanel._waypointBtn._openMapID = zoneMapID
                -- Only set _openMapDrop for actual dungeon/raid drops (triggers
                -- expensive EJ boss-floor scan); skip for catch-all zones
                local hasDungeonEntrance = NS.CatalogData
                    and NS.CatalogData.DungeonEntrances
                    and NS.CatalogData.DungeonEntrances[item.zone]
                detailPanel._waypointBtn._openMapDrop = (item.sourceType == "Drop"
                    and hasDungeonEntrance) and item.sourceDetail or nil
                detailPanel._waypointStatus:Hide()
            else
                detailPanel._waypointBtn:Disable()
                detailPanel._waypointBtn:SetText("No Coordinates")
                detailPanel._waypointStatus:SetText("Located in " .. item.zone)
                detailPanel._waypointStatus:Show()
            end
        else
            detailPanel._waypointBtn:Disable()
            detailPanel._waypointBtn:SetText("Location Unknown")
            detailPanel._waypointStatus:Hide()
        end
    end

    -- Arcantina override: always disable button, show cross-region warning,
    -- and append quest/achievement requirement when applicable.
    if showArcHint then
        detailPanel._waypointBtn:Disable()
        detailPanel._waypointBtn:SetText("Teleport to Arcantina")
        -- Cross-region warning (same format as other zones)
        local arcCrossLine =
            "|TInterface\\DialogFrame\\UI-Dialog-Icon-AlertNew:0|t "
            .. "|cffFFCC00Different travel region|r — travel to "
            .. "|cff00CCFFArcantina|r"
        -- Prepend quest/achievement requirement if applicable
        local arcStatusMsg
        if firstIncomplete and not chainComplete then
            local ge = NS.QuestChains and NS.QuestChains[firstIncomplete]
            local reqName = ge and ge.name
                or item.sourceDetail
                or C_QuestLog.GetTitleForQuestID(firstIncomplete)
                or ("Quest " .. firstIncomplete)
            arcStatusMsg = "Complete \"" .. reqName .. "\" first\n\n"
                .. arcCrossLine
            detailPanel._waypointBtnHit._tooltip =
                "You first need to complete quest \""
                .. reqName
                .. "\" before being able to purchase from "
                .. item.vendorName .. "."
            detailPanel._waypointBtnHit:Show()
        elseif isAchVendor and not achCompleted then
            arcStatusMsg = "Complete \""
                .. (item.achievementName or "achievement")
                .. "\" first\n\n" .. arcCrossLine
            detailPanel._waypointBtnHit._tooltip =
                "You first need to complete \""
                .. (item.achievementName or "achievement")
                .. "\" before being able to purchase from "
                .. item.vendorName .. "."
            detailPanel._waypointBtnHit:Show()
        else
            arcStatusMsg = arcCrossLine
        end
        detailPanel._waypointStatus:SetText(arcStatusMsg)
        detailPanel._waypointStatus:Show()
        detailPanel._waypointStatusHit._tooltip =
            "Arcantina is a special zone that cannot be navigated to "
            .. "with the map pin system. Use your Personal Key to "
            .. "the Arcantina toy to teleport there."
        detailPanel._waypointStatusHit:Show()
    end

    -- Cross-continent note: if the button is enabled, check whether the
    -- destination is on a different continent and show a hint if so.
    if detailPanel._waypointBtn:IsEnabled() then
        -- Determine destination zone (mirrors OnClick logic)
        local destZone = item.zone
        -- For portal-redirected zones, the actual destination is the portal room
        if portalRedirect then
            destZone = portalRedirect.zone
        -- For dungeon entrance navigation, use the entrance's outdoor zone
        elseif detailPanel._waypointBtn._entranceData then
            destZone = detailPanel._waypointBtn._entranceData.zone
        elseif firstIncomplete and not chainComplete then
            local giverEntry = NS.QuestChains and NS.QuestChains[firstIncomplete]
            if giverEntry and giverEntry.giverZone then
                destZone = giverEntry.giverZone
            end
        end
        local destMapID = destZone and GetZoneMapID(destZone)
        if destMapID then
            local isCross, continentName = GetCrossContinentInfo(destMapID)
            if isCross then
                local expName = NS.ContinentExpansion and NS.ContinentExpansion[continentName]
                local expHex = expName and NS.ExpansionColors and NS.ExpansionColors[expName] or "00CCFF"
                local statusMsg =
                    "|TInterface\\DialogFrame\\UI-Dialog-Icon-AlertNew:0|t "
                    .. "|cffFFCC00Different travel region|r — travel to "
                    .. "|cff" .. expHex .. continentName .. "|r"
                local crossPortalText = GetPortalStatusText(item.zone, portalRedirect)
                if crossPortalText then
                    statusMsg = statusMsg .. "\n" .. crossPortalText
                end
                detailPanel._waypointStatus:SetText(statusMsg)
                detailPanel._waypointStatus:Show()
                detailPanel._waypointStatusHit._tooltip =
                    "Blizzard's navigation arrow only works when you are "
                    .. "in the same travel region as the destination. "
                    .. "The map pin has been placed, but you need to "
                    .. "travel to " .. continentName .. "."
                -- Store portal coords for Ctrl+Right-Click navigation
                if portalRedirect then
                    detailPanel._waypointStatusHit._portalCoords = {
                        zone = portalRedirect.zone,
                        x = portalRedirect.x,
                        y = portalRedirect.y,
                    }
                end
                detailPanel._waypointStatusHit:Show()
            end
        end
    end

    -- Covenant requirement warning (Shadowlands covenant-locked vendors)
    if item.covenantID then
        local cov = COVENANT_DATA[item.covenantID]
        local covName = cov and cov.name or ("Covenant " .. item.covenantID)
        local covColor = cov and cov.color or "FFFFFF"
        local covMsg =
            "|TInterface\\DialogFrame\\UI-Dialog-Icon-AlertNew:0|t "
            .. "|cffFFCC00Requires |cff" .. covColor .. covName
            .. "|r|cffFFCC00 covenant|r — change at "
            .. "|cff00CCFFEnclave in Oribos|r"
        detailPanel._covenantStatus:SetText(covMsg)
        detailPanel._covenantStatus:Show()
        detailPanel._covenantStatusHit._tooltip =
            "This vendor requires the " .. covName
            .. " covenant to be active.\n"
            .. "Visit the Enclave in Oribos to switch covenants."
        detailPanel._covenantStatusHit._enclave = ORIBOS_ENCLAVE
        detailPanel._covenantStatusHit:ClearAllPoints()
        detailPanel._covenantStatusHit:SetAllPoints(detailPanel._covenantStatus)
        detailPanel._covenantStatusHit:Show()
    end

    -- Zidormi button: show when destination zone is a known Zidormi zone
    local destZoneForZidormi = item.zone
    if firstIncomplete and not chainComplete then
        local ge = NS.QuestChains and NS.QuestChains[firstIncomplete]
        if ge and ge.giverZone then destZoneForZidormi = ge.giverZone end
    end
    local zInfo = destZoneForZidormi
        and NS.ZidormiZones and NS.ZidormiZones[destZoneForZidormi]
    if zInfo then
        detailPanel._zidormiBtn._zidormiInfo = zInfo
        TruncateButtonText(detailPanel._zidormiBtn,
            "|TInterface\\Icons\\Spell_Holy_BorrowedTime:14:14:0:0|t "
            .. "Visit Zidormi (" .. zInfo.npcZone .. ")")
        detailPanel._zidormiBtn:Show()
        -- Zidormi timeline warning in NAVIGATION section
        detailPanel._zidormiWarning:SetText(
            "|TInterface\\Icons\\Spell_Holy_BorrowedTime:16:16|t "
            .. "|cffffcc00Zidormi timeline change might be required!|r")
        detailPanel._zidormiWarning:Show()
    else
        detailPanel._zidormiBtn._zidormiInfo = nil
        detailPanel._zidormiBtn:Hide()
        detailPanel._zidormiWarning:Hide()
    end

    ---------------------------------------------------------------------------
    -- Top-down navigation stacking in bottomSection
    ---------------------------------------------------------------------------
    local showZidormi = zInfo ~= nil
    local navStack = {}

    -- Optional text elements first
    if detailPanel._zidormiWarning:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._zidormiWarning, gap = -4, isBtn = false }
    end
    if detailPanel._waypointStatus:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._waypointStatus, gap = -4, isBtn = false }
    end
    if detailPanel._covenantStatus:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._covenantStatus, gap = -4, isBtn = false }
    end
    if showArcHint then
        detailPanel._arcHint:Show()
        navStack[#navStack + 1] = { elem = detailPanel._arcHint, gap = -2, isBtn = false }
    end
    if detailPanel._wowheadLabel:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._wowheadLabel, gap = -4, isBtn = false }
        navStack[#navStack + 1] = { elem = detailPanel._wowheadBox, gap = -2, isBtn = false }
    end
    -- Buttons: Navigate, Alt Navigate, Open Map (Drop), Zidormi, Achievement
    navStack[#navStack + 1] = { elem = detailPanel._waypointBtn, gap = -6, isBtn = true }
    if detailPanel._altNavBtn:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._altNavBtn, gap = -4, isBtn = true }
    end
    if detailPanel._openMapBtn:IsShown() then
        navStack[#navStack + 1] = { elem = detailPanel._openMapBtn, gap = -4, isBtn = true }
    end
    if showZidormi then
        navStack[#navStack + 1] = { elem = detailPanel._zidormiBtn, gap = -4, isBtn = true }
    end
    if showAchBtn then
        navStack[#navStack + 1] = { elem = detailPanel._achieveBtn, gap = -4, isBtn = true }
    end

    -- Anchor each element top-down from navHeader using cumulative Y offset.
    -- All elements anchor to navHeader (not to each other) to prevent
    -- TOPLEFT/TOPRIGHT offsets from accumulating and shrinking buttons.
    local cumY = 0
    for _, entry in ipairs(navStack) do
        entry.elem:ClearAllPoints()
        cumY = cumY + entry.gap
        if entry.isBtn then
            entry.elem:SetPoint("TOPLEFT", detailPanel._navHeader, "BOTTOMLEFT", 4, cumY)
            entry.elem:SetPoint("TOPRIGHT", detailPanel._navHeader, "BOTTOMRIGHT", -4, cumY)
        else
            entry.elem:SetPoint("TOPLEFT", detailPanel._navHeader, "BOTTOMLEFT", 0, cumY)
            entry.elem:SetPoint("RIGHT", detailPanel._bottomSection, "RIGHT", -4, 0)
        end
        if entry.elem.GetStringHeight then
            cumY = cumY - math.max(14, entry.elem:GetStringHeight() or 14)
        else
            cumY = cumY - (entry.elem:GetHeight() or 0)
        end
    end

    ---------------------------------------------------------------------------
    -- Recalculate bottom section height
    ---------------------------------------------------------------------------
    local bottomH = 2 + 22 -- top padding + navHeader height
    for _, entry in ipairs(navStack) do
        bottomH = bottomH + math.abs(entry.gap)
        if entry.elem.GetStringHeight then
            -- FontString
            bottomH = bottomH + math.max(14, entry.elem:GetStringHeight() or 14)
        else
            -- Frame / Button / EditBox
            bottomH = bottomH + (entry.elem:GetHeight() or 0)
        end
    end
    bottomH = bottomH + 8 -- bottom padding
    detailPanel._bottomSection:SetHeight(bottomH)

    -- Reset source scroll position and scrollbar
    detailPanel._middleScroll:SetVerticalScroll(0)
    if detailPanel._UpdateSourceScrollBar then
        detailPanel._UpdateSourceScrollBar()
    end

    -- Defer height calc by one frame (font metrics need a render pass)
    C_Timer.After(0, function()
        if not detailPanel or not detailPanel._middleChild then return end

        -- Recalculate fixed info section height
        local infoH = 6 -- top padding (gap before _itemName)
        infoH = infoH + (detailPanel._itemName:GetStringHeight() or 16)
        infoH = infoH + 6 + 22 -- gap + infoHeader
        infoH = infoH + 2 + 18 -- gap + infoRow1
        infoH = infoH + 2 + 18 -- gap + infoRow2
        infoH = infoH + 4 + 22 -- gap + collectedBanner
        infoH = infoH + 2 -- bottom padding
        detailPanel._infoSection:SetHeight(infoH)

        -- Recalculate scrollable source content height
        local sourceH = 4 + 22 -- top padding + sourceHeader
        sourceH = sourceH + 4 + (detailPanel._sourceLine:GetStringHeight() or 16)
        if detailPanel._acquireLine:IsShown() then
            sourceH = sourceH + 4 + (detailPanel._acquireLine:GetStringHeight() or 14)
        end
        for poolIdx = 1, (detailPanel._dropMobCount or 0) do
            local mobLine = detailPanel._dropMobPool[poolIdx].line
            if mobLine:IsShown() then
                sourceH = sourceH + 2 + (mobLine:GetStringHeight() or 14)
            end
        end
        if detailPanel._zoneLine:IsShown() then
            sourceH = sourceH + 2 + (detailPanel._zoneLine:GetStringHeight() or 14)
        end
        if detailPanel._vendorLine:IsShown() then
            sourceH = sourceH + 6 + (detailPanel._vendorLine:GetStringHeight() or 14)
        end
        if detailPanel._vendorZonePart:IsShown() and vendorZoneWrapped then
            sourceH = sourceH + 1 + (detailPanel._vendorZonePart:GetStringHeight() or 14)
        end
        if detailPanel._vendorNote:IsShown() then
            sourceH = sourceH + 2 + (detailPanel._vendorNote:GetStringHeight() or 14)
        end
        if detailPanel._altVendorLine:IsShown() then
            sourceH = sourceH + 1 + (detailPanel._altVendorLine:GetStringHeight() or 14)
        end
        if detailPanel._treasureLine:IsShown() then
            sourceH = sourceH + 6 + (detailPanel._treasureLine:GetStringHeight() or 14)
        end
        if detailPanel._treasureZonePart:IsShown() and treasureZoneWrapped then
            sourceH = sourceH + 1 + (detailPanel._treasureZonePart:GetStringHeight() or 14)
        end
        if detailPanel._treasureHintLines[1].text:IsShown() then
            local lineH = detailPanel._treasureHintLines[1].text:GetStringHeight() or 12
            local rows = detailPanel._hintRowCount or 1
            sourceH = sourceH + 2 + lineH * rows + (rows - 1) * 1
        end
        for i = 1, #detailPanel._containerLines do
            if detailPanel._containerLines[i].text:IsShown() then
                sourceH = sourceH + 2 + (detailPanel._containerLines[i].text:GetStringHeight() or 12)
            end
        end
        if detailPanel._sepBeforeChain:IsShown() then
            sourceH = sourceH + 6 + 1 -- gap + sepBeforeChain height
        end
        if detailPanel._chainContainer:IsShown() then
            sourceH = sourceH + 6 + (detailPanel._chainContainer:GetHeight() or 0)
        end
        sourceH = sourceH + 12 -- bottom padding
        detailPanel._middleChild:SetHeight(sourceH)

        -- Update scrollbar visibility and thumb size
        if detailPanel._UpdateSourceScrollBar then
            detailPanel._UpdateSourceScrollBar()
        end
    end)
end

--- Re-render the currently displayed item (called on collection/quest/achievement events).
function NS.UI.RefreshDetailPanel()
    if detailPanel and detailPanel._currentItem then
        NS.UI.CatalogDetail_ShowItem(detailPanel._currentItem)
    end
end
