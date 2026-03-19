-------------------------------------------------------------------------------
-- HearthAndSeek: TooltipModelPreview.lua
-- Shows a slowly rotating 3D model preview beneath the tooltip when
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

-------------------------------------------------------------------------------
-- Reverse lookup: itemID → catalog item (built lazily)
-------------------------------------------------------------------------------
local itemIDToDecor = nil

local function EnsureItemLookup()
    if itemIDToDecor then return end
    itemIDToDecor = {}
    local items = NS.CatalogData and NS.CatalogData.Items
    if not items then return end
    for _, item in pairs(items) do
        if item.itemID and item.itemID > 0 and item.asset and item.asset > 0 then
            itemIDToDecor[item.itemID] = item
        end
    end
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
    return f
end

-------------------------------------------------------------------------------
-- Show / hide
-------------------------------------------------------------------------------
local currentItemID = nil  -- tracks which item is currently previewed

local function ShowPreview(item, tooltip)
    if not item or not item.asset or item.asset <= 0 then return end

    -- Already showing this item — don't reset the scene (avoids stutter)
    if currentItemID == item.itemID and previewFrame and previewFrame:IsShown() then
        return
    end

    local f = GetPreviewFrame()
    currentItemID = item.itemID
    f:SetSize(PREVIEW_SIZE, PREVIEW_SIZE)

    -- Position below the tooltip; if not enough screen space below,
    -- anchor to the side instead
    f:ClearAllPoints()
    local tipBottom = tooltip:GetBottom() or 0
    if tipBottom - PREVIEW_SIZE - 4 < 0 then
        -- Not enough room below — anchor above the tooltip
        f:SetPoint("BOTTOMLEFT", tooltip, "TOPLEFT", 0, 2)
    else
        f:SetPoint("TOPLEFT", tooltip, "BOTTOMLEFT", 0, -2)
    end

    -- Load the model (match CatalogDetail pcall pattern)
    local sceneID = item.uiModelSceneID or DEFAULT_SCENE_ID
    local ok = pcall(function()
        f._modelScene:TransitionToModelSceneID(
            sceneID,
            CAMERA_TRANSITION_TYPE_IMMEDIATE,
            CAMERA_MODIFICATION_TYPE_DISCARD,
            true
        )
    end)

    if ok then
        local actor = f._modelScene:GetActorByTag("decor")
        if actor then
            actor:SetPreferModelCollisionBounds(true)
            actor:SetModelByFileID(item.asset)
        end
        f:Show()
    end
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
            if not itemID then return end

            local item = itemIDToDecor[itemID]
            if item then
                ShowPreview(item, tooltip)
            end
        end)
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
