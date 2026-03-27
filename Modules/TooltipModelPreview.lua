-------------------------------------------------------------------------------
-- HearthAndSeek: TooltipModelPreview.lua
-- Shows a slowly rotating 3D model preview beside the tooltip when
-- hovering over any decoration item found in the catalog.
-------------------------------------------------------------------------------
local _, NS = ...

-- Guard: bail if required APIs don't exist
if not C_HousingCatalog then return end

-------------------------------------------------------------------------------
-- Constants
-------------------------------------------------------------------------------
local PREVIEW_SIZE = 200
local ROTATION_SPEED = 0.5  -- radians per second
local DEFAULT_SCENE_ID = 859

-- Debug: set to true to print diagnostic info to chat
local DEBUG = false
local function dbg(...)
    if DEBUG then print("|cff00ccff[H&S Preview]|r", ...) end
end

-------------------------------------------------------------------------------
-- Reverse lookup: itemID → catalog item (built lazily)
-------------------------------------------------------------------------------
local itemIDToDecor = nil

local function EnsureItemLookup()
    if itemIDToDecor then return end
    itemIDToDecor = {}
    local items = NS.CatalogData and NS.CatalogData.Items
    if not items then
        dbg("WARNING: CatalogData.Items is nil!")
        return
    end
    local count = 0
    for _, item in pairs(items) do
        if item.itemID and item.itemID > 0 and item.asset and item.asset > 0 then
            itemIDToDecor[item.itemID] = item
            count = count + 1
        end
    end
    dbg("Built itemID lookup:", count, "entries")
end

-------------------------------------------------------------------------------
-- Preview frame (lazy-created)
-------------------------------------------------------------------------------
local previewFrame = nil

local function GetPreviewFrame()
    if previewFrame then return previewFrame end

    -- Wrapper frame with tooltip-matching backdrop
    local f = CreateFrame("Frame", nil, UIParent, "BackdropTemplate")
    f:SetSize(PREVIEW_SIZE, PREVIEW_SIZE)
    f:SetFrameStrata("TOOLTIP")
    f:SetFrameLevel(200)
    f:SetBackdrop({
        bgFile   = "Interface\\Tooltips\\UI-Tooltip-Background",
        edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
        edgeSize = 12,
        insets   = { left = 3, right = 3, top = 3, bottom = 3 },
    })
    f:SetBackdropColor(0.05, 0.05, 0.08, 0.92)
    f:SetBackdropBorderColor(0.6, 0.6, 0.6, 0.8)
    f:SetClampedToScreen(true)
    f:EnableMouse(false)
    f:Hide()

    -- ModelScene inside the wrapper (no mouse interaction — display only)
    local scene = CreateFrame("ModelScene", nil, f,
        "PanningModelSceneMixinTemplate")
    scene:SetPoint("TOPLEFT", 6, -6)
    scene:SetPoint("BOTTOMRIGHT", -6, 6)
    scene:EnableMouse(false)
    scene:EnableMouseWheel(false)

    f._modelScene = scene

    -- Auto-rotation on the wrapper frame (not on the ModelScene, which has
    -- its own OnUpdate from PanningModelSceneMixinTemplate that must not
    -- be replaced)
    f:SetScript("OnUpdate", function(self, elapsed)
        if not self:IsVisible() then return end
        local actor = self._modelScene:GetActorByTag("decor")
        if actor then
            local yaw = actor:GetYaw() or 0
            actor:SetYaw(yaw + ROTATION_SPEED * elapsed)
        end
    end)
    previewFrame = f
    dbg("Preview frame created")
    return f
end

-------------------------------------------------------------------------------
-- Show / hide
-------------------------------------------------------------------------------
local currentItemID = nil  -- tracks which item is currently previewed
local pendingGen = 0       -- generation counter for deferred timer validation

