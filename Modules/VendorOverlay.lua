-------------------------------------------------------------------------------
-- HearthAndSeek: VendorOverlay.lua
-- Shows ownership icons on decor items in the merchant (vendor) frame:
--   Green checkmark      = already collected
--   Yellow exclamation   = not yet collected (no bonus)
--   Blue exclamation     = not yet collected (first-acquisition bonus available)
-------------------------------------------------------------------------------
local _, NS = ...

-- Guard: bail if the Housing Catalog API for items doesn't exist
if not C_HousingCatalog or not C_HousingCatalog.GetCatalogEntryInfoByItem then
    return
end

-------------------------------------------------------------------------------
-- Constants
-------------------------------------------------------------------------------
local ICON_SIZE = 15
local ICON_OFFSET = 2                -- pixels past icon edge (badge look)

local CHECK_TEXTURE        = "Interface\\RaidFrame\\ReadyCheck-Ready"
local EXCLAIM_BONUS_TEXTURE = "Interface\\GossipFrame\\DailyQuestIcon"      -- blue
local EXCLAIM_NO_BONUS_TEXTURE = "Interface\\GossipFrame\\AvailableQuestIcon"  -- yellow

-------------------------------------------------------------------------------
-- Caches
-------------------------------------------------------------------------------
local overlayPool = {}   -- [itemButton] = { check, exclaimBonus, exclaimNoBonus }
local decorCache  = {}   -- [itemID]     = { owned, bonus, uncollected } | false

-------------------------------------------------------------------------------
-- Lazy-create overlay textures on a merchant item button
-------------------------------------------------------------------------------
local function GetOverlay(itemButton)
    if overlayPool[itemButton] then
        return overlayPool[itemButton]
    end

    -- Find the icon texture inside the button (anchor badge to the icon, not the whole button)
    local iconRef = itemButton.icon
        or itemButton.Icon
        or (itemButton.GetName and _G[itemButton:GetName() .. "IconTexture"])
    local anchor = iconRef or itemButton

    -- Green checkmark (owned)
    local check = itemButton:CreateTexture(nil, "OVERLAY", nil, 7)
    check:SetTexture(CHECK_TEXTURE)
    check:SetSize(ICON_SIZE, ICON_SIZE)
    check:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                   ICON_OFFSET, -ICON_OFFSET)
    check:Hide()

    -- Blue exclamation (not yet collected, bonus available)
    local exclaimBonus = itemButton:CreateTexture(nil, "OVERLAY", nil, 7)
    exclaimBonus:SetTexture(EXCLAIM_BONUS_TEXTURE)
    exclaimBonus:SetSize(ICON_SIZE, ICON_SIZE)
    exclaimBonus:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                     ICON_OFFSET, -ICON_OFFSET)
    exclaimBonus:Hide()

    -- Yellow exclamation (not yet collected, no bonus)
    local exclaimNoBonus = itemButton:CreateTexture(nil, "OVERLAY", nil, 7)
    exclaimNoBonus:SetTexture(EXCLAIM_NO_BONUS_TEXTURE)
    exclaimNoBonus:SetSize(ICON_SIZE, ICON_SIZE)
    exclaimNoBonus:SetPoint("BOTTOMRIGHT", anchor, "BOTTOMRIGHT",
                     ICON_OFFSET, -ICON_OFFSET)
    exclaimNoBonus:Hide()

    local entry = { check = check, exclaimBonus = exclaimBonus, exclaimNoBonus = exclaimNoBonus }
    overlayPool[itemButton] = entry
    return entry
end

-------------------------------------------------------------------------------
-- Query the Housing Catalog for a given itemID (cached)
-------------------------------------------------------------------------------
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
-- Hide all overlays (used when feature is disabled or on wrong tab)
-------------------------------------------------------------------------------
local function HideAllOverlays()
    for _, overlay in pairs(overlayPool) do
        overlay.check:Hide()
        overlay.exclaimBonus:Hide()
        overlay.exclaimNoBonus:Hide()
    end
end

-------------------------------------------------------------------------------
-- Main: update overlays on all visible merchant buttons
-------------------------------------------------------------------------------
local function UpdateVendorOverlays()
    local settings = NS.db and NS.db.settings
    local showOwned      = not (settings and settings.showVendorOwned == false)
    local showBonus      = not (settings and settings.showVendorBonus == false)
    local showUncollected = not (settings and settings.showVendorUncollected == false)

    -- All disabled — hide everything and bail
    if not showOwned and not showBonus and not showUncollected then
        HideAllOverlays()
        return
    end

    -- Only process the buy tab (tab 1), not buyback (tab 2)
    if not MerchantFrame or not MerchantFrame:IsShown()
       or MerchantFrame.selectedTab ~= 1 then
        HideAllOverlays()
        return
    end

    local numItems = GetMerchantNumItems()

    for i = 1, MERCHANT_ITEMS_PER_PAGE do
        local index = ((MerchantFrame.page - 1) * MERCHANT_ITEMS_PER_PAGE) + i
        local itemButton = _G["MerchantItem" .. i .. "ItemButton"]
        if not itemButton then break end

        local overlay = GetOverlay(itemButton)
        overlay.check:Hide()
        overlay.exclaimBonus:Hide()
        overlay.exclaimNoBonus:Hide()

        if index <= numItems then
            local itemID = GetMerchantItemID(index)
            if itemID and itemID > 0 then
                local info = QueryDecorInfo(itemID)
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
        end
    end
end

-------------------------------------------------------------------------------
-- Expose refresh so the settings toggle can trigger an immediate update
-------------------------------------------------------------------------------
NS.VendorOverlay = { Refresh = UpdateVendorOverlays }

-------------------------------------------------------------------------------
-- Event handling: cache invalidation + debounced refresh
-------------------------------------------------------------------------------
local pendingUpdate = false

local function ScheduleUpdate(delay, wipeCache)
    if pendingUpdate then return end
    pendingUpdate = true
    C_Timer.After(delay, function()
        pendingUpdate = false
        if wipeCache then wipe(decorCache) end
        UpdateVendorOverlays()
    end)
end

local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("MERCHANT_SHOW")
eventFrame:RegisterEvent("MERCHANT_UPDATE")
eventFrame:RegisterEvent("MERCHANT_CLOSED")
eventFrame:RegisterEvent("BAG_UPDATE_DELAYED")

eventFrame:SetScript("OnEvent", function(_, event)
    if event == "MERCHANT_SHOW" then
        wipe(decorCache)
        ScheduleUpdate(0.1, false)
    elseif event == "MERCHANT_UPDATE" then
        ScheduleUpdate(0.2, true)
    elseif event == "MERCHANT_CLOSED" then
        HideAllOverlays()
        wipe(decorCache)
    elseif event == "BAG_UPDATE_DELAYED" then
        if MerchantFrame and MerchantFrame:IsShown() then
            ScheduleUpdate(0.3, true)
        end
    end
end)

-------------------------------------------------------------------------------
-- Hook the merchant frame redraw so overlays update on page changes, etc.
-------------------------------------------------------------------------------
if MerchantFrame_Update then
    hooksecurefunc("MerchantFrame_Update", UpdateVendorOverlays)
end
