-------------------------------------------------------------------------------
-- HearthAndSeek: CatalogFrame.lua
-- Main catalog browser frame: title bar, search box, top filter bar with
-- dropdown panels, progress bar, and footer (active filter tags).
-- Dynamic filter counts update after every filter change.
-- Toggle with /hseek catalog
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.UI = NS.UI or {}

local CatSizing = nil

local catalogFrame = nil
local footerFrame = nil
local footerTags = {}

-- Solid dark backdrop — uses WHITE8X8 (1px white) tinted via SetBackdropColor.
local BACKDROP_SOLID = {
    bgFile   = "Interface\\Buttons\\WHITE8X8",
    edgeFile = "Interface\\Buttons\\WHITE8X8",
    edgeSize = 1,
    insets   = { left = 1, right = 1, top = 1, bottom = 1 },
}

-- Widget references for dynamic count updates
-- Each entry: { check = CheckButton, label = FontString, namePrefix = "display name" }
local filterWidgets = {
    favorites     = {},   -- ["onlyFavorites"] = { check, label, namePrefix }
    collection    = {},   -- ["collected"] = {...}, ["notCollected"] = {...}
    categories    = {},   -- [catID] = { check, label, namePrefix, childKeys }
    subcategories = {},   -- [subcatID] = { check, label, namePrefix }
    sources       = {},   -- ["Vendor"] = {...}, ...
    professions   = {},   -- ["Tailoring"] = {...}, ...
    expansions    = {},   -- ["Midnight"] = { check, label, toggle, container, expanded, childKeys, namePrefix }
    zones         = {},   -- ["Stormwind City"] = { check, label, namePrefix }
    qualities     = {},   -- [1] = {...}, [2] = {...}, ...
    themes        = {},   -- [themeID] = { check, label, namePrefix }
}

-------------------------------------------------------------------------------
-- FILTER_SECTIONS — data-driven filter layout config
-------------------------------------------------------------------------------
local FILTER_SECTIONS = {
    {
        id = "favorites",
        title = "FAVORITES",
        type = "boolean",
        items = {
            { key = "onlyFavorites", label = "Only Favorites", color = {0.25, 0.78, 0.78},
              countKey = "favorites" },
        },
        toggle = "CatalogGrid_ToggleFavorites",
    },
    {
        id = "collection",
        title = "COLLECTION",
        type = "boolean_pair",
        items = {
            { key = "collected",    label = "Hide Collected",     color = {0.12, 1.00, 0.00} },
            { key = "notCollected", label = "Hide Not Collected", color = {1.00, 0.27, 0.27} },
        },
        toggle = "CatalogGrid_ToggleCollection",
    },
    {
        id = "themes_aesthetic",
        title = "AESTHETIC",
        type = "theme_group",
        groupID = 2,
        widgetTable = "themes",

        toggle = "CatalogGrid_ToggleTheme",
        -- Per-theme colors keyed by theme name
        themeColors = {
            ["Arcane Sanctum"]     = { 0.65, 0.40, 0.95 },  -- bright purple
            ["Cottage Hearth"]     = { 0.85, 0.60, 0.30 },  -- warm orange
            ["Enchanted Grove"]    = { 0.30, 0.80, 0.70 },  -- teal/cyan
            ["Feast Hall"]         = { 0.85, 0.40, 0.30 },  -- warm red
            ["Fel Forge"]          = { 0.45, 0.85, 0.20 },  -- toxic green
            ["Haunted Manor"]      = { 0.55, 0.50, 0.65 },  -- muted violet
            ["Royal Court"]        = { 0.90, 0.78, 0.25 },  -- gold
            ["Sacred Temple"]      = { 0.95, 0.88, 0.50 },  -- holy light
            ["Scholar's Archive"]  = { 0.78, 0.65, 0.45 },  -- parchment tan
            ["Seafarer's Haven"]   = { 0.30, 0.60, 0.80 },  -- ocean blue
            ["Tinker's Workshop"]  = { 0.65, 0.72, 0.78 },  -- steel grey
            ["Void Rift"]          = { 0.45, 0.25, 0.70 },  -- deep indigo
            ["War Room"]           = { 0.80, 0.35, 0.30 },  -- crimson
            ["Primal Camp"]        = { 0.70, 0.50, 0.30 },  -- earth brown
            ["Wild Garden"]        = { 0.35, 0.72, 0.35 },  -- forest green
        },
    },
    {
        id = "categories",
        title = "CATEGORY",
        type = "hierarchical",
        groupOrder = "CategoryOrder",
        groupNames = "CategoryNames",
        childMap = "CategorySubcategories",
        childNames = "SubcategoryNames",
        childCounts = "BySubcategory",
        toggleChild = "CatalogGrid_ToggleSubcategory",
        toggleGroup = "CatalogGrid_ToggleCategory",
        widgetTable = "categories",
        childWidgetTable = "subcategories",
        uniformColor = { 0.85, 0.70, 0.30 },  -- gold
    },
    {
        id = "themes_culture",
        title = "CULTURE",
        type = "theme_group",
        groupID = 1,
        widgetTable = "themes",

        toggle = "CatalogGrid_ToggleTheme",
        uniformColor = { 0.70, 0.55, 0.30 },  -- warm bronze
        defaultCollapsed = true,
    },
    {
        id = "sources",
        title = "SOURCE",
        type = "multiselect",
        order = "SourceOrder",
        counts = "BySource",
        colors = "SourceColors",
        toggle = "CatalogGrid_ToggleSource",
        widgetTable = "sources",
        defaultCollapsed = true,
        subGroup = {
            parentKey = "Profession",
            order = "ProfessionOrder",
            counts = "ByProfession",
            icons = "ProfessionIcons",
            color = { 0.60, 0.40, 0.20 },
            toggleChild = "CatalogGrid_ToggleProfession",
            toggleGroup = "CatalogGrid_ToggleAllProfessions",
            widgetTable = "professions",
        },
    },
    {
        id = "expansions",
        title = "ZONE",
        type = "hierarchical",
        groupOrder = "ExpansionOrder",
        groupColors = "ExpansionColors",
        childSource = "ZoneToExpansionMap",
        childCounts = "ByZone",
        toggleChild = "CatalogGrid_ToggleZone",
        toggleGroup = "CatalogGrid_ToggleExpansion",
        widgetTable = "expansions",
        childWidgetTable = "zones",
        defaultCollapsed = true,
        childColorOverrides = {
            ["Founder's Point"] = "3399FF",
            ["Razorwind Shores"] = "FF3333",
        },
    },
    {
        id = "qualities",
        title = "RARITY",
        type = "multiselect",
        order = "QualityOrder",
        names = "QualityNames",
        colors = "QualityColors",
        toggle = "CatalogGrid_ToggleQuality",
        widgetTable = "qualities",
        defaultCollapsed = true,
    },
}

-------------------------------------------------------------------------------
-- Dropdown panel helpers
-------------------------------------------------------------------------------

--- Generic: update a parent checkbox based on whether all children are checked.
local function UpdateParentCheckState(parentWidget, childKeys, childWidgetTable)
    if not parentWidget then return end
    local allChecked = true
    for _, cKey in ipairs(childKeys) do
        local cWidget = childWidgetTable[cKey]
        if cWidget and not cWidget.check:GetChecked() then
            allChecked = false
            break
        end
    end
    parentWidget.check:SetChecked(allChecked)
end

-------------------------------------------------------------------------------
-- Dropdown panel infrastructure
-------------------------------------------------------------------------------
local activeDropdown      = nil   -- currently open dropdown panel (or nil)
local clickCatcher        = nil   -- full-screen click-catcher frame
local filterBar           = nil   -- the 30px filter bar frame
local filterBarButtons    = {}    -- ordered list of bar button frames
local dropdownPanels      = {}    -- buttonKey -> dropdown panel frame
local progressBar         = nil   -- bottom progress bar frame
local progressFill        = nil   -- progress bar fill texture
local progressLabel       = nil   -- progress bar FontString

--- Close whichever dropdown is currently shown.
local function CloseActiveDropdown()
    if activeDropdown then
        activeDropdown:Hide()
        -- Reset the owning button's dropdown-open state
        if activeDropdown._ownerBtn then
            local ownerBtn = activeDropdown._ownerBtn
            ownerBtn._isDropdownOpen = false
            -- Only reset appearance if no active filters on this button
            if not ownerBtn._isActive then
                ownerBtn._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonNormal")
                ownerBtn._label:SetTextColor(0.75, 0.75, 0.75, 1)
            end
        end
        activeDropdown = nil
    end
    if clickCatcher then clickCatcher:Hide() end
end

--- Open a specific dropdown panel (closing any other first).
local function OpenDropdown(panel, ownerBtn)
    if activeDropdown == panel then
        CloseActiveDropdown()
        return
    end
    CloseActiveDropdown()
    activeDropdown = panel
    panel._ownerBtn = ownerBtn
    -- Reset scroll position to top
    panel._scrollOffset = 0
    if panel._scrollChild and panel._scroll then
        panel._scrollChild:ClearAllPoints()
        panel._scrollChild:SetPoint("TOPLEFT", panel._scroll, "TOPLEFT", 0, 0)
    end
    if panel._scrollThumb then panel._scrollThumb:Hide() end
    panel:Show()
    -- Set the button to active/open appearance
    if ownerBtn then
        ownerBtn._isDropdownOpen = true
        ownerBtn._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonActive")
        ownerBtn._label:SetTextColor(1, 0.82, 0, 1)
    end
    if clickCatcher then
        clickCatcher:Show()
    end
end

--- Create a dropdown panel anchored below a filter-bar button.
--- @param parent  Frame  The main catalogFrame.
--- @param button  Frame  The bar button this dropdown hangs from.
--- @param width   number Panel width (px).
--- @return Frame  The dropdown panel (hidden by default).
local function CreateDropdownPanel(parent, button, width)
    local panel = CreateFrame("Frame", nil, UIParent, "BackdropTemplate")
    panel:SetBackdrop(BACKDROP_SOLID)
    panel:SetBackdropColor(0.08, 0.08, 0.10, 0.97)
    panel:SetBackdropBorderColor(0.4, 0.35, 0.2, 1)
    panel:SetFrameStrata("DIALOG")
    panel:SetFrameLevel(10)
    panel:SetClampedToScreen(true)
    panel:SetWidth(width)
    panel:SetPoint("TOPLEFT", button, "BOTTOMLEFT", 0, -2)
    panel:EnableMouse(true)
    panel:Hide()
    return panel
end

--- Add a scrollable content area inside a dropdown panel.
--- clipFrame clips visual overflow; mouse wheel on panel scrolls content.
--- Returns clipFrame, scrollChild.
local function AddDropdownScroll(panel, panelWidth)
    -- Clip frame: visual clipping only (no EnableMouse to avoid eating clicks)
    local clipFrame = CreateFrame("Frame", nil, panel)
    clipFrame:SetPoint("TOPLEFT", 4, -4)
    clipFrame:SetPoint("BOTTOMRIGHT", -4, 4)
    clipFrame:SetClipsChildren(true)

    -- Scroll child: holds all dropdown content
    local scrollChild = CreateFrame("Frame", nil, clipFrame)
    scrollChild:SetWidth(panelWidth - 12) -- leave room for scroll thumb
    scrollChild:SetPoint("TOPLEFT", clipFrame, "TOPLEFT", 0, 0)

    -- Scroll thumb (thin vertical bar, right side)
    local scrollThumb = panel:CreateTexture(nil, "OVERLAY")
    scrollThumb:SetWidth(3)
    scrollThumb:SetColorTexture(0.5, 0.45, 0.3, 0.5)
    scrollThumb:Hide()
    panel._scrollThumb = scrollThumb

    local function UpdateScrollThumb(scrollOffset)
        local clipH = clipFrame:GetHeight()
        local contentH = scrollChild:GetHeight()
        if contentH <= clipH or clipH <= 0 then
            scrollThumb:Hide()
            return
        end
        local thumbH = math.max(20, clipH * (clipH / contentH))
        local maxScroll = contentH - clipH
        local thumbTravel = clipH - thumbH
        local thumbOffset = maxScroll > 0 and (thumbTravel * (scrollOffset / maxScroll)) or 0
        scrollThumb:SetHeight(thumbH)
        scrollThumb:ClearAllPoints()
        scrollThumb:SetPoint("TOPRIGHT", clipFrame, "TOPRIGHT", 0, -thumbOffset)
        scrollThumb:Show()
    end

    -- Mouse wheel scrolling (on panel, so it works even over checkboxes)
    panel._scrollOffset = 0
    panel:EnableMouseWheel(true)
    panel:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, scrollChild:GetHeight() - clipFrame:GetHeight())
        local newOffset = math.max(0, math.min(maxScroll, self._scrollOffset + delta * -30))
        self._scrollOffset = newOffset
        scrollChild:ClearAllPoints()
        scrollChild:SetPoint("TOPLEFT", clipFrame, "TOPLEFT", 0, newOffset)
        UpdateScrollThumb(newOffset)
    end)

    panel._scroll = clipFrame
    panel._scrollChild = scrollChild

    -- Expose scroll updater for use after group expand/collapse
    panel._updateScrollThumb = function()
        UpdateScrollThumb(panel._scrollOffset)
    end

    return clipFrame, scrollChild
end

--- Compute actual content height inside a dropdown scrollChild and resize panel
--- (capped at DropdownMaxHeight).
local function FitDropdownToContent(panel, scrollChild)
    local contentH = scrollChild:GetHeight()
    local maxH = (CatSizing and CatSizing.DropdownMaxHeight) or 400
    local panelH = math.min(contentH + 8, maxH)
    panel:SetHeight(panelH)
end

local function CreateFilterCheckbox(parent, labelText, yOffset, color, onClick)
    local check = CreateFrame("CheckButton", nil, parent, "UICheckButtonTemplate")
    check:SetSize(22, 22)
    check:SetPoint("TOPLEFT", parent, "TOPLEFT", 6, yOffset)

    local label = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("LEFT", check, "RIGHT", 2, 0)
    label:SetText(labelText)
    if color then
        label:SetTextColor(color[1], color[2], color[3], 1)
    else
        label:SetTextColor(0.70, 0.70, 0.70, 1)
    end
    check._label = label

    check:SetScript("OnClick", function(self)
        local checked = self:GetChecked()
        if onClick then onClick(checked) end
    end)

    return check, yOffset - 22
