-------------------------------------------------------------------------------
-- HearthAndSeek: CollectionsManager.lua
--
-- Full-area overlay for creating, renaming, and deleting user collections.
-- Replaces the catalog's grid + detail panel while open; restores them on
-- close. Title bar, filter bar, and footer stay visible.
--
-- Public API:
--   NS.UI.OpenCollectionsManager()
--   NS.UI.CloseCollectionsManager()
--
-- The manager is built lazily on first open. State (selected collection,
-- inline edit) does not persist across opens.
-------------------------------------------------------------------------------
local _, NS = ...
NS.UI = NS.UI or {}

local managerFrame = nil  -- top-level frame, parented to catalogFrame
local detailFiller = nil  -- texture covering the strip below the detail panel
                          -- while the manager is open (progress bar is hidden,
                          -- which would otherwise expose the catalog frame's
                          -- lighter backdrop in that strip)
local rowPool      = {}   -- recycled row frames
local emptyText    = nil
local capText      = nil
local listFrame    = nil  -- clip viewport (fixed bounds inside managerFrame)
local scrollChild  = nil  -- scrolls inside listFrame; rows live here
local scrollBar    = nil  -- Slider widget (matches main grid's scroll bar)
local scrollBarUpdating = false  -- guard against OnValueChanged feedback
local scrollOffset = 0
local newBtn       = nil

-------------------------------------------------------------------------------
-- Layout constants
-------------------------------------------------------------------------------
local ROW_H        = 30
local ROW_GAP      = 2
local LIST_PAD_TOP = 12
local SIDE_PAD     = 16

-- Row layout — name + count + Show Items packed on the LEFT;
-- Rename + Delete anchored to the RIGHT edge of the row. Names left
-- can grow to fill remaining space.
local COL_NAME_X      = 12
local COL_NAME_W      = 170
local COL_COUNT_X     = 188
local COL_COUNT_W     = 40
local COL_HINT_X      = 232    -- subtle "click to show items" hint position
local COL_EDIT_W      = 70
local COL_DELETE_W    = 82
local COL_SWATCH_W    = 22
local RIGHT_PAD       = 10

-- Sub-grid uses the same item-button template as the main catalog
-- grid (HearthAndSeekCatalogItemTemplate) so rendering — atlas card,
-- hover overlay, collected check, favorite star, model preview, 3D
-- ModelScene fallback — matches exactly. Buttons are smaller so more
-- items fit per row in the manager view.
local SUBGRID_BTN      = 64
local SUBGRID_GAP      = 4
local SUBGRID_COLS     = 8
local SUBGRID_SCALE    = SUBGRID_BTN / 110  -- Overlay metrics scale

-- Color palette (matches the rest of the addon's dark theme)
local COL_HEADER   = { 1.00, 0.82, 0.00 }    -- gold (titles)
local COL_NAME     = { 1.00, 0.95, 0.85 }    -- ivory (collection names)
local COL_COUNT    = { 0.55, 0.55, 0.55 }    -- gray (item counts)
local COL_HINT     = { 0.45, 0.45, 0.50 }    -- dim gray (hints)
local COL_DANGER   = { 0.95, 0.35, 0.35 }    -- red (delete hint text)

-------------------------------------------------------------------------------
-- Helpers
-------------------------------------------------------------------------------

local function findCatalogFrame()
    return _G["HearthAndSeekCatalogFrame"]
end

-- The detail panel and grid|detail separator stay visible while the
-- manager is open, so clicking an item in a collection's sub-grid
-- shows its info on the right just like in the catalog. The main
-- grid + count text + top filter bar + bottom progress bar are
-- hidden — the filter bar and progress bar don't apply to the
-- manager view, and showing them would clutter what is otherwise
-- a focused list of named collections.
local function showCatalogContent(show)
    local cf = findCatalogFrame()
    if not cf then return end
    local f = show and "Show" or "Hide"
    if cf._grid        then cf._grid[f](cf._grid)               end
    if cf._countText   then cf._countText[f](cf._countText)     end
    if cf._progressBar then cf._progressBar[f](cf._progressBar) end
    -- Filter bar: keep the stone-textured background visible at all
    -- times for visual continuity; only the buttons go away.
    if NS.UI.SetFilterBarButtonsShown then
        NS.UI.SetFilterBarButtonsShown(show)
    end
    -- detailFiller's visibility is the inverse of the catalog's: shown
    -- when the catalog chrome is hidden (manager view), hidden otherwise.
    if detailFiller then
        if show then detailFiller:Hide() else detailFiller:Show() end
    end
end

-- True iff this row's collection currently has at least one item. Used
-- to gate the "click to show items" hint, the row's expand-on-click
-- behaviour, and a couple of display fall-throughs. Centralised so the
-- "is the row interactive?" rule lives in one place.
local function rowHasItems(row)
    return row and row._name and NS.Collections.Count(row._name) > 0
end

local function failureMessage(reason)
    if reason == NS.Collections.ERR_EMPTY then
        return "Name can't be empty."
    elseif reason == NS.Collections.ERR_RESERVED then
        return "Names can't start with '_'."
    elseif reason == NS.Collections.ERR_TOO_LONG then
        return "Name is too long (max " .. NS.Collections.MAX_NAME_LEN .. ")."
    elseif reason == NS.Collections.ERR_DUPLICATE then
        return "A collection with that name already exists."
    elseif reason == NS.Collections.ERR_AT_CAP then
        return "You've reached the limit of " .. NS.Collections.MAX_COUNT
            .. " collections."
    end
    return "Couldn't update collection."
end

-------------------------------------------------------------------------------
-- Row construction
--
-- Each row holds:
--   • Editable name field (read-only by default; click ✏ to edit)
--   • Item count
--   • Edit (✏) button
--   • Delete (✕) button — two-step (arm → confirm) within 3 seconds
-------------------------------------------------------------------------------
local function flashError(text)
    if NS.Utils and NS.Utils.PrintMessage then
        NS.Utils.PrintMessage("|cffff5555" .. text .. "|r")
    end
end

local Refresh  -- forward declare

-- Notify the Collections filter dropdown that the user-collection set
-- changed so it rebuilds checkboxes + recomputes counts. Defensive nil
-- check since the dropdown is built lazily and may not exist yet.
local function notifyDropdown()
    if NS.UI and NS.UI.RefreshCollectionsDropdown then
        NS.UI.RefreshCollectionsDropdown()
    end
end

-- Forward declaration so buildSubGridButton can hook the template
-- click without ordering issues.
local applySubGridSelection

-- Build a single decor item button using the same template as the
-- main grid. The template's OnLoad wires tooltip, click, hover,
-- model-preview, etc. — all we do is set size and rescale the
-- overlays (Collected check + Favorite star use main-grid metrics
-- by default and end up oversized on a smaller button).
local function buildSubGridButton(parent)
    local btn = CreateFrame("Button", nil, parent, "HearthAndSeekCatalogItemTemplate")
    btn:SetSize(SUBGRID_BTN, SUBGRID_BTN)
    -- After the template's OnClick fires (which calls
    -- CatalogDetail_ShowItem and updates NS.UI._currentDetailItem),
    -- repaint every sub-grid button so the new selection highlight
    -- (golden tinted SlotBg) shows on the clicked item just like in
    -- the main catalog grid.
    btn:HookScript("OnClick", function(_, mouseBtn)
        if mouseBtn == "LeftButton" and not IsControlKeyDown()
                and not IsShiftKeyDown() then
            if applySubGridSelection then applySubGridSelection() end
        end
    end)
    -- Resize overlays proportionally to the smaller button
    if btn.Collected then
        btn.Collected:SetSize(20 * SUBGRID_SCALE, 20 * SUBGRID_SCALE)
        btn.Collected:ClearAllPoints()
        btn.Collected:SetPoint("BOTTOMRIGHT", -3, 3)
    end
    if btn.FavoriteStar then
        btn.FavoriteStar:SetSize(16 * SUBGRID_SCALE, 16 * SUBGRID_SCALE)
        btn.FavoriteStar:ClearAllPoints()
        btn.FavoriteStar:SetPoint("TOPLEFT", 4, -4)
    end
    -- Tighter icon inset for the smaller button — the template's
    -- default 10px is too much at 64px.
    if btn.Icon then
        btn.Icon:ClearAllPoints()
        btn.Icon:SetPoint("TOPLEFT", 4, -4)
        btn.Icon:SetPoint("BOTTOMRIGHT", -4, 4)
    end
    return btn
end

local function populateSubGrid(row, name)
    if not row.subGrid then return end
    -- Hide previously-bound item buttons
    for _, b in ipairs(row.subGrid._buttons) do
        b.itemData = nil
        b:Hide()
    end

    local set = NS.Collections._db and NS.Collections._db[name]
    local ids = {}
    if set then
        for id in pairs(set) do
            if type(id) == "number" then ids[#ids + 1] = id end
        end
        table.sort(ids)
    end

    local items = NS.CatalogData and NS.CatalogData.Items or {}
    for i, id in ipairs(ids) do
        local b = row.subGrid._buttons[i]
        if not b then
            b = buildSubGridButton(row.subGrid)
            row.subGrid._buttons[i] = b
        end
        local item = items[id]
        if item then
            b.itemData = item
            -- Use the shared catalog renderer so paintings (3D model
            -- only) and other special-case items show correctly.
            if NS.UI.RenderCatalogItemIcon then
                NS.UI.RenderCatalogItemIcon(b, item)
            elseif b.Icon and item.iconTexture then
                b.Icon:SetTexture(item.iconTexture)
            end
            local col = (i - 1) % SUBGRID_COLS
            local r   = math.floor((i - 1) / SUBGRID_COLS)
            b:ClearAllPoints()
            b:SetPoint("TOPLEFT", row.subGrid, "TOPLEFT",
                col * (SUBGRID_BTN + SUBGRID_GAP),
                -r * (SUBGRID_BTN + SUBGRID_GAP))
            b:Show()
        end
    end

    local rows = math.max(1, math.ceil(#ids / SUBGRID_COLS))
    row.subGrid:SetHeight(rows * SUBGRID_BTN + (rows - 1) * SUBGRID_GAP + 6)

    -- After binding items, paint the selection highlight to match
    -- the main grid's behaviour (golden tinted card on the currently
    -- selected detail item).
    if applySubGridSelection then applySubGridSelection() end
end

-- Mirror the main grid's selection styling onto every visible sub-
-- grid item button. Called from populateSubGrid (after binding) and
-- from each button's OnClick hook (after the template fires).
applySubGridSelection = function()
    local sel = NS.UI._currentDetailItem
    for _, row in pairs(rowPool) do
        if row and row.subGrid and row.subGrid:IsShown() then
            for _, b in ipairs(row.subGrid._buttons or {}) do
                if b:IsShown() and b.SlotBg and b.itemData then
                    if sel and sel.decorID == b.itemData.decorID then
                        b.SlotBg:SetAtlas("house-chest-list-Item-active")
                        b.SlotBg:SetDesaturated(true)
                        b.SlotBg:SetVertexColor(1.0, 0.82, 0.2, 1)
                    else
                        b.SlotBg:SetAtlas("house-chest-list-Item-default")
                        b.SlotBg:SetDesaturated(false)
                        b.SlotBg:SetVertexColor(1, 1, 1, 1)
                    end
                end
            end
        end
    end
end

local function startEdit(row)
    if row._isEditing then return end
    row._isEditing = true
    row.editBox:SetText(row._name)
    row.editBox:Show()
    row.editBox:SetFocus()
    row.editBox:HighlightText()
    row.okBtn:Show()
    row.cancelBtn:Show()
    row.nameLabel:Hide()
    row.countLabel:Hide()
    row.editBtn:Hide()
    row.deleteBtn:Hide()
    if row.colorSwatch then row.colorSwatch:Hide() end
    if row.resetBtn then row.resetBtn:Hide() end
    if row.expandHint then row.expandHint:Hide() end
end

local function cancelEdit(row)
    row._isEditing = false
    row.editBox:Hide()
    row.editBox:ClearFocus()
    row.okBtn:Hide()
    row.cancelBtn:Hide()
    row.nameLabel:Show()
    row.countLabel:Show()
    row.editBtn:Show()
    row.deleteBtn:Show()
    if row.colorSwatch then row.colorSwatch:Show() end
    if row.resetBtn then row.resetBtn:Show() end
    -- Only restore the expand hint if the collection actually has items.
    -- Without this, freshly-created empty collections (which auto-enter
    -- rename mode) end up showing "click to show items" once the rename
    -- finishes, even though clicking is a no-op for a 0-item collection.
    if row.expandHint and rowHasItems(row) then
        row.expandHint:Show()
    end
end

local function commitEdit(row)
    local newName = row.editBox:GetText() or ""
    cancelEdit(row)
    if newName == row._name then return end
    local oldName = row._name
    local ok, reason = NS.Collections.Rename(oldName, newName)
    if not ok then
        flashError(failureMessage(reason))
    elseif NS.UI.CatalogGrid_ForgetCollectionKey then
        -- Drop the orphaned `userCol_<oldname>` key from filter state
        -- so it doesn't accumulate. The new key gets registered on the
        -- next dropdown rebuild.
        NS.UI.CatalogGrid_ForgetCollectionKey("userCol_" .. oldName)
    end
    Refresh()
    notifyDropdown()
end

local function disarmDelete(row)
    row._deleteArmed = false
    row.deleteBtn:SetText("Delete")
end

local function armDelete(row)
    row._deleteArmed = true
    row.deleteBtn:SetText("Confirm?")
    -- Auto-disarm after 3 seconds
    C_Timer.After(3, function()
        if row and row._deleteArmed then
            disarmDelete(row)
        end
    end)
end

local function buildRow(parent)
    local row = CreateFrame("Frame", nil, parent, "BackdropTemplate")
    row:SetHeight(ROW_H)
    row:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    row:SetBackdropColor(0.10, 0.10, 0.12, 0.6)

    -- Click anywhere on the row to toggle expand. Child buttons
    -- (Rename / Delete) consume their own clicks, so OnMouseUp fires
    -- only for the rest of the row.
    row:EnableMouse(true)
    row:SetScript("OnMouseUp", function(self, mouseBtn)
        if mouseBtn ~= "LeftButton" then return end
        if row._isEditing then return end
        if not rowHasItems(row) then return end
        row._expanded = not row._expanded
        if row._expanded then
            populateSubGrid(row, row._name)
            row.subGrid:Show()
        else
            row.subGrid:Hide()
        end
        if Refresh then Refresh() end
    end)
    -- Subtle hover tint so users discover the row is clickable.
    row:SetScript("OnEnter", function() row:SetBackdropColor(0.14, 0.14, 0.18, 0.7) end)
    row:SetScript("OnLeave", function() row:SetBackdropColor(0.10, 0.10, 0.12, 0.6) end)

    -- Name label (read-only display) — fixed width on the LEFT.
    row.nameLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    row.nameLabel:SetPoint("LEFT", row, "LEFT", COL_NAME_X, 0)
    row.nameLabel:SetWidth(COL_NAME_W)
    row.nameLabel:SetJustifyH("LEFT")
    row.nameLabel:SetWordWrap(false)
    row.nameLabel:SetTextColor(unpack(COL_NAME))

    -- EditBox + OK + Cancel (hidden until Rename clicked). The
    -- editbox sits where the name label is; OK/Cancel anchor to the
    -- row's RIGHT edge (where Rename/Delete sit when not editing).
    row.editBox = CreateFrame("EditBox", nil, row, "InputBoxTemplate")
    row.editBox:SetPoint("LEFT", row, "LEFT", COL_NAME_X + 6, 0)
    row.editBox:SetWidth(COL_NAME_W + COL_COUNT_W - 8)
    row.editBox:SetHeight(20)
    row.editBox:SetAutoFocus(false)
    row.editBox:SetMaxLetters(NS.Collections.MAX_NAME_LEN)
    row.editBox:SetFontObject("GameFontHighlight")
    row.editBox:Hide()
    row.editBox:SetScript("OnEnterPressed", function() commitEdit(row) end)
    row.editBox:SetScript("OnEscapePressed", function() cancelEdit(row) end)

    row.cancelBtn = CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.cancelBtn:SetSize(COL_DELETE_W, 22)
    row.cancelBtn:SetPoint("RIGHT", row, "RIGHT", -RIGHT_PAD, 0)
    row.cancelBtn:SetText("Cancel")
    row.cancelBtn:Hide()
    row.cancelBtn:SetScript("OnClick", function() cancelEdit(row) end)

    row.okBtn = CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.okBtn:SetSize(COL_EDIT_W, 22)
    row.okBtn:SetPoint("RIGHT", row.cancelBtn, "LEFT", -6, 0)
    row.okBtn:SetText("OK")
    row.okBtn:Hide()
    row.okBtn:SetScript("OnClick", function() commitEdit(row) end)

    -- Item count, immediately right of the name
    row.countLabel = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    row.countLabel:SetPoint("LEFT", row, "LEFT", COL_COUNT_X, 0)
    row.countLabel:SetWidth(COL_COUNT_W)
    row.countLabel:SetJustifyH("LEFT")
    row.countLabel:SetTextColor(unpack(COL_COUNT))

    -- Subtle expand hint — replaces the old "Show Items" button.
    -- Whole row is the click target (see OnMouseUp above); this just
    -- whispers what the click does.
    row._expanded = false
    row.expandHint = row:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    row.expandHint:SetPoint("LEFT", row, "LEFT", COL_HINT_X, 0)
    row.expandHint:SetTextColor(unpack(COL_HINT))
    row.expandHint:SetText("click to show items")

    -- Sub-grid container (children: pooled item buttons). Anchored
    -- below the row. Height grows with item count when populated.
    row.subGrid = CreateFrame("Frame", nil, row)
    row.subGrid:SetPoint("TOPLEFT", row, "BOTTOMLEFT", 12, -4)
    row.subGrid:SetPoint("TOPRIGHT", row, "BOTTOMRIGHT", -12, -4)
    row.subGrid:SetHeight(1)
    row.subGrid._buttons = {}
    row.subGrid:Hide()

    -- Delete + Rename anchored to the row's RIGHT edge so they always
    -- sit at the far right regardless of row width. Delete sits
    -- rightmost; Rename to its left.
    row.deleteBtn = CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.deleteBtn:SetSize(COL_DELETE_W, 22)
    row.deleteBtn:SetPoint("RIGHT", row, "RIGHT", -RIGHT_PAD, 0)
    row.deleteBtn:SetText("Delete")

    row.editBtn = CreateFrame("Button", nil, row, "UIPanelButtonTemplate")
    row.editBtn:SetSize(COL_EDIT_W, 22)
    row.editBtn:SetPoint("RIGHT", row.deleteBtn, "LEFT", -6, 0)
    row.editBtn:SetText("Rename")
    row.editBtn:SetScript("OnClick", function() startEdit(row) end)

    -- Color swatch sits LEFT of Rename. Clicking opens the standard
    -- WoW colour picker; the chosen RGB is persisted on the collection
    -- and immediately reapplied to the dropdown checkbox + footer pill
    -- via notifyDropdown(). Refresh() keeps the swatch tint in sync
    -- with the current collection on each rebuild.
    row.colorSwatch = CreateFrame("Button", nil, row, "BackdropTemplate")
    row.colorSwatch:SetSize(COL_SWATCH_W, COL_SWATCH_W)
    row.colorSwatch:SetPoint("RIGHT", row.editBtn, "LEFT", -6, 0)
    row.colorSwatch:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8x8",
        edgeFile = "Interface\\Buttons\\WHITE8x8",
        edgeSize = 1,
        insets   = { left = 1, right = 1, top = 1, bottom = 1 },
    })
    row.colorSwatch:SetBackdropBorderColor(0.55, 0.45, 0.20, 1)
    row.colorSwatch:SetScript("OnEnter", function(self)
        self:SetBackdropBorderColor(1.00, 0.82, 0.00, 1)
    end)
    row.colorSwatch:SetScript("OnLeave", function(self)
        self:SetBackdropBorderColor(0.55, 0.45, 0.20, 1)
    end)
    -- Reset-to-default button. Sits LEFT of the swatch, smaller and
    -- icon-only so it doesn't compete visually with Rename/Delete.
    -- Tooltip explains the action; click clears any custom color and
    -- reverts the collection to DEFAULT_COLOR everywhere it shows up.
    row.resetBtn = CreateFrame("Button", nil, row, "BackdropTemplate")
    row.resetBtn:SetSize(COL_SWATCH_W, COL_SWATCH_W)
    row.resetBtn:SetPoint("RIGHT", row.colorSwatch, "LEFT", -4, 0)
    row.resetBtn:SetBackdrop({
        bgFile   = "Interface\\Buttons\\WHITE8x8",
        edgeFile = "Interface\\Buttons\\WHITE8x8",
        edgeSize = 1,
        insets   = { left = 1, right = 1, top = 1, bottom = 1 },
    })
    row.resetBtn:SetBackdropColor(0.10, 0.10, 0.12, 1)
    row.resetBtn:SetBackdropBorderColor(0.55, 0.45, 0.20, 1)
    local resetIcon = row.resetBtn:CreateTexture(nil, "ARTWORK")
    resetIcon:SetTexture("Interface\\Buttons\\UI-RefreshButton")
    resetIcon:SetPoint("CENTER", row.resetBtn, "CENTER", 0, 0)
    resetIcon:SetSize(14, 14)
    resetIcon:SetVertexColor(0.85, 0.80, 0.60, 1)
    row.resetBtn:SetScript("OnEnter", function(self)
        self:SetBackdropBorderColor(1.00, 0.82, 0.00, 1)
        resetIcon:SetVertexColor(1.00, 0.95, 0.50, 1)
        GameTooltip:SetOwner(self, "ANCHOR_TOP")
        GameTooltip:SetText("Reset color to default")
        GameTooltip:Show()
    end)
    row.resetBtn:SetScript("OnLeave", function(self)
        self:SetBackdropBorderColor(0.55, 0.45, 0.20, 1)
        resetIcon:SetVertexColor(0.85, 0.80, 0.60, 1)
        GameTooltip:Hide()
    end)
    row.resetBtn:SetScript("OnClick", function()
        local name = row._name
        if not name or not NS.Collections.Exists(name) then return end
        NS.Collections.ResetColor(name)
        local r, g, b = NS.Collections.GetColor(name)
        row.colorSwatch:SetBackdropColor(r, g, b, 1)
        row.nameLabel:SetTextColor(r, g, b, 1)
        notifyDropdown()
    end)

    row.colorSwatch:SetScript("OnClick", function()
        local name = row._name
        if not name or not NS.Collections.Exists(name) then return end
        local startR, startG, startB = NS.Collections.GetColor(name)
        local function apply()
            local r, g, b = ColorPickerFrame:GetColorRGB()
            NS.Collections.SetColor(name, r, g, b)
            row.colorSwatch:SetBackdropColor(r, g, b, 1)
            row.nameLabel:SetTextColor(r, g, b, 1)
            notifyDropdown()
        end
        local function cancel(prev)
            if prev then
                NS.Collections.SetColor(name, prev.r, prev.g, prev.b)
                row.colorSwatch:SetBackdropColor(prev.r, prev.g, prev.b, 1)
                row.nameLabel:SetTextColor(prev.r, prev.g, prev.b, 1)
                notifyDropdown()
            end
        end
        if ColorPickerFrame.SetupColorPickerAndShow then
            ColorPickerFrame:SetupColorPickerAndShow({
                r = startR, g = startG, b = startB,
                hasOpacity  = false,
                swatchFunc  = apply,
                cancelFunc  = cancel,
            })
        else
            -- Pre-Dragonflight legacy API fallback. Same effect.
            ColorPickerFrame.previousValues = { r = startR, g = startG, b = startB }
            ColorPickerFrame.hasOpacity     = false
            ColorPickerFrame.func           = apply
            ColorPickerFrame.cancelFunc     = cancel
            ColorPickerFrame:SetColorRGB(startR, startG, startB)
            ColorPickerFrame:Hide()  -- re-show fires OnShow, which reads previousValues
            ColorPickerFrame:Show()
        end
    end)
    row.deleteBtn:SetScript("OnClick", function(self)
        if not row._deleteArmed then
            armDelete(row)
            return
        end
        local name = row._name
        disarmDelete(row)
        local ok, reason = NS.Collections.Delete(name)
        if not ok then
            flashError(failureMessage(reason))
        elseif NS.UI.CatalogGrid_ForgetCollectionKey then
            NS.UI.CatalogGrid_ForgetCollectionKey("userCol_" .. name)
        end
        Refresh()
        notifyDropdown()
    end)

    return row
end

local function getRow(idx, parent)
    local row = rowPool[idx]
    if not row then
        row = buildRow(parent)
        rowPool[idx] = row
    end
    return row
end

-------------------------------------------------------------------------------
-- Refresh: rebuild the list from NS.Collections
-------------------------------------------------------------------------------
Refresh = function()
    if not managerFrame then return end
    local names = NS.Collections.List()
    local total = #names
    local cap   = NS.Collections.MAX_COUNT

    -- Show/hide rows
    for i = 1, math.max(total, #rowPool) do
        local row = rowPool[i]
        if i <= total then
            row = row or getRow(i, scrollChild)
            row._name = names[i]
            row._isEditing = false
            row._deleteArmed = false
            row.editBox:Hide()
            row.okBtn:Hide()
            row.cancelBtn:Hide()
            row.nameLabel:SetText(names[i])
            row.nameLabel:Show()
            local n = NS.Collections.Count(names[i])
            row.countLabel:SetText("(" .. n .. ")")
            row.countLabel:Show()
            row.editBtn:Show()
            row.deleteBtn:Show()
            row.deleteBtn:SetText("Delete")
            local cr, cg, cb = NS.Collections.GetColor(names[i])
            row.colorSwatch:SetBackdropColor(cr, cg, cb, 1)
            row.colorSwatch:Show()
            row.resetBtn:Show()
            row.nameLabel:SetTextColor(cr, cg, cb, 1)

            -- Auto-collapse if all items were removed since the row
            -- was last expanded (e.g. user unchecked the collection
            -- in the right-click submenu).
            if row._expanded and n == 0 then
                row._expanded = false
                row.subGrid:Hide()
            end

            -- Hint text reflects current state. Hidden entirely when
            -- the collection is empty (clicks are no-ops there).
            if n == 0 then
                row.expandHint:Hide()
            else
                row.expandHint:Show()
                row.expandHint:SetText(row._expanded
                    and "click to hide items"
                    or  "click to show items")
            end

            -- Refresh the sub-grid contents if currently expanded so
            -- thumbnails reflect any items added/removed since the
            -- row was last rendered (e.g. via right-click submenu).
            if row._expanded then
                populateSubGrid(row, names[i])
                row.subGrid:Show()
            else
                row.subGrid:Hide()
            end

            row:ClearAllPoints()
            row:Show()
        elseif row then
            row:Hide()
            if row.subGrid then row.subGrid:Hide() end
        end
    end

    -- Pass 2: anchor each visible row inside scrollChild, accumulating
    -- y-offset so expanded rows (with sub-grid below) push subsequent
    -- rows down. Done after populateSubGrid sized each sub-grid.
    local yOffset = -LIST_PAD_TOP
    for i = 1, total do
        local row = rowPool[i]
        if row then
            row:ClearAllPoints()
            row:SetPoint("TOPLEFT", scrollChild, "TOPLEFT", 0, yOffset)
            row:SetPoint("TOPRIGHT", scrollChild, "TOPRIGHT", 0, yOffset)
            yOffset = yOffset - ROW_H - ROW_GAP
            if row._expanded and row.subGrid then
                yOffset = yOffset - row.subGrid:GetHeight() - 4
            end
        end
    end

    -- Resize the scroll content to match what we just laid out and
    -- clamp scroll position when content shrinks (e.g. after a
    -- collapse / delete). Sync the Slider's value range too.
    local contentH = math.max(1, math.abs(yOffset) + LIST_PAD_TOP)
    scrollChild:SetHeight(contentH)
    local viewportH = listFrame:GetHeight()
    local maxScroll = math.max(0, contentH - viewportH)
    if scrollOffset > maxScroll then
        scrollOffset = maxScroll
    end
    if listFrame._applyScroll then listFrame._applyScroll() end
    if scrollBar then
        scrollBarUpdating = true
        scrollBar:SetMinMaxValues(0, maxScroll)
        scrollBar:SetValue(scrollOffset)
        scrollBarUpdating = false
        if maxScroll > 0 then
            scrollBar:Show()
        else
            scrollBar:Hide()
        end
    end

    -- Empty-state hint (no collections yet)
    if total == 0 then
        emptyText:Show()
    else
        emptyText:Hide()
    end

    -- Cap counter at bottom
    capText:SetText(total .. " / " .. cap .. " collections")
    if total >= cap then
        capText:SetTextColor(unpack(COL_DANGER))
        newBtn:Disable()
    else
        capText:SetTextColor(unpack(COL_HINT))
        newBtn:Enable()
    end
end

-------------------------------------------------------------------------------
-- "+ New Collection" handler — generate a unique default name then start
-- an inline rename on the new row.
-------------------------------------------------------------------------------
local function createNewCollection()
    -- Find the next free "Collection N" name
    local i = 1
    local base = "Collection "
    while NS.Collections.Exists(base .. i) do
        i = i + 1
    end
    local name = base .. i
    local ok, reason = NS.Collections.Create(name)
    if not ok then
        flashError(failureMessage(reason))
        return
    end
    Refresh()
    notifyDropdown()
    -- Find the row we just created and start editing it
    local list = NS.Collections.List()
    for idx, n in ipairs(list) do
        if n == name then
            local row = rowPool[idx]
            if row then startEdit(row) end
            return
        end
    end
end

-------------------------------------------------------------------------------
-- Build the manager frame (lazy, once)
-------------------------------------------------------------------------------
local function build()
    local cf = findCatalogFrame()
    if not cf then return end

    local CatSizing = NS.CatalogSizing or { FilterBarHeight = 32, ProgressBarHeight = 10 }

    managerFrame = CreateFrame("Frame", nil, cf, "BackdropTemplate")
    managerFrame:SetPoint("TOPLEFT", cf, "TOPLEFT", 1, -(43 + CatSizing.FilterBarHeight))
    -- Right edge anchors to the detail panel's left edge (so the detail
    -- panel stays visible). Bottom edge anchors to the catalog frame
    -- itself rather than to the detail panel — when the manager is open
    -- the progress bar is hidden, so the manager is free to fill the
    -- bottom strip that would otherwise reveal a different shade.
    if cf._detail then
        managerFrame:SetPoint("RIGHT",  cf._detail, "LEFT",   -1, 0)
    else
        managerFrame:SetPoint("RIGHT",  cf,         "RIGHT",  -1, 0)
    end
    managerFrame:SetPoint("BOTTOM", cf, "BOTTOM", 0, 1)
    managerFrame:SetBackdrop({ bgFile = "Interface\\Buttons\\WHITE8X8" })
    managerFrame:SetBackdropColor(0.05, 0.05, 0.07, 1)
    managerFrame:Hide()

    -- Strip under the detail panel: while the manager is open the progress
    -- bar is hidden, which exposes the catalog frame's lighter backdrop
    -- in the small horizontal band below the detail panel. Cover it with
    -- the same dark tone the manager + detail panel use so the bottom
    -- edge of the view reads as one continuous surface.
    if cf._detail then
        -- ARTWORK (not BACKGROUND) so we draw on top of the catalog frame's
        -- own backdrop, which also lives at the BACKGROUND layer and would
        -- otherwise occlude this filler depending on creation order.
        detailFiller = cf:CreateTexture(nil, "ARTWORK")
        detailFiller:SetColorTexture(0.05, 0.05, 0.07, 1)
        detailFiller:SetPoint("TOPLEFT",     cf._detail, "BOTTOMLEFT",  0, 0)
        detailFiller:SetPoint("BOTTOMRIGHT", cf,         "BOTTOMRIGHT", -1, 1)
        detailFiller:Hide()
    end

    -- Header row: title + Done button
    local header = CreateFrame("Frame", nil, managerFrame)
    header:SetHeight(40)
    header:SetPoint("TOPLEFT", managerFrame, "TOPLEFT", 0, 0)
    header:SetPoint("TOPRIGHT", managerFrame, "TOPRIGHT", 0, 0)

    local title = header:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    title:SetPoint("LEFT", header, "LEFT", SIDE_PAD, 0)
    title:SetText("Manage Collections")
    title:SetTextColor(unpack(COL_HEADER))

    local doneBtn = CreateFrame("Button", nil, header, "UIPanelButtonTemplate")
    doneBtn:SetSize(80, 24)
    doneBtn:SetPoint("RIGHT", header, "RIGHT", -SIDE_PAD, 0)
    doneBtn:SetText("Done")
    doneBtn:SetScript("OnClick", NS.UI.CloseCollectionsManager)

    -- Header bottom separator
    local sep = managerFrame:CreateTexture(nil, "ARTWORK")
    sep:SetHeight(1)
    sep:SetPoint("TOPLEFT", header, "BOTTOMLEFT", SIDE_PAD, -2)
    sep:SetPoint("TOPRIGHT", header, "BOTTOMRIGHT", -SIDE_PAD, -2)
    sep:SetColorTexture(0.25, 0.20, 0.10, 0.9)

    -- "New Collection" button
    newBtn = CreateFrame("Button", nil, managerFrame, "UIPanelButtonTemplate")
    newBtn:SetSize(180, 24)
    newBtn:SetPoint("TOPLEFT", header, "BOTTOMLEFT", SIDE_PAD, -16)
    newBtn:SetText("New Collection")
    newBtn:SetScript("OnClick", createNewCollection)

    -- List container — fixed bounds inside the manager frame; clips
    -- its children so the row list scrolls instead of overflowing.
    listFrame = CreateFrame("Frame", nil, managerFrame)
    listFrame:SetPoint("TOPLEFT", newBtn, "BOTTOMLEFT", 0, -10)
    listFrame:SetPoint("RIGHT", managerFrame, "RIGHT", -SIDE_PAD - 10, 0)
    listFrame:SetPoint("BOTTOM", managerFrame, "BOTTOM", 0, 36)
    listFrame:SetClipsChildren(true)

    -- Scroll content — rows are parented here. We translate this
    -- frame upward inside listFrame to scroll. Following the dropdown
    -- panel pattern (manual scroll, no Blizzard ScrollFrame) for
    -- consistency with the rest of the addon.
    scrollChild = CreateFrame("Frame", nil, listFrame)
    scrollChild:SetPoint("TOPLEFT", listFrame, "TOPLEFT", 0, 0)
    scrollChild:SetPoint("RIGHT", listFrame, "RIGHT", 0, 0)
    scrollChild:SetHeight(1)

    -- Apply current scrollOffset to scrollChild's vertical anchor.
    local function applyScroll()
        scrollChild:ClearAllPoints()
        scrollChild:SetPoint("TOPLEFT", listFrame, "TOPLEFT", 0, scrollOffset)
        scrollChild:SetPoint("RIGHT", listFrame, "RIGHT", 0, 0)
    end

    -- Scroll bar — Slider with the same minimal Blizzard atlas thumb
    -- the main grid uses, so the catalog and the manager have a
    -- consistent scroll feel.
    scrollBar = CreateFrame("Slider", nil, managerFrame)
    scrollBar:SetWidth(6)
    scrollBar:SetPoint("TOPRIGHT", listFrame, "TOPRIGHT", 8, 0)
    scrollBar:SetPoint("BOTTOMRIGHT", listFrame, "BOTTOMRIGHT", 8, 0)
    scrollBar:SetOrientation("VERTICAL")
    scrollBar:SetMinMaxValues(0, 0)
    scrollBar:SetValue(0)
    scrollBar:SetValueStep(0.001)
    scrollBar:SetObeyStepOnDrag(false)

    local trackBg = scrollBar:CreateTexture(nil, "BACKGROUND")
    trackBg:SetAllPoints()
    trackBg:SetColorTexture(0.15, 0.15, 0.15, 0.5)

    scrollBar:SetThumbTexture("Interface\\Buttons\\WHITE8X8")
    local thumb = scrollBar:GetThumbTexture()
    thumb:SetAtlas("minimal-scrollbar-small-thumb-middle")
    thumb:SetSize(6, 40)
    scrollBar:SetScript("OnValueChanged", function(_, value)
        if scrollBarUpdating then return end
        scrollOffset = value
        applyScroll()
    end)
    scrollBar:Hide()  -- shown after Refresh computes content height

    listFrame:EnableMouseWheel(true)
    listFrame:SetScript("OnMouseWheel", function(_, delta)
        local maxScroll = math.max(0,
            scrollChild:GetHeight() - listFrame:GetHeight())
        scrollOffset = math.max(0, math.min(maxScroll,
            scrollOffset - delta * 30))
        applyScroll()
        if scrollBar:IsShown() then
            scrollBarUpdating = true
            scrollBar:SetValue(scrollOffset)
            scrollBarUpdating = false
        end
    end)

    listFrame._applyScroll = applyScroll

    -- Empty-state hint
    emptyText = managerFrame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    emptyText:SetPoint("CENTER", listFrame, "CENTER", 0, 0)
    emptyText:SetText("No collections yet. Click \"New Collection\" to create one.")
    emptyText:SetTextColor(unpack(COL_HINT))
    emptyText:Hide()

    -- Cap counter at the bottom
    capText = managerFrame:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    capText:SetPoint("BOTTOM", managerFrame, "BOTTOM", 0, 12)
    capText:SetTextColor(unpack(COL_HINT))
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
function NS.UI.OpenCollectionsManager()
    if not managerFrame then build() end
    if not managerFrame then return end
    showCatalogContent(false)
    managerFrame:Show()
    Refresh()
end

-- External refresh hook for code that mutates collections from
-- elsewhere (e.g. right-click Collections submenu) so any open
-- sub-grids re-render with the latest state.
function NS.UI.RefreshCollectionsManager()
    if managerFrame and managerFrame:IsShown() then
        Refresh()
    end
end

function NS.UI.CloseCollectionsManager()
    if managerFrame then managerFrame:Hide() end
    showCatalogContent(true)
end

-- Safety net: when the catalog frame hides (user closes the addon),
-- ensure the next time it opens it shows the catalog view, not the
-- last-used manager view.
local function attachCatalogOnHide()
    local cf = findCatalogFrame()
    if not cf or cf._collectionsManagerHooked then return end
    cf._collectionsManagerHooked = true
    cf:HookScript("OnHide", function()
        if managerFrame and managerFrame:IsShown() then
            managerFrame:Hide()
            showCatalogContent(true)
        end
    end)
end

-- Defer hooking until after InitCatalog runs. PLAYER_LOGIN fires after
-- ADDON_LOADED, by which point the catalog frame exists.
local f = CreateFrame("Frame")
f:RegisterEvent("PLAYER_LOGIN")
f:SetScript("OnEvent", function(self)
    attachCatalogOnHide()
    self:UnregisterAllEvents()
end)
