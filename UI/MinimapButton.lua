-------------------------------------------------------------------------------
-- HearthAndSeek: MinimapButton.lua
-- LibDBIcon minimap button. Left-click opens catalog, right-click opens
-- catalog filtered to the player's current zone.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.UI = NS.UI or {}

-------------------------------------------------------------------------------
-- GetPlayerZoneName: resolve the player's current zone to a catalog zone name.
-------------------------------------------------------------------------------
local function GetPlayerZoneName()
    local mapID = C_Map.GetBestMapForUnit("player")
    if not mapID then return nil end
    local info = C_Map.GetMapInfo(mapID)
    return info and info.name or nil
end

-------------------------------------------------------------------------------
-- InitMinimapButton: Registers the minimap button via LibDataBroker + LibDBIcon.
-- Called from Init.lua on ADDON_LOADED.
-------------------------------------------------------------------------------
function NS.UI.InitMinimapButton()
    local LDB = LibStub("LibDataBroker-1.1")
    local LDBIcon = LibStub("LibDBIcon-1.0")

    if not LDB or not LDBIcon then
        NS.Utils.PrintMessage("Warning: minimap button libraries not available.")
        return
    end

    -- Create the data object for the minimap button
    local dataObj = LDB:NewDataObject("HearthAndSeek", {
        type = "launcher",
        text = "Hearth & Seek",
        icon = "Interface\\AddOns\\HearthAndSeek\\Media\\Icons\\HearthAndSeek",

        OnClick = function(self, button)
            if button == "LeftButton" then
                if NS.UI.ToggleCatalog then
                    NS.UI.ToggleCatalog()
                end
            elseif button == "RightButton" then
                -- Right-click: open catalog filtered to current zone
                local zoneName = GetPlayerZoneName()
                if zoneName and NS.UI.OpenCatalogForZone then
                    NS.UI.OpenCatalogForZone(zoneName)
                elseif NS.UI.ToggleCatalog then
                    NS.UI.ToggleCatalog()
                end
            end
        end,

        OnTooltipShow = function(tooltip)
            if not tooltip then return end

            tooltip:AddLine("Hearth & Seek", 1, 0.82, 0)
            tooltip:AddLine("Decor Catalog", 0.7, 0.7, 0.7)
            tooltip:AddLine(" ")
            tooltip:AddLine("|cffffd200Left-click|r Open catalog", 0.8, 0.8, 0.8)
            tooltip:AddLine("|cffffd200Right-click|r Browse current zone", 0.8, 0.8, 0.8)
        end,
    })

    if not dataObj then
        NS.Utils.PrintMessage("Warning: could not create minimap data object.")
        return
    end

    -- Register with LibDBIcon using saved position data
    if NS.db then
        LDBIcon:Register("HearthAndSeek", dataObj, NS.db.minimapIcon)
    end
end
