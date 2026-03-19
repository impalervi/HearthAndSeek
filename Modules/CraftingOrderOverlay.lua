-------------------------------------------------------------------------------
-- HearthAndSeek: CraftingOrderOverlay.lua
-- Shows ownership icons on decor items in the crafting orders (customer) frame:
--   Green checkmark      = already collected
--   Yellow exclamation   = not yet collected (no bonus)
--   Blue exclamation     = not yet collected (first-acquisition bonus available)
-- Reuses the same settings as VendorOverlay (showVendorOwned/Bonus/Uncollected).
-------------------------------------------------------------------------------
local _, NS = ...

-- Guard: bail if the Housing Catalog API for items doesn't exist
if not C_HousingCatalog or not C_HousingCatalog.GetCatalogEntryInfoByItem then
    return
end

-------------------------------------------------------------------------------
-- Constants (same icons as VendorOverlay)
-------------------------------------------------------------------------------
local ICON_SIZE = 14
local ICON_OFFSET = 4

local CHECK_TEXTURE        = "Interface\\RaidFrame\\ReadyCheck-Ready"
local EXCLAIM_BONUS_TEXTURE = "Interface\\GossipFrame\\DailyQuestIcon"      -- blue
local EXCLAIM_NO_BONUS_TEXTURE = "Interface\\GossipFrame\\AvailableQuestIcon"  -- yellow

-------------------------------------------------------------------------------
-- Cache (shared decor info cache, same logic as VendorOverlay)
-------------------------------------------------------------------------------
local decorCache = {}

local function QueryDecorInfo(itemID)
    if decorCache[itemID] ~= nil then
        return decorCache[itemID]
    end

    local ok, info = pcall(C_HousingCatalog.GetCatalogEntryInfoByItem, itemID, true)
    if not ok or type(info) ~= "table" then
        decorCache[itemID] = false
        return false
    end

    local quantity   = info.quantity or 0
    local placed     = info.numPlaced or 0
    local redeemable = info.remainingRedeemable or 0
    local owned      = (quantity + placed + redeemable) > 0
    local hasBonus   = info.firstAcquisitionBonus and info.firstAcquisitionBonus > 0

    decorCache[itemID] = {
        owned = owned,
        bonus = (not owned) and hasBonus,
        uncollected = (not owned) and (not hasBonus),
    }
    return decorCache[itemID]
end

-------------------------------------------------------------------------------
-- Overlay pool for ScrollBox frames (keyed by cell frame)
-------------------------------------------------------------------------------
local overlayPool = {}

local function GetOverlay(cell)
    if overlayPool[cell] then
        return overlayPool[cell]
    end

    local anchor = cell.Icon or cell

    local check = cell:CreateTexture(nil, "OVERLAY", nil, 7)
    check:SetTexture(CHECK_TEXTURE)
    check:SetSize(ICON_SIZE, ICON_SIZE)
    check:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                   ICON_OFFSET, -ICON_OFFSET)
    check:Hide()

    local exclaimBonus = cell:CreateTexture(nil, "OVERLAY", nil, 7)
    exclaimBonus:SetTexture(EXCLAIM_BONUS_TEXTURE)
    exclaimBonus:SetSize(ICON_SIZE, ICON_SIZE)
    exclaimBonus:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                     ICON_OFFSET, -ICON_OFFSET)
    exclaimBonus:Hide()

    local exclaimNoBonus = cell:CreateTexture(nil, "OVERLAY", nil, 7)
    exclaimNoBonus:SetTexture(EXCLAIM_NO_BONUS_TEXTURE)
    exclaimNoBonus:SetSize(ICON_SIZE, ICON_SIZE)
    exclaimNoBonus:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                     ICON_OFFSET, -ICON_OFFSET)
    exclaimNoBonus:Hide()

    local entry = { check = check, exclaimBonus = exclaimBonus, exclaimNoBonus = exclaimNoBonus }
    overlayPool[cell] = entry
    return entry
end

local function HideOverlay(cell)
    local ov = overlayPool[cell]
    if ov then
        ov.check:Hide()
        ov.exclaimBonus:Hide()
        ov.exclaimNoBonus:Hide()
    end
end