end

local function CreateFilterGroup(contentFrame, groupName, anchorFrame, color, childList, dropdownPanel, groupsListRef, childWidgetTbl, groupWidgetTbl, toggleGroupFn, toggleKey)
    local group = CreateFrame("Frame", nil, contentFrame)
    group:SetPoint("LEFT", contentFrame, "LEFT", 0, 0)
    group:SetPoint("RIGHT", contentFrame, "RIGHT", 0, 0)
    if anchorFrame then
        group:SetPoint("TOP", anchorFrame, "BOTTOM", 0, 0)
    else
        group:SetPoint("TOP", contentFrame, "TOP", 0, 0)
    end

    -- Header row (22px)
    local row = CreateFrame("Frame", nil, group)
    row:SetHeight(22)
    row:SetPoint("TOPLEFT", group, "TOPLEFT", 0, 0)
    row:SetPoint("TOPRIGHT", group, "TOPRIGHT", 0, 0)

    -- Checkbox for mass toggle (leftmost)
    local groupCheck = CreateFrame("CheckButton", nil, row, "UICheckButtonTemplate")
    groupCheck:SetSize(22, 22)
    groupCheck:SetPoint("LEFT", row, "LEFT", 4, 0)

    -- Label (after checkbox)
    local label = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("LEFT", groupCheck, "RIGHT", 2, 0)
    if color then
        label:SetText("|cff" .. color .. groupName .. "|r")
    else
        label:SetText(groupName)
    end
    groupCheck._label = label

    -- Toggle indicator (+/- at right edge of row, gold, larger font)
    local toggleText = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    toggleText:SetPoint("RIGHT", row, "RIGHT", -8, 0)
    toggleText:SetText("+")
    toggleText:SetTextColor(1.00, 0.82, 0.00, 1)

    -- Child container (below row, inside group)
    local childFrame = CreateFrame("Frame", nil, group)
    childFrame:SetPoint("TOPLEFT", row, "BOTTOMLEFT", 0, 0)
    childFrame:SetPoint("RIGHT", group, "RIGHT", 0, 0)
    childFrame:SetHeight(1)
    childFrame:Hide()

    -- Group starts collapsed: height = just the header row
    group:SetHeight(22)

    local numChildren = #childList

    -- Mass toggle callback
    groupCheck:SetScript("OnClick", function(self)
        local checked = self:GetChecked()
        -- Update child checkboxes FIRST so RefreshFooterBar sees correct state
        local gData = groupWidgetTbl[groupName]
        if gData and gData.childKeys then
            for _, cKey in ipairs(gData.childKeys) do
                local cWidget = childWidgetTbl[cKey]
                if cWidget then cWidget.check:SetChecked(checked) end
            end
        end
        if toggleGroupFn then
            toggleGroupFn(toggleKey or groupName, checked)
        end
    end)

    -- Recalculate total content height for all groups in the dropdown
    local function RecalcDropdownGroupHeights()
        local totalH = 0
        for _, g in ipairs(groupsListRef) do
            totalH = totalH + g:GetHeight()
        end
        contentFrame:SetHeight(totalH)
        -- Update scrollChild height + panel size (handles scroll clipping)
        if dropdownPanel and dropdownPanel._recalcScrollHeight then
            dropdownPanel._recalcScrollHeight()
        elseif dropdownPanel and dropdownPanel._scrollChild then
            FitDropdownToContent(dropdownPanel, dropdownPanel._scrollChild)
        end
    end

    -- Toggle expand/collapse
    group._expanded = false
    local function ToggleExpand()
        group._expanded = not group._expanded
        if group._expanded then
            toggleText:SetText("-")
            childFrame:SetHeight(numChildren * 22)
            childFrame:Show()
            group:SetHeight(22 + numChildren * 22)
        else
            toggleText:SetText("+")
            childFrame:SetHeight(1)
            childFrame:Hide()
            group:SetHeight(22)
        end
        RecalcDropdownGroupHeights()
    end

    -- Click area covers label + toggle (everything right of checkbox)
    local clickArea = CreateFrame("Button", nil, row)
    clickArea:SetPoint("LEFT", groupCheck, "RIGHT", 0, 0)
    clickArea:SetPoint("RIGHT", row, "RIGHT", 0, 0)
    clickArea:SetHeight(22)
    clickArea:SetScript("OnClick", ToggleExpand)

    groupsListRef[#groupsListRef + 1] = group
    return group, groupCheck, label, childFrame
end

-------------------------------------------------------------------------------
-- SectionBuilders — dispatch table for building each section type
-- Each builder receives (content, sectionDef, panel) where:
--   content = scrollChild frame inside the dropdown panel
--   panel   = the dropdown panel frame (for resizing)
-------------------------------------------------------------------------------
local SectionBuilders = {}

--- boolean: single toggle checkbox (e.g. Favorites)
function SectionBuilders.boolean(content, sectionDef)
    local items = sectionDef.items or {}

    local yOff = 0
    for _, itemDef in ipairs(items) do
        local displayLabel = itemDef.label .. "  |cff40c8c8(0)|r"
        local chk, newY = CreateFilterCheckbox(content, displayLabel, yOff, itemDef.color,
            function(checked)
                local fn = NS.UI[sectionDef.toggle]
                if fn then fn(checked) end
            end)
        chk:SetChecked(false)
        filterWidgets[sectionDef.id][itemDef.key] = {
            check      = chk,
            label      = chk._label,
            namePrefix = itemDef.label,
            countKey   = itemDef.countKey,
        }
        yOff = newY
    end

    content:SetHeight(math.abs(yOff))
end

--- boolean_pair: inverted toggle pair (e.g. Collection — checked = hide)
function SectionBuilders.boolean_pair(content, sectionDef)
    local items = sectionDef.items or {}

    local yOff = 0
    for _, itemDef in ipairs(items) do
        local chk, newY = CreateFilterCheckbox(content, itemDef.label, yOff, itemDef.color,
            function(checked)
                -- checked = "Hide X" is active -> invert for filterState (true = show)
                local fn = NS.UI[sectionDef.toggle]
                if fn then fn(itemDef.key, not checked) end
            end)
        chk:SetChecked(false)
        filterWidgets[sectionDef.id][itemDef.key] = {
            check      = chk,
            label      = chk._label,
            namePrefix = itemDef.label,
        }
        yOff = newY
    end

    content:SetHeight(math.abs(yOff))
end

--- multiselect: flat checkbox list with optional embedded sub-group (e.g. Source, Rarity)
function SectionBuilders.multiselect(content, sectionDef, panel)
    local wTable = sectionDef.widgetTable   -- e.g. "sources", "qualities"
    local subDef = sectionDef.subGroup

    -- Resolve order list
    local orderList
    if sectionDef.order then
        orderList = (NS.CatalogData and NS.CatalogData[sectionDef.order])
            or NS[sectionDef.order]
    end
    if not orderList then
        if wTable == "sources" then
            orderList = { "Vendor", "Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Other" }
        elseif wTable == "qualities" then
            orderList = { 1, 2, 3, 4, 5, 0 }
        else
            orderList = {}
        end
    end

    -- Resolve counts table
    local countsTable
    if sectionDef.counts then
        countsTable = NS.CatalogData and NS.CatalogData[sectionDef.counts]
    end

    -- Resolve colors table (check NS root first, then CatalogData)
    local colorsTable
    if sectionDef.colors then
        colorsTable = NS[sectionDef.colors] or (NS.CatalogData and NS.CatalogData[sectionDef.colors])
    end

    -- Resolve names table
    local namesTable
    if sectionDef.names then
        namesTable = NS[sectionDef.names] or (NS.CatalogData and NS.CatalogData[sectionDef.names])
    end

    -- Determine the sub-group parent key to skip in main loop
    local skipKey = subDef and subDef.parentKey or nil

    -- Pass 1: create checkboxes for all items except the sub-group parent
    local yOff = 0
    local itemCount = 0
    for _, key in ipairs(orderList) do
        if key ~= skipKey then
            -- Compute count for this key
            local count = 0
            if countsTable and countsTable[key] then
                if type(countsTable[key]) == "table" then
                    count = #countsTable[key]
                else
                    count = countsTable[key]
                end
            elseif wTable == "qualities" then
                if NS.CatalogData and NS.CatalogData.Items then
                    for _, item in pairs(NS.CatalogData.Items) do
                        if item.quality == key then count = count + 1 end
                    end
                end
            end

            if count > 0 then
                local displayName = (namesTable and namesTable[key]) or (type(key) == "string" and key or ("Quality " .. key))
                local itemColor = colorsTable and colorsTable[key] or nil
                local labelText = displayName .. "  |cff888888(" .. count .. ")|r"

                local chk, newY = CreateFilterCheckbox(content, labelText, yOff, itemColor,
                    function(checked)
                        local fn = NS.UI[sectionDef.toggle]
                        if fn then fn(key, checked) end
                    end)
                filterWidgets[wTable][key] = {
                    check      = chk,
                    label      = chk._label,
                    namePrefix = displayName,
                }
                yOff = newY
                itemCount = itemCount + 1
            end
        end
    end

    -- Pass 2: create expandable sub-group if defined (e.g. Profession inside Source)
    if subDef then
        local subOrderList = NS.CatalogData and NS.CatalogData[subDef.order] or {}
        local subCountsTable = NS.CatalogData and NS.CatalogData[subDef.counts] or {}
        local subIconsTable = NS[subDef.icons] or {}
        local subColor = subDef.color
        local subWTable = subDef.widgetTable   -- e.g. "professions"

        -- Compute total count for the parent key
        local parentCount = 0
        if countsTable and countsTable[skipKey] then
            if type(countsTable[skipKey]) == "table" then
                parentCount = #countsTable[skipKey]
            else
                parentCount = countsTable[skipKey]
            end
        end

        local hasSubGroup = parentCount > 0 and #subOrderList > 0
        if hasSubGroup then
            local profGroup = CreateFrame("Frame", nil, content)
            profGroup:SetPoint("LEFT", content, "LEFT", 0, 0)
            profGroup:SetPoint("RIGHT", content, "RIGHT", 0, 0)
            profGroup:SetPoint("TOP", content, "TOP", 0, yOff)

            -- Header row (22px) with checkbox + label + toggle
            local profRow = CreateFrame("Frame", nil, profGroup)
            profRow:SetHeight(22)
            profRow:SetPoint("TOPLEFT", profGroup, "TOPLEFT", 0, 0)
            profRow:SetPoint("TOPRIGHT", profGroup, "TOPRIGHT", 0, 0)

            local parentColor = colorsTable and colorsTable[skipKey] or nil
            local profCheck = CreateFrame("CheckButton", nil, profRow, "UICheckButtonTemplate")
            profCheck:SetSize(22, 22)
            profCheck:SetPoint("TOPLEFT", profRow, "TOPLEFT", 6, 0)

            local profLabel = profRow:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            profLabel:SetPoint("LEFT", profCheck, "RIGHT", 2, 0)
            profLabel:SetText(skipKey .. "  |cff888888(" .. parentCount .. ")|r")
            if parentColor then
                profLabel:SetTextColor(parentColor[1], parentColor[2], parentColor[3], 1)
            end
            profCheck._label = profLabel

            -- Toggle indicator (+/- at right edge, gold)
            local profToggle = profRow:CreateFontString(nil, "OVERLAY", "GameFontNormal")
            profToggle:SetPoint("RIGHT", profRow, "RIGHT", -8, 0)
            profToggle:SetText("+")
            profToggle:SetTextColor(1.00, 0.82, 0.00, 1)

            -- Sub-checkboxes container
            local subsFrame = CreateFrame("Frame", nil, profGroup)
            subsFrame:SetPoint("TOPLEFT", profRow, "BOTTOMLEFT", 0, 0)
            subsFrame:SetPoint("RIGHT", profGroup, "RIGHT", 0, 0)
            subsFrame:SetHeight(1)
            subsFrame:Hide()

            -- Build sub-checkboxes
            local subYOff = 0
            local subCount = 0
            local childNames = {}
            for _, childKey in ipairs(subOrderList) do
                local cCount = 0
                if subCountsTable[childKey] then
                    if type(subCountsTable[childKey]) == "table" then
                        cCount = #subCountsTable[childKey]
                    else
                        cCount = subCountsTable[childKey]
                    end
                end
                if cCount > 0 then
                    local childIcon = subIconsTable[childKey]
                    local cLabel = childKey .. "  |cff888888(" .. cCount .. ")|r"
                    local cChk, newCY = CreateFilterCheckbox(subsFrame, cLabel, subYOff, subColor,
                        function(checked)
                            local fn = NS.UI[subDef.toggleChild]
                            if fn then fn(childKey, checked) end
                            UpdateParentCheckState(
                                filterWidgets[wTable][skipKey],
                                filterWidgets[wTable][skipKey] and filterWidgets[wTable][skipKey].childKeys or {},
                                filterWidgets[subWTable]
                            )
                        end)
                    -- Indent sub-checkboxes
                    cChk:ClearAllPoints()
                    cChk:SetPoint("TOPLEFT", subsFrame, "TOPLEFT", 28, subYOff)

                    local displayPrefix = childKey
                    if childIcon then
                        displayPrefix = "|T" .. childIcon .. ":14:14|t " .. childKey
                        cChk._label:SetText("|T" .. childIcon .. ":14:14|t " .. cLabel)
                    end

                    filterWidgets[subWTable][childKey] = {
                        check      = cChk,
                        label      = cChk._label,
                        namePrefix = displayPrefix,
                    }
                    childNames[#childNames + 1] = childKey
                    subYOff = newCY
                    subCount = subCount + 1
                end
            end

            -- Group starts collapsed
            profGroup:SetHeight(22)
            profGroup._expanded = false

            -- Mass-toggle: checking header checks all sub-items
            profCheck:SetScript("OnClick", function(self)
                local checked = self:GetChecked()
                for _, cName in ipairs(childNames) do
                    local cWidget = filterWidgets[subWTable][cName]
                    if cWidget then cWidget.check:SetChecked(checked) end
                end
                local fn = NS.UI[subDef.toggleGroup]
                if fn then fn(checked) end
            end)

            -- Expand/collapse sub-group and resize dropdown
            local function ToggleSubGroupExpand()
                profGroup._expanded = not profGroup._expanded
                if profGroup._expanded then
                    profToggle:SetText("-")
                    subsFrame:SetHeight(subCount * 22)
                    subsFrame:Show()
                    profGroup:SetHeight(22 + subCount * 22)
                else
                    profToggle:SetText("+")
                    subsFrame:SetHeight(1)
                    subsFrame:Hide()
                    profGroup:SetHeight(22)
                end
                -- Recalc content height: base items + profGroup height
                local newH = (itemCount * 22) + profGroup:GetHeight()
                content:SetHeight(newH)
                if panel and panel._recalcScrollHeight then
                    panel._recalcScrollHeight()
                elseif panel then
                    FitDropdownToContent(panel, content)
                end
            end

            local profClickArea = CreateFrame("Button", nil, profRow)
            profClickArea:SetPoint("LEFT", profCheck, "RIGHT", 0, 0)
            profClickArea:SetPoint("RIGHT", profRow, "RIGHT", 0, 0)
            profClickArea:SetHeight(22)
            profClickArea:SetScript("OnClick", ToggleSubGroupExpand)

            filterWidgets[wTable][skipKey] = {
                check        = profCheck,
                label        = profLabel,
                namePrefix   = skipKey,
                childKeys    = childNames,
                _isProfMaster = true,
            }
            itemCount = itemCount + 1
        end
    end

    content:SetHeight(itemCount * 22)
end

--- hierarchical: expandable groups with children (e.g. Category, Expansion)
function SectionBuilders.hierarchical(content, sectionDef, panel)
    local wTable = sectionDef.widgetTable        -- e.g. "expansions", "categories"
    local cwTable = sectionDef.childWidgetTable   -- e.g. "zones", "subcategories"

    local groupsList = {}

    -- Resolve the group order list
    local groupOrder = NS.CatalogData and NS.CatalogData[sectionDef.groupOrder] or {}

    -- Resolve toggle functions
    local toggleChildFn = sectionDef.toggleChild and function(key, checked)
        local fn = NS.UI[sectionDef.toggleChild]
        if fn then fn(key, checked) end
    end or nil
    local toggleGroupFn = sectionDef.toggleGroup and function(groupName, checked)
        local fn = NS.UI[sectionDef.toggleGroup]
        if fn then fn(groupName, checked) end
    end or nil

    -- Resolve group colors (hex string table)
    local groupColorsTable
    if sectionDef.groupColors then
        groupColorsTable = NS[sectionDef.groupColors]
            or (NS.CatalogData and NS.CatalogData[sectionDef.groupColors])
    end

    -- uniformColor: single RGB {r,g,b} applied to all groups/children as fallback
    local uniformHex
    if sectionDef.uniformColor then
        local uc = sectionDef.uniformColor
        uniformHex = string.format("%02X%02X%02X",
            math.floor(uc[1] * 255 + 0.5),
            math.floor(uc[2] * 255 + 0.5),
            math.floor(uc[3] * 255 + 0.5))
    end

    -- Resolve group names (for named groups like categories)
    local groupNamesTable
    if sectionDef.groupNames then
        groupNamesTable = NS.CatalogData and NS.CatalogData[sectionDef.groupNames]
    end

    -- Resolve child color overrides
    local childColorOverrides = sectionDef.childColorOverrides or {}

    -- Build the children map: groupKey -> sorted list of { key, name, count }
    local childrenByGroup = {}

    if sectionDef.childMap then
        local childMap = NS.CatalogData and NS.CatalogData[sectionDef.childMap] or {}
        local childNames = NS.CatalogData and NS.CatalogData[sectionDef.childNames] or {}
        local childCountsMap = NS.CatalogData and NS.CatalogData[sectionDef.childCounts] or {}

        for _, groupKey in ipairs(groupOrder) do
            local children = childMap[groupKey]
            if children then
                local list = {}
                for _, childKey in ipairs(children) do
                    local cCount = 0
                    if childCountsMap[childKey] then
                        if type(childCountsMap[childKey]) == "table" then
                            cCount = #childCountsMap[childKey]
                        else
                            cCount = childCountsMap[childKey]
                        end
                    end
                    if cCount > 0 then
                        list[#list + 1] = {
                            key   = childKey,
                            name  = childNames[childKey] or tostring(childKey),
                            count = cCount,
                        }
                    end
                end
                if #list > 0 then
                    table.sort(list, function(a, b) return a.name < b.name end)
                    childrenByGroup[groupKey] = list
                end
            end
        end
    elseif sectionDef.childSource then
        local reverseMap = NS.CatalogData and NS.CatalogData[sectionDef.childSource] or {}
        local childCountsMap = NS.CatalogData and NS.CatalogData[sectionDef.childCounts] or {}

        local grouped = {}
        for childKey, parentKey in pairs(reverseMap) do
            if childCountsMap[childKey] then
                if not grouped[parentKey] then
                    grouped[parentKey] = {}
                end
                local raw = childCountsMap[childKey]
                local cCount = type(raw) == "table" and #raw or raw
                if cCount > 0 then
                    table.insert(grouped[parentKey], {
                        key   = childKey,
                        name  = childKey,
                        count = cCount,
                    })
                end
            end
        end
        for childKey, ids in pairs(childCountsMap) do
            if not reverseMap[childKey] then
                if not grouped["Unknown"] then
                    grouped["Unknown"] = {}
                end
                local cCount = type(ids) == "table" and #ids or ids
                if cCount > 0 then
                    table.insert(grouped["Unknown"], { key = childKey, name = childKey, count = cCount })
                end
            end
        end
        for _, list in pairs(grouped) do
            table.sort(list, function(a, b) return a.name < b.name end)
        end
        childrenByGroup = grouped
    end

    -- Build full group order (append "Unknown" if needed)
    local fullGroupOrder = {}
    for _, gKey in ipairs(groupOrder) do
        fullGroupOrder[#fullGroupOrder + 1] = gKey
    end
    if childrenByGroup["Unknown"] and #childrenByGroup["Unknown"] > 0 then
        local found = false
        for _, gKey in ipairs(fullGroupOrder) do
            if gKey == "Unknown" then found = true; break end
        end
        if not found then
            fullGroupOrder[#fullGroupOrder + 1] = "Unknown"
        end
    end

    -- Create groups
    local prevGroup = nil
    local totalContentH = 0
    for _, groupKey in ipairs(fullGroupOrder) do
        local childList = childrenByGroup[groupKey]
        if childList and #childList > 0 then
            local groupDisplayName = (groupNamesTable and groupNamesTable[groupKey])
                or (type(groupKey) == "string" and groupKey or tostring(groupKey))

            local groupColor = groupColorsTable and groupColorsTable[groupDisplayName] or
                (groupColorsTable and groupColorsTable[groupKey]) or uniformHex or "888888"

            local group, groupCheck, groupLabel, childFrame = CreateFilterGroup(
                content, groupDisplayName, prevGroup, groupColor, childList,
                panel, groupsList, filterWidgets[cwTable], filterWidgets[wTable],
                toggleGroupFn, groupKey)

            local childKeys = {}
            filterWidgets[wTable][groupKey] = {
                check      = groupCheck,
                label      = groupLabel,
                expanded   = false,
                childKeys  = childKeys,
                namePrefix = "|cff" .. groupColor .. groupDisplayName .. "|r",
            }
            if groupDisplayName ~= groupKey then
                filterWidgets[wTable][groupDisplayName] = filterWidgets[wTable][groupKey]
            end

            local childYOff = 0
            for _, cInfo in ipairs(childList) do
                local childColor = groupColor
                if childColorOverrides[cInfo.name] then
                    childColor = childColorOverrides[cInfo.name]
                end

                local cLabelText = "|cff" .. childColor .. cInfo.name .. "|r  |cff888888(" .. cInfo.count .. ")|r"

                local cChk, newCY = CreateFilterCheckbox(childFrame, cLabelText, childYOff, nil,
                    function(checked)
                        if toggleChildFn then toggleChildFn(cInfo.key, checked) end
                        UpdateParentCheckState(
                            filterWidgets[wTable][groupKey],
                            filterWidgets[wTable][groupKey] and filterWidgets[wTable][groupKey].childKeys or {},
                            filterWidgets[cwTable]
                        )
                    end)
                cChk:ClearAllPoints()
                cChk:SetPoint("TOPLEFT", childFrame, "TOPLEFT", 28, childYOff)

                filterWidgets[cwTable][cInfo.key] = {
                    check      = cChk,
                    label      = cChk._label,
                    namePrefix = "|cff" .. childColor .. cInfo.name .. "|r",
                }
                childKeys[#childKeys + 1] = cInfo.key
                childYOff = newCY
            end

            prevGroup = group
            totalContentH = totalContentH + group:GetHeight()
        end
    end

    content:SetHeight(totalContentH)
end

--- theme_group: flat list of theme checkboxes for a single theme group
--- (Culture or Aesthetic). Reads theme metadata from CatalogData.
function SectionBuilders.theme_group(content, sectionDef)
    local groupID = sectionDef.groupID
    local themeGroupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
    local themeNames = NS.CatalogData and NS.CatalogData.ThemeNames
    local byTheme = NS.CatalogData and NS.CatalogData.ByTheme

    if not themeGroupThemes or not themeNames then return end

    local themeIDs = themeGroupThemes[groupID]
    if not themeIDs or #themeIDs == 0 then return end

    local wTable = sectionDef.widgetTable  -- "themes"

    -- Color helpers: per-theme colors or uniform fallback
    local perThemeColors = sectionDef.themeColors
    local uniformHex = "888888"
    if sectionDef.uniformColor then
        local uc = sectionDef.uniformColor
        uniformHex = string.format("%02X%02X%02X",
            math.floor(uc[1] * 255 + 0.5),
            math.floor(uc[2] * 255 + 0.5),
            math.floor(uc[3] * 255 + 0.5))
    end

    local function getThemeHex(themeName)
        if perThemeColors and perThemeColors[themeName] then
            local tc = perThemeColors[themeName]
            return string.format("%02X%02X%02X",
                math.floor(tc[1] * 255 + 0.5),
                math.floor(tc[2] * 255 + 0.5),
                math.floor(tc[3] * 255 + 0.5))
        end
        return uniformHex
    end

    local function getThemeTagColor(themeName)
        if perThemeColors and perThemeColors[themeName] then
            return perThemeColors[themeName]
        end
        return sectionDef.uniformColor
    end

    -- Build sorted theme list
    local themeList = {}
    for _, tid in ipairs(themeIDs) do
        local name = themeNames[tid]
        local count = byTheme and byTheme[tid] and #byTheme[tid] or 0
        if name and count > 0 then
            themeList[#themeList + 1] = { id = tid, name = name, count = count }
        end
    end
    table.sort(themeList, function(a, b) return a.name < b.name end)

    if #themeList == 0 then return end

    -- Resolve toggle function
    local toggleFnName = sectionDef.toggle
    local toggleFn = toggleFnName and function(themeID, checked)
        local fn = NS.UI[toggleFnName]
        if fn then fn(themeID, checked) end
    end or nil

    -- Create checkboxes
    local yOff = -2
    for _, tInfo in ipairs(themeList) do
        local hex = getThemeHex(tInfo.name)
        local labelText = "|cff" .. hex .. tInfo.name .. "|r  |cff888888(" .. tInfo.count .. ")|r"
        local chk, newY = CreateFilterCheckbox(content, labelText, yOff, nil,
            function(checked)
                if toggleFn then toggleFn(tInfo.id, checked) end
            end)

        filterWidgets[wTable][tInfo.id] = {
            check      = chk,
            label      = chk._label,
            namePrefix = "|cff" .. hex .. tInfo.name .. "|r",
            tagColor   = getThemeTagColor(tInfo.name),
        }
        yOff = newY
    end

    content:SetHeight(math.abs(yOff) + 2)
end

-------------------------------------------------------------------------------
-- Filter bar button mapping: which FILTER_SECTIONS go in which dropdown
-------------------------------------------------------------------------------
local FILTER_BAR_BUTTONS = {
    { key = "favorites",  label = "Favorites",  sectionIDs = { "favorites" },  width = 160, isDirect = true },
    { key = "sources",    label = "Source",      sectionIDs = { "sources" },    width = 200 },
    { key = "categories", label = "Category",    sectionIDs = { "categories" }, width = 280 },
    { key = "expansions", label = "Zone",        sectionIDs = { "expansions" }, width = 260 },
    { key = "qualities",  label = "Rarity",      sectionIDs = { "qualities" },  width = 200 },
    { key = "themes",     label = "Theme",       sectionIDs = { "themes_aesthetic", "themes_culture" }, width = 240 },
    { key = "collection", label = "Collection",  sectionIDs = { "collection" }, width = 200 },
}

--- Lookup: sectionDef.id -> FILTER_SECTIONS entry
local function FindSectionDef(sectionID)
    for _, secDef in ipairs(FILTER_SECTIONS) do
        if secDef.id == sectionID then return secDef end
    end
    return nil
end

-------------------------------------------------------------------------------
-- InitFilterBar: creates filter bar, dropdown panels, and populates them
-- Called once from InitCatalog.
-------------------------------------------------------------------------------
local function InitFilterBar(parentFrame)
    -- Reset widget tables
    for key in pairs(filterWidgets) do
        filterWidgets[key] = {}
    end

    -- Click-catcher: closes dropdowns on click-outside.
    -- HIGH strata (below DIALOG-strata dropdown panels).
    -- Forwards clicks to filter buttons to enable dropdown switching.
    clickCatcher = CreateFrame("Frame", nil, UIParent)
    clickCatcher:SetAllPoints()
    clickCatcher:SetFrameStrata("HIGH")
    clickCatcher:SetFrameLevel(100)
    clickCatcher:EnableMouse(true)
    clickCatcher:SetScript("OnMouseDown", function()
        -- If mouse is over a filter bar button, forward the click
        for _, btn in ipairs(filterBarButtons) do
            if btn:IsVisible() and btn:IsMouseOver() then
                if btn._panel then
                    -- Toggle: if this button's dropdown is already open, just close
                    if activeDropdown == btn._panel then
                        CloseActiveDropdown()
                    else
                        CloseActiveDropdown()
                        OpenDropdown(btn._panel, btn)
                    end
                else
                    -- Direct toggle (favorites)
                    CloseActiveDropdown()
                    btn:Click()
                end
                return
            end
        end
        CloseActiveDropdown()
    end)
    clickCatcher:Hide()

    -- Filter bar frame
    filterBar = CreateFrame("Frame", nil, parentFrame)
    filterBar:SetHeight(CatSizing.FilterBarHeight)
    filterBar:SetPoint("TOPLEFT", parentFrame, "TOPLEFT", 1, -43)
    filterBar:SetPoint("TOPRIGHT", parentFrame, "TOPRIGHT", -1, -43)

    -- Filter bar background — stone pattern extracted from asset1 center
    local barBg = filterBar:CreateTexture(nil, "BACKGROUND")
    barBg:SetAllPoints()
    barBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterBarStone", "REPEAT", "CLAMP")
    barBg:SetVertexColor(1, 1, 1, 1)
    local function UpdateBarBgCoords(_, w)
        if not w or w <= 0 then w = filterBar:GetWidth() end
        if w > 0 then
            barBg:SetTexCoord(0, w / 512, 0, 1)
        end
    end
    filterBar:HookScript("OnSizeChanged", UpdateBarBgCoords)
    filterBar:HookScript("OnShow", UpdateBarBgCoords)

    -- Separator below filter bar
    local barSep = filterBar:CreateTexture(nil, "ARTWORK")
    barSep:SetHeight(1)
    barSep:SetPoint("BOTTOMLEFT", filterBar, "BOTTOMLEFT", 0, 0)
    barSep:SetPoint("BOTTOMRIGHT", filterBar, "BOTTOMRIGHT", 0, 0)
    barSep:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Create each filter bar button
    local prevBtn = nil
    for _, btnDef in ipairs(FILTER_BAR_BUTTONS) do
        local btn = CreateFrame("Button", nil, filterBar)
        btn:SetSize(86, CatSizing.FilterBarHeight - 8)

        if prevBtn then
            btn:SetPoint("LEFT", prevBtn, "RIGHT", 3, 0)
        else
            btn:SetPoint("LEFT", filterBar, "LEFT", 6, 0)
        end

        -- Normal state background texture
        local btnBg = btn:CreateTexture(nil, "BACKGROUND")
        btnBg:SetAllPoints()
        btnBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonNormal")
        btn._bg = btnBg

        -- Label (centered, no arrow)
        local btnLabel = btn:CreateFontString(nil, "OVERLAY", "GameFontNormal")
        btnLabel:SetPoint("CENTER", btn, "CENTER", 0, 0)
        btnLabel:SetText(btnDef.label)
        btnLabel:SetTextColor(0.75, 0.75, 0.75, 1)
        btn._label = btnLabel

        -- Hover highlight
        btn:SetScript("OnEnter", function(self)
            if not self._isDropdownOpen and not self._isActive then
                self._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonActive")
                self._label:SetTextColor(1, 0.82, 0, 1)
            end
        end)
        btn:SetScript("OnLeave", function(self)
            if not self._isDropdownOpen and not self._isActive then
                self._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonNormal")
                self._label:SetTextColor(0.75, 0.75, 0.75, 1)
            end
        end)

        btn._isDropdownOpen = false
        btn._btnDef = btnDef

        -- Create dropdown panel (except for direct-toggle "favorites")
        if not btnDef.isDirect then
            local panel = CreateDropdownPanel(parentFrame, btn, btnDef.width)
            local _, scrollChild = AddDropdownScroll(panel, btnDef.width)

            -- Build section content inside the dropdown scrollChild
            scrollChild._secContents = {}
            scrollChild._headerOverhead = 0
            local contentYOff = 0
            for secIdx, secID in ipairs(btnDef.sectionIDs) do
                local secDef = FindSectionDef(secID)
                if secDef then
                    -- For multi-section dropdowns (Theme), add a sub-header
                    if #btnDef.sectionIDs > 1 then
                        local subHeader = scrollChild:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
                        subHeader:SetPoint("TOPLEFT", scrollChild, "TOPLEFT", 6, contentYOff - 2)
                        subHeader:SetText(secDef.title)
                        subHeader:SetTextColor(0.50, 0.50, 0.50, 1)
                        contentYOff = contentYOff - 18
                        scrollChild._headerOverhead = scrollChild._headerOverhead + 18
                    end

                    -- Create a container for this section's content
                    local secContent = CreateFrame("Frame", nil, scrollChild)
                    secContent:SetPoint("TOPLEFT", scrollChild, "TOPLEFT", 0, contentYOff)
                    secContent:SetPoint("RIGHT", scrollChild, "RIGHT", 0, 0)
                    secContent:SetHeight(1)

                    local builder = SectionBuilders[secDef.type]
                    if builder then
                        builder(secContent, secDef, panel)
                    end

                    scrollChild._secContents[#scrollChild._secContents + 1] = secContent
                    contentYOff = contentYOff - secContent:GetHeight()

                    -- Gap between sections in multi-section dropdowns
                    if secIdx < #btnDef.sectionIDs then
                        contentYOff = contentYOff - 6
                        scrollChild._headerOverhead = scrollChild._headerOverhead + 6
                    end
                end
            end

            scrollChild:SetHeight(math.abs(contentYOff))
            FitDropdownToContent(panel, scrollChild)

            -- Recalc helper: updates scrollChild height + panel size after group expand/collapse
            panel._recalcScrollHeight = function()
                local sc = panel._scrollChild
                local total = (sc._headerOverhead or 0)
                for _, sec in ipairs(sc._secContents or {}) do
                    total = total + sec:GetHeight()
                end
                sc:SetHeight(total)
                FitDropdownToContent(panel, sc)
                -- Clamp scroll offset if content shrunk
                local clipH = panel._scroll:GetHeight()
                local maxScroll = math.max(0, total - clipH)
                if panel._scrollOffset > maxScroll then
                    panel._scrollOffset = maxScroll
                    sc:ClearAllPoints()
                    sc:SetPoint("TOPLEFT", panel._scroll, "TOPLEFT", 0, panel._scrollOffset)
                end
                if panel._updateScrollThumb then panel._updateScrollThumb() end
            end

            -- Store panel reference
            dropdownPanels[btnDef.key] = panel
            btn._panel = panel

            btn:SetScript("OnClick", function(self)
                OpenDropdown(panel, self)
            end)
        else
            -- Direct toggle: favorites star button
            btn:SetScript("OnClick", function(self)
                local favWidget = filterWidgets.favorites and filterWidgets.favorites.onlyFavorites
                if favWidget then
                    local newState = not favWidget.check:GetChecked()
                    favWidget.check:SetChecked(newState)
                    local fn = NS.UI.CatalogGrid_ToggleFavorites
                    if fn then fn(newState) end
                end
            end)

            -- Build the favorites widgets even though there's no dropdown panel
            -- (they are needed for filter state tracking and footer tags)
            local secDef = FindSectionDef("favorites")
            if secDef then
                -- Create a hidden content frame to hold the checkbox
                local hiddenContent = CreateFrame("Frame", nil, parentFrame)
                hiddenContent:SetSize(1, 1)
                hiddenContent:SetPoint("TOPLEFT", parentFrame, "TOPLEFT", 0, 0)
                hiddenContent:Hide()
                SectionBuilders.boolean(hiddenContent, secDef)
            end
        end

        filterBarButtons[#filterBarButtons + 1] = btn
        prevBtn = btn
    end

    -- Close dropdowns when main frame hides
    parentFrame:HookScript("OnHide", function()
        CloseActiveDropdown()
    end)
end

--- Update filter bar button appearances based on active filters.
--- Called from UpdateFilterCounts.
local function UpdateFilterBarButtonStates()
    for _, btn in ipairs(filterBarButtons) do
        local btnDef = btn._btnDef
        local hasActive = false

        for _, secID in ipairs(btnDef.sectionIDs) do
            local secDef = FindSectionDef(secID)
            if not secDef then
                -- skip
            elseif secDef.type == "boolean" then
                for _, itemDef in ipairs(secDef.items or {}) do
                    local w = filterWidgets[secDef.id][itemDef.key]
                    if w and w.check:GetChecked() then hasActive = true end
                end
            elseif secDef.type == "boolean_pair" then
                for _, itemDef in ipairs(secDef.items or {}) do
                    local w = filterWidgets[secDef.id][itemDef.key]
                    if w and w.check:GetChecked() then hasActive = true end
                end
            elseif secDef.type == "multiselect" then
                local wt = secDef.widgetTable
                for _, w in pairs(filterWidgets[wt]) do
                    if w.check and w.check:GetChecked() and not w._isProfMaster then
                        hasActive = true
                    end
                end
                if secDef.subGroup then
                    local swt = secDef.subGroup.widgetTable
                    for _, w in pairs(filterWidgets[swt]) do
                        if w.check and w.check:GetChecked() then hasActive = true end
                    end
                end
            elseif secDef.type == "hierarchical" then
                local cwt = secDef.childWidgetTable
                for _, w in pairs(filterWidgets[cwt]) do
                    if w.check and w.check:GetChecked() then hasActive = true end
                end
            elseif secDef.type == "theme_group" then
                local wt = secDef.widgetTable
                local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                    and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
                for _, tid in ipairs(groupThemes) do
                    local w = filterWidgets[wt][tid]
                    if w and w.check:GetChecked() then hasActive = true end
                end
            end
            if hasActive then break end
        end

        btn._isActive = hasActive
        if hasActive or btn._isDropdownOpen then
            btn._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonActive")
            btn._label:SetTextColor(1, 0.82, 0, 1)
        else
            btn._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterButtonNormal")
            btn._label:SetTextColor(0.75, 0.75, 0.75, 1)
        end
    end
end

-------------------------------------------------------------------------------
-- Footer bar: shows active filter tags with X to clear each one
-------------------------------------------------------------------------------
local function RefreshFooterBar()
    if not footerFrame then return end

    -- Hide all existing tags
    for _, tag in ipairs(footerTags) do
        tag:Hide()
    end

    local tagIdx = 0
    local ROW_H = footerFrame._FOOTER_ROW_H or 20
    local TOP_PAD = footerFrame._FOOTER_TOP_PAD or 6
    local MAX_ROWS = footerFrame._FOOTER_MAX_ROWS or 4
    local SCROLL_W = footerFrame._FOOTER_SCROLL_W or 6
    local BOTTOM_PAD = footerFrame._FOOTER_BOTTOM_PAD or 12
    local scrollChild = footerFrame._filterScrollChild
    local hasScroll = scrollChild ~= nil
    local chipParent = hasScroll and scrollChild or footerFrame
    local footerW = footerFrame:GetWidth() or 800
    local labelW = footerFrame._filterLabel
        and (footerFrame._filterLabel:GetStringWidth() or 80) + 8 or 0
    local resetW = footerFrame._resetBtn
        and (footerFrame._resetBtn:GetWidth() + 10) or 0
    local xPos = 8 + labelW + resetW
    local yRow = 0

    local function AddTag(text, color, onRemove)
        tagIdx = tagIdx + 1
        local tag = footerTags[tagIdx]
        if not tag then
            tag = CreateFrame("Button", nil, chipParent)
            tag:SetHeight(18)

            local bg = tag:CreateTexture(nil, "BACKGROUND")
            bg:SetAllPoints()
            bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterPillNormal")
            tag._bg = bg

            tag._label = tag:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            tag._label:SetPoint("LEFT", tag, "LEFT", 6, 0)

            tag._xText = tag:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
            tag._xText:SetPoint("LEFT", tag._label, "RIGHT", 5, 0)
            tag._xText:SetText("|cffaa4444x|r")

            footerTags[tagIdx] = tag
        end

        -- Reparent to chipParent if needed (scroll child vs footer)
        if tag:GetParent() ~= chipParent then
            tag:SetParent(chipParent)
        end

        tag._label:SetText(text)
        if color then
            tag._label:SetTextColor(color[1], color[2], color[3], 1)
        else
            tag._label:SetTextColor(0.75, 0.75, 0.75, 1)
        end

        tag:SetScript("OnClick", function()
            if onRemove then onRemove() end
        end)
        tag:SetScript("OnEnter", function(self)
            if self._bg then
                self._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterPillHover")
            end
        end)
        tag:SetScript("OnLeave", function(self)
            if self._bg then
                self._bg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterPillNormal")
            end
        end)

        -- Size and position
        local tagLabelW = tag._label:GetStringWidth() or 30
        local xW = tag._xText:GetStringWidth() or 8
        local tagWidth = 6 + tagLabelW + 5 + xW + 6
        tag:SetWidth(math.max(tagWidth, 30))

        -- Wrap to next row if tag doesn't fit
        -- Reserve space for scrollbar on the right
        local wrapEdge = footerW - 8 - (hasScroll and (SCROLL_W + 2) or 0)
        if xPos + tagWidth > wrapEdge and tagIdx > 1 then
            yRow = yRow + 1
            xPos = 16
        end

        tag:ClearAllPoints()
        tag:SetPoint("TOPLEFT", chipParent, "TOPLEFT", xPos, -(yRow * ROW_H))
        xPos = xPos + tagWidth + 4

        tag:Show()
    end

    -- Data-driven footer tag generation: loop through FILTER_SECTIONS
    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" then
            -- Single toggle checkbox (Favorites)
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = filterWidgets[secDef.id][itemDef.key]
                if widget and widget.check:GetChecked() then
                    AddTag(itemDef.label, itemDef.color, function()
                        widget.check:SetChecked(false)
                        local fn = NS.UI[secDef.toggle]
                        if fn then fn(false) end
                    end)
                end
            end

        elseif secDef.type == "boolean_pair" then
            -- Inverted toggle pair (Collection)
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = filterWidgets[secDef.id][itemDef.key]
                if widget and widget.check:GetChecked() then
                    AddTag(itemDef.label, itemDef.color, function()
                        widget.check:SetChecked(false)
                        local fn = NS.UI[secDef.toggle]
                        if fn then fn(itemDef.key, true) end
                    end)
                end
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            local subDef = secDef.subGroup

            -- Resolve order list
            local orderList = (NS.CatalogData and NS.CatalogData[secDef.order])
                or NS[secDef.order]
            if not orderList then
                if wTable == "sources" then
                    orderList = { "Vendor", "Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Other" }
                elseif wTable == "qualities" then
                    orderList = { 1, 2, 3, 4, 5, 0 }
                else
                    orderList = {}
                end
            end

            -- Resolve colors table
            local colorsTable
            if secDef.colors then
                colorsTable = NS[secDef.colors] or (NS.CatalogData and NS.CatalogData[secDef.colors])
            end
            -- Resolve names table
            local namesTable
            if secDef.names then
                namesTable = NS[secDef.names] or (NS.CatalogData and NS.CatalogData[secDef.names])
            end

            local skipKey = subDef and subDef.parentKey or nil
            for _, key in ipairs(orderList) do
                local widget = filterWidgets[wTable][key]
                if widget and not widget._isProfMaster and widget.check:GetChecked() then
                    local tagColor = colorsTable and colorsTable[key] or nil
                    local tagName = (namesTable and namesTable[key])
                        or (type(key) == "string" and key or ("Quality " .. key))
                    AddTag(tagName, tagColor, function()
                        widget.check:SetChecked(false)
                        local fn = NS.UI[secDef.toggle]
                        if fn then fn(key, false) end
                    end)
                end
            end

            -- Sub-group children (e.g. Professions)
            if subDef then
                local subOrderList = NS.CatalogData and NS.CatalogData[subDef.order] or {}
                local subWTable = subDef.widgetTable
                for _, childKey in ipairs(subOrderList) do
                    local widget = filterWidgets[subWTable][childKey]
                    if widget and widget.check:GetChecked() then
                        AddTag(childKey, subDef.color, function()
                            widget.check:SetChecked(false)
                            local fn = NS.UI[subDef.toggleChild]
                            if fn then fn(childKey, false) end
                            -- Update parent check state
                            local parentWidget = filterWidgets[wTable][skipKey]
                            if parentWidget and parentWidget.childKeys then
                                UpdateParentCheckState(parentWidget, parentWidget.childKeys, filterWidgets[subWTable])
                            end
                        end)
                    end
                end
            end

        elseif secDef.type == "hierarchical" then
            local wTable = secDef.widgetTable
            local cwTable = secDef.childWidgetTable

            -- Resolve group order
            local groupOrder = NS.CatalogData and NS.CatalogData[secDef.groupOrder] or {}

            -- Resolve child names table (for numeric keys like subcategory IDs)
            local childNamesTable
            if secDef.childNames then
                childNamesTable = NS.CatalogData and NS.CatalogData[secDef.childNames]
            end

            -- Resolve group colors (hex string table)
            local groupColorsTable
            if secDef.groupColors then
                groupColorsTable = NS[secDef.groupColors]
                    or (NS.CatalogData and NS.CatalogData[secDef.groupColors])
            end

            for _, groupKey in ipairs(groupOrder) do
                local gData = filterWidgets[wTable][groupKey]
                if gData and gData.childKeys then
                    -- Convert hex color to RGB table for tags
                    local tagColor = secDef.uniformColor or nil
                    if not tagColor and groupColorsTable then
                        local hexColor = groupColorsTable[groupKey]
                        if hexColor and type(hexColor) == "string" and #hexColor >= 6 then
                            local r = tonumber(hexColor:sub(1, 2), 16) / 255
                            local g = tonumber(hexColor:sub(3, 4), 16) / 255
                            local b = tonumber(hexColor:sub(5, 6), 16) / 255
                            tagColor = { r, g, b }
                        end
                    end

                    for _, cKey in ipairs(gData.childKeys) do
                        local cWidget = filterWidgets[cwTable][cKey]
                        if cWidget and cWidget.check:GetChecked() then
                            -- Resolve display name: use childNames table for numeric keys
                            local tagLabel = (childNamesTable and childNamesTable[cKey])
                                or (type(cKey) == "string" and cKey or tostring(cKey))
                            AddTag(tagLabel, tagColor, function()
                                cWidget.check:SetChecked(false)
                                local fn = NS.UI[secDef.toggleChild]
                                if fn then fn(cKey, false) end
                                UpdateParentCheckState(gData, gData.childKeys, filterWidgets[cwTable])
                            end)
                        end
                    end
                end
            end

        elseif secDef.type == "theme_group" then
            local wTable = secDef.widgetTable  -- "themes"
            local themeNames = NS.CatalogData and NS.CatalogData.ThemeNames or {}
            -- Only iterate themes belonging to THIS group (avoid triplication)
            local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
            for _, tid in ipairs(groupThemes) do
                local widget = filterWidgets[wTable][tid]
                if widget and widget.check:GetChecked() then
                    local tagLabel = themeNames[tid] or tostring(tid)
                    local tagColor = widget.tagColor or secDef.uniformColor or nil
                    AddTag(tagLabel, tagColor, function()
                        widget.check:SetChecked(false)
                        local fn = NS.UI[secDef.toggle]
                        if fn then fn(tid, false) end
                    end)
                end
            end
        end

    end  -- for FILTER_SECTIONS

    -- Update footer height: grow downward, capped at MAX_ROWS with scroll
    local numRows = tagIdx > 0 and (yRow + 1) or 1  -- always at least 1 row for label

    if hasScroll and footerFrame._filterScroll then
        local contentH = numRows * ROW_H
        scrollChild:SetHeight(math.max(1, contentH))

        local visibleRows = math.min(numRows, MAX_ROWS)
        local scrollH = visibleRows * ROW_H
        footerFrame._filterScroll:SetHeight(math.max(ROW_H, scrollH))

        local newH = TOP_PAD + scrollH + BOTTOM_PAD
        footerFrame:SetHeight(math.max(28, newH))

        -- Reset scroll position when filters change
        footerFrame._filterScroll:SetVerticalScroll(0)
        if footerFrame._updateScrollBar then
            footerFrame._updateScrollBar()
        end
    else
        local newH = TOP_PAD + numRows * ROW_H + BOTTOM_PAD
        footerFrame:SetHeight(math.max(28, newH))
    end
end

-- Expose for OnSizeChanged to re-wrap filter chips on resize
NS.UI._RefreshFooterBar = RefreshFooterBar

-------------------------------------------------------------------------------
-- ResetAllFilters: uncheck all filters and clear search text
-------------------------------------------------------------------------------
function NS.UI.ResetAllFilters()
    -- Data-driven: uncheck all widgets across all section types
    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" or secDef.type == "boolean_pair" then
            for _, widget in pairs(filterWidgets[secDef.id]) do
                widget.check:SetChecked(false)
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            for _, widget in pairs(filterWidgets[wTable]) do
                widget.check:SetChecked(false)
            end
            -- Sub-group children
            if secDef.subGroup then
                local subWTable = secDef.subGroup.widgetTable
                for _, widget in pairs(filterWidgets[subWTable]) do
                    widget.check:SetChecked(false)
                end
                -- Update parent check state for the sub-group master
                local skipKey = secDef.subGroup.parentKey
                local parentWidget = filterWidgets[wTable][skipKey]
                if parentWidget and parentWidget.childKeys then
                    UpdateParentCheckState(parentWidget, parentWidget.childKeys, filterWidgets[subWTable])
                end
            end

        elseif secDef.type == "hierarchical" then
            local cwTable = secDef.childWidgetTable
            for _, widget in pairs(filterWidgets[cwTable]) do
                widget.check:SetChecked(false)
            end
            -- Update parent check states for all groups
            local wTable = secDef.widgetTable
            for _, gData in pairs(filterWidgets[wTable]) do
                if gData.childKeys then
                    UpdateParentCheckState(gData, gData.childKeys, filterWidgets[cwTable])
                end
            end

        elseif secDef.type == "theme_group" then
            local wTable = secDef.widgetTable
            -- Only iterate themes belonging to THIS group
            local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
            for _, tid in ipairs(groupThemes) do
                local widget = filterWidgets[wTable][tid]
                if widget then
                    widget.check:SetChecked(false)
                end
            end
        end

    end

    -- Clear search box
    if NS.UI._catalogSearchBox then
        NS.UI._catalogSearchBox:SetText("")
        NS.UI._catalogSearchBox:ClearFocus()
    end

    -- Reset grid filter state and reapply
    if NS.UI.CatalogGrid_ResetFilters then
        NS.UI.CatalogGrid_ResetFilters()
    end
end

-------------------------------------------------------------------------------
-- RestoreFiltersFromSaved: sync filter checkboxes to restored filter state.
-- Called once during init when rememberFilters is active.
-------------------------------------------------------------------------------
local function RestoreFiltersFromSaved()
    local fs = NS.UI.CatalogGrid_GetFilterState and NS.UI.CatalogGrid_GetFilterState()
    if not fs then return end

    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" then
            -- Single toggle (Favorites)
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = filterWidgets[secDef.id][itemDef.key]
                if widget then
                    widget.check:SetChecked(fs[itemDef.key] == true)
                end
            end

        elseif secDef.type == "boolean_pair" then
            -- Inverted pair (Collection): checked = "hide" → invert filterState
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = filterWidgets[secDef.id][itemDef.key]
                if widget then
                    widget.check:SetChecked(not fs[itemDef.key])
                end
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            local fsDim = fs[wTable]
            if fsDim then
                for key, widget in pairs(filterWidgets[wTable]) do
                    if widget.check then
                        widget.check:SetChecked(fsDim[key] == true)
                    end
                end
            end
            -- Sub-group children (e.g. Professions)
            if secDef.subGroup then
                local subWTable = secDef.subGroup.widgetTable
                local fsSub = fs[subWTable]
                if fsSub then
                    for key, widget in pairs(filterWidgets[subWTable]) do
                        if widget.check then
                            widget.check:SetChecked(fsSub[key] == true)
                        end
                    end
                end
                -- Update parent check state
                local skipKey = secDef.subGroup.parentKey
                local parentWidget = filterWidgets[wTable][skipKey]
                if parentWidget and parentWidget.childKeys then
                    UpdateParentCheckState(parentWidget, parentWidget.childKeys, filterWidgets[subWTable])
                end
            end

        elseif secDef.type == "hierarchical" then
            local cwTable = secDef.childWidgetTable
            local fsDim = fs[cwTable]
            if fsDim then
                for key, widget in pairs(filterWidgets[cwTable]) do
                    if widget.check then
                        widget.check:SetChecked(fsDim[key] == true)
                    end
                end
            end
            -- Update parent check states
            local wTable = secDef.widgetTable
            for _, gData in pairs(filterWidgets[wTable]) do
                if gData.childKeys then
                    UpdateParentCheckState(gData, gData.childKeys, filterWidgets[cwTable])
                end
            end

        elseif secDef.type == "theme_group" then
            local wTable = secDef.widgetTable
            local fsDim = fs[wTable]
            if fsDim then
                local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                    and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
                for _, tid in ipairs(groupThemes) do
                    local widget = filterWidgets[wTable][tid]
                    if widget then
                        widget.check:SetChecked(fsDim[tid] == true)
                    end
                end
            end
        end

    end
end

-------------------------------------------------------------------------------
-- UpdateFilterCounts: refresh all filter labels with dynamic counts
-- Called from CatalogGrid after every filter application.
-- counts = { sources={}, zones={}, qualities={}, professions={},
--            subcategories={}, collection={ collected=N, notCollected=N },
--            favorites=N }
-------------------------------------------------------------------------------
function NS.UI.UpdateFilterCounts(counts)
    if not counts then return end

    -- Data-driven count updates across all section types
    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" then
            -- Single value count (e.g. favorites)
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = filterWidgets[secDef.id][itemDef.key]
                if widget then
                    local c = 0
                    if itemDef.countKey then
                        c = counts[itemDef.countKey] or 0
                    end
                    widget.label:SetText(widget.namePrefix .. "  |cff40c8c8(" .. c .. ")|r")
                end
            end

        elseif secDef.type == "boolean_pair" then
            -- Keyed counts from a sub-table (e.g. collection.collected)
            for key, widget in pairs(filterWidgets[secDef.id]) do
                local c = counts.collection and counts.collection[key] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            -- Main items: look up counts from the dimension table
            local countsDim = counts[wTable]  -- e.g. counts.sources, counts.qualities
            for key, widget in pairs(filterWidgets[wTable]) do
                local c = countsDim and countsDim[key] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end
            -- Sub-group children (e.g. professions)
            if secDef.subGroup then
                local subWTable = secDef.subGroup.widgetTable
                local subCountsDim = counts[subWTable]
                for childKey, widget in pairs(filterWidgets[subWTable]) do
                    local c = subCountsDim and subCountsDim[childKey] or 0
                    widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
                end
            end

        elseif secDef.type == "hierarchical" then
            local wTable = secDef.widgetTable
            local cwTable = secDef.childWidgetTable
            -- Determine which counts dimension to use for children
            local childCountsDim = counts[cwTable]  -- e.g. counts.zones, counts.subcategories

            -- Update child labels
            for cKey, widget in pairs(filterWidgets[cwTable]) do
                local c = childCountsDim and childCountsDim[cKey] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end

            -- Update group labels (sum of child counts)
            for _, gData in pairs(filterWidgets[wTable]) do
                if gData.childKeys then
                    local total = 0
                    for _, cKey in ipairs(gData.childKeys) do
                        total = total + (childCountsDim and childCountsDim[cKey] or 0)
                    end
                    gData.label:SetText(gData.namePrefix .. "  |cff888888(" .. total .. ")|r")
                end
            end

        elseif secDef.type == "theme_group" then
            -- Theme checkboxes: counts come from dynCounts.themes
            local themeCounts = counts.themes
            local wTable = secDef.widgetTable  -- "themes"
            -- Only iterate themes belonging to THIS group
            local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
            for _, tid in ipairs(groupThemes) do
                local widget = filterWidgets[wTable][tid]
                if widget then
                    local c = themeCounts and themeCounts[tid] or 0
                    widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
                end
            end
        end

    end

    -- Update filter bar button highlight states (before progress bar so we can check _isActive)
    UpdateFilterBarButtonStates()

    -- Update progress bar with collection totals from the filtered set
    local collected = counts.progressCollected or 0
    local total = counts.progressTotal or 0
    NS.UI.UpdateProgressBar(collected, total)

    -- Refresh footer bar active filter tags
    RefreshFooterBar()
end

-------------------------------------------------------------------------------
-- Progress bar update
-------------------------------------------------------------------------------
function NS.UI.UpdateProgressBar(collected, total)
    if not progressBar then return end
    local pct = total > 0 and (collected / total) or 0
    progressBar._lastPct = pct
    local barWidth = math.max(1, (progressBar:GetWidth() - 2) * pct)
    progressFill:SetWidth(barWidth)
    -- Show "Filters:" prefix only when filters are active
    local hasFilters = false
    for _, btn in ipairs(filterBarButtons) do
        if btn._isActive then hasFilters = true; break end
    end
    local prefix = hasFilters and "Filters: " or ""
    progressLabel:SetText(string.format("%s%d / %d Collected (%.1f%%)", prefix, collected, total, pct * 100))
end

-------------------------------------------------------------------------------
-- InitCatalog: Creates the main catalog frame
-------------------------------------------------------------------------------
function NS.UI.InitCatalog()
    if catalogFrame then return end

    CatSizing = NS.CatalogSizing

    -- Main frame — solid dark background
    catalogFrame = CreateFrame("Frame", "HearthAndSeekCatalogFrame", UIParent,
                               "BackdropTemplate")
    catalogFrame:SetSize(CatSizing.FrameWidth, CatSizing.FrameHeight)
    catalogFrame:SetPoint("CENTER", UIParent, "CENTER", 0, 30)
    catalogFrame:SetBackdrop(BACKDROP_SOLID)
    catalogFrame:SetBackdropColor(0.06, 0.06, 0.08, 0.95)
    catalogFrame:SetBackdropBorderColor(0.20, 0.20, 0.22, 1)
    catalogFrame:SetFrameStrata("MEDIUM")
    catalogFrame:SetToplevel(true)      -- click brings to front
    catalogFrame:SetMovable(true)
    catalogFrame:EnableMouse(true)
    catalogFrame:SetResizable(true)
    if catalogFrame.SetResizeBounds then
        catalogFrame:SetResizeBounds(CatSizing.FrameWidth, CatSizing.FrameHeight)
    end

    local function SaveFramePosition()
        catalogFrame:StopMovingOrSizing()
        local point, _, relativePoint, x, y = catalogFrame:GetPoint()
        if NS.db then
            NS.db.catalogPosition = { point, nil, relativePoint, x, y }
        end
    end

    local function SaveFrameSize()
        catalogFrame:StopMovingOrSizing()
        if NS.db then
            NS.db.catalogSize = {
                catalogFrame:GetWidth(),
                catalogFrame:GetHeight(),
            }
        end
        SaveFramePosition()
    end
    catalogFrame:SetScript("OnMouseDown", function(self, button)
        if button == "LeftButton" then self:StartMoving() end
    end)
    catalogFrame:SetScript("OnMouseUp", function(self, button)
        if button == "LeftButton" then SaveFramePosition() end
    end)

    -- ESC closes frame
    tinsert(UISpecialFrames, "HearthAndSeekCatalogFrame")

    -- Title bar (draggable)
    local titleBar = CreateFrame("Frame", nil, catalogFrame)
    titleBar:SetHeight(42)
    titleBar:SetPoint("TOPLEFT", 1, -1)
    titleBar:SetPoint("TOPRIGHT", -1, -1)
    titleBar:EnableMouse(true)
    titleBar:SetScript("OnMouseDown", function(_, button)
        if button == "LeftButton" then catalogFrame:StartMoving() end
    end)
    titleBar:SetScript("OnMouseUp", function(_, button)
        if button == "LeftButton" then SaveFramePosition() end
    end)

    -- Title bar background — uses the metallic strip texture
    local titleBg = titleBar:CreateTexture(nil, "BACKGROUND")
    titleBg:SetAllPoints()
    titleBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\FilterBarBackground", "REPEAT", "CLAMP")
    local function UpdateTitleBgCoords(_, w)
        if not w or w <= 0 then w = titleBar:GetWidth() end
        if w > 0 then
            titleBg:SetTexCoord(0, w / 512, 0, 1)
        end
    end
    titleBar:HookScript("OnSizeChanged", UpdateTitleBgCoords)
    titleBar:HookScript("OnShow", UpdateTitleBgCoords)

    -- Title bar bottom separator
    local titleSep = titleBar:CreateTexture(nil, "ARTWORK")
    titleSep:SetHeight(1)
    titleSep:SetPoint("BOTTOMLEFT", titleBar, "BOTTOMLEFT", 0, 0)
    titleSep:SetPoint("BOTTOMRIGHT", titleBar, "BOTTOMRIGHT", 0, 0)
    titleSep:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Title text (Morpheus = WoW quest title font, ornate medieval style)
    local titleText = titleBar:CreateFontString(nil, "OVERLAY")
    titleText:SetFont("Fonts\\MORPHEUS.TTF", 24)
    titleText:SetPoint("CENTER", titleBar, "CENTER", 0, 0)
    titleText:SetText("Hearth & Seek")
    titleText:SetTextColor(1, 0.82, 0, 1)
    titleText:SetShadowOffset(1, -1)
    titleText:SetShadowColor(0, 0, 0, 0.7)

    -- Close button (centered vertically on title bar)
    local closeBtn = CreateFrame("Button", nil, titleBar, "UIPanelCloseButton")
    closeBtn:SetPoint("RIGHT", titleBar, "RIGHT", -2, 0)
    closeBtn:SetScript("OnClick", function() catalogFrame:Hide() end)

    -- Settings button (gear icon with red highlight matching close button)
    local settingsBtn = CreateFrame("Button", nil, titleBar)
    settingsBtn:SetSize(32, 32)
    settingsBtn:SetPoint("RIGHT", closeBtn, "LEFT", -2, 0)
    -- Gear icon
    local settingsIcon = settingsBtn:CreateTexture(nil, "ARTWORK")
    settingsIcon:SetSize(18, 18)
    settingsIcon:SetPoint("CENTER", 0, 0)
    settingsIcon:SetAtlas("QuestLog-Icon-Setting")
    settingsIcon:SetVertexColor(1, 0.9, 0.45, 1)
    -- Red highlight on hover (same texture as close button)
    local settingsHL = settingsBtn:CreateTexture(nil, "HIGHLIGHT")
    settingsHL:SetAllPoints()
    settingsHL:SetTexture("Interface\\Buttons\\UI-Panel-MinimizeButton-Highlight")
    settingsHL:SetBlendMode("ADD")
    settingsBtn:SetScript("OnEnter", function()
        settingsIcon:SetVertexColor(1, 0.85, 0.2, 1)
    end)
    settingsBtn:SetScript("OnLeave", function()
        if not catalogFrame._settingsPanel:IsShown() then
            settingsIcon:SetVertexColor(1, 0.9, 0.45, 1)
        end
    end)
    catalogFrame._settingsBtn = settingsBtn
    catalogFrame._settingsIcon = settingsIcon
    NS.UI.RegisterWhatsNewAnchor("settingsBtn", settingsBtn)

    -- Settings panel (opens to the right of the main window)
    local settingsPanel = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    settingsPanel:SetWidth(220)
    settingsPanel:SetHeight(400)
    settingsPanel:SetPoint("TOPLEFT", catalogFrame, "TOPRIGHT", 2, 0)
    settingsPanel:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8X8",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        edgeSize = 12,
        insets   = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    settingsPanel:SetBackdropColor(0.06, 0.06, 0.08, 0.97)
    settingsPanel:SetBackdropBorderColor(0.20, 0.20, 0.22, 1)
    settingsPanel:EnableMouse(true)
    settingsPanel:Hide()
    catalogFrame._settingsPanel = settingsPanel

    -- Separator helper (reusable for section dividers)
    local function CreateSettingsSep(parent, anchorFrame, offsetY)
        local sep = parent:CreateTexture(nil, "ARTWORK")
        sep:SetHeight(1)
        sep:SetPoint("TOPLEFT", anchorFrame, "BOTTOMLEFT", 0, offsetY or -10)
        sep:SetPoint("RIGHT", parent, "RIGHT", -12, 0)
        sep:SetColorTexture(0.25, 0.25, 0.28, 0.6)
        return sep
    end

    -- Settings header
    local settingsHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    settingsHeader:SetPoint("TOPLEFT", 12, -10)
    settingsHeader:SetText("|cffffd200Settings|r")

    -- === DISPLAY section ===
    local displaySep = CreateSettingsSep(settingsPanel, settingsHeader, -10)

    local displayHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    displayHeader:SetPoint("TOPLEFT", displaySep, "BOTTOMLEFT", 0, -8)
    displayHeader:SetText("DISPLAY")
    displayHeader:SetTextColor(1, 0.82, 0, 0.8)

    -- Icon Size slider
    local sliderLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    sliderLabel:SetPoint("TOPLEFT", displayHeader, "BOTTOMLEFT", 0, -8)
    sliderLabel:SetText("Icon Size")
    sliderLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    local sliderValue = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    sliderValue:SetPoint("RIGHT", settingsPanel, "RIGHT", -12, 0)
    sliderValue:SetPoint("TOP", sliderLabel, "TOP", 0, 0)
    sliderValue:SetTextColor(0.7, 0.7, 0.7, 1)

    local DEFAULT_ICON_SIZE = 110
    local iconSlider = CreateFrame("Slider", nil, settingsPanel, "OptionsSliderTemplate")
    iconSlider:SetSize(180, 16)
    iconSlider:SetPoint("TOPLEFT", sliderLabel, "BOTTOMLEFT", 8, -10)
    iconSlider:SetMinMaxValues(0.5, 1.5)
    iconSlider:SetValueStep(0.05)
    iconSlider:SetObeyStepOnDrag(true)
    iconSlider.Low:SetText("Small")
    iconSlider.High:SetText("Large")

    local currentMult = (NS.db and NS.db.settings and NS.db.settings.iconSizeMultiplier)
        or 1.0
    iconSlider:SetValue(currentMult)
    sliderValue:SetText(math.floor(DEFAULT_ICON_SIZE * currentMult) .. "px")

    iconSlider:SetScript("OnValueChanged", function(_, value)
        local mult = math.floor(value * 20 + 0.5) / 20  -- snap to 0.05 steps
        local newSize = math.floor(DEFAULT_ICON_SIZE * mult)
        sliderValue:SetText(newSize .. "px")
        CatSizing.GridItemSize = newSize
        if NS.db and NS.db.settings then
            NS.db.settings.iconSizeMultiplier = mult
        end
        if NS.UI.CatalogGrid_Reflow then
            NS.UI.CatalogGrid_Reflow()
        end
    end)

    -- === GENERAL section ===
    local generalSep = CreateSettingsSep(settingsPanel, iconSlider, -14)

    local generalHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    generalHeader:SetPoint("TOPLEFT", generalSep, "BOTTOMLEFT", 0, -8)
    generalHeader:SetText("GENERAL")
    generalHeader:SetTextColor(1, 0.82, 0, 0.8)

    -- "Show minimap icon" checkbox
    local LDBIcon = LibStub("LibDBIcon-1.0", true)

    local minimapCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    minimapCheck:SetSize(22, 22)
    minimapCheck:SetPoint("TOPLEFT", generalHeader, "BOTTOMLEFT", -2, -6)
    minimapCheck:SetChecked(not (NS.db and NS.db.minimapIcon and NS.db.minimapIcon.hide))
    minimapCheck:SetScript("OnClick", function(self)
        local hide = not self:GetChecked()
        if NS.db and NS.db.minimapIcon then
            NS.db.minimapIcon.hide = hide
        end
        if LDBIcon then
            if hide then
                LDBIcon:Hide("HearthAndSeek")
            else
                LDBIcon:Show("HearthAndSeek")
            end
        end
    end)
    local minimapLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    minimapLabel:SetPoint("LEFT", minimapCheck, "RIGHT", 2, 0)
    minimapLabel:SetText("Show minimap icon")
    minimapLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- "Show new feature tips" checkbox
    local whatsNewCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    whatsNewCheck:SetSize(22, 22)
    whatsNewCheck:SetPoint("TOPLEFT", minimapCheck, "BOTTOMLEFT", 0, -4)
    whatsNewCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.showWhatsNew == false))
    whatsNewCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.showWhatsNew = self:GetChecked()
        end
    end)
    local whatsNewLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    whatsNewLabel:SetPoint("LEFT", whatsNewCheck, "RIGHT", 2, 0)
    whatsNewLabel:SetText("Show new feature tips")
    whatsNewLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- "Remember filter selections" checkbox
    local rememberCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    rememberCheck:SetSize(22, 22)
    rememberCheck:SetPoint("TOPLEFT", whatsNewCheck, "BOTTOMLEFT", 0, -4)
    rememberCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.rememberFilters == false))
    rememberCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.rememberFilters = self:GetChecked()
            if not self:GetChecked() then
                NS.db.savedFilters = nil
            end
        end
    end)
    local rememberLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    rememberLabel:SetPoint("LEFT", rememberCheck, "RIGHT", 2, 0)
    rememberLabel:SetText("Remember filter selections")
    rememberLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- === VENDOR OVERLAYS section ===
    local vendorSep = CreateSettingsSep(settingsPanel, rememberCheck, -10)

    local vendorHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    vendorHeader:SetPoint("TOPLEFT", vendorSep, "BOTTOMLEFT", 0, -8)
    vendorHeader:SetText("VENDOR & CRAFTING OVERLAYS")
    vendorHeader:SetTextColor(1, 0.82, 0, 0.8)

    -- "Show collected checkmark" checkbox
    local vendorOwnedCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    vendorOwnedCheck:SetSize(22, 22)
    vendorOwnedCheck:SetPoint("TOPLEFT", vendorHeader, "BOTTOMLEFT", -2, -6)
    vendorOwnedCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.showVendorOwned == false))
    vendorOwnedCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.showVendorOwned = self:GetChecked()
        end
        if NS.VendorOverlay and NS.VendorOverlay.Refresh then
            NS.VendorOverlay.Refresh()
        end
        if NS.CraftingOrderOverlay and NS.CraftingOrderOverlay.Refresh then
            NS.CraftingOrderOverlay.Refresh()
        end
    end)
    local vendorOwnedLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    vendorOwnedLabel:SetPoint("LEFT", vendorOwnedCheck, "RIGHT", 2, 0)
    vendorOwnedLabel:SetText("|TInterface\\RaidFrame\\ReadyCheck-Ready:14|t  Collected")
    vendorOwnedLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- "Show uncollected with bonus (blue exclamation)" checkbox
    local vendorBonusCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    vendorBonusCheck:SetSize(22, 22)
    vendorBonusCheck:SetPoint("TOPLEFT", vendorOwnedCheck, "BOTTOMLEFT", 0, -4)
    vendorBonusCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.showVendorBonus == false))
    vendorBonusCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.showVendorBonus = self:GetChecked()
        end
        if NS.VendorOverlay and NS.VendorOverlay.Refresh then
            NS.VendorOverlay.Refresh()
        end
        if NS.CraftingOrderOverlay and NS.CraftingOrderOverlay.Refresh then
            NS.CraftingOrderOverlay.Refresh()
        end
    end)
    local vendorBonusLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    vendorBonusLabel:SetPoint("LEFT", vendorBonusCheck, "RIGHT", 2, 0)
    vendorBonusLabel:SetText("|TInterface\\GossipFrame\\DailyQuestIcon:14|t  Not collected (bonus)")
    vendorBonusLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- "Show uncollected without bonus (yellow exclamation)" checkbox
    local vendorUncollCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    vendorUncollCheck:SetSize(22, 22)
    vendorUncollCheck:SetPoint("TOPLEFT", vendorBonusCheck, "BOTTOMLEFT", 0, -4)
    vendorUncollCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.showVendorUncollected == false))
    vendorUncollCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.showVendorUncollected = self:GetChecked()
        end
        if NS.VendorOverlay and NS.VendorOverlay.Refresh then
            NS.VendorOverlay.Refresh()
        end
        if NS.CraftingOrderOverlay and NS.CraftingOrderOverlay.Refresh then
            NS.CraftingOrderOverlay.Refresh()
        end
    end)
    local vendorUncollLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    vendorUncollLabel:SetPoint("LEFT", vendorUncollCheck, "RIGHT", 2, 0)
    vendorUncollLabel:SetText("|TInterface\\GossipFrame\\AvailableQuestIcon:14|t  Not collected (no bonus)")
    vendorUncollLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- === TOOLTIP section ===
    local tooltipSep = CreateSettingsSep(settingsPanel, vendorUncollCheck, -10)

    local tooltipHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    tooltipHeader:SetPoint("TOPLEFT", tooltipSep, "BOTTOMLEFT", 0, -8)
    tooltipHeader:SetText("TOOLTIP")
    tooltipHeader:SetTextColor(1, 0.82, 0, 0.8)

    local tooltipModelCheck = CreateFrame("CheckButton", nil, settingsPanel, "UICheckButtonTemplate")
    tooltipModelCheck:SetSize(22, 22)
    tooltipModelCheck:SetPoint("TOPLEFT", tooltipHeader, "BOTTOMLEFT", -2, -6)
    tooltipModelCheck:SetChecked(not (NS.db and NS.db.settings and NS.db.settings.showTooltipModel == false))
    tooltipModelCheck:SetScript("OnClick", function(self)
        if NS.db and NS.db.settings then
            NS.db.settings.showTooltipModel = self:GetChecked()
        end
        if NS.TooltipModelPreview and NS.TooltipModelPreview.Refresh then
            NS.TooltipModelPreview.Refresh()
        end
    end)
    local tooltipModelLabel = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    tooltipModelLabel:SetPoint("LEFT", tooltipModelCheck, "RIGHT", 2, 0)
    tooltipModelLabel:SetText("Show 3D model on tooltip")
    tooltipModelLabel:SetTextColor(0.9, 0.9, 0.9, 1)

    -- === RESTORE DEFAULTS section ===
    local restoreSep = CreateSettingsSep(settingsPanel, tooltipModelCheck, -10)

    local restoreHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    restoreHeader:SetPoint("TOPLEFT", restoreSep, "BOTTOMLEFT", 0, -8)
    restoreHeader:SetText("RESTORE DEFAULTS")
    restoreHeader:SetTextColor(1, 0.82, 0, 0.8)

    -- Restore Default Icon Size button
    local resetIconBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    resetIconBtn:SetSize(180, 22)
    resetIconBtn:SetPoint("TOPLEFT", restoreHeader, "BOTTOMLEFT", 8, -8)
    resetIconBtn:SetText("Icon Size")
    resetIconBtn:SetScript("OnClick", function()
        iconSlider:SetValue(1.0)
    end)

    -- Restore Default Window Size button
    local resetWindowBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    resetWindowBtn:SetSize(180, 22)
    resetWindowBtn:SetPoint("TOPLEFT", resetIconBtn, "BOTTOMLEFT", 0, -4)
    resetWindowBtn:SetText("Window Size")
    resetWindowBtn:SetScript("OnClick", function()
        catalogFrame:SetSize(CatSizing.FrameWidth, CatSizing.FrameHeight)
        if NS.db then
            NS.db.catalogSize = nil
        end
    end)

    -- Restore Default Filters button (resets active filter selections)
    local resetFiltersBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    resetFiltersBtn:SetSize(180, 22)
    resetFiltersBtn:SetPoint("TOPLEFT", resetWindowBtn, "BOTTOMLEFT", 0, -4)
    resetFiltersBtn:SetText("Reset Filters")
    resetFiltersBtn:SetScript("OnClick", function()
        if NS.UI.ResetAllFilters then
            NS.UI.ResetAllFilters()
        end
    end)

    -- Clear Favorites button
    local clearFavBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    clearFavBtn:SetSize(180, 22)
    clearFavBtn:SetPoint("TOPLEFT", resetFiltersBtn, "BOTTOMLEFT", 0, -4)
    clearFavBtn:SetText("Clear All Favorites")
    clearFavBtn:SetScript("OnClick", function()
        if NS.favorites then
            wipe(NS.favorites)
        end
        if NS.UI.CatalogGrid_ApplyFilters then
            NS.UI.CatalogGrid_ApplyFilters()
        end
    end)

    -- Toggle settings panel on button click
    settingsBtn:SetScript("OnClick", function()
        if settingsPanel:IsShown() then
            settingsPanel:Hide()
            settingsIcon:SetVertexColor(1, 0.9, 0.45, 1)
        else
            settingsPanel:Show()
            settingsIcon:SetVertexColor(1, 0.85, 0.2, 1)
        end
    end)

    -- Hide settings panel when main frame hides
    catalogFrame:HookScript("OnHide", function()
        settingsPanel:Hide()
        settingsIcon:SetVertexColor(1, 0.9, 0.45, 1)
        -- Dismiss any active What's New callouts
        if NS.UI.DismissWhatsNew then
            NS.UI.DismissWhatsNew()
        end
    end)

    -- Search box
    local searchBox = CreateFrame("EditBox", "HearthAndSeekCatalogSearch", titleBar,
                                   "SearchBoxTemplate")
    searchBox:SetSize(CatSizing.SearchBoxWidth, 26)
    searchBox:SetPoint("RIGHT", settingsBtn, "LEFT", -8, 0)
    searchBox:SetAutoFocus(false)
    searchBox:SetScript("OnTextChanged", function(self)
        SearchBoxTemplate_OnTextChanged(self)
        if NS.UI._catalogSearchTimer then
            NS.UI._catalogSearchTimer:Cancel()
        end
        NS.UI._catalogSearchTimer = C_Timer.NewTimer(0.2, function()
            if NS.UI.CatalogGrid_ApplyFilters then
                NS.UI.CatalogGrid_ApplyFilters()
            end
        end)
    end)
    searchBox:SetScript("OnEscapePressed", function(self)
        self:ClearFocus()
    end)
    searchBox.Instructions:SetText("Search name, keyword, vendor, zone...")
    NS.UI._catalogSearchBox = searchBox
    NS.UI.RegisterWhatsNewAnchor("searchBox", searchBox)

    -- Build the filter bar, dropdown panels, and populate all filter widgets
    InitFilterBar(catalogFrame)
    NS.UI.RegisterWhatsNewAnchor("filterBar", filterBar)

    -- Progress bar (bottom of main frame)
    progressBar = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    progressBar:SetHeight(CatSizing.ProgressBarHeight)
    progressBar:SetPoint("BOTTOMLEFT", catalogFrame, "BOTTOMLEFT", 1, 1)
    progressBar:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -1, 1)
    progressBar:SetBackdrop(BACKDROP_SOLID)
    progressBar:SetBackdropColor(0.03, 0.03, 0.05, 0.9)
    progressBar:SetBackdropBorderColor(0.15, 0.15, 0.18, 1)

    -- Progress fill texture
    progressFill = progressBar:CreateTexture(nil, "ARTWORK")
    progressFill:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\ProgressBarFill")
    progressFill:SetPoint("TOPLEFT", 1, -1)
    progressFill:SetPoint("BOTTOMLEFT", 1, 1)
    progressFill:SetWidth(1)

    -- Progress label
    progressLabel = progressBar:CreateFontString(nil, "OVERLAY", "GameFontHighlightExtraSmall")
    progressLabel:SetPoint("CENTER")
    progressLabel:SetTextColor(0.95, 0.90, 0.70, 1)

    -- Re-render fill width when frame resizes
    progressBar:SetScript("OnSizeChanged", function(self)
        local pct = self._lastPct or 0
        local barWidth = math.max(1, (self:GetWidth() - 2) * pct)
        progressFill:SetWidth(barWidth)
    end)

    -- Detail panel (right) — anchored above progress bar
    local detail = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    detail:SetWidth(CatSizing.DetailPanelWidth)
    detail:SetPoint("TOPRIGHT", catalogFrame, "TOPRIGHT", -1, -(43 + CatSizing.FilterBarHeight))
    detail:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -1, CatSizing.ProgressBarHeight + 1)
    detail:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    detail:SetBackdropColor(0.05, 0.05, 0.07, 1)

    -- Vertical separator: grid | detail
    local sepRight = catalogFrame:CreateTexture(nil, "ARTWORK")
    sepRight:SetWidth(1)
    sepRight:SetPoint("TOPRIGHT", detail, "TOPLEFT", 0, 0)
    sepRight:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", 0, 0)
    sepRight:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Grid area (center) — below filter bar, above count text, left of detail
    -- Clips descendants so scrolled items don't bleed past the grid bounds
    local grid = CreateFrame("Frame", nil, catalogFrame)
    grid:SetPoint("TOPLEFT", catalogFrame, "TOPLEFT", 1, -(43 + CatSizing.FilterBarHeight))
    grid:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", -1, 18)
    grid:SetClipsChildren(true)

    -- "Showing X items" text — below the clipped grid, above the progress bar
    local countText = catalogFrame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    countText:SetPoint("TOP", grid, "BOTTOM", 0, -2)
    countText:SetTextColor(0.45, 0.45, 0.45, 1)
    grid._countText = countText

    -- Initialize sub-components
    if NS.UI.InitCatalogGrid then
        NS.UI.InitCatalogGrid(grid)
    end
    if NS.UI.InitCatalogDetail then
        NS.UI.InitCatalogDetail(detail)
    end

    -- Restore filter checks from saved filter state
    if NS.db and NS.db.settings and NS.db.settings.rememberFilters and NS.db.savedFilters then
        RestoreFiltersFromSaved()
    end

    -- Footer bar (active filter tags — always visible, below the main window)
    local footer = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    footer:SetHeight(44)
    footer:SetPoint("TOPLEFT", catalogFrame, "BOTTOMLEFT", 0, 0)
    footer:SetPoint("TOPRIGHT", catalogFrame, "BOTTOMRIGHT", 0, 0)
    footer:SetBackdrop(BACKDROP_SOLID)
    footer:SetBackdropColor(0.06, 0.06, 0.08, 0.95)
    footer:SetBackdropBorderColor(0.20, 0.20, 0.22, 1)

    local footerSep = footer:CreateTexture(nil, "ARTWORK")
    footerSep:SetHeight(1)
    footerSep:SetPoint("TOPLEFT", footer, "TOPLEFT", 1, 0)
    footerSep:SetPoint("TOPRIGHT", footer, "TOPRIGHT", -1, 0)
    footerSep:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Metallic border strip at bottom of footer
    local footerBorder = footer:CreateTexture(nil, "ARTWORK")
    footerBorder:SetHeight(8)
    footerBorder:SetPoint("BOTTOMLEFT", footer, "BOTTOMLEFT", 0, 0)
    footerBorder:SetPoint("BOTTOMRIGHT", footer, "BOTTOMRIGHT", 0, 0)
    footerBorder:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\MetallicBorderStrip", "REPEAT", "CLAMP")
    local function UpdateFooterBorderCoords(_, w)
        if not w or w <= 0 then w = footer:GetWidth() end
        if w > 0 then
            footerBorder:SetTexCoord(0, w / 512, 0, 1)
        end
    end
    footer:HookScript("OnSizeChanged", UpdateFooterBorderCoords)
    footer:HookScript("OnShow", UpdateFooterBorderCoords)

    footerFrame = footer

    local filterLabel = footer:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    filterLabel:SetPoint("TOPLEFT", footer, "TOPLEFT", 8, -4)
    filterLabel:SetText("|cff888888Active Filters:|r")
    footer._filterLabel = filterLabel

    -- Reset Filters button (after "Active Filters:" label)
    local resetBtn = CreateFrame("Button", nil, footer)
    resetBtn:SetSize(50, 16)
    resetBtn:SetPoint("LEFT", filterLabel, "RIGHT", 6, 0)
    local resetBg = resetBtn:CreateTexture(nil, "BACKGROUND")
    resetBg:SetAllPoints()
    resetBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\ResetButtonNormal")
    local resetLabel = resetBtn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    resetLabel:SetPoint("CENTER", resetBtn, "CENTER", 0, 0)
    resetLabel:SetText("|cffcc8888Reset|r")
    resetBtn:SetScript("OnEnter", function()
        resetBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\ResetButtonHover")
        resetLabel:SetText("|cffeeaaaaReset|r")
    end)
    resetBtn:SetScript("OnLeave", function()
        resetBg:SetTexture("Interface\\AddOns\\HearthAndSeek\\Media\\Textures\\ResetButtonNormal")
        resetLabel:SetText("|cffcc8888Reset|r")
    end)
    resetBtn:SetScript("OnClick", function()
        if NS.UI.ResetAllFilters then
            NS.UI.ResetAllFilters()
        end
    end)
    footer._resetBtn = resetBtn

    -- Scrollable filter chip area (capped at 4 visible rows)
    local FOOTER_SCROLL_W = 6
    local FOOTER_MAX_ROWS = 4
    local FOOTER_ROW_H = 20
    local FOOTER_TOP_PAD = 6
    local FOOTER_BOTTOM_PAD = 12  -- space for metallic border strip

    local filterScroll = CreateFrame("ScrollFrame", nil, footer)
    filterScroll:SetPoint("TOPLEFT", footer, "TOPLEFT", 0, -FOOTER_TOP_PAD)
    filterScroll:SetPoint("RIGHT", footer, "RIGHT", -(FOOTER_SCROLL_W + 2), 0)
    filterScroll:SetHeight(FOOTER_ROW_H) -- updated in RefreshFooterBar
    filterScroll:EnableMouseWheel(true)

    local filterScrollChild = CreateFrame("Frame", nil, filterScroll)
    filterScrollChild:SetWidth(1) -- updated on resize
    filterScrollChild:SetHeight(1)
    filterScroll:SetScrollChild(filterScrollChild)

    -- Reparent label and reset button into scroll child so they scroll with content
    filterLabel:SetParent(filterScrollChild)
    filterLabel:ClearAllPoints()
    filterLabel:SetPoint("TOPLEFT", filterScrollChild, "TOPLEFT", 8, -3)
    resetBtn:SetParent(filterScrollChild)
    resetBtn:ClearAllPoints()
    resetBtn:SetPoint("LEFT", filterLabel, "RIGHT", 6, 0)

    filterScroll:SetScript("OnSizeChanged", function(self, width)
        filterScrollChild:SetWidth(width)
    end)

    -- Scrollbar track
    local fScrollTrack = CreateFrame("Frame", nil, footer)
    fScrollTrack:SetPoint("TOPLEFT", filterScroll, "TOPRIGHT", 1, 0)
    fScrollTrack:SetPoint("BOTTOMLEFT", filterScroll, "BOTTOMRIGHT", 1, 0)
    fScrollTrack:SetWidth(FOOTER_SCROLL_W)
    local fTrackBg = fScrollTrack:CreateTexture(nil, "BACKGROUND")
    fTrackBg:SetAllPoints()
    fTrackBg:SetColorTexture(0.12, 0.12, 0.14, 0.4)
    fScrollTrack:Hide()

    -- Scrollbar thumb
    local fScrollThumb = CreateFrame("Frame", nil, fScrollTrack)
    fScrollThumb:SetWidth(FOOTER_SCROLL_W)
    fScrollThumb:SetHeight(16)
    fScrollThumb:SetPoint("TOP", fScrollTrack, "TOP", 0, 0)
    local fThumbTex = fScrollThumb:CreateTexture(nil, "OVERLAY")
    fThumbTex:SetAllPoints()
    fThumbTex:SetColorTexture(0.55, 0.48, 0.28, 0.7)
    fScrollThumb:EnableMouse(true)

    -- Scrollbar update function (defined first so scripts below can reference it)
    local function UpdateFooterScrollBar()
        local contentH = filterScrollChild:GetHeight()
        local viewH = filterScroll:GetHeight()
        local maxScroll = math.max(0, contentH - viewH)
        if maxScroll <= 0 then
            fScrollTrack:Hide()
            filterScroll:SetVerticalScroll(0)
        else
            fScrollTrack:Show()
            local trackH = fScrollTrack:GetHeight()
            local thumbH = math.max(12, trackH * (viewH / contentH))
            fScrollThumb:SetHeight(thumbH)
            local scrollPos = filterScroll:GetVerticalScroll()
            local ratio = scrollPos / maxScroll
            local travel = trackH - thumbH
            fScrollThumb:ClearAllPoints()
            fScrollThumb:SetPoint("TOP", fScrollTrack, "TOP", 0, -(ratio * travel))
        end
    end

    -- Thumb drag support
    local fThumbDragging = false
    local fThumbDragStartY = 0
    local fThumbDragStartScroll = 0

    fScrollThumb:SetScript("OnMouseDown", function(self, button)
        if button ~= "LeftButton" then return end
        fThumbDragging = true
        fThumbDragStartY = select(2, GetCursorPosition()) / (self:GetEffectiveScale() or 1)
        fThumbDragStartScroll = filterScroll:GetVerticalScroll()
    end)
    fScrollThumb:SetScript("OnMouseUp", function() fThumbDragging = false end)
    fScrollThumb:SetScript("OnUpdate", function(self)
        if not fThumbDragging then return end
        local curY = select(2, GetCursorPosition()) / (self:GetEffectiveScale() or 1)
        local deltaY = fThumbDragStartY - curY
        local trackH = fScrollTrack:GetHeight() - self:GetHeight()
        if trackH <= 0 then return end
        local maxScroll = math.max(0, filterScrollChild:GetHeight() - filterScroll:GetHeight())
        local scrollDelta = (deltaY / trackH) * maxScroll
        local newScroll = math.max(0, math.min(maxScroll, fThumbDragStartScroll + scrollDelta))
        filterScroll:SetVerticalScroll(newScroll)
        UpdateFooterScrollBar()
    end)

    -- Track click: jump to click position
    fScrollTrack:EnableMouse(true)
    fScrollTrack:SetScript("OnMouseDown", function(self, button)
        if button ~= "LeftButton" then return end
        local scale = self:GetEffectiveScale() or 1
        local curY = select(2, GetCursorPosition()) / scale
        local trackTop = select(2, self:GetCenter()) + self:GetHeight() / 2
        local clickRatio = (trackTop - curY) / self:GetHeight()
        clickRatio = math.max(0, math.min(1, clickRatio))
        local maxScroll = math.max(0, filterScrollChild:GetHeight() - filterScroll:GetHeight())
        filterScroll:SetVerticalScroll(clickRatio * maxScroll)
        UpdateFooterScrollBar()
    end)

    -- Mouse wheel on scroll frame and track
    filterScroll:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, filterScrollChild:GetHeight() - self:GetHeight())
        local current = self:GetVerticalScroll()
        local newScroll = math.max(0, math.min(maxScroll, current - delta * FOOTER_ROW_H))
        self:SetVerticalScroll(newScroll)
        UpdateFooterScrollBar()
    end)
    filterScroll:SetScript("OnScrollRangeChanged", function() UpdateFooterScrollBar() end)

    fScrollTrack:EnableMouseWheel(true)
    fScrollTrack:SetScript("OnMouseWheel", function(self, delta)
        local maxScroll = math.max(0, filterScrollChild:GetHeight() - filterScroll:GetHeight())
        local current = filterScroll:GetVerticalScroll()
        local newScroll = math.max(0, math.min(maxScroll, current - delta * FOOTER_ROW_H))
        filterScroll:SetVerticalScroll(newScroll)
        UpdateFooterScrollBar()
    end)

    footer._filterScroll = filterScroll
    footer._filterScrollChild = filterScrollChild
    footer._filterScrollTrack = fScrollTrack
    footer._updateScrollBar = UpdateFooterScrollBar
    footer._FOOTER_MAX_ROWS = FOOTER_MAX_ROWS
    footer._FOOTER_ROW_H = FOOTER_ROW_H
    footer._FOOTER_TOP_PAD = FOOTER_TOP_PAD
    footer._FOOTER_SCROLL_W = FOOTER_SCROLL_W
    footer._FOOTER_BOTTOM_PAD = FOOTER_BOTTOM_PAD

    -- Credits (subtle, right-aligned in footer)
    -- "Hearth & Seek" in Morpheus font, rest in GameFontNormalSmall
    local creditsInfo = footer:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    creditsInfo:SetPoint("BOTTOMRIGHT", footer, "BOTTOMRIGHT", -8, 10)
    creditsInfo:SetText("|cff888888v" .. NS.ADDON_VERSION
        .. "|r |cff888888|||r "
        .. "|cff888888Author:|r |cff888888ImpalerV|r "
        .. "|cff888888(|r|cff40c8c8Vaelthos|r |cff888888@ Illidan)|r")
    creditsInfo:SetAlpha(0.45)

    local creditsName = footer:CreateFontString(nil, "ARTWORK")
    creditsName:SetFont("Fonts\\MORPHEUS.TTF", 12)
    creditsName:SetPoint("RIGHT", creditsInfo, "LEFT", -2, 0)
    creditsName:SetText("|cffffd200Hearth & Seek|r")
    creditsName:SetAlpha(0.45)

    -- Resize grip (bottom-right of main frame, above the footer)
    local resizeGrip = CreateFrame("Button", nil, catalogFrame)
    resizeGrip:SetFrameLevel(catalogFrame:GetFrameLevel() + 10)
    resizeGrip:SetSize(16, 16)
    resizeGrip:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -4, 9)
    resizeGrip:SetNormalTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Up")
    resizeGrip:SetHighlightTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Highlight")
    resizeGrip:SetPushedTexture("Interface\\ChatFrame\\UI-ChatIM-SizeGrabber-Down")
    local resizeTimer = nil
    local isResizing = false

    local function DoReflow()
        if NS.UI.CatalogGrid_Reflow then
            NS.UI.CatalogGrid_Reflow()
        end
        if NS.UI._RefreshFooterBar then
            NS.UI._RefreshFooterBar()
        end
    end

    resizeGrip:SetScript("OnMouseDown", function()
        isResizing = true
        catalogFrame:StartSizing("BOTTOMRIGHT")
    end)
    resizeGrip:SetScript("OnMouseUp", function()
        isResizing = false
        if resizeTimer then
            resizeTimer:Cancel()
            resizeTimer = nil
        end
        SaveFrameSize()
        DoReflow()
    end)

    -- Throttle reflow during drag; immediate when not dragging (e.g. SetSize)
    catalogFrame:SetScript("OnSizeChanged", function()
        if not isResizing then
            DoReflow()
            return
        end
        if resizeTimer then return end
        resizeTimer = C_Timer.NewTimer(0.1, function()
            resizeTimer = nil
            DoReflow()
        end)
    end)

    -- Clean up resize state if frame is hidden during drag (e.g. ESC key)
    catalogFrame:HookScript("OnHide", function()
        isResizing = false
        if resizeTimer then
            resizeTimer:Cancel()
            resizeTimer = nil
        end
    end)

    -- Restore saved position and size
    if NS.db and NS.db.catalogPosition then
        local pos = NS.db.catalogPosition
        catalogFrame:ClearAllPoints()
        catalogFrame:SetPoint(pos[1], UIParent, pos[3], pos[4], pos[5])
    end
    if NS.db and NS.db.catalogSize then
        local sz = NS.db.catalogSize
        catalogFrame:SetSize(sz[1], sz[2])
    end

    catalogFrame:Hide()
