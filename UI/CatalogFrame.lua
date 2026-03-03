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
    favorites   = {},   -- ["onlyFavorites"] = { check, label, namePrefix }
    collection  = {},   -- ["collected"] = {...}, ["notCollected"] = {...}, ["redeemable"] = {...}
    sources     = {},   -- ["Vendor"] = {...}, ...
    professions = {},   -- ["Tailoring"] = {...}, ...
    expansions  = {},   -- ["Midnight"] = { check, label, toggle, container, expanded, zoneNames, namePrefix }
    zones       = {},   -- ["Stormwind City"] = { check, label, namePrefix }
    qualities   = {},   -- [1] = {...}, [2] = {...}, ...
}
-------------------------------------------------------------------------------
-- Sidebar helpers: anchor-chained collapsible sections
-------------------------------------------------------------------------------
local allSections = {}        -- ordered list of section frames (for RecalcSidebarHeight)
local expansionGroups = {}    -- ordered list of expansion group frames
local expansionSection = nil  -- reference to EXPANSION section
local sourceSection = nil     -- reference to SOURCE section
local professionGroup = nil   -- expandable group for professions inside SOURCE

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

local function RecalcExpansionHeight(scrollChild)
    if not expansionSection then return end
    local totalH = 0
    for _, group in ipairs(expansionGroups) do
        totalH = totalH + group:GetHeight()
    end
    expansionSection._contentHeight = totalH
    expansionSection._content:SetHeight(totalH)
    if expansionSection._expanded then
        expansionSection:SetHeight(20 + totalH)
    end
    RecalcSidebarHeight(scrollChild)
end

local function RecalcSourceHeight(scrollChild)
    if not sourceSection then return end
    -- baseContentHeight includes 22px for profGroup header row (collapsed).
    -- Replace that 22px with the actual current height when expanded.
    local baseH = sourceSection._baseContentHeight or 0
    if professionGroup then
        baseH = baseH - 22 + professionGroup:GetHeight()
    end
    sourceSection._contentHeight = baseH
    sourceSection._content:SetHeight(baseH)
    if sourceSection._expanded then
        sourceSection:SetHeight(20 + baseH)
    end
    RecalcSidebarHeight(scrollChild)
end

local function UpdateProfessionCheckState()
    local profWidget = sidebarWidgets.sources["Profession"]
    if not profWidget then return end
    local allChecked = true
    local profOrder = NS.CatalogData and NS.CatalogData.ProfessionOrder or {}
    for _, profName in ipairs(profOrder) do
        local pWidget = sidebarWidgets.professions[profName]
        if pWidget and not pWidget.check:GetChecked() then
            allChecked = false
            break
        end
    end
    profWidget.check:SetChecked(allChecked)
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

local function UpdateExpansionCheckState(contName)
    local contData = sidebarWidgets.expansions[contName]
    if not contData then return end
    local allChecked = true
    for _, zName in ipairs(contData.zoneNames) do
        local zWidget = sidebarWidgets.zones[zName]
        if zWidget and not zWidget.check:GetChecked() then
            allChecked = false
            break
        end
    end
    contData.check:SetChecked(allChecked)
end

