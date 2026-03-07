-------------------------------------------------------------------------------
-- HearthAndSeek: CatalogFrame.lua
-- Main catalog browser frame: title bar, search box, sidebar filters.
-- Sidebar uses ScrollFrame + UICheckButtonTemplate for multi-select filters.
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
local sidebarWidgets = {
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
-- FILTER_SECTIONS — data-driven sidebar layout config
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
            ["Arcane"]     = { 0.60, 0.40, 0.90 },  -- purple
            ["Armory"]     = { 0.70, 0.55, 0.35 },  -- bronze
            ["Fae"]        = { 0.55, 0.85, 0.65 },  -- mint green
            ["Fel"]        = { 0.55, 0.80, 0.25 },  -- fel green
            ["Lorekeeper"] = { 0.75, 0.65, 0.45 },  -- parchment tan
            ["Macabre"]    = { 0.65, 0.45, 0.55 },  -- dusty rose
            ["Nature"]     = { 0.40, 0.75, 0.40 },  -- forest green
            ["Noble"]      = { 0.85, 0.75, 0.35 },  -- gold
            ["Pirate"]     = { 0.70, 0.55, 0.40 },  -- weathered wood
            ["Rugged"]     = { 0.65, 0.55, 0.45 },  -- leather brown
            ["Rustic"]     = { 0.80, 0.65, 0.45 },  -- warm straw
            ["Sacred"]     = { 0.90, 0.85, 0.50 },  -- holy light
            ["Tavern"]     = { 0.80, 0.60, 0.30 },  -- amber
            ["Tinker"]     = { 0.60, 0.70, 0.75 },  -- steel blue
            ["Void"]       = { 0.50, 0.35, 0.70 },  -- deep violet
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
        title = "EXPANSION",
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
-- Sidebar helpers: anchor-chained collapsible sections
-------------------------------------------------------------------------------
local allSections = {}        -- ordered list of section frames (for RecalcSidebarHeight)

local function RecalcSidebarHeight(scrollChild)
    local totalH = 4  -- top padding
    for i, section in ipairs(allSections) do
        totalH = totalH + section:GetHeight()
        if i < #allSections then
            totalH = totalH + 6  -- gap between sections
        end
    end
    totalH = totalH + 8  -- bottom padding
    scrollChild:SetHeight(totalH)
    if NS.UI._UpdateSidebarScrollBar then
        NS.UI._UpdateSidebarScrollBar()
    end
end

--- Generic: recalculate a hierarchical section's height from its group list.
local function RecalcHierarchicalHeight(section, scrollChild)
    if not section or not section._groups then return end
    local totalH = 0
    for _, group in ipairs(section._groups) do
        totalH = totalH + group:GetHeight()
    end
    section._contentHeight = totalH
    section._content:SetHeight(totalH)
    if section._expanded then
        section:SetHeight(20 + totalH)
    end
    RecalcSidebarHeight(scrollChild)
end

--- Generic: recalculate a multiselect section with an embedded sub-group.
local function RecalcMultiselectHeight(section, subGroupFrame, scrollChild)
    if not section then return end
    local baseH = section._baseContentHeight or 0
    if subGroupFrame then
        baseH = baseH - 22 + subGroupFrame:GetHeight()
    end
    section._contentHeight = baseH
    section._content:SetHeight(baseH)
    if section._expanded then
        section:SetHeight(20 + baseH)
    end
    RecalcSidebarHeight(scrollChild)
end

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

local function CreateSidebarSection(scrollChild, title, anchorFrame, gap)
    local section = CreateFrame("Frame", nil, scrollChild)
    section:SetPoint("LEFT", scrollChild, "LEFT", 0, 0)
    section:SetPoint("RIGHT", scrollChild, "RIGHT", 0, 0)
    if anchorFrame then
        section:SetPoint("TOP", anchorFrame, "BOTTOM", 0, gap or -6)
    else
        section:SetPoint("TOP", scrollChild, "TOP", 0, -4)
    end

    -- Header row (clickable to collapse/expand)
    local header = CreateFrame("Button", nil, section)
    header:SetHeight(20)
    header:SetPoint("TOPLEFT", section, "TOPLEFT", 0, 0)
    header:SetPoint("TOPRIGHT", section, "TOPRIGHT", 0, 0)

    -- Section title
    local titleFS = header:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    titleFS:SetPoint("LEFT", header, "LEFT", 10, 0)
    titleFS:SetText(title)
    titleFS:SetTextColor(0.50, 0.50, 0.50, 1)

    -- Toggle indicator (- = expanded, + = collapsed)
    local toggleText = header:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    toggleText:SetPoint("RIGHT", header, "RIGHT", -8, 0)
    toggleText:SetText("-")
    toggleText:SetTextColor(1.00, 0.82, 0.00, 1)

    -- Underline (amber-gold, matching right sidebar section headers)
    local underline = header:CreateTexture(nil, "ARTWORK")
    underline:SetHeight(2)
    underline:SetPoint("BOTTOMLEFT", header, "BOTTOMLEFT", 8, 0)
    underline:SetPoint("BOTTOMRIGHT", header, "BOTTOMRIGHT", -8, 0)
    underline:SetColorTexture(0.72, 0.58, 0.25, 0.5)

    -- Content area (below header)
    local content = CreateFrame("Frame", nil, section)
    content:SetPoint("TOPLEFT", header, "BOTTOMLEFT", 0, 0)
    content:SetPoint("RIGHT", section, "RIGHT", 0, 0)

    section._header = header
    section._content = content
    section._toggle = toggleText
    section._expanded = true
    section._contentHeight = 0

    header:SetScript("OnClick", function()
        section._expanded = not section._expanded
        if section._expanded then
            content:Show()
            toggleText:SetText("-")
            section:SetHeight(20 + section._contentHeight)
        else
            content:Hide()
            toggleText:SetText("+")
            section:SetHeight(20)
        end
        RecalcSidebarHeight(scrollChild)
    end)

    allSections[#allSections + 1] = section
    return section, content
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

local function CreateFilterGroup(contentFrame, groupName, anchorFrame, color, childList, scrollChild, sectionRef, groupsListRef, childWidgetTbl, groupWidgetTbl, toggleGroupFn, toggleKey)
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
        RecalcHierarchicalHeight(sectionRef, scrollChild)
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
-------------------------------------------------------------------------------
local SectionBuilders = {}

--- boolean: single toggle checkbox (e.g. Favorites)
function SectionBuilders.boolean(scrollChild, sectionDef, prevSection)
    local section, content = CreateSidebarSection(scrollChild, sectionDef.title, prevSection)
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
        sidebarWidgets[sectionDef.id][itemDef.key] = {
            check      = chk,
            label      = chk._label,
            namePrefix = itemDef.label,
            countKey   = itemDef.countKey,
        }
        yOff = newY
    end

    section._contentHeight = #items * 22
    content:SetHeight(section._contentHeight)
    section:SetHeight(20 + section._contentHeight)
    return section
end

--- boolean_pair: inverted toggle pair (e.g. Collection — checked = hide)
function SectionBuilders.boolean_pair(scrollChild, sectionDef, prevSection)
    local section, content = CreateSidebarSection(scrollChild, sectionDef.title, prevSection)
    local items = sectionDef.items or {}

    local yOff = 0
    for _, itemDef in ipairs(items) do
        local chk, newY = CreateFilterCheckbox(content, itemDef.label, yOff, itemDef.color,
            function(checked)
                -- checked = "Hide X" is active → invert for filterState (true = show)
                local fn = NS.UI[sectionDef.toggle]
                if fn then fn(itemDef.key, not checked) end
            end)
        chk:SetChecked(false)
        sidebarWidgets[sectionDef.id][itemDef.key] = {
            check      = chk,
            label      = chk._label,
            namePrefix = itemDef.label,
        }
        yOff = newY
    end

    section._contentHeight = #items * 22
    content:SetHeight(section._contentHeight)
    section:SetHeight(20 + section._contentHeight)
    return section
end

--- multiselect: flat checkbox list with optional embedded sub-group (e.g. Source, Rarity)
function SectionBuilders.multiselect(scrollChild, sectionDef, prevSection)
    local section, content = CreateSidebarSection(scrollChild, sectionDef.title, prevSection)
    local wTable = sectionDef.widgetTable   -- e.g. "sources", "qualities"
    local subDef = sectionDef.subGroup

    -- Resolve order list
    local orderList
    if sectionDef.order then
        -- Check NS.CatalogData first, then NS root
        orderList = (NS.CatalogData and NS.CatalogData[sectionDef.order])
            or NS[sectionDef.order]
    end
    if not orderList then
        -- Fallback for SOURCE
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
                -- Quality counts must be computed from Items table
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
                sidebarWidgets[wTable][key] = {
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
    local subGroupFrame = nil
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
            subGroupFrame = profGroup

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
                                sidebarWidgets[wTable][skipKey],
                                sidebarWidgets[wTable][skipKey] and sidebarWidgets[wTable][skipKey].childKeys or {},
                                sidebarWidgets[subWTable]
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

                    sidebarWidgets[subWTable][childKey] = {
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
                    local cWidget = sidebarWidgets[subWTable][cName]
                    if cWidget then cWidget.check:SetChecked(checked) end
                end
                local fn = NS.UI[subDef.toggleGroup]
                if fn then fn(checked) end
            end)

            -- Expand/collapse
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
                RecalcMultiselectHeight(section, subGroupFrame, scrollChild)
            end

            local profClickArea = CreateFrame("Button", nil, profRow)
            profClickArea:SetPoint("LEFT", profCheck, "RIGHT", 0, 0)
            profClickArea:SetPoint("RIGHT", profRow, "RIGHT", 0, 0)
            profClickArea:SetHeight(22)
            profClickArea:SetScript("OnClick", ToggleSubGroupExpand)

            sidebarWidgets[wTable][skipKey] = {
                check        = profCheck,
                label        = profLabel,
                namePrefix   = skipKey,
                childKeys    = childNames,
                _isProfMaster = true,
            }
            itemCount = itemCount + 1
        end
    end

    -- Base content height = item checkboxes (subGroup counts as one 22px row when collapsed)
    section._baseContentHeight = itemCount * 22
    section._contentHeight = section._baseContentHeight
    content:SetHeight(section._contentHeight)
    section:SetHeight(20 + section._contentHeight)

    -- Store sub-group frame reference for height recalculation
    section._subGroupFrame = subGroupFrame

    -- Store recalc function on the section for external callers
    section._recalcHeight = function()
        RecalcMultiselectHeight(section, subGroupFrame, scrollChild)
    end

    -- Default collapsed behavior
    if sectionDef.defaultCollapsed then
        section._expanded = false
        content:Hide()
        section._toggle:SetText("+")
        section:SetHeight(20)
    end

    return section
end

--- hierarchical: expandable groups with children (e.g. Category, Expansion)
function SectionBuilders.hierarchical(scrollChild, sectionDef, prevSection)
    local section, content = CreateSidebarSection(scrollChild, sectionDef.title, prevSection)
    local wTable = sectionDef.widgetTable        -- e.g. "expansions", "categories"
    local cwTable = sectionDef.childWidgetTable   -- e.g. "zones", "subcategories"

    -- Store group list on the section
    local groupsList = {}
    section._groups = groupsList

    -- Store recalc function on the section for expand/collapse
    section._recalcHeight = function()
        RecalcHierarchicalHeight(section, scrollChild)
    end

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

    -- Resolve group colors (hex string table: groupName → hex)
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

    -- Build the children map: groupKey → sorted list of { key, name, count }
    local childrenByGroup = {}

    if sectionDef.childMap then
        -- Direct mapping: e.g. CategorySubcategories[catID] → list of subcatIDs
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
        -- Reverse lookup: e.g. ZoneToExpansionMap (zone → expansion)
        local reverseMap = NS.CatalogData and NS.CatalogData[sectionDef.childSource] or {}
        local childCountsMap = NS.CatalogData and NS.CatalogData[sectionDef.childCounts] or {}

        -- Group children by parent (reverse the map)
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
                        name  = childKey,   -- zone name IS the key
                        count = cCount,
                    })
                end
            end
        end
        -- Collect children not in the mapping → "Unknown"
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
        -- Sort children alphabetically within each group
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
            -- Resolve group display name
            local groupDisplayName = (groupNamesTable and groupNamesTable[groupKey])
                or (type(groupKey) == "string" and groupKey or tostring(groupKey))

            -- Resolve group color (hex string)
            local groupColor = groupColorsTable and groupColorsTable[groupDisplayName] or
                (groupColorsTable and groupColorsTable[groupKey]) or uniformHex or "888888"

            local group, groupCheck, groupLabel, childFrame = CreateFilterGroup(
                content, groupDisplayName, prevGroup, groupColor, childList, scrollChild,
                section, groupsList, sidebarWidgets[cwTable], sidebarWidgets[wTable],
                toggleGroupFn, groupKey)

            -- Store group widget data
            local childKeys = {}
            sidebarWidgets[wTable][groupKey] = {
                check      = groupCheck,
                label      = groupLabel,
                expanded   = false,
                childKeys  = childKeys,
                namePrefix = "|cff" .. groupColor .. groupDisplayName .. "|r",
            }
            -- Also store under display name if different from key (for reverse lookups)
            if groupDisplayName ~= groupKey then
                sidebarWidgets[wTable][groupDisplayName] = sidebarWidgets[wTable][groupKey]
            end

            -- Create child checkboxes inside the child frame
            local childYOff = 0
            for _, cInfo in ipairs(childList) do
                -- Determine child color: use override if present, else group color
                local childColor = groupColor
                if childColorOverrides[cInfo.name] then
                    childColor = childColorOverrides[cInfo.name]
                end

                local cLabelText = "|cff" .. childColor .. cInfo.name .. "|r  |cff888888(" .. cInfo.count .. ")|r"

                local cChk, newCY = CreateFilterCheckbox(childFrame, cLabelText, childYOff, nil,
                    function(checked)
                        if toggleChildFn then toggleChildFn(cInfo.key, checked) end
                        UpdateParentCheckState(
                            sidebarWidgets[wTable][groupKey],
                            sidebarWidgets[wTable][groupKey] and sidebarWidgets[wTable][groupKey].childKeys or {},
                            sidebarWidgets[cwTable]
                        )
                    end)
                -- Indent child checkboxes
                cChk:ClearAllPoints()
                cChk:SetPoint("TOPLEFT", childFrame, "TOPLEFT", 28, childYOff)

                sidebarWidgets[cwTable][cInfo.key] = {
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

    -- Set section height (all groups start collapsed)
    section._contentHeight = totalContentH
    content:SetHeight(totalContentH)
    section:SetHeight(20 + totalContentH)

    -- Default collapsed behavior
    if sectionDef.defaultCollapsed then
        section._expanded = false
        content:Hide()
        section._toggle:SetText("+")
        section:SetHeight(20)
    end

    return section
end

--- theme_group: flat list of theme checkboxes for a single theme group
--- (Culture or Aesthetic). Reads theme metadata from CatalogData.
function SectionBuilders.theme_group(scrollChild, sectionDef, prevSection)
    local groupID = sectionDef.groupID
    local themeGroupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
    local themeNames = NS.CatalogData and NS.CatalogData.ThemeNames
    local byTheme = NS.CatalogData and NS.CatalogData.ByTheme

    -- Skip section entirely if no theme data loaded
    if not themeGroupThemes or not themeNames then return prevSection end

    local themeIDs = themeGroupThemes[groupID]
    if not themeIDs or #themeIDs == 0 then return prevSection end

    local section, content = CreateSidebarSection(scrollChild, sectionDef.title, prevSection)
    local wTable = sectionDef.widgetTable  -- "themes"

    -- Color helpers: per-theme colors or uniform fallback
    local perThemeColors = sectionDef.themeColors  -- { ["Name"] = {r,g,b} } or nil
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

    -- Build sorted theme list: { themeID, name, count }
    local themeList = {}
    for _, tid in ipairs(themeIDs) do
        local name = themeNames[tid]
        local count = byTheme and byTheme[tid] and #byTheme[tid] or 0
        if name and count > 0 then
            themeList[#themeList + 1] = { id = tid, name = name, count = count }
        end
    end
    table.sort(themeList, function(a, b) return a.name < b.name end)

    if #themeList == 0 then return prevSection end

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

        sidebarWidgets[wTable][tInfo.id] = {
            check      = chk,
            label      = chk._label,
            namePrefix = "|cff" .. hex .. tInfo.name .. "|r",
            tagColor   = getThemeTagColor(tInfo.name),
        }
        yOff = newY
    end

    local totalH = math.abs(yOff) + 2
    section._contentHeight = totalH
    content:SetHeight(totalH)
    section:SetHeight(20 + totalH)

    -- Default collapsed
    if sectionDef.defaultCollapsed then
        section._expanded = false
        content:Hide()
        section._toggle:SetText("+")
        section:SetHeight(20)
    end

    return section
end

-------------------------------------------------------------------------------
-- Build sidebar content inside scroll child
-------------------------------------------------------------------------------
local function InitSidebarContent(scrollChild)
    -- Reset tracking tables
    allSections = {}
    for key in pairs(sidebarWidgets) do
        sidebarWidgets[key] = {}
    end

    local prevSection = nil
    for _, sectionDef in ipairs(FILTER_SECTIONS) do
        local builder = SectionBuilders[sectionDef.type]
        if builder then
            prevSection = builder(scrollChild, sectionDef, prevSection)
        end
    end

    RecalcSidebarHeight(scrollChild)
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
    local TOP_PAD = footerFrame._FOOTER_TOP_PAD or 5
    local MAX_ROWS = footerFrame._FOOTER_MAX_ROWS or 4
    local SCROLL_W = footerFrame._FOOTER_SCROLL_W or 6
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
            bg:SetColorTexture(0.15, 0.15, 0.18, 0.9)

            local border = CreateFrame("Frame", nil, tag, "BackdropTemplate")
            border:SetAllPoints()
            border:SetBackdrop({
                edgeFile = "Interface\\Buttons\\WHITE8X8",
                edgeSize = 1,
            })
            border:SetBackdropBorderColor(0.30, 0.30, 0.35, 0.6)

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
                local widget = sidebarWidgets[secDef.id][itemDef.key]
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
                local widget = sidebarWidgets[secDef.id][itemDef.key]
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
                local widget = sidebarWidgets[wTable][key]
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
                    local widget = sidebarWidgets[subWTable][childKey]
                    if widget and widget.check:GetChecked() then
                        AddTag(childKey, subDef.color, function()
                            widget.check:SetChecked(false)
                            local fn = NS.UI[subDef.toggleChild]
                            if fn then fn(childKey, false) end
                            -- Update parent check state
                            local parentWidget = sidebarWidgets[wTable][skipKey]
                            if parentWidget and parentWidget.childKeys then
                                UpdateParentCheckState(parentWidget, parentWidget.childKeys, sidebarWidgets[subWTable])
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
                local gData = sidebarWidgets[wTable][groupKey]
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
                        local cWidget = sidebarWidgets[cwTable][cKey]
                        if cWidget and cWidget.check:GetChecked() then
                            -- Resolve display name: use childNames table for numeric keys
                            local tagLabel = (childNamesTable and childNamesTable[cKey])
                                or (type(cKey) == "string" and cKey or tostring(cKey))
                            AddTag(tagLabel, tagColor, function()
                                cWidget.check:SetChecked(false)
                                local fn = NS.UI[secDef.toggleChild]
                                if fn then fn(cKey, false) end
                                UpdateParentCheckState(gData, gData.childKeys, sidebarWidgets[cwTable])
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
                local widget = sidebarWidgets[wTable][tid]
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

        local newH = TOP_PAD + scrollH + 3
        footerFrame:SetHeight(math.max(28, newH))

        -- Reset scroll position when filters change
        footerFrame._filterScroll:SetVerticalScroll(0)
        if footerFrame._updateScrollBar then
            footerFrame._updateScrollBar()
        end
    else
        local newH = TOP_PAD + numRows * ROW_H + 3
        footerFrame:SetHeight(math.max(28, newH))
    end
end

-- Expose for OnSizeChanged to re-wrap filter chips on resize
NS.UI._RefreshFooterBar = RefreshFooterBar

-------------------------------------------------------------------------------
-- ResetAllFilters: uncheck all sidebar filters and clear search text
-------------------------------------------------------------------------------
function NS.UI.ResetAllFilters()
    -- Data-driven: uncheck all widgets across all section types
    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" or secDef.type == "boolean_pair" then
            for _, widget in pairs(sidebarWidgets[secDef.id]) do
                widget.check:SetChecked(false)
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            for _, widget in pairs(sidebarWidgets[wTable]) do
                widget.check:SetChecked(false)
            end
            -- Sub-group children
            if secDef.subGroup then
                local subWTable = secDef.subGroup.widgetTable
                for _, widget in pairs(sidebarWidgets[subWTable]) do
                    widget.check:SetChecked(false)
                end
                -- Update parent check state for the sub-group master
                local skipKey = secDef.subGroup.parentKey
                local parentWidget = sidebarWidgets[wTable][skipKey]
                if parentWidget and parentWidget.childKeys then
                    UpdateParentCheckState(parentWidget, parentWidget.childKeys, sidebarWidgets[subWTable])
                end
            end

        elseif secDef.type == "hierarchical" then
            local cwTable = secDef.childWidgetTable
            for _, widget in pairs(sidebarWidgets[cwTable]) do
                widget.check:SetChecked(false)
            end
            -- Update parent check states for all groups
            local wTable = secDef.widgetTable
            for _, gData in pairs(sidebarWidgets[wTable]) do
                if gData.childKeys then
                    UpdateParentCheckState(gData, gData.childKeys, sidebarWidgets[cwTable])
                end
            end

        elseif secDef.type == "theme_group" then
            local wTable = secDef.widgetTable
            -- Only iterate themes belonging to THIS group
            local groupThemes = NS.CatalogData and NS.CatalogData.ThemeGroupThemes
                and NS.CatalogData.ThemeGroupThemes[secDef.groupID] or {}
            for _, tid in ipairs(groupThemes) do
                local widget = sidebarWidgets[wTable][tid]
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
-- UpdateSidebarCounts: refresh all sidebar labels with dynamic counts
-- Called from CatalogGrid after every filter application.
-- counts = { sources={}, zones={}, qualities={}, professions={},
--            subcategories={}, collection={ collected=N, notCollected=N },
--            favorites=N }
-------------------------------------------------------------------------------
function NS.UI.UpdateSidebarCounts(counts)
    if not counts then return end

    -- Data-driven count updates across all section types
    for _, secDef in ipairs(FILTER_SECTIONS) do

        if secDef.type == "boolean" then
            -- Single value count (e.g. favorites)
            for _, itemDef in ipairs(secDef.items or {}) do
                local widget = sidebarWidgets[secDef.id][itemDef.key]
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
            for key, widget in pairs(sidebarWidgets[secDef.id]) do
                local c = counts.collection and counts.collection[key] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end

        elseif secDef.type == "multiselect" then
            local wTable = secDef.widgetTable
            -- Main items: look up counts from the dimension table
            local countsDim = counts[wTable]  -- e.g. counts.sources, counts.qualities
            for key, widget in pairs(sidebarWidgets[wTable]) do
                local c = countsDim and countsDim[key] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end
            -- Sub-group children (e.g. professions)
            if secDef.subGroup then
                local subWTable = secDef.subGroup.widgetTable
                local subCountsDim = counts[subWTable]
                for childKey, widget in pairs(sidebarWidgets[subWTable]) do
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
            for cKey, widget in pairs(sidebarWidgets[cwTable]) do
                local c = childCountsDim and childCountsDim[cKey] or 0
                widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
            end

            -- Update group labels (sum of child counts)
            for _, gData in pairs(sidebarWidgets[wTable]) do
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
                local widget = sidebarWidgets[wTable][tid]
                if widget then
                    local c = themeCounts and themeCounts[tid] or 0
                    widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
                end
            end
        end

    end

    -- Refresh footer bar active filter tags
    RefreshFooterBar()
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
    titleBar:SetHeight(32)
    titleBar:SetPoint("TOPLEFT", 1, -1)
    titleBar:SetPoint("TOPRIGHT", -1, -1)
    titleBar:EnableMouse(true)
    titleBar:SetScript("OnMouseDown", function(_, button)
        if button == "LeftButton" then catalogFrame:StartMoving() end
    end)
    titleBar:SetScript("OnMouseUp", function(_, button)
        if button == "LeftButton" then SaveFramePosition() end
    end)

    -- Title bar background
    local titleBg = titleBar:CreateTexture(nil, "BACKGROUND")
    titleBg:SetAllPoints()
    titleBg:SetColorTexture(0.10, 0.10, 0.12, 1)

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

    -- Close button
    local closeBtn = CreateFrame("Button", nil, titleBar, "UIPanelCloseButton")
    closeBtn:SetPoint("TOPRIGHT", catalogFrame, "TOPRIGHT", -2, -2)
    closeBtn:SetScript("OnClick", function() catalogFrame:Hide() end)

    -- Settings button (cogwheel, next to close button)
    local settingsBtn = CreateFrame("Button", nil, titleBar)
    settingsBtn:SetSize(20, 20)
    settingsBtn:SetPoint("RIGHT", closeBtn, "LEFT", -8, 0)
    local settingsIcon = settingsBtn:CreateTexture(nil, "ARTWORK")
    settingsIcon:SetAllPoints()
    settingsIcon:SetAtlas("OptionsIcon-Brown")
    settingsIcon:SetVertexColor(0.75, 0.75, 0.75, 0.8)
    settingsBtn:SetScript("OnEnter", function()
        settingsIcon:SetVertexColor(1, 0.82, 0, 1)
    end)
    settingsBtn:SetScript("OnLeave", function()
        if not catalogFrame._settingsPanel:IsShown() then
            settingsIcon:SetVertexColor(0.75, 0.75, 0.75, 0.8)
        end
    end)
    catalogFrame._settingsBtn = settingsBtn
    catalogFrame._settingsIcon = settingsIcon
    NS.UI.RegisterWhatsNewAnchor("settingsBtn", settingsBtn)

    -- Settings panel (opens to the right of the main window)
    local settingsPanel = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    settingsPanel:SetWidth(220)
    settingsPanel:SetHeight(300)
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
    local displayHeader = settingsPanel:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    displayHeader:SetPoint("TOPLEFT", settingsHeader, "BOTTOMLEFT", 0, -14)
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
    iconSlider:SetPoint("TOPLEFT", sliderLabel, "BOTTOMLEFT", 2, -10)
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

    -- Restore Default Icon Size button
    local resetIconBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    resetIconBtn:SetSize(180, 22)
    resetIconBtn:SetPoint("TOPLEFT", iconSlider, "BOTTOMLEFT", -2, -18)
    resetIconBtn:SetText("Restore Default Icon Size")
    resetIconBtn:SetScript("OnClick", function()
        iconSlider:SetValue(1.0)
    end)

    -- Restore Default Window Size button
    local resetWindowBtn = CreateFrame("Button", nil, settingsPanel, "UIPanelButtonTemplate")
    resetWindowBtn:SetSize(180, 22)
    resetWindowBtn:SetPoint("TOPLEFT", resetIconBtn, "BOTTOMLEFT", 0, -4)
    resetWindowBtn:SetText("Restore Default Window Size")
    resetWindowBtn:SetScript("OnClick", function()
        catalogFrame:SetSize(CatSizing.FrameWidth, CatSizing.FrameHeight)
        if NS.db then
            NS.db.catalogSize = nil
        end
    end)

    -- === GENERAL section ===
    local generalSep = CreateSettingsSep(settingsPanel, resetWindowBtn, -10)

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

    -- Toggle settings panel on button click
    settingsBtn:SetScript("OnClick", function()
        if settingsPanel:IsShown() then
            settingsPanel:Hide()
            settingsIcon:SetVertexColor(0.75, 0.75, 0.75, 0.8)
        else
            settingsPanel:Show()
            settingsIcon:SetVertexColor(1, 0.82, 0, 1)
        end
    end)

    -- Hide settings panel when main frame hides
    catalogFrame:HookScript("OnHide", function()
        settingsPanel:Hide()
        settingsIcon:SetVertexColor(0.75, 0.75, 0.75, 0.8)
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

    -- Sidebar panel (left) — darker background
    local sidebar = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    sidebar:SetWidth(CatSizing.SidebarWidth)
    sidebar:SetPoint("TOPLEFT", catalogFrame, "TOPLEFT", 1, -33)
    sidebar:SetPoint("BOTTOMLEFT", catalogFrame, "BOTTOMLEFT", 1, 1)
    sidebar:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    sidebar:SetBackdropColor(0.04, 0.04, 0.06, 1)
    NS.UI.RegisterWhatsNewAnchor("sidebar", sidebar)

    -- ScrollFrame inside sidebar (with thin scrollbar)
    local SIDEBAR_SB_WIDTH = 3
    local scrollFrame = CreateFrame("ScrollFrame", nil, sidebar)
    scrollFrame:SetPoint("TOPLEFT", 0, 0)
    scrollFrame:SetPoint("BOTTOMRIGHT", 0, 0)
    scrollFrame:EnableMouseWheel(true)

    -- Manual scrollbar: track + thumb (same pattern as detail panel)
    local sbTrack = CreateFrame("Frame", nil, sidebar)
    sbTrack:SetPoint("TOPRIGHT", sidebar, "TOPRIGHT", -1, -1)
    sbTrack:SetPoint("BOTTOMRIGHT", sidebar, "BOTTOMRIGHT", -1, 1)
    sbTrack:SetWidth(SIDEBAR_SB_WIDTH)
    local sbTrackBg = sbTrack:CreateTexture(nil, "BACKGROUND")
    sbTrackBg:SetAllPoints()
    sbTrackBg:SetColorTexture(0.10, 0.10, 0.12, 0.5)
    sbTrack:SetFrameLevel(scrollFrame:GetFrameLevel() + 5)

    local sbThumb = CreateFrame("Frame", nil, sbTrack)
    sbThumb:SetWidth(SIDEBAR_SB_WIDTH)
    local sbThumbTex = sbThumb:CreateTexture(nil, "OVERLAY")
    sbThumbTex:SetAllPoints()
    sbThumbTex:SetColorTexture(0.55, 0.50, 0.35, 0.7)

    local function UpdateSidebarScrollBar()
        local child = scrollFrame:GetScrollChild()
        if not child then sbTrack:Hide() return end
        local contentH = child:GetHeight()
        local viewH = scrollFrame:GetHeight()
        local maxScroll = math.max(0, contentH - viewH)
        if maxScroll <= 0 then
            sbTrack:Hide()
            scrollFrame:SetVerticalScroll(0)
            return
        end
        sbTrack:Show()
        local trackH = sbTrack:GetHeight()
        local thumbH = math.max(16, trackH * (viewH / contentH))
        sbThumb:SetHeight(thumbH)
        local scrollPos = scrollFrame:GetVerticalScroll()
        local ratio = scrollPos / maxScroll
        local travel = trackH - thumbH
        sbThumb:ClearAllPoints()
        sbThumb:SetPoint("TOP", sbTrack, "TOP", 0, -(ratio * travel))
    end

    scrollFrame:SetScript("OnMouseWheel", function(self, delta)
        local current = self:GetVerticalScroll()
        local child = self:GetScrollChild()
        if not child then return end
        local maxScroll = math.max(0, child:GetHeight() - self:GetHeight())
        local newScroll = math.max(0, math.min(maxScroll, current - delta * 24))
        self:SetVerticalScroll(newScroll)
        UpdateSidebarScrollBar()
    end)

    -- Thumb drag support
    sbThumb:EnableMouse(true)
    sbThumb:SetScript("OnMouseDown", function(self, button)
        if button == "LeftButton" then
            self._dragging = true
            local _, cursorY = GetCursorPosition()
            local scale = self:GetEffectiveScale()
            self._dragStartCursor = cursorY / scale
            self._dragStartScroll = scrollFrame:GetVerticalScroll()
        end
    end)
    sbThumb:SetScript("OnMouseUp", function(self)
        self._dragging = false
    end)
    sbThumb:SetScript("OnUpdate", function(self)
        if not self._dragging then return end
        local _, cursorY = GetCursorPosition()
        local scale = self:GetEffectiveScale()
        local curY = cursorY / scale
        local deltaPixels = self._dragStartCursor - curY
        local child = scrollFrame:GetScrollChild()
        if not child then return end
        local contentH = child:GetHeight()
        local viewH = scrollFrame:GetHeight()
        local maxScroll = math.max(1, contentH - viewH)
        local trackH = sbTrack:GetHeight()
        local thumbH = sbThumb:GetHeight()
        local travel = math.max(1, trackH - thumbH)
        local scrollDelta = deltaPixels * (maxScroll / travel)
        local newScroll = math.max(0, math.min(maxScroll,
            self._dragStartScroll + scrollDelta))
        scrollFrame:SetVerticalScroll(newScroll)
        UpdateSidebarScrollBar()
    end)

    -- Track click to jump
    sbTrack:EnableMouse(true)
    sbTrack:SetScript("OnMouseDown", function(self, button)
        if button ~= "LeftButton" then return end
        local _, cursorY = GetCursorPosition()
        local scale = self:GetEffectiveScale()
        local trackTop = self:GetTop() * scale
        local trackH = self:GetHeight()
        local clickRatio = (trackTop - cursorY) / (scale * trackH)
        clickRatio = math.max(0, math.min(1, clickRatio))
        local child = scrollFrame:GetScrollChild()
        if not child then return end
        local maxScroll = math.max(0, child:GetHeight() - scrollFrame:GetHeight())
        scrollFrame:SetVerticalScroll(clickRatio * maxScroll)
        UpdateSidebarScrollBar()
    end)

    sbTrack:EnableMouseWheel(true)
    sbTrack:SetScript("OnMouseWheel", function(_, delta)
        local current = scrollFrame:GetVerticalScroll()
        local child = scrollFrame:GetScrollChild()
        if not child then return end
        local maxScroll = math.max(0, child:GetHeight() - scrollFrame:GetHeight())
        local newScroll = math.max(0, math.min(maxScroll, current - delta * 24))
        scrollFrame:SetVerticalScroll(newScroll)
        UpdateSidebarScrollBar()
    end)

    NS.UI._UpdateSidebarScrollBar = UpdateSidebarScrollBar

    local scrollChild = CreateFrame("Frame", nil, scrollFrame)
    scrollChild:SetWidth(CatSizing.SidebarWidth)
    scrollFrame:SetScrollChild(scrollChild)

    -- Build sidebar content into scroll child
    InitSidebarContent(scrollChild)

    -- Vertical separator: sidebar | grid
    local sepLeft = catalogFrame:CreateTexture(nil, "ARTWORK")
    sepLeft:SetWidth(1)
    sepLeft:SetPoint("TOPLEFT", sidebar, "TOPRIGHT", 0, 0)
    sepLeft:SetPoint("BOTTOMLEFT", sidebar, "BOTTOMRIGHT", 0, 0)
    sepLeft:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Detail panel (right)
    local detail = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    detail:SetWidth(CatSizing.DetailPanelWidth)
    detail:SetPoint("TOPRIGHT", catalogFrame, "TOPRIGHT", -1, -33)
    detail:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -1, 15)
    detail:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    detail:SetBackdropColor(0.05, 0.05, 0.07, 1)

    -- Vertical separator: grid | detail
    local sepRight = catalogFrame:CreateTexture(nil, "ARTWORK")
    sepRight:SetWidth(1)
    sepRight:SetPoint("TOPRIGHT", detail, "TOPLEFT", 0, 0)
    sepRight:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", 0, 0)
    sepRight:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Bottom status bar (spans center + right, below grid and detail)
    local bottomBar = CreateFrame("Frame", nil, catalogFrame)
    bottomBar:SetHeight(14)
    bottomBar:SetPoint("BOTTOMLEFT", sidebar, "BOTTOMRIGHT", 1, 0)
    bottomBar:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -1, 1)

    -- Bottom-right panel (under detail panel, matching its dark styling)
    local bottomRight = CreateFrame("Frame", nil, bottomBar, "BackdropTemplate")
    bottomRight:SetWidth(CatSizing.DetailPanelWidth)
    bottomRight:SetPoint("TOPRIGHT", bottomBar, "TOPRIGHT", 0, 0)
    bottomRight:SetPoint("BOTTOMRIGHT", bottomBar, "BOTTOMRIGHT", 0, 0)
    bottomRight:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    bottomRight:SetBackdropColor(0.05, 0.05, 0.07, 1)

    -- Vertical separator extension under the grid|detail separator
    local sepBottomRight = bottomBar:CreateTexture(nil, "ARTWORK")
    sepBottomRight:SetWidth(1)
    sepBottomRight:SetPoint("TOPRIGHT", bottomRight, "TOPLEFT", 0, 0)
    sepBottomRight:SetPoint("BOTTOMRIGHT", bottomRight, "BOTTOMLEFT", 0, 0)
    sepBottomRight:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Grid area (center)
    local grid = CreateFrame("Frame", nil, catalogFrame)
    grid:SetPoint("TOPLEFT", sidebar, "TOPRIGHT", 1, 0)
    grid:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", -1, 0)

    -- Count text on the bottom status bar (centered in the grid area)
    local countText = bottomBar:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    countText:SetPoint("RIGHT", sepBottomRight, "LEFT", -8, 0)
    countText:SetPoint("LEFT", bottomBar, "LEFT", 8, 0)
    countText:SetJustifyH("CENTER")
    countText:SetTextColor(0.55, 0.55, 0.55, 1)
    grid._countText = countText  -- grid module reads this

    -- Initialize sub-components
    if NS.UI.InitCatalogGrid then
        NS.UI.InitCatalogGrid(grid)
    end
    if NS.UI.InitCatalogDetail then
        NS.UI.InitCatalogDetail(detail)
    end

    -- Footer bar (active filter tags — always visible, below the main window)
    local footer = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    footer:SetHeight(28)
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

    footerFrame = footer

    local filterLabel = footer:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    filterLabel:SetPoint("TOPLEFT", footer, "TOPLEFT", 8, -7)
    filterLabel:SetText("|cff888888Active Filters:|r")
    footer._filterLabel = filterLabel

    -- Reset Filters button (after "Active Filters:" label)
    local resetBtn = CreateFrame("Button", nil, footer)
    resetBtn:SetSize(50, 16)
    resetBtn:SetPoint("LEFT", filterLabel, "RIGHT", 6, 0)
    local resetBg = resetBtn:CreateTexture(nil, "BACKGROUND")
    resetBg:SetAllPoints()
    resetBg:SetColorTexture(0.20, 0.12, 0.12, 0.8)
    local resetBorder = CreateFrame("Frame", nil, resetBtn, "BackdropTemplate")
    resetBorder:SetAllPoints()
    resetBorder:SetBackdrop({ edgeFile = "Interface\\Buttons\\WHITE8X8", edgeSize = 1 })
    resetBorder:SetBackdropBorderColor(0.45, 0.25, 0.25, 0.7)
    local resetLabel = resetBtn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    resetLabel:SetPoint("CENTER", resetBtn, "CENTER", 0, 0)
    resetLabel:SetText("|cffcc8888Reset|r")
    resetBtn:SetScript("OnEnter", function()
        resetBg:SetColorTexture(0.30, 0.15, 0.15, 0.9)
        resetLabel:SetText("|cffeeaaaaReset|r")
    end)
    resetBtn:SetScript("OnLeave", function()
        resetBg:SetColorTexture(0.20, 0.12, 0.12, 0.8)
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
    local FOOTER_TOP_PAD = 5

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
    filterLabel:SetPoint("TOPLEFT", filterScrollChild, "TOPLEFT", 8, 0)
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

    -- Credits (subtle, right-aligned in footer)
    -- "Hearth & Seek" in Morpheus font, rest in GameFontNormalSmall
    local creditsInfo = footer:CreateFontString(nil, "ARTWORK", "GameFontNormalSmall")
    creditsInfo:SetPoint("BOTTOMRIGHT", footer, "BOTTOMRIGHT", -8, 5)
    creditsInfo:SetText("|cff888888v" .. NS.ADDON_VERSION
        .. "|r |cff888888|||r "
        .. "|cff888888Author:|r |cff888888ImpalerV|r "
        .. "|cff888888(|r|cff40c8c8Vaelthos|r |cff888888@ Proudmoore)|r")
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
    resizeGrip:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -4, 4)
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
    -- Reset all filters first (visual + data)
    NS.UI.ResetAllFilters()
    -- Check the zone checkbox if it exists in the sidebar
    if zoneName and sidebarWidgets.zones[zoneName] then
        sidebarWidgets.zones[zoneName].check:SetChecked(true)
        -- Find the expansion for this zone and update its check state
        for _, gData in pairs(sidebarWidgets.expansions) do
            if gData.childKeys then
                for _, cKey in ipairs(gData.childKeys) do
                    if cKey == zoneName then
                        UpdateParentCheckState(gData, gData.childKeys, sidebarWidgets.zones)
                        break
                    end
                end
            end
        end
    end
    -- Set the zone filter in grid state
    if zoneName and NS.UI.CatalogGrid_ToggleZone then
        NS.UI.CatalogGrid_ToggleZone(zoneName, true)
    end
    -- Show the catalog
    if not catalogFrame:IsShown() then
        catalogFrame:Show()
    end
    if NS.UI.RefreshOwnershipCache then
        NS.UI.RefreshOwnershipCache()
    end
    if NS.UI.CatalogGrid_ApplyFilters then
        NS.UI.CatalogGrid_ApplyFilters()
    end
    if NS.UI.RefreshDetailPanel then
        NS.UI.RefreshDetailPanel()
    end
end
