-------------------------------------------------------------------------------
-- HearthAndSeek: CollectionsSubmenu.lua
--
-- Right-click submenu shown over a grid item. Each row is a checkbox
-- representing membership of the item in a user collection. 7 rows are
-- visible at a time; the panel scrolls for the rest. "+ New Collection"
-- is pinned at the bottom and always visible.
--
-- Public API:
--   NS.UI.OpenCollectionsSubmenu(decorID, anchorFrame)
--   NS.UI.CloseCollectionsSubmenu()
--
-- The list is rebuilt every Open() so it always reflects the current
-- NS.Collections state and the current item's membership.
-------------------------------------------------------------------------------
local _, NS = ...
NS.UI = NS.UI or {}

local PANEL_WIDTH    = 230
local ROW_H          = 22
local ROWS_VISIBLE   = 7
local HEADER_H       = 28
local FOOTER_H       = 60     -- room for two buttons stacked vertically
local SIDE_PAD       = 10

local panel        = nil
local clickCatcher = nil
local listFrame    = nil   -- clipped scroll viewport
local scrollChild  = nil   -- scrolling content
local rowPool      = {}
local headerLabel  = nil
local newBtn       = nil
local manageBtn    = nil
local emptyLabel   = nil

-- Per-open state
local currentDecorID = nil
local scrollOffset   = 0
local maxScroll      = 0

-------------------------------------------------------------------------------
-- Forward declarations
-------------------------------------------------------------------------------
local rebuild
local applyScroll

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------
local function getItemName(decorID)
    local items = NS.CatalogData and NS.CatalogData.Items
    local entry = items and items[decorID]
    return entry and entry.name or "Item"
end

local function notifyDropdown()
    if NS.UI.RefreshCollectionsDropdown then
        NS.UI.RefreshCollectionsDropdown()
    end
    if NS.UI.RefreshCollectionsManager then
        NS.UI.RefreshCollectionsManager()
    end
    if NS.UI.CatalogGrid_ApplyFilters then
        NS.UI.CatalogGrid_ApplyFilters()
    end
end

-------------------------------------------------------------------------------
-- Row factory
-------------------------------------------------------------------------------
-- Forward declarations local to row functions
local commitRowEdit, cancelRowEdit, startRowEdit

