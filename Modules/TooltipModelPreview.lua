-------------------------------------------------------------------------------
-- HearthAndSeek: TooltipModelPreview.lua
-- Shows a slowly rotating 3D model preview beside the tooltip when
-- hovering over any decoration item found in the catalog.
--
-- Taint workaround: OrbitCameraMixin (called by TransitionToModelSceneID)
-- reads GetWidth()/GetHeight() on the ModelScene. For addon-created frames,
-- WoW's layout engine taints these values as "secret numbers" (for the
-- session) after combat, causing the camera setup to error. We override
-- GetWidth and GetHeight on the ModelScene instance to return clean Lua
-- numbers, which the Lua-side OrbitCameraMixin picks up instead of the
-- tainted C values.
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
local SCENE_INSET = 6
local SCENE_SIZE = PREVIEW_SIZE - SCENE_INSET * 2  -- 188

-------------------------------------------------------------------------------
-- Reverse lookup: itemID → catalog item (built lazily)
-------------------------------------------------------------------------------
local itemIDToDecor = nil
local currentItem = nil  -- full catalog item for the currently hovered decor

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

    -- TAINT WORKAROUND: Override GetWidth/GetHeight on the wrapper frame
    -- with plain Lua functions that return clean numbers. After combat,
    -- WoW's layout engine taints addon frame geometry as "secret numbers".
    -- BackdropTemplate's SetupTextureCoordinates calls self:GetWidth() on
    -- Show(), which crashes if tainted. Our instance-level override shadows
    -- the C-side widget method with untainted values.
    f.GetWidth  = function() return PREVIEW_SIZE end
    f.GetHeight = function() return PREVIEW_SIZE end

    -- ModelScene inside the wrapper (no mouse interaction — display only)
    local scene = CreateFrame("ModelScene", nil, f,
        "PanningModelSceneMixinTemplate")
    scene:SetPoint("TOPLEFT", SCENE_INSET, -SCENE_INSET)
    scene:SetPoint("BOTTOMRIGHT", -SCENE_INSET, SCENE_INSET)
    scene:EnableMouse(false)
    scene:EnableMouseWheel(false)

    -- Same taint workaround for the ModelScene (OrbitCameraMixin calls
    -- self:GetWidth/GetHeight through Lua).
    scene.GetWidth  = function() return SCENE_SIZE end
    scene.GetHeight = function() return SCENE_SIZE end

    f._modelScene = scene

    -- Auto-rotation on the wrapper frame (not on the ModelScene, which has
    -- its own OnUpdate from PanningModelSceneMixinTemplate that must not
    -- be replaced)
    f:SetScript("OnUpdate", function(self, elapsed)
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
local pendingGen = 0       -- generation counter for deferred timer validation

local function ShowPreview(item, tooltip)
    if not item or not item.asset or item.asset <= 0 then return end

    -- Already showing this item — don't reset the scene (avoids stutter)
    if currentItemID == item.itemID and previewFrame and previewFrame:IsShown() then
        return
    end

    local f = GetPreviewFrame()
    currentItemID = item.itemID
    currentItem = item

    -- Position beside the tooltip. The screen-bounds check reads tooltip
    -- geometry via pcall in case values are tainted from a previous session.
    f:ClearAllPoints()

    local anchorLeft = false
    pcall(function()
        local tipRight = tooltip:GetRight() or 0
        local screenW = UIParent:GetRight() or UIParent:GetWidth()
        if tipRight + PREVIEW_SIZE + 4 > screenW then
            anchorLeft = true
        end
    end)

    if anchorLeft then
        f:SetPoint("TOPRIGHT", tooltip, "TOPLEFT", -2, 0)
    else
        f:SetPoint("TOPLEFT", tooltip, "TOPRIGHT", 2, 0)
    end

    -- Clear stale scene state before transitioning; after loading screens the
    -- engine can invalidate C-side actors while the Lua-side tagToActor table
    -- still holds references to them, causing silent failures.
    f._modelScene:ClearScene()

    local sceneID = item.uiModelSceneID or DEFAULT_SCENE_ID
    local ok = pcall(function()
        f._modelScene:TransitionToModelSceneID(
            sceneID,
            CAMERA_TRANSITION_TYPE_IMMEDIATE,
            CAMERA_MODIFICATION_TYPE_DISCARD,
            true
        )
    end)

    if not ok then return end

    local actor = f._modelScene:GetActorByTag("decor")
    if not actor then return end

    actor:SetPreferModelCollisionBounds(true)
    actor:SetModelByFileID(item.asset)
    f:Show()
end

local function HidePreview()
    currentItemID = nil
    currentItem = nil
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
                -- Add hint line to the tooltip
                tooltip:AddLine("|cff55aaeeALT+Left Click|r for full screen preview", 0.5, 0.5, 0.5)

                -- Bump generation so any older pending timer is invalidated
                pendingGen = pendingGen + 1
                local myGen = pendingGen
                currentItemID = item.itemID

                -- Defer to next frame to escape the secure callback context;
                -- TransitionToModelSceneID and Show() can fail silently from
                -- taint when called inside securecallfunction
                C_Timer.After(0, function()
                    if myGen ~= pendingGen then return end
                    if GameTooltip:IsShown() then
                        ShowPreview(item, GameTooltip)
                    end
                end)
            else
                pendingGen = pendingGen + 1
                HidePreview()
            end
        end)
end

-------------------------------------------------------------------------------
-- Alt+Left Click: open big viewer for the currently hovered decor item
-------------------------------------------------------------------------------
local clickListener = CreateFrame("Frame")
clickListener:RegisterEvent("GLOBAL_MOUSE_DOWN")
clickListener:SetScript("OnEvent", function(_, _, button)
    if button ~= "LeftButton" or not IsAltKeyDown() then return end
    if not currentItem then return end
    if not GameTooltip:IsShown() then return end
    if NS.UI and NS.UI.ShowBigModelViewer then
        NS.UI.ShowBigModelViewer(currentItem)
    end
end)

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
    Hide = HidePreview,
}