local function ShowPreview(item, tooltip)
    if not item or not item.asset or item.asset <= 0 then
        dbg("ShowPreview: no item or no asset")
        return
    end

    -- Already showing this item — don't reset the scene (avoids stutter)
    if currentItemID == item.itemID and previewFrame and previewFrame:IsShown() then
        return
    end

    local f = GetPreviewFrame()
    currentItemID = item.itemID
    f:SetSize(PREVIEW_SIZE, PREVIEW_SIZE)

    -- Position beside the tooltip. During combat, tooltip geometry is
    -- tainted ("secret number" values), so fall back to cursor position.
    f:ClearAllPoints()

    if InCombatLockdown() then
        -- Combat: position above cursor (tooltip sits top-left of cursor)
        local uiScale = UIParent:GetEffectiveScale()
        local screenW = UIParent:GetWidth()
        local cx, cy = GetCursorPosition()
        local cursorX = cx / uiScale
        local cursorY = cy / uiScale
        local left = cursorX - PREVIEW_SIZE / 2
        if left < 0 then left = 0 end
        if left + PREVIEW_SIZE > screenW then left = screenW - PREVIEW_SIZE end
        f:SetPoint("BOTTOMLEFT", UIParent, "BOTTOMLEFT", left, cursorY + 25)
    else
        -- Normal: anchor directly to tooltip
        local tipRight = tooltip:GetRight() or 0
        local screenW = UIParent:GetRight() or UIParent:GetWidth()
        if tipRight + PREVIEW_SIZE + 4 > screenW then
            f:SetPoint("TOPRIGHT", tooltip, "TOPLEFT", -2, 0)
        else
            f:SetPoint("TOPLEFT", tooltip, "TOPRIGHT", 2, 0)
        end
    end

    -- Clear stale scene state before transitioning; after loading screens the
    -- engine can invalidate C-side actors while the Lua-side tagToActor table
    -- still holds references to them, causing silent failures.
    f._modelScene:ClearScene()

    local sceneID = item.uiModelSceneID or DEFAULT_SCENE_ID
    local ok, err = pcall(function()
        f._modelScene:TransitionToModelSceneID(
            sceneID,
            CAMERA_TRANSITION_TYPE_IMMEDIATE,
            CAMERA_MODIFICATION_TYPE_DISCARD,
            true
        )
    end)

    if not ok then
        dbg("TransitionToModelSceneID FAILED:", tostring(err))
        return
    end

    local actor = f._modelScene:GetActorByTag("decor")
    if not actor then
        dbg("GetActorByTag('decor') returned nil for sceneID:", sceneID)
        return
    end

    actor:SetPreferModelCollisionBounds(true)
    actor:SetModelByFileID(item.asset)
    f:Show()
    dbg("Showing model for", item.name or "?", "asset:", item.asset, "scene:", sceneID)
end

local function HidePreview()
    currentItemID = nil
    if previewFrame and previewFrame:IsShown() then
        previewFrame:Hide()
    end
end

-------------------------------------------------------------------------------
-- Tooltip hooks
-------------------------------------------------------------------------------
GameTooltip:HookScript("OnHide", HidePreview)

-- Modern tooltip data processor (WoW 10.0.2+)
if TooltipDataProcessor and TooltipDataProcessor.AddTooltipPostCall then
    TooltipDataProcessor.AddTooltipPostCall(Enum.TooltipDataType.Item,
        function(tooltip, data)
            if tooltip ~= GameTooltip then return end

            -- Check settings (defaults to enabled)
            local settings = NS.db and NS.db.settings
            if settings and settings.showTooltipModel == false then return end

            -- Skip when hovering catalog grid icons (detail panel already shows the model)
            local owner = tooltip:GetOwner()
            if owner and owner.itemData then return end

            EnsureItemLookup()

            local itemID = data and data.id
            if not itemID then
                pendingGen = pendingGen + 1
                HidePreview()
                return
            end

            local item = itemIDToDecor[itemID]
            if item then
                -- Bump generation so any older pending timer is invalidated
                pendingGen = pendingGen + 1
                local myGen = pendingGen
                currentItemID = item.itemID

                -- Defer to next frame to escape the secure callback context;
                -- TransitionToModelSceneID and Show() can fail silently from
                -- taint when called inside securecallfunction
                C_Timer.After(0, function()
                    -- Generation mismatch means a newer hover superseded us
                    if myGen ~= pendingGen then return end
                    if GameTooltip:IsShown() then
                        ShowPreview(item, GameTooltip)
                    end
                end)
            else
                -- Non-decor item: cancel any pending decor preview timer
                pendingGen = pendingGen + 1
                HidePreview()
            end
        end)
    dbg("TooltipDataProcessor hook registered")
else
    dbg("WARNING: TooltipDataProcessor not available!")
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------
NS.TooltipModelPreview = {
    Refresh = function()
        local settings = NS.db and NS.db.settings
        if settings and settings.showTooltipModel == false then
            HidePreview()
        end
    end,
}