local function buildRow(parent, idx)
    local row = CreateFrame("Frame", nil, parent)
    row:SetHeight(ROW_H)

    -- Make the whole row clickable so users can click the name (or
    -- empty space) and have it toggle the checkbox. The checkbox
    -- itself consumes its own clicks; this fires only for the
    -- non-checkbox area.
    row:EnableMouse(true)
    row:SetScript("OnMouseUp", function(self, mouseBtn)
        if mouseBtn ~= "LeftButton" then return end
        if row._isEditing then return end
        if not row._name or not currentDecorID then return end
        row.check:Click()
    end)
    -- Subtle hover hint
    row.hoverBg = row:CreateTexture(nil, "BACKGROUND")
    row.hoverBg:SetAllPoints()
    row.hoverBg:SetColorTexture(1, 0.82, 0, 0.08)
    row.hoverBg:Hide()
    row:SetScript("OnEnter", function() row.hoverBg:Show() end)
    row:SetScript("OnLeave", function() row.hoverBg:Hide() end)

    row.check = CreateFrame("CheckButton", nil, row, "UICheckButtonTemplate")
    row.check:SetSize(22, 22)
    row.check:SetPoint("LEFT", row, "LEFT", 4, 0)
    row.check:SetScript("OnClick", function(self)
        if not row._name or not currentDecorID then return end
        local checked = self:GetChecked()
        if checked then
            NS.Collections.AddItem(row._name, currentDecorID)
        else
            NS.Collections.RemoveItem(row._name, currentDecorID)
        end
        notifyDropdown()
    end)

    -- Read-only label
    row.label = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    row.label:SetPoint("LEFT", row.check, "RIGHT", 4, 0)
    row.label:SetPoint("RIGHT", row, "RIGHT", -8, 0)
    row.label:SetJustifyH("LEFT")
    row.label:SetWordWrap(false)
    row.label:SetTextColor(0.95, 0.95, 0.85, 1)

    -- Inline edit box for rename-on-create. Hidden by default; shown
    -- when the user just created a fresh collection via "New Collection".
    row.editBox = CreateFrame("EditBox", nil, row, "InputBoxTemplate")
    row.editBox:SetPoint("LEFT", row.check, "RIGHT", 8, 0)
    row.editBox:SetPoint("RIGHT", row, "RIGHT", -28, 0)
    row.editBox:SetHeight(18)
    row.editBox:SetAutoFocus(false)
    row.editBox:SetMaxLetters(NS.Collections.MAX_NAME_LEN)
    row.editBox:SetFontObject("GameFontHighlightSmall")
    row.editBox:Hide()
    row.editBox:SetScript("OnEnterPressed", function() commitRowEdit(row) end)
    row.editBox:SetScript("OnEscapePressed", function() cancelRowEdit(row) end)

    -- Small red commit button (visible only while editing). Custom
    -- backdrop so the red is clearly visible against the dark panel.
    row.commitBtn = CreateFrame("Button", nil, row, "BackdropTemplate")
    row.commitBtn:SetSize(28, 18)
    row.commitBtn:SetPoint("RIGHT", row, "RIGHT", -4, 0)
    row.commitBtn:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8X8",
        edgeFile = "Interface\\Buttons\\WHITE8X8",
        edgeSize = 1,
    })
    row.commitBtn:SetBackdropColor(0.65, 0.15, 0.15, 1)
    row.commitBtn:SetBackdropBorderColor(0.95, 0.35, 0.35, 1)
    row.commitBtn._label = row.commitBtn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    row.commitBtn._label:SetPoint("CENTER")
    row.commitBtn._label:SetText("OK")
    row.commitBtn._label:SetTextColor(1, 1, 1, 1)
    row.commitBtn:SetScript("OnEnter", function(self)
        self:SetBackdropColor(0.85, 0.20, 0.20, 1)
    end)
    row.commitBtn:SetScript("OnLeave", function(self)
        self:SetBackdropColor(0.65, 0.15, 0.15, 1)
    end)
    row.commitBtn:Hide()
    row.commitBtn:SetScript("OnClick", function() commitRowEdit(row) end)

    return row
end

startRowEdit = function(row)
    if row._isEditing then return end
    row._isEditing = true
    row.editBox:SetText(row._name or "")
    row.editBox:Show()
    row.editBox:SetFocus()
    row.editBox:HighlightText()
    row.commitBtn:Show()
    row.label:Hide()
    row.check:Hide()
end

cancelRowEdit = function(row)
    row._isEditing = false
    row.editBox:Hide()
    row.editBox:ClearFocus()
    row.commitBtn:Hide()
    row.label:Show()
    row.check:Show()
end

commitRowEdit = function(row)
    local newName = (row.editBox:GetText() or ""):gsub("^%s+", ""):gsub("%s+$", "")
    if newName == "" or newName == row._name then
        cancelRowEdit(row)
        return
    end
    local ok = NS.Collections.Rename(row._name, newName)
    cancelRowEdit(row)
    if ok then
        rebuild()
        notifyDropdown()
    end
end

local function getRow(idx)
    local row = rowPool[idx]
    if not row then
        row = buildRow(scrollChild, idx)
        rowPool[idx] = row
    end
    return row
end

