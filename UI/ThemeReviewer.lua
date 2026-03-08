-------------------------------------------------------------------------------
-- HearthAndSeek: ThemeReviewer.lua
-- DEV_MODE-only UI for reviewing and annotating item aesthetic assignments.
-- Saves annotations to HearthAndSeekDB.themeAnnotations for pipeline export.
--
-- Usage (via /hs debug review):
--   /hs debug review              Review uncertain items
--   /hs debug review <aesthetic>  Review items in a specific aesthetic
--   /hs debug review all          Review ALL themed items
-------------------------------------------------------------------------------
local _, NS = ...

if not NS.DEV_MODE then return end

NS.UI = NS.UI or {}

-------------------------------------------------------------------------------
-- Constants
-------------------------------------------------------------------------------
local AESTHETIC_THEME_IDS = NS.CatalogData.ThemeGroupThemes
    and NS.CatalogData.ThemeGroupThemes[2] or {}

local THEME_ID_BY_NAME = {}
local AESTHETIC_NAMES = {}
for _, tid in ipairs(AESTHETIC_THEME_IDS) do
    local name = NS.CatalogData.ThemeNames[tid]
    if name then
        THEME_ID_BY_NAME[name] = tid
        AESTHETIC_NAMES[#AESTHETIC_NAMES + 1] = name
    end
end
table.sort(AESTHETIC_NAMES)

local THEME_COLORS = {
    ["Arcane Sanctum"]     = { 0.60, 0.40, 1.00 },
    ["Cottage Hearth"]     = { 0.80, 0.60, 0.20 },
    ["Enchanted Grove"]    = { 0.40, 0.80, 0.60 },
    ["Feast Hall"]         = { 0.80, 0.40, 0.00 },
    ["Fel Forge"]          = { 0.20, 0.80, 0.20 },
    ["Haunted Manor"]      = { 0.60, 0.40, 0.60 },
    ["Royal Court"]        = { 1.00, 0.80, 0.20 },
    ["Sacred Temple"]      = { 1.00, 1.00, 0.40 },
    ["Scholar's Archive"]  = { 0.80, 0.60, 0.40 },
    ["Seafarer's Haven"]   = { 0.80, 0.40, 0.20 },
    ["Tinker's Workshop"]  = { 0.40, 0.67, 0.80 },
    ["Void Rift"]          = { 0.60, 0.20, 0.80 },
    ["War Room"]           = { 0.70, 0.55, 0.35 },
    ["Wild Frontier"]      = { 0.60, 0.40, 0.20 },
    ["Wild Garden"]        = { 0.20, 0.60, 0.20 },
}

local PANEL_WIDTH    = 460
local PANEL_HEIGHT   = 620
local MODEL_HEIGHT   = 180
local NUM_AESTHETICS = #AESTHETIC_NAMES
local CHECK_COLS     = 2
local CHECK_ROWS     = math.ceil(NUM_AESTHETICS / CHECK_COLS)
local CHECK_ROW_H    = 26
local COL_WIDTH      = 210

-------------------------------------------------------------------------------
-- State
-------------------------------------------------------------------------------
local frame
local items = {}
local currentIndex = 0
local unsavedCount = 0

-------------------------------------------------------------------------------
-- Annotations persistence
-------------------------------------------------------------------------------
local function GetAnnotations()
    if not NS.db then return {} end
    NS.db.themeAnnotations = NS.db.themeAnnotations or {}
    return NS.db.themeAnnotations
end

local function SaveAnnotation(decorID, selectedAesthetics, algorithmScores)
    local annotations = GetAnnotations()
    local item = NS.CatalogData.Items[decorID]
    annotations[tostring(decorID)] = {
        name = item and item.name or "Unknown",
        aesthetics = selectedAesthetics,
        algorithmScores = algorithmScores,
    }
    unsavedCount = unsavedCount + 1
end

-------------------------------------------------------------------------------
-- Item discovery
-------------------------------------------------------------------------------
local function GetItemAesthetics(item)
    if not item.themeIDs or not item.themeScores then return nil end
    local aesthetics = {}
    for _, tid in ipairs(item.themeIDs) do
        local name = NS.CatalogData.ThemeNames[tid]
        if name and THEME_ID_BY_NAME[name] then
            aesthetics[name] = item.themeScores[tid] or 0
        end
    end
    if not next(aesthetics) then return nil end
    return aesthetics
end

local function IsUncertain(aesthetics)
    local flags = 0
    for _, score in pairs(aesthetics) do
        if score >= 25 and score <= 40 then
            flags = flags + 1
            break
        end
    end
    local count = 0
    for _ in pairs(aesthetics) do count = count + 1 end
    if count >= 4 then flags = flags + 1 end
    if count >= 2 then
        local sorted = {}
        for _, score in pairs(aesthetics) do sorted[#sorted + 1] = score end
        table.sort(sorted, function(a, b) return a > b end)
        if sorted[1] - sorted[2] <= 15 then flags = flags + 1 end
    end
    local top100 = 0
    for _, score in pairs(aesthetics) do
        if score == 100 then top100 = top100 + 1 end
    end
    if top100 == 1 and count >= 3 then flags = flags + 1 end
    return flags >= 3
end

local function BuildItemList(filterTheme, showAll)
    local result = {}
    local annotations = GetAnnotations()
    local itemsDB = NS.CatalogData and NS.CatalogData.Items or {}
    for decorID, item in pairs(itemsDB) do
        local aesthetics = GetItemAesthetics(item)
        if aesthetics then
            -- Skip already-annotated items in uncertain mode
            local dominated = not showAll and not filterTheme
                and annotations[tostring(decorID)]
            if dominated then
                -- skip
            elseif filterTheme and not aesthetics[filterTheme] then
                -- skip
            elseif showAll or IsUncertain(aesthetics) then
                result[#result + 1] = {
                    decorID = decorID,
                    aesthetics = aesthetics,
                    item = item,
                }
            end
        end
    end
    table.sort(result, function(a, b)
        return (a.item.name or "") < (b.item.name or "")
    end)
    return result
end

-------------------------------------------------------------------------------
-- 3D Model Viewer
-------------------------------------------------------------------------------
local function UpdateModelViewer(item)
    if not frame._modelScene then return end
    if item.asset and item.asset > 0 then
        local sceneID = item.uiModelSceneID or 859
        local ok = pcall(function()
            frame._modelScene:TransitionToModelSceneID(
                sceneID,
                CAMERA_TRANSITION_TYPE_IMMEDIATE,
                CAMERA_MODIFICATION_TYPE_DISCARD,
                true)
        end)
        if ok then
            local actor = frame._modelScene:GetActorByTag("decor")
            if actor then
                actor:SetPreferModelCollisionBounds(true)
                actor:SetModelByFileID(item.asset)
            end
            frame._modelScene:Show()
        end
        frame._noModelText:Hide()
    else
        frame._modelScene:Hide()
        frame._noModelText:Show()
    end
end

-------------------------------------------------------------------------------
-- UI Creation
-------------------------------------------------------------------------------
local function CreateReviewerFrame()
    if frame then return frame end

    local PAD = 12
    local GAP = 6

    frame = CreateFrame("Frame", "HearthAndSeekThemeReviewer", UIParent,
        "BackdropTemplate")
    frame:SetSize(PANEL_WIDTH, PANEL_HEIGHT)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:SetFrameStrata("DIALOG")
    frame:SetClampedToScreen(true)

    frame:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 16,
        insets = { left = 4, right = 4, top = 4, bottom = 4 },
    })
    frame:SetBackdropColor(0.08, 0.08, 0.10, 0.95)
    frame:SetBackdropBorderColor(0.6, 0.6, 0.6, 0.8)

    -- Title
    local titleText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    titleText:SetPoint("TOPLEFT", PAD, -PAD)
    titleText:SetText("Theme Reviewer")
    frame._titleText = titleText

    local closeBtn = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
    closeBtn:SetPoint("TOPRIGHT", -2, -2)
    closeBtn:SetScript("OnClick", function() frame:Hide() end)

    -- Progress
    local progressLabel = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    progressLabel:SetPoint("TOPLEFT", titleText, "BOTTOMLEFT", 0, -2)
    progressLabel:SetTextColor(0.50, 0.50, 0.50)
    frame._progressLabel = progressLabel

    -- Item info card
    local infoFrame = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    infoFrame:SetPoint("TOPLEFT", progressLabel, "BOTTOMLEFT", 0, -GAP)
    infoFrame:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    infoFrame:SetHeight(38)
    infoFrame:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 12,
        insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    infoFrame:SetBackdropColor(0.12, 0.12, 0.15, 0.9)

    local nameLabel = infoFrame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    nameLabel:SetPoint("TOPLEFT", 8, -5)
    nameLabel:SetPoint("RIGHT", infoFrame, "RIGHT", -8, 0)
    nameLabel:SetJustifyH("LEFT")
    nameLabel:SetWordWrap(false)
    frame._nameLabel = nameLabel

    local sourceLabel = infoFrame:CreateFontString(nil, "OVERLAY",
        "GameFontHighlightSmall")
    sourceLabel:SetPoint("TOPLEFT", nameLabel, "BOTTOMLEFT", 0, -1)
    sourceLabel:SetPoint("RIGHT", infoFrame, "RIGHT", -8, 0)
    sourceLabel:SetJustifyH("LEFT")
    sourceLabel:SetTextColor(0.50, 0.50, 0.50)
    sourceLabel:SetWordWrap(false)
    frame._sourceLabel = sourceLabel

    -- 3D Model
    local modelBg = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    modelBg:SetPoint("TOPLEFT", infoFrame, "BOTTOMLEFT", 0, -GAP)
    modelBg:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    modelBg:SetHeight(MODEL_HEIGHT)
    modelBg:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        tile = true, tileSize = 16, edgeSize = 12,
        insets = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    modelBg:SetBackdropColor(0.04, 0.04, 0.06, 0.95)

    local modelScene = CreateFrame("ModelScene", nil, modelBg,
        "PanningModelSceneMixinTemplate")
    modelScene:SetPoint("TOPLEFT", 4, -4)
    modelScene:SetPoint("BOTTOMRIGHT", -4, 4)
    frame._modelScene = modelScene

    local dragLastX, dragLastY = nil, nil
    modelScene:HookScript("OnMouseDown", function(_, button)
        if button == "LeftButton" then
            dragLastX, dragLastY = GetCursorPosition()
        end
    end)
    modelScene:HookScript("OnMouseUp", function(_, button)
        if button == "LeftButton" then
            dragLastX, dragLastY = nil, nil
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

    local controls = CreateFrame("Frame", nil, modelBg,
        "ModelSceneControlFrameTemplate")
    controls:SetPoint("BOTTOM", modelBg, "BOTTOM", 0, 6)
    controls:SetModelScene(modelScene)

    local noModelText = modelBg:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    noModelText:SetPoint("CENTER")
    noModelText:SetText("No 3D model")
    noModelText:SetTextColor(0.4, 0.4, 0.4)
    noModelText:Hide()
    frame._noModelText = noModelText

    -- Aesthetics header + status
    local selectHeader = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    selectHeader:SetPoint("TOPLEFT", modelBg, "BOTTOMLEFT", 0, -GAP - 2)
    selectHeader:SetText("|cffFFD100Select Aesthetics|r")
    frame._selectHeader = selectHeader

    local statusLabel = frame:CreateFontString(nil, "OVERLAY",
        "GameFontHighlightSmall")
    statusLabel:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    statusLabel:SetPoint("TOP", selectHeader, "TOP", 0, 0)
    statusLabel:SetJustifyH("RIGHT")
    frame._statusLabel = statusLabel

    -- Checkbox slots (dynamically reordered per item by score)
    local checkFrame = CreateFrame("Frame", nil, frame)
    checkFrame:SetPoint("TOPLEFT", selectHeader, "BOTTOMLEFT", 0, -4)
    checkFrame:SetPoint("RIGHT", frame, "RIGHT", -PAD, 0)
    checkFrame:SetHeight(CHECK_ROWS * CHECK_ROW_H)
    frame._checkFrame = checkFrame

    frame._checkSlots = {}
    for i = 1, NUM_AESTHETICS do
        local row = math.floor((i - 1) / CHECK_COLS)
        local col = (i - 1) % CHECK_COLS

        local slot = CreateFrame("CheckButton", nil, checkFrame,
            "UICheckButtonTemplate")
        slot:SetSize(26, 26)
        slot:SetPoint("TOPLEFT", checkFrame, "TOPLEFT",
            col * COL_WIDTH, -row * CHECK_ROW_H)

        local label = slot:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
        label:SetPoint("LEFT", slot, "RIGHT", 2, 0)
        label:SetJustifyH("LEFT")
        slot._label = label

        local scoreText = slot:CreateFontString(nil, "OVERLAY",
            "GameFontHighlightSmall")
        scoreText:SetPoint("LEFT", label, "RIGHT", 4, 0)
        slot._scoreText = scoreText

        slot._themeName = ""
        frame._checkSlots[i] = slot
    end

    -- ─── Bottom section (anchored from bottom up) ───────────────────────

    -- Shortcut bar
    local shortcutBar = CreateFrame("Frame", nil, frame, "BackdropTemplate")
    shortcutBar:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", PAD, PAD)
    shortcutBar:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, PAD)
    shortcutBar:SetHeight(22)
    shortcutBar:SetBackdrop({
        bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
        tile = true, tileSize = 16,
        insets = { left = 2, right = 2, top = 2, bottom = 2 },
    })
    shortcutBar:SetBackdropColor(0.15, 0.15, 0.18, 0.9)

    local shortcutText = shortcutBar:CreateFontString(nil, "OVERLAY",
        "GameFontHighlightSmall")
    shortcutText:SetPoint("CENTER")
    shortcutText:SetText(
        "|cffFFD100[Enter]|r Confirm   " ..
        "|cffFFD100[A]|r Accept   " ..
        "|cffFFD100[\226\134\144\226\134\146]|r Nav   " ..
        "|cffFFD100[Tab]|r Skip   " ..
        "|cffFFD100[Esc]|r Close"
    )
    shortcutText:SetTextColor(0.7, 0.7, 0.7)

    -- Action row (above shortcut bar)
    local actionY = PAD + 22 + 4

    local saveExitBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    saveExitBtn:SetSize(85, 24)
    saveExitBtn:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, actionY)
    saveExitBtn:SetText("Save & Exit")
    saveExitBtn:SetScript("OnClick", function()
        NS.Utils.PrintMessage(string.format(
            "Theme reviewer: %d annotations in SavedVariables. /reload to persist.",
            unsavedCount))
        frame:Hide()
    end)

    local confirmBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    confirmBtn:SetSize(75, 24)
    confirmBtn:SetPoint("RIGHT", saveExitBtn, "LEFT", -4, 0)
    confirmBtn:SetText("Confirm")
    confirmBtn:SetScript("OnClick", function() NS.UI.ThemeReviewer_Confirm() end)

    local acceptBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    acceptBtn:SetSize(85, 24)
    acceptBtn:SetPoint("RIGHT", confirmBtn, "LEFT", -4, 0)
    acceptBtn:SetText("Accept As-Is")
    acceptBtn:SetScript("OnClick", function()
        NS.UI.ThemeReviewer_AcceptCurrent()
    end)

    local clearBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    clearBtn:SetSize(55, 24)
    clearBtn:SetPoint("RIGHT", acceptBtn, "LEFT", -4, 0)
    clearBtn:SetText("Clear")
    clearBtn:SetScript("OnClick", function()
        for i = 1, NUM_AESTHETICS do
            frame._checkSlots[i]:SetChecked(false)
        end
    end)

    -- Nav row (above action row)
    local navY = actionY + 24 + 4

    local prevBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    prevBtn:SetSize(55, 24)
    prevBtn:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", PAD, navY)
    prevBtn:SetText("< Prev")
    prevBtn:SetScript("OnClick", function() NS.UI.ThemeReviewer_Prev() end)

    local nextBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    nextBtn:SetSize(55, 24)
    nextBtn:SetPoint("LEFT", prevBtn, "RIGHT", 4, 0)
    nextBtn:SetText("Next >")
    nextBtn:SetScript("OnClick", function() NS.UI.ThemeReviewer_Next() end)

    local skipBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    skipBtn:SetSize(55, 24)
    skipBtn:SetPoint("LEFT", nextBtn, "RIGHT", 4, 0)
    skipBtn:SetText("Skip >>")
    skipBtn:SetScript("OnClick", function()
        NS.UI.ThemeReviewer_SkipToUnreviewed()
    end)

    local previewBtn = CreateFrame("Button", nil, frame, "UIPanelButtonTemplate")
    previewBtn:SetSize(105, 24)
    previewBtn:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -PAD, navY)
    previewBtn:SetText("Show in Catalog")
    previewBtn:SetScript("OnClick", function()
        if currentIndex < 1 or currentIndex > #items then return end
        local entry = items[currentIndex]
        local catFrame = _G["HearthAndSeekCatalogFrame"]
        if catFrame and not catFrame:IsShown() then
            if NS.UI.ToggleCatalog then NS.UI.ToggleCatalog() end
        elseif not catFrame and NS.UI.ToggleCatalog then
            NS.UI.ToggleCatalog()
        end
        if NS.UI.CatalogDetail_ShowItem then
            NS.UI.CatalogDetail_ShowItem(entry.item)
        end
    end)

    -- Keyboard shortcuts
    frame:EnableKeyboard(true)
    frame:SetScript("OnKeyDown", function(self, key)
        if key == "ENTER" then
            NS.UI.ThemeReviewer_Confirm()
        elseif key == "A" and not IsModifierKeyDown() then
            NS.UI.ThemeReviewer_AcceptCurrent()
        elseif key == "LEFT" then
            NS.UI.ThemeReviewer_Prev()
        elseif key == "RIGHT" then
            NS.UI.ThemeReviewer_Next()
        elseif key == "TAB" then
            NS.UI.ThemeReviewer_SkipToUnreviewed()
        elseif key == "ESCAPE" then
            self:Hide()
        end
    end)

    return frame