end

-------------------------------------------------------------------------------
-- ToggleCatalog
-------------------------------------------------------------------------------
function NS.UI.ToggleCatalog()
    if not catalogFrame then return end
    if catalogFrame:IsShown() then
        catalogFrame:Hide()
    else
        catalogFrame:Show()
        -- Force fresh collection data on every open
        if NS.UI.RefreshOwnershipCache then
            NS.UI.RefreshOwnershipCache()
        end
        if NS.UI.CatalogGrid_ApplyFilters then
            NS.UI.CatalogGrid_ApplyFilters()
        end
        -- Re-render detail panel (quest chain, collected banner, etc.)
        if NS.UI.RefreshDetailPanel then
            NS.UI.RefreshDetailPanel()
        end
        -- Show "What's New" callouts on first open after install/update
        if not NS.UI._whatsNewChecked then
            NS.UI._whatsNewChecked = true
            if NS.UI.TryShowWhatsNew then
                NS.UI.TryShowWhatsNew()
            end
        end
    end
end

-------------------------------------------------------------------------------
-- OpenCatalogForZone: open catalog pre-filtered to a specific zone
-------------------------------------------------------------------------------
function NS.UI.OpenCatalogForZone(zoneName)
    if not catalogFrame then return end
    -- Add the zone to existing filters (only if it exists in the filter bar)
    if zoneName and filterWidgets.zones[zoneName] then
        filterWidgets.zones[zoneName].check:SetChecked(true)
        -- Update the expansion parent check state
        for _, gData in pairs(filterWidgets.expansions) do
            if gData.childKeys then
                for _, cKey in ipairs(gData.childKeys) do
                    if cKey == zoneName then
                        UpdateParentCheckState(gData, gData.childKeys, filterWidgets.zones)
                        break
                    end
                end
            end
        end
        -- Show the catalog
        if not catalogFrame:IsShown() then
            catalogFrame:Show()
        end
        -- Refresh ownership before applying filters so data is fresh
        if NS.UI.RefreshOwnershipCache then
            NS.UI.RefreshOwnershipCache()
        end
        -- Set the zone filter (additive — calls ApplyFilters internally)
        NS.UI.CatalogGrid_ToggleZone(zoneName, true)
    else
        -- Zone not in catalog — just open normally
        if not catalogFrame:IsShown() then
            catalogFrame:Show()
        end
        if NS.UI.RefreshOwnershipCache then
            NS.UI.RefreshOwnershipCache()
        end
        if NS.UI.CatalogGrid_ApplyFilters then
            NS.UI.CatalogGrid_ApplyFilters()
        end
    end
    if NS.UI.RefreshDetailPanel then
        NS.UI.RefreshDetailPanel()
    end
end