-------------------------------------------------------------------------------
-- Build / show / hide
-------------------------------------------------------------------------------
local function ensureBuilt()
    if panel then return end

    -- Click-catcher (full-screen, dismisses panel on outside click)
    clickCatcher = CreateFrame("Frame", nil, UIParent)
    clickCatcher:SetAllPoints(UIParent)
    clickCatcher:SetFrameStrata("DIALOG")
    clickCatcher:SetFrameLevel(50)
    clickCatcher:EnableMouse(true)
    clickCatcher:SetScript("OnMouseDown", function()
        if NS.UI.CloseCollectionsSubmenu then
            NS.UI.CloseCollectionsSubmenu()
        end
    end)
    clickCatcher:Hide()

    panel = CreateFrame("Frame", nil, UIParent, "BackdropTemplate")
    panel:SetWidth(PANEL_WIDTH)
    panel:SetFrameStrata("DIALOG")
    panel:SetFrameLevel(60)
    panel:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8X8",
        edgeFile = "Interface\\Buttons\\WHITE8X8",
        edgeSize = 1,
    })
    panel:SetBackdropColor(0.06, 0.06, 0.08, 0.97)
    panel:SetBackdropBorderColor(0.40, 0.35, 0.20, 1)
    panel:EnableMouse(true)  -- block clicks from reaching the click-catcher
    panel:Hide()

    -- Header (item name)
    headerLabel = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    headerLabel:SetPoint("TOPLEFT", panel, "TOPLEFT", SIDE_PAD, -8)
    headerLabel:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -SIDE_PAD, -8)
    headerLabel:SetJustifyH("LEFT")
    headerLabel:SetWordWrap(false)
    headerLabel:SetTextColor(1, 0.82, 0, 1)

    -- Header separator
    local sep = panel:CreateTexture(nil, "ARTWORK")
    sep:SetHeight(1)
    sep:SetPoint("TOPLEFT", panel, "TOPLEFT", 6, -HEADER_H)
    sep:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -6, -HEADER_H)
    sep:SetColorTexture(0.40, 0.35, 0.20, 0.7)

    -- Scrollable list area (clipped)
    listFrame = CreateFrame("Frame", nil, panel)
    listFrame:SetPoint("TOPLEFT", panel, "TOPLEFT", 4, -(HEADER_H + 2))
    listFrame:SetPoint("TOPRIGHT", panel, "TOPRIGHT", -4, -(HEADER_H + 2))
    listFrame:SetHeight(ROWS_VISIBLE * ROW_H)
    listFrame:SetClipsChildren(true)

    scrollChild = CreateFrame("Frame", nil, listFrame)
    scrollChild:SetPoint("TOPLEFT", listFrame, "TOPLEFT", 0, 0)
    scrollChild:SetPoint("RIGHT", listFrame, "RIGHT", 0, 0)
    scrollChild:SetHeight(1)

    listFrame:EnableMouseWheel(true)
    listFrame:SetScript("OnMouseWheel", function(_, delta)
        scrollOffset = math.max(0, math.min(maxScroll, scrollOffset - delta * ROW_H))
        applyScroll()
    end)

    -- Empty-state label (shown if user has zero collections)
    emptyLabel = listFrame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    emptyLabel:SetPoint("CENTER", listFrame, "CENTER", 0, 0)
    emptyLabel:SetText("No collections yet.\nClick \"New Collection\" below.")
    emptyLabel:SetTextColor(0.55, 0.55, 0.60, 1)
    emptyLabel:SetJustifyH("CENTER")
    emptyLabel:Hide()

    -- Footer separator
    local sep2 = panel:CreateTexture(nil, "ARTWORK")
    sep2:SetHeight(1)
    sep2:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 6, FOOTER_H)
    sep2:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -6, FOOTER_H)
    sep2:SetColorTexture(0.40, 0.35, 0.20, 0.7)

    -- Footer: two side-by-side buttons. "New Collection" creates a fresh
    -- entry, adds the current item to it, and starts an inline rename on
    -- the new row so the user can name it before doing anything else.
    -- "Manage Collections" jumps to the full overlay for bulk management.
    newBtn = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    newBtn:SetHeight(22)
    newBtn:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 6, 32)
    newBtn:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -6, 32)
    newBtn:SetText("New Collection")
    newBtn:SetScript("OnClick", function()
        if not currentDecorID then return end
        local i = 1
        local base = "Collection "
        while NS.Collections.Exists(base .. i) do i = i + 1 end
        local name = base .. i
        local ok = NS.Collections.Create(name)
        if not ok then return end
        NS.Collections.AddItem(name, currentDecorID)
        rebuild()
        notifyDropdown()
        -- Find the new row and start inline rename so the user can name
        -- it immediately. List() is sorted by creation order, so the new
        -- one is at the end.
        local list = NS.Collections.List()
        for idx, n in ipairs(list) do
            if n == name then
                local row = rowPool[idx]
                if row then
                    -- Scroll the new row into view (it'll be at the bottom)
                    if (idx - 1) * ROW_H >= ROWS_VISIBLE * ROW_H then
                        scrollOffset = math.max(0, idx * ROW_H - ROWS_VISIBLE * ROW_H)
                        applyScroll()
                    end
                    startRowEdit(row)
                end
                return
            end
        end
    end)

    manageBtn = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    manageBtn:SetHeight(22)
    manageBtn:SetPoint("BOTTOMLEFT", panel, "BOTTOMLEFT", 6, 6)
    manageBtn:SetPoint("BOTTOMRIGHT", panel, "BOTTOMRIGHT", -6, 6)
    manageBtn:SetText("Manage Collections")
    manageBtn:SetScript("OnClick", function()
        if NS.UI.CloseCollectionsSubmenu then
            NS.UI.CloseCollectionsSubmenu()
        end
        if NS.UI.OpenCollectionsManager then
            NS.UI.OpenCollectionsManager()
        end
    end)

    panel:SetHeight(HEADER_H + ROWS_VISIBLE * ROW_H + FOOTER_H + 6)
end

applyScroll = function()
    if not scrollChild or not listFrame then return end
    scrollChild:ClearAllPoints()
    scrollChild:SetPoint("TOPLEFT", listFrame, "TOPLEFT", 0, scrollOffset)
    scrollChild:SetPoint("RIGHT", listFrame, "RIGHT", 0, 0)
end

rebuild = function()
    local names = NS.Collections.List()
    local total = #names

    -- Position rows + bind state
    for i = 1, math.max(total, #rowPool) do
        local row = rowPool[i]
        if i <= total then
            row = row or getRow(i)
            row._name = names[i]
            row.label:SetText(names[i])
            local checked = NS.Collections.Contains(names[i], currentDecorID)
            row.check:SetChecked(checked)
            row:ClearAllPoints()
            row:SetPoint("TOPLEFT", scrollChild, "TOPLEFT", 0, -(i - 1) * ROW_H)
            row:SetPoint("RIGHT", scrollChild, "RIGHT", 0, 0)
            row:Show()
        elseif row then
            row:Hide()
        end
    end

    scrollChild:SetHeight(math.max(1, total * ROW_H))
    -- Reset scroll for fresh open
    scrollOffset = 0
    maxScroll = math.max(0, total * ROW_H - ROWS_VISIBLE * ROW_H)
    applyScroll()

    if total == 0 then
        emptyLabel:Show()
    else
        emptyLabel:Hide()
    end
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function NS.UI.OpenCollectionsSubmenu(decorID, anchor)
    if not decorID then return end
    ensureBuilt()
    currentDecorID = decorID
    headerLabel:SetText(getItemName(decorID))
    rebuild()

    -- Position relative to the anchor (right of the grid item, then clamp).
    panel:ClearAllPoints()
    if anchor then
        panel:SetPoint("TOPLEFT", anchor, "TOPRIGHT", 4, 0)
    else
        local x, y = GetCursorPosition()
        local s = UIParent:GetEffectiveScale()
        panel:SetPoint("TOPLEFT", UIParent, "BOTTOMLEFT", x / s, y / s)
    end

    clickCatcher:Show()
    panel:Show()
end

function NS.UI.CloseCollectionsSubmenu()
    if panel then panel:Hide() end
    if clickCatcher then clickCatcher:Hide() end
    currentDecorID = nil
end