local function CreateFilterGroup(contentFrame, contName, anchorFrame, color, zoneList, scrollChild)
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
    local contCheck = CreateFrame("CheckButton", nil, row, "UICheckButtonTemplate")
    contCheck:SetSize(22, 22)
    contCheck:SetPoint("LEFT", row, "LEFT", 4, 0)

    -- Label (after checkbox)
    local label = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    label:SetPoint("LEFT", contCheck, "RIGHT", 2, 0)
    if color then
        label:SetText("|cff" .. color .. contName .. "|r")
    else
        label:SetText(contName)
    end
    contCheck._label = label

    -- Toggle indicator (+/- at right edge of row, gold, larger font)
    local toggleText = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    toggleText:SetPoint("RIGHT", row, "RIGHT", -8, 0)
    toggleText:SetText("+")
    toggleText:SetTextColor(1.00, 0.82, 0.00, 1)

    -- Zone container (below row, inside group)
    local zonesFrame = CreateFrame("Frame", nil, group)
    zonesFrame:SetPoint("TOPLEFT", row, "BOTTOMLEFT", 0, 0)
    zonesFrame:SetPoint("RIGHT", group, "RIGHT", 0, 0)
    zonesFrame:SetHeight(1)
    zonesFrame:Hide()

    -- Group starts collapsed: height = just the header row
    group:SetHeight(22)

    local numZones = #zoneList

    -- Mass toggle callback
    contCheck:SetScript("OnClick", function(self)
        local checked = self:GetChecked()
        -- Update zone checkboxes FIRST so RefreshFooterBar sees correct state
        local contData = sidebarWidgets.expansions[contName]
        if contData then
            for _, zName in ipairs(contData.zoneNames) do
                local zWidget = sidebarWidgets.zones[zName]
                if zWidget then zWidget.check:SetChecked(checked) end
            end
        end
        if NS.UI.CatalogGrid_ToggleExpansion then
            NS.UI.CatalogGrid_ToggleExpansion(contName, checked)
        end
    end)

    -- Toggle expand/collapse
    group._expanded = false
    local function ToggleExpand()
        group._expanded = not group._expanded
        if group._expanded then
            toggleText:SetText("-")
            zonesFrame:SetHeight(numZones * 22)
            zonesFrame:Show()
            group:SetHeight(22 + numZones * 22)
        else
            toggleText:SetText("+")
            zonesFrame:SetHeight(1)
            zonesFrame:Hide()
            group:SetHeight(22)
        end
        RecalcExpansionHeight(scrollChild)
    end

    -- Click area covers label + toggle (everything right of checkbox)
    local clickArea = CreateFrame("Button", nil, row)
    clickArea:SetPoint("LEFT", contCheck, "RIGHT", 0, 0)
    clickArea:SetPoint("RIGHT", row, "RIGHT", 0, 0)
    clickArea:SetHeight(22)
    clickArea:SetScript("OnClick", ToggleExpand)

    expansionGroups[#expansionGroups + 1] = group
    return group, contCheck, label, zonesFrame
end

-------------------------------------------------------------------------------
-- Build sidebar content inside scroll child
-------------------------------------------------------------------------------
local function InitSidebarContent(scrollChild)
    -- Reset tracking tables
    allSections = {}
    expansionGroups = {}
    expansionSection = nil
    sourceSection = nil
    professionGroup = nil

    ---------------------------------------------------------------------------
    -- FAVORITES section
    ---------------------------------------------------------------------------
    local sectionFav, contentFav = CreateSidebarSection(scrollChild, "FAVORITES", nil)

    local favChk, favNewY = CreateFilterCheckbox(contentFav, "Only Favorites  |cff40c8c8(0)|r", 0,
        { 0.25, 0.78, 0.78 },
        function(checked)
            if NS.UI.CatalogGrid_ToggleFavorites then
                NS.UI.CatalogGrid_ToggleFavorites(checked)
            end
        end)
    favChk:SetChecked(false)
    sidebarWidgets.favorites["onlyFavorites"] = {
        check      = favChk,
        label      = favChk._label,
        namePrefix = "Only Favorites",
    }
    sectionFav._contentHeight = 22
    contentFav:SetHeight(22)
    sectionFav:SetHeight(20 + 22)

    ---------------------------------------------------------------------------
    -- COLLECTION section (all checked by default)
    ---------------------------------------------------------------------------
    local sectionColl, contentColl = CreateSidebarSection(scrollChild, "COLLECTION", sectionFav)

    local collDefs = {
        { key = "collected",    label = "Hide Collected",     color = { 0.12, 1.00, 0.00 } },
        { key = "notCollected", label = "Hide Not Collected", color = { 1.00, 0.27, 0.27 } },
    }

    local yOff = 0
    for _, def in ipairs(collDefs) do
        local chk, newY = CreateFilterCheckbox(contentColl, def.label, yOff, def.color,
            function(checked)
                -- checked = "Hide X" is active → invert for filterState (true = show)
                if NS.UI.CatalogGrid_ToggleCollection then
                    NS.UI.CatalogGrid_ToggleCollection(def.key, not checked)
                end
            end)
        chk:SetChecked(false)
        sidebarWidgets.collection[def.key] = {
            check      = chk,
            label      = chk._label,
            namePrefix = def.label,
        }
        yOff = newY
    end
    sectionColl._contentHeight = #collDefs * 22
    contentColl:SetHeight(sectionColl._contentHeight)
    sectionColl:SetHeight(20 + sectionColl._contentHeight)

    ---------------------------------------------------------------------------
    -- SOURCE section (with Profession sub-filters)
    ---------------------------------------------------------------------------
    local sectionSrc, contentSrc = CreateSidebarSection(scrollChild, "SOURCE", sectionColl)
    sourceSection = sectionSrc

    local sourceOrder = NS.CatalogData and NS.CatalogData.SourceOrder or
        { "Vendor", "Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Other" }
    local professionOrder = NS.CatalogData and NS.CatalogData.ProfessionOrder or {}
    local profSourceCount = NS.CatalogData and NS.CatalogData.BySource
        and NS.CatalogData.BySource["Profession"]
        and #NS.CatalogData.BySource["Profession"] or 0
    local hasProfGroup = profSourceCount > 0 and #professionOrder > 0

    -- Pass 1: create all non-Profession source checkboxes
    yOff = 0
    local srcCount = 0
    for _, srcType in ipairs(sourceOrder) do
        if srcType == "Profession" then
            -- skip — rendered last so expansion doesn't shift other checkboxes
        else
            local count = NS.CatalogData and NS.CatalogData.BySource
                and NS.CatalogData.BySource[srcType]
                and #NS.CatalogData.BySource[srcType] or 0
            if count > 0 then
                local srcColor = NS.SourceColors and NS.SourceColors[srcType]
                local label = srcType .. "  |cff888888(" .. count .. ")|r"
                local chk, newY = CreateFilterCheckbox(contentSrc, label, yOff, srcColor,
                    function(checked)
                        if NS.UI.CatalogGrid_ToggleSource then
                            NS.UI.CatalogGrid_ToggleSource(srcType, checked)
                        end
                    end)
                sidebarWidgets.sources[srcType] = {
                    check      = chk,
                    label      = chk._label,
                    namePrefix = srcType,
                }
                yOff = newY
                srcCount = srcCount + 1
            end
        end
    end

    -- Pass 2: create expandable Profession group at the bottom of SOURCE
    if hasProfGroup then
        local profGroup = CreateFrame("Frame", nil, contentSrc)
        profGroup:SetPoint("LEFT", contentSrc, "LEFT", 0, 0)
        profGroup:SetPoint("RIGHT", contentSrc, "RIGHT", 0, 0)
        profGroup:SetPoint("TOP", contentSrc, "TOP", 0, yOff)
        professionGroup = profGroup

        -- Header row (22px) with checkbox + label + toggle
        local profRow = CreateFrame("Frame", nil, profGroup)
        profRow:SetHeight(22)
        profRow:SetPoint("TOPLEFT", profGroup, "TOPLEFT", 0, 0)
        profRow:SetPoint("TOPRIGHT", profGroup, "TOPRIGHT", 0, 0)

        local srcColor = NS.SourceColors and NS.SourceColors["Profession"]
        local profCheck = CreateFrame("CheckButton", nil, profRow, "UICheckButtonTemplate")
        profCheck:SetSize(22, 22)
        profCheck:SetPoint("TOPLEFT", profRow, "TOPLEFT", 6, 0)

        local profLabel = profRow:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
        profLabel:SetPoint("LEFT", profCheck, "RIGHT", 2, 0)
        profLabel:SetText("Profession  |cff888888(" .. profSourceCount .. ")|r")
        if srcColor then
            profLabel:SetTextColor(srcColor[1], srcColor[2], srcColor[3], 1)
        end
        profCheck._label = profLabel

        -- Toggle indicator (+/- at right edge, gold)
        local profToggle = profRow:CreateFontString(nil, "OVERLAY", "GameFontNormal")
        profToggle:SetPoint("RIGHT", profRow, "RIGHT", -8, 0)
        profToggle:SetText("+")
        profToggle:SetTextColor(1.00, 0.82, 0.00, 1)

        -- Profession sub-checkboxes container
        local profsFrame = CreateFrame("Frame", nil, profGroup)
        profsFrame:SetPoint("TOPLEFT", profRow, "BOTTOMLEFT", 0, 0)
        profsFrame:SetPoint("RIGHT", profGroup, "RIGHT", 0, 0)
        profsFrame:SetHeight(1)
        profsFrame:Hide()

        -- Build profession sub-checkboxes
        local profYOff = 0
        local profSubCount = 0
        local profNames = {}
        for _, profName in ipairs(professionOrder) do
            local pCount = NS.CatalogData and NS.CatalogData.ByProfession
                and NS.CatalogData.ByProfession[profName]
                and #NS.CatalogData.ByProfession[profName] or 0
            if pCount > 0 then
                local profIcon = NS.ProfessionIcons and NS.ProfessionIcons[profName]
                local profColor = { 0.60, 0.40, 0.20, 1.00 }
                local pLabel = profName .. "  |cff888888(" .. pCount .. ")|r"
                local pChk, newPY = CreateFilterCheckbox(profsFrame, pLabel, profYOff, profColor,
                    function(checked)
                        if NS.UI.CatalogGrid_ToggleProfession then
                            NS.UI.CatalogGrid_ToggleProfession(profName, checked)
                        end
                        UpdateProfessionCheckState()
                    end)
                -- Indent sub-checkboxes
                pChk:ClearAllPoints()
                pChk:SetPoint("TOPLEFT", profsFrame, "TOPLEFT", 28, profYOff)

                local displayPrefix = profName
                if profIcon then
                    displayPrefix = "|T" .. profIcon .. ":14:14|t " .. profName
                    pChk._label:SetText("|T" .. profIcon .. ":14:14|t " .. pLabel)
                end

                sidebarWidgets.professions[profName] = {
                    check      = pChk,
                    label      = pChk._label,
                    namePrefix = displayPrefix,
                }
                profNames[#profNames + 1] = profName
                profYOff = newPY
                profSubCount = profSubCount + 1
            end
        end

        -- Group starts collapsed
        profGroup:SetHeight(22)
        profGroup._expanded = false

        -- Mass-toggle: checking Profession header checks all sub-professions
        profCheck:SetScript("OnClick", function(self)
            local checked = self:GetChecked()
            -- Update sub-checkboxes first
            for _, pName in ipairs(profNames) do
                local pWidget = sidebarWidgets.professions[pName]
                if pWidget then pWidget.check:SetChecked(checked) end
            end
            if NS.UI.CatalogGrid_ToggleAllProfessions then
                NS.UI.CatalogGrid_ToggleAllProfessions(checked)
            end
        end)

        -- Expand/collapse
        local function ToggleProfExpand()
            profGroup._expanded = not profGroup._expanded
            if profGroup._expanded then
                profToggle:SetText("-")
                profsFrame:SetHeight(profSubCount * 22)
                profsFrame:Show()
                profGroup:SetHeight(22 + profSubCount * 22)
            else
                profToggle:SetText("+")
                profsFrame:SetHeight(1)
                profsFrame:Hide()
                profGroup:SetHeight(22)
            end
            RecalcSourceHeight(scrollChild)
        end

        local profClickArea = CreateFrame("Button", nil, profRow)
        profClickArea:SetPoint("LEFT", profCheck, "RIGHT", 0, 0)
        profClickArea:SetPoint("RIGHT", profRow, "RIGHT", 0, 0)
        profClickArea:SetHeight(22)
        profClickArea:SetScript("OnClick", ToggleProfExpand)

        sidebarWidgets.sources["Profession"] = {
            check      = profCheck,
            label      = profLabel,
            namePrefix = "Profession",
            profNames  = profNames,
            _isProfMaster = true,
        }
        srcCount = srcCount + 1
    end

    -- Base content height = source checkboxes (profGroup counts as one 22px row when collapsed)
    sectionSrc._baseContentHeight = srcCount * 22
    sectionSrc._contentHeight = sectionSrc._baseContentHeight
    contentSrc:SetHeight(sectionSrc._contentHeight)
    sectionSrc:SetHeight(20 + sectionSrc._contentHeight)

    ---------------------------------------------------------------------------
    -- EXPANSION section (Expansion > Zone collapsible hierarchy)
    ---------------------------------------------------------------------------
    local sectionExp, contentExp = CreateSidebarSection(scrollChild, "EXPANSION", sectionSrc)
    expansionSection = sectionExp

    local expOrder = NS.CatalogData and NS.CatalogData.ExpansionOrder or {}
    local zoneToExp = NS.CatalogData and NS.CatalogData.ZoneToExpansionMap or {}
    local byZone = NS.CatalogData and NS.CatalogData.ByZone or {}

    -- Group zones by expansion (only zones that have items)
    local expansionZonesMap = {}
    for zone, expansion in pairs(zoneToExp) do
        if byZone[zone] then
            if not expansionZonesMap[expansion] then
                expansionZonesMap[expansion] = {}
            end
            table.insert(expansionZonesMap[expansion], {
                zone = zone,
                count = #byZone[zone],
            })
        end
    end
    -- Collect zones not in the mapping
    for zone, ids in pairs(byZone) do
        if not zoneToExp[zone] then
            if not expansionZonesMap["Unknown"] then
                expansionZonesMap["Unknown"] = {}
            end
            table.insert(expansionZonesMap["Unknown"], { zone = zone, count = #ids })
        end
    end
    -- Sort zones within each expansion alphabetically
    for _, zoneList in pairs(expansionZonesMap) do
        table.sort(zoneList, function(a, b) return a.zone < b.zone end)
    end

    -- Build full expansion order (append Unknown if needed)
    local fullExpOrder = {}
    for _, e in ipairs(expOrder) do
        fullExpOrder[#fullExpOrder + 1] = e
    end
    if expansionZonesMap["Unknown"] and #expansionZonesMap["Unknown"] > 0 then
        fullExpOrder[#fullExpOrder + 1] = "Unknown"
    end

    local prevGroup = nil
    local expContentH = 0
    for _, expName in ipairs(fullExpOrder) do
        local zoneList = expansionZonesMap[expName]
        if zoneList and #zoneList > 0 then
            local expColor = NS.ExpansionColors and NS.ExpansionColors[expName] or "888888"

            local group, expCheck, expLabel, zonesFrame = CreateFilterGroup(
                contentExp, expName, prevGroup, expColor, zoneList, scrollChild)

            -- Store expansion widget data
            local zoneNames = {}
            sidebarWidgets.expansions[expName] = {
                check     = expCheck,
                label     = expLabel,
                expanded  = false,
                zoneNames = zoneNames,
                namePrefix = "|cff" .. expColor .. expName .. "|r",
            }

            -- Create zone checkboxes inside the zones frame
            local zoneYOff = 0
            for _, zInfo in ipairs(zoneList) do
                local displayName = zInfo.zone
                -- Neighborhood zones: color by faction instead of expansion color
                local zoneColor = expColor
                if expName == "Neighborhoods" then
                    if zInfo.zone == "Founder's Point" then
                        zoneColor = "3399FF"   -- Alliance blue
                    elseif zInfo.zone == "Razorwind Shores" then
                        zoneColor = "FF3333"   -- Horde red
                    end
                end
                local zLabel = "|cff" .. zoneColor .. displayName .. "|r  |cff888888(" .. zInfo.count .. ")|r"

                local zChk, newZY = CreateFilterCheckbox(zonesFrame, zLabel, zoneYOff, nil,
                    function(checked)
                        if NS.UI.CatalogGrid_ToggleZone then
                            NS.UI.CatalogGrid_ToggleZone(zInfo.zone, checked)
                        end
                        UpdateExpansionCheckState(expName)
                    end)
                -- Indent zone checkboxes
                zChk:ClearAllPoints()
                zChk:SetPoint("TOPLEFT", zonesFrame, "TOPLEFT", 28, zoneYOff)

                sidebarWidgets.zones[zInfo.zone] = {
                    check      = zChk,
                    label      = zChk._label,
                    namePrefix = "|cff" .. zoneColor .. displayName .. "|r",
                }
                zoneNames[#zoneNames + 1] = zInfo.zone
                zoneYOff = newZY
            end

            prevGroup = group
            expContentH = expContentH + group:GetHeight()
        end
    end

    -- Set EXPANSION section height (all expansion groups start collapsed)
    sectionExp._contentHeight = expContentH
    contentExp:SetHeight(expContentH)
    sectionExp:SetHeight(20 + expContentH)

    ---------------------------------------------------------------------------
    -- RARITY section
    ---------------------------------------------------------------------------
    local sectionRar, contentRar = CreateSidebarSection(scrollChild, "RARITY", sectionExp)

    local qualityOrder = NS.QualityOrder or { 1, 2, 3, 4, 5, 0 }
    local qualityNames = NS.QualityNames or {}
    local qualityColors = NS.QualityColors or {}

    yOff = 0
    local qualCount = 0
    for _, q in ipairs(qualityOrder) do
        local qName = qualityNames[q] or ("Quality " .. q)
        local qc = qualityColors[q] or { 1, 1, 1, 1 }

        local count = 0
        if NS.CatalogData and NS.CatalogData.Items then
            for _, item in pairs(NS.CatalogData.Items) do
                if item.quality == q then count = count + 1 end
            end
        end

        if count > 0 then
            local label = qName .. "  |cff888888(" .. count .. ")|r"
            local chk, newY = CreateFilterCheckbox(contentRar, label, yOff, qc,
                function(checked)
                    if NS.UI.CatalogGrid_ToggleQuality then
                        NS.UI.CatalogGrid_ToggleQuality(q, checked)
                    end
                end)
            sidebarWidgets.qualities[q] = {
                check      = chk,
                label      = chk._label,
                namePrefix = qName,
            }
            yOff = newY
            qualCount = qualCount + 1
        end
    end
    sectionRar._contentHeight = qualCount * 22
    contentRar:SetHeight(sectionRar._contentHeight)
    -- Start rarity section collapsed by default
    sectionRar._expanded = false
    contentRar:Hide()
    sectionRar._toggle:SetText("+")
    sectionRar:SetHeight(20)

    -- Calculate initial total scroll height
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

    -- Favorites
    local favWidgetTag = sidebarWidgets.favorites["onlyFavorites"]
    if favWidgetTag and favWidgetTag.check:GetChecked() then
        AddTag("Only Favorites", { 0.25, 0.78, 0.78 }, function()
            favWidgetTag.check:SetChecked(false)
            if NS.UI.CatalogGrid_ToggleFavorites then
                NS.UI.CatalogGrid_ToggleFavorites(false)
            end
        end)
    end

    -- Collection (show tags when "Hide X" is checked)
    local collTagDefs = {
        { key = "collected",    label = "Hide Collected",     color = { 0.12, 1.00, 0.00 } },
        { key = "notCollected", label = "Hide Not Collected", color = { 1.00, 0.27, 0.27 } },
    }
    for _, def in ipairs(collTagDefs) do
        local widget = sidebarWidgets.collection[def.key]
        if widget and widget.check:GetChecked() then
            AddTag(def.label, def.color, function()
                widget.check:SetChecked(false)
                if NS.UI.CatalogGrid_ToggleCollection then
                    -- Unchecking "Hide X" → show that category again
                    NS.UI.CatalogGrid_ToggleCollection(def.key, true)
                end
            end)
        end
    end

    -- Sources (skip Profession master — its sub-checkboxes are shown separately)
    local sourceOrder = NS.CatalogData and NS.CatalogData.SourceOrder
        or { "Vendor", "Quest", "Achievement", "Prey", "Profession", "Drop", "Treasure", "Other" }
    for _, srcType in ipairs(sourceOrder) do
        local widget = sidebarWidgets.sources[srcType]
        if widget and not widget._isProfMaster and widget.check:GetChecked() then
            local srcColor = NS.SourceColors and NS.SourceColors[srcType]
            AddTag(srcType, srcColor, function()
                widget.check:SetChecked(false)
                if NS.UI.CatalogGrid_ToggleSource then
                    NS.UI.CatalogGrid_ToggleSource(srcType, false)
                end
            end)
        end
    end

    -- Qualities
    local qualityOrder = NS.QualityOrder or { 1, 2, 3, 4, 5, 0 }
    for _, q in ipairs(qualityOrder) do
        local widget = sidebarWidgets.qualities[q]
        if widget and widget.check:GetChecked() then
            local qc = NS.QualityColors and NS.QualityColors[q]
            local qName = NS.QualityNames and NS.QualityNames[q] or ("Quality " .. q)
            AddTag(qName, qc, function()
                widget.check:SetChecked(false)
                if NS.UI.CatalogGrid_ToggleQuality then
                    NS.UI.CatalogGrid_ToggleQuality(q, false)
                end
            end)
        end
    end

    -- Professions
    local profOrder = NS.CatalogData and NS.CatalogData.ProfessionOrder or {}
    for _, profName in ipairs(profOrder) do
        local widget = sidebarWidgets.professions[profName]
        if widget and widget.check:GetChecked() then
            AddTag(profName, { 0.60, 0.40, 0.20 }, function()
                widget.check:SetChecked(false)
                if NS.UI.CatalogGrid_ToggleProfession then
                    NS.UI.CatalogGrid_ToggleProfession(profName, false)
                end
                UpdateProfessionCheckState()
            end)
        end
    end

    -- Zones (grouped by expansion for consistent ordering, colored by expansion)
    local expOrder = NS.CatalogData and NS.CatalogData.ExpansionOrder or {}
    for _, expName in ipairs(expOrder) do
        local expData = sidebarWidgets.expansions[expName]
        if expData then
            -- Convert expansion hex color to RGB table for tags
            local expColorHex = NS.ExpansionColors and NS.ExpansionColors[expName]
            local tagColor = nil
            if expColorHex then
                local r = tonumber(expColorHex:sub(1, 2), 16) / 255
                local g = tonumber(expColorHex:sub(3, 4), 16) / 255
                local b = tonumber(expColorHex:sub(5, 6), 16) / 255
                tagColor = { r, g, b }
            end

            for _, zName in ipairs(expData.zoneNames) do
                local zWidget = sidebarWidgets.zones[zName]
                if zWidget and zWidget.check:GetChecked() then
                    AddTag(zName, tagColor, function()
                        zWidget.check:SetChecked(false)
                        if NS.UI.CatalogGrid_ToggleZone then
                            NS.UI.CatalogGrid_ToggleZone(zName, false)
                        end
                        UpdateExpansionCheckState(expName)
                    end)
                end
            end
        end
    end

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

-------------------------------------------------------------------------------
-- ResetAllFilters: uncheck all sidebar filters and clear search text
-------------------------------------------------------------------------------
function NS.UI.ResetAllFilters()
    -- Favorites: uncheck
    local favWidget = sidebarWidgets.favorites["onlyFavorites"]
    if favWidget then
        favWidget.check:SetChecked(false)
    end

    -- Collection: uncheck all "Hide X" checkboxes (unchecked = show)
    for _, widget in pairs(sidebarWidgets.collection) do
        widget.check:SetChecked(false)
    end

    -- Sources: uncheck all
    for _, widget in pairs(sidebarWidgets.sources) do
        widget.check:SetChecked(false)
    end

    -- Professions: uncheck all
    for _, widget in pairs(sidebarWidgets.professions) do
        widget.check:SetChecked(false)
    end
    UpdateProfessionCheckState()

    -- Qualities: uncheck all
    for _, widget in pairs(sidebarWidgets.qualities) do
        widget.check:SetChecked(false)
    end

    -- Zones: uncheck all
    for _, widget in pairs(sidebarWidgets.zones) do
        widget.check:SetChecked(false)
    end
    -- Update expansion check states
    for contName, _ in pairs(sidebarWidgets.expansions) do
        UpdateExpansionCheckState(contName)
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
--            collection={ collected=N, notCollected=N } }
-------------------------------------------------------------------------------
function NS.UI.UpdateSidebarCounts(counts)
    if not counts then return end

    -- Favorites
    local favWidget = sidebarWidgets.favorites["onlyFavorites"]
    if favWidget then
        local fc = counts.favorites or 0
        favWidget.label:SetText(favWidget.namePrefix .. "  |cff40c8c8(" .. fc .. ")|r")
    end

    -- Collection
    for key, widget in pairs(sidebarWidgets.collection) do
        local c = 0
        if key == "collected" then
            c = counts.collection and counts.collection.collected or 0
        elseif key == "notCollected" then
            c = counts.collection and counts.collection.notCollected or 0
        end
        widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
    end

    -- Sources
    for srcType, widget in pairs(sidebarWidgets.sources) do
        local c = counts.sources and counts.sources[srcType] or 0
        widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
    end

    -- Professions
    for profName, widget in pairs(sidebarWidgets.professions) do
        local c = counts.professions and counts.professions[profName] or 0
        widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
    end

    -- Zones
    for zoneName, widget in pairs(sidebarWidgets.zones) do
        local c = counts.zones and counts.zones[zoneName] or 0
        widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
    end

    -- Expansion totals (sum of child zones)
    for contName, contData in pairs(sidebarWidgets.expansions) do
        local total = 0
        for _, zName in ipairs(contData.zoneNames) do
            total = total + (counts.zones and counts.zones[zName] or 0)
        end
        contData.label:SetText(contData.namePrefix .. "  |cff888888(" .. total .. ")|r")
    end

    -- Qualities
    for q, widget in pairs(sidebarWidgets.qualities) do
        local c = counts.qualities and counts.qualities[q] or 0
        widget.label:SetText(widget.namePrefix .. "  |cff888888(" .. c .. ")|r")
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

    local function SaveFramePosition()
        catalogFrame:StopMovingOrSizing()
        local point, _, relativePoint, x, y = catalogFrame:GetPoint()
        if NS.db then
            NS.db.catalogPosition = { point, nil, relativePoint, x, y }
        end
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

    -- Search box
    local searchBox = CreateFrame("EditBox", "HearthAndSeekCatalogSearch", titleBar,
                                   "SearchBoxTemplate")
    searchBox:SetSize(CatSizing.SearchBoxWidth, 26)
    searchBox:SetPoint("RIGHT", closeBtn, "LEFT", -8, 0)
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
    searchBox.Instructions:SetText("Search Decor")
    NS.UI._catalogSearchBox = searchBox

    -- Sidebar panel (left) — darker background
    local sidebar = CreateFrame("Frame", nil, catalogFrame, "BackdropTemplate")
    sidebar:SetWidth(CatSizing.SidebarWidth)
    sidebar:SetPoint("TOPLEFT", catalogFrame, "TOPLEFT", 1, -33)
    sidebar:SetPoint("BOTTOMLEFT", catalogFrame, "BOTTOMLEFT", 1, 1)
    sidebar:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    sidebar:SetBackdropColor(0.04, 0.04, 0.06, 1)

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
    detail:SetPoint("BOTTOMRIGHT", catalogFrame, "BOTTOMRIGHT", -1, 1)
    detail:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    detail:SetBackdropColor(0.05, 0.05, 0.07, 1)

    -- Vertical separator: grid | detail
    local sepRight = catalogFrame:CreateTexture(nil, "ARTWORK")
    sepRight:SetWidth(1)
    sepRight:SetPoint("TOPRIGHT", detail, "TOPLEFT", 0, 0)
    sepRight:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", 0, 0)
    sepRight:SetColorTexture(0.25, 0.25, 0.28, 1)

    -- Grid area (center)
    local grid = CreateFrame("Frame", nil, catalogFrame)
    grid:SetPoint("TOPLEFT", sidebar, "TOPRIGHT", 1, 0)
    grid:SetPoint("BOTTOMRIGHT", detail, "BOTTOMLEFT", -1, 0)

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

    -- Restore saved position
    if NS.db and NS.db.catalogPosition then
        local pos = NS.db.catalogPosition
        catalogFrame:ClearAllPoints()
        catalogFrame:SetPoint(pos[1], UIParent, pos[3], pos[4], pos[5])
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
        for contName, contData in pairs(sidebarWidgets.expansions) do
            if contData.zoneNames then
                for _, zName in ipairs(contData.zoneNames) do
                    if zName == zoneName then
                        UpdateExpansionCheckState(contName)
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