end

-------------------------------------------------------------------------------
-- Display current item
-------------------------------------------------------------------------------
local function ShowItem()
    if #items == 0 then
        frame._nameLabel:SetText("No items to review!")
        frame._sourceLabel:SetText("")
        frame._progressLabel:SetText("")
        frame._statusLabel:SetText("")
        for i = 1, NUM_AESTHETICS do
            frame._checkSlots[i]:SetChecked(false)
            frame._checkSlots[i]:Hide()
        end
        frame._modelScene:Hide()
        frame._noModelText:Show()
        return
    end

    local entry = items[currentIndex]
    local item = entry.item
    local aesthetics = entry.aesthetics
    local annotations = GetAnnotations()

    -- Count reviewed
    local reviewed = 0
    for _, e in ipairs(items) do
        if annotations[tostring(e.decorID)] then
            reviewed = reviewed + 1
        end
    end

    frame._progressLabel:SetText(string.format(
        "%d / %d   |cff888888Done:|r %d   |cff888888Pending:|r %d",
        currentIndex, #items, reviewed, unsavedCount))

    -- Item info
    frame._nameLabel:SetText(string.format("%s  |cff666666#%d|r",
        item.name or "Unknown", entry.decorID))

    local sourceParts = {}
    if item.sourceType and item.sourceType ~= "" then
        sourceParts[#sourceParts + 1] = item.sourceType
    end
    if item.sourceDetail and item.sourceDetail ~= "" then
        sourceParts[#sourceParts + 1] = item.sourceDetail
    end
    if item.zone and item.zone ~= "" then
        sourceParts[#sourceParts + 1] = item.zone
    end
    frame._sourceLabel:SetText(table.concat(sourceParts, " \226\128\148 "))

    -- 3D model
    UpdateModelViewer(item)

    -- Sort aesthetics: scored first (desc by score), then unscored (alpha)
    local sortedNames = {}
    for _, name in ipairs(AESTHETIC_NAMES) do
        sortedNames[#sortedNames + 1] = {
            name = name,
            score = aesthetics[name] or 0,
        }
    end
    table.sort(sortedNames, function(a, b)
        if a.score > 0 and b.score > 0 then return a.score > b.score end
        if a.score > 0 then return true end
        if b.score > 0 then return false end
        return a.name < b.name
    end)

    -- Annotation state
    local didStr = tostring(entry.decorID)
    local savedChecks = nil
    if annotations[didStr] then
        savedChecks = {}
        local annAesthetics = annotations[didStr].aesthetics or {}
        for _, name in ipairs(annAesthetics) do savedChecks[name] = true end
        frame._statusLabel:SetText("|cff00cc00Reviewed|r")
    else
        frame._statusLabel:SetText("|cffaaaaaaNot reviewed|r")
    end

    -- Update checkbox slots (reordered by score)
    for i, data in ipairs(sortedNames) do
        local slot = frame._checkSlots[i]
        slot._themeName = data.name
        local color = THEME_COLORS[data.name] or { 0.5, 0.5, 0.5 }

        if data.score > 0 then
            slot._label:SetTextColor(color[1], color[2], color[3])
            slot._label:SetText(data.name)
            slot._scoreText:SetText("|cff999999" .. data.score .. "|r")
            slot._scoreText:Show()
        else
            slot._label:SetTextColor(0.40, 0.40, 0.40)
            slot._label:SetText(data.name)
            slot._scoreText:Hide()
        end

        if savedChecks then
            slot:SetChecked(savedChecks[data.name] or false)
        else
            slot:SetChecked(data.score > 0)
        end
        slot:Show()
    end
end

-------------------------------------------------------------------------------
-- Navigation
-------------------------------------------------------------------------------
function NS.UI.ThemeReviewer_Next()
    if currentIndex < #items then
        currentIndex = currentIndex + 1
        ShowItem()
    end
end

function NS.UI.ThemeReviewer_Prev()
    if currentIndex > 1 then
        currentIndex = currentIndex - 1
        ShowItem()
    end
end

function NS.UI.ThemeReviewer_SkipToUnreviewed()
    local annotations = GetAnnotations()
    for i = currentIndex + 1, #items do
        if not annotations[tostring(items[i].decorID)] then
            currentIndex = i
            ShowItem()
            return
        end
    end
    for i = 1, currentIndex do
        if not annotations[tostring(items[i].decorID)] then
            currentIndex = i
            ShowItem()
            return
        end
    end
    NS.Utils.PrintMessage("All items have been reviewed!")
end

function NS.UI.ThemeReviewer_Confirm()
    if currentIndex < 1 or currentIndex > #items then return end
    local entry = items[currentIndex]
    local selected = {}
    for i = 1, NUM_AESTHETICS do
        local slot = frame._checkSlots[i]
        if slot:GetChecked() then
            selected[#selected + 1] = slot._themeName
        end
    end
    local algoScores = {}
    for name, score in pairs(entry.aesthetics) do
        algoScores[name] = score
    end
    SaveAnnotation(entry.decorID, selected, algoScores)
    frame._statusLabel:SetText("|cff00cc00Confirmed|r")
    NS.UI.ThemeReviewer_Next()
end

function NS.UI.ThemeReviewer_AcceptCurrent()
    if currentIndex < 1 or currentIndex > #items then return end
    local entry = items[currentIndex]
    local selected = {}
    for name in pairs(entry.aesthetics) do
        selected[#selected + 1] = name
    end
    table.sort(selected)
    local algoScores = {}
    for name, score in pairs(entry.aesthetics) do
        algoScores[name] = score
    end
    SaveAnnotation(entry.decorID, selected, algoScores)
    frame._statusLabel:SetText("|cff00cc00Accepted|r")
    NS.UI.ThemeReviewer_Next()
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function NS.UI.OpenThemeReviewer(filterArg)
    local filterTheme = nil
    local showAll = false

    if filterArg and filterArg ~= "" then
        local lower = filterArg:lower()
        if lower == "all" then
            showAll = true
        else
            for _, name in ipairs(AESTHETIC_NAMES) do
                if name:lower():find(lower, 1, true) == 1 then
                    filterTheme = name
                    break
                end
            end
            if not filterTheme then
                NS.Utils.PrintMessage("Unknown aesthetic: " .. filterArg)
                NS.Utils.PrintMessage("Available: " ..
                    table.concat(AESTHETIC_NAMES, ", "))
                return
            end
        end
    end

    items = BuildItemList(filterTheme, showAll)
    if #items == 0 then
        NS.Utils.PrintMessage("No items to review.")
        return
    end

    CreateReviewerFrame()

    local title = "Theme Reviewer"
    if filterTheme then
        title = title .. " \226\128\148 " .. filterTheme
    elseif showAll then
        title = title .. " \226\128\148 All"
    else
        title = title .. " \226\128\148 Uncertain"
    end
    frame._titleText:SetText(title)

    local annotations = GetAnnotations()
    currentIndex = 1
    for i, entry in ipairs(items) do
        if not annotations[tostring(entry.decorID)] then
            currentIndex = i
            break
        end
    end

    local reviewed = 0
    for _, e in ipairs(items) do
        if annotations[tostring(e.decorID)] then
            reviewed = reviewed + 1
        end
    end

    NS.Utils.PrintMessage(string.format(
        "Theme reviewer: %d items (%d reviewed).", #items, reviewed))

    ShowItem()
    frame:Show()
end