-------------------------------------------------------------------------------
-- Apply overlay to a single item-name cell after it populates
-------------------------------------------------------------------------------
local function ApplyOverlayToCell(cell)
    local settings = NS.db and NS.db.settings
    local showOwned       = not (settings and settings.showVendorOwned == false)
    local showBonus       = not (settings and settings.showVendorBonus == false)
    local showUncollected = not (settings and settings.showVendorUncollected == false)

    if not showOwned and not showBonus and not showUncollected then
        HideOverlay(cell)
        return
    end

    -- Walk up to the row frame to find the option data
    local rowData = cell.rowData
    local itemID
    if rowData then
        -- rowData.option holds the CraftingOrderCustomerOptionInfo or CraftingOrderInfo
        local option = rowData.option or rowData
        itemID = option.itemID
    end

    if not itemID or itemID <= 0 then
        HideOverlay(cell)
        return
    end

    local info = QueryDecorInfo(itemID)
    local overlay = GetOverlay(cell)
    overlay.check:Hide()
    overlay.exclaimBonus:Hide()
    overlay.exclaimNoBonus:Hide()

    if info then
        if info.owned and showOwned then
            overlay.check:Show()
        elseif info.bonus and showBonus then
            overlay.exclaimBonus:Show()
        elseif info.uncollected and showUncollected then
            overlay.exclaimNoBonus:Show()
        end
    end
end

-------------------------------------------------------------------------------
-- Hook the item-name cell Populate method (fires whenever a cell is shown)
-------------------------------------------------------------------------------
local hooked = false

local function HookCraftingOrderCells()
    if hooked then return end

    -- Customer-side item name cell mixin
    if ProfessionsCustomerTableCellItemNameMixin
       and ProfessionsCustomerTableCellItemNameMixin.Populate then
        hooksecurefunc(ProfessionsCustomerTableCellItemNameMixin, "Populate",
            function(self, ...)
                ApplyOverlayToCell(self)
            end)
        hooked = true
    end
end

-------------------------------------------------------------------------------
-- Event handling
-------------------------------------------------------------------------------
local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("ADDON_LOADED")
eventFrame:RegisterEvent("CRAFTINGORDERS_SHOW_CUSTOMER")
eventFrame:RegisterEvent("CRAFTINGORDERS_HIDE_CUSTOMER")
eventFrame:RegisterEvent("CRAFTINGORDERS_CUSTOMER_OPTIONS_PARSED")
eventFrame:RegisterEvent("BAG_UPDATE_DELAYED")

eventFrame:SetScript("OnEvent", function(_, event, arg1)
    if event == "ADDON_LOADED" and arg1 == "Blizzard_ProfessionsCustomerOrders" then
        -- The Blizzard addon is now loaded, hook into it
        C_Timer.After(0.1, HookCraftingOrderCells)
    elseif event == "CRAFTINGORDERS_SHOW_CUSTOMER" then
        wipe(decorCache)
        HookCraftingOrderCells()
    elseif event == "CRAFTINGORDERS_HIDE_CUSTOMER" then
        wipe(decorCache)
    elseif event == "CRAFTINGORDERS_CUSTOMER_OPTIONS_PARSED" then
        -- Results just loaded — overlays will be applied via Populate hook
        wipe(decorCache)
    elseif event == "BAG_UPDATE_DELAYED" then
        if ProfessionsCustomerOrdersFrame
           and ProfessionsCustomerOrdersFrame:IsShown() then
            wipe(decorCache)
            if NS.CraftingOrderOverlay and NS.CraftingOrderOverlay.Refresh then
                NS.CraftingOrderOverlay.Refresh()
            end
        end
    end
end)

-- If the Blizzard addon is already loaded (unlikely but safe)
if C_AddOns and C_AddOns.IsAddOnLoaded
   and C_AddOns.IsAddOnLoaded("Blizzard_ProfessionsCustomerOrders") then
    HookCraftingOrderCells()
end

-------------------------------------------------------------------------------
-- Expose refresh for settings toggles
-------------------------------------------------------------------------------
NS.CraftingOrderOverlay = {
    Refresh = function()
        -- Re-apply overlays on all currently visible cells
        for cell, _ in pairs(overlayPool) do
            if cell:IsShown() then
                ApplyOverlayToCell(cell)
            else
                HideOverlay(cell)
            end
        end
    end,
}
