-------------------------------------------------------------------------------
-- HearthAndSeek: Init.lua
-- Addon bootstrap: namespace initialization, SavedVariables setup,
-- event registration, and slash commands.
-------------------------------------------------------------------------------
local addonName, NS = ...

-------------------------------------------------------------------------------
-- Namespace sub-tables (populated by other files loaded before/after Init)
-------------------------------------------------------------------------------
NS.Data           = NS.Data or {}
NS.Navigation     = NS.Navigation or {}
NS.CatalogDumper  = NS.CatalogDumper or {}
NS.UI             = NS.UI or {}

-------------------------------------------------------------------------------
-- SavedVariables defaults
-------------------------------------------------------------------------------
local DEFAULTS = {
    minimapIcon = {
        hide = false,
    },
    catalogPosition = nil,  -- Saved as { point, relativeTo, relativePoint, x, y }
    settings = {},
    favorites = {},         -- { [decorID] = true } — account-wide favorite decor items
}

local CHAR_DEFAULTS = {
}

-------------------------------------------------------------------------------
-- Build a character-specific key: "Name-Realm"
-------------------------------------------------------------------------------
local function GetCharacterKey()
    local name = UnitName("player") or "Unknown"
    local realm = GetRealmName() or "UnknownRealm"
    return name .. "-" .. realm
end

-------------------------------------------------------------------------------
-- Initialize or migrate SavedVariables
-------------------------------------------------------------------------------
local function InitSavedVars()
    if not HearthAndSeekDB then
        HearthAndSeekDB = {}
    end

    -- Apply top-level defaults
    for k, v in pairs(DEFAULTS) do
        if HearthAndSeekDB[k] == nil then
            if type(v) == "table" then
                HearthAndSeekDB[k] = CopyTable(v)
            else
                HearthAndSeekDB[k] = v
            end
        end
    end

    -- Per-character data
    HearthAndSeekDB.characters = HearthAndSeekDB.characters or {}
    local charKey = GetCharacterKey()
    if not HearthAndSeekDB.characters[charKey] then
        HearthAndSeekDB.characters[charKey] = CopyTable(CHAR_DEFAULTS)
    end

    -- Dump storage (dev-only, persists across sessions until overwritten)
    if NS.DEV_MODE then
        HearthAndSeekDB.catalogDump = HearthAndSeekDB.catalogDump or {}
        HearthAndSeekDB.bossDump = HearthAndSeekDB.bossDump or {}
    end

    NS.db = HearthAndSeekDB
    NS.favorites = HearthAndSeekDB.favorites
    NS.charKey = charKey
    NS.charDB = HearthAndSeekDB.characters[charKey]
end

-------------------------------------------------------------------------------
-- Event frame: bootstrap the addon on ADDON_LOADED
-------------------------------------------------------------------------------
local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("ADDON_LOADED")

eventFrame:SetScript("OnEvent", function(self, event, ...)
    if event == "ADDON_LOADED" then
        local loadedAddon = ...
        if loadedAddon == addonName then
            InitSavedVars()

            -- Initialize minimap button (after libs and UI are loaded)
            if NS.UI.InitMinimapButton then
                NS.UI.InitMinimapButton()
            end

            -- Initialize the Catalog browser UI
            if NS.UI.InitCatalog then
                NS.UI.InitCatalog()
            end

            NS.Utils.PrintMessage("v" .. NS.ADDON_VERSION .. " loaded. Type /hs for options.")

            self:UnregisterEvent("ADDON_LOADED")
        end
    end
end)

-------------------------------------------------------------------------------
-- Slash Commands
-------------------------------------------------------------------------------
SLASH_HEARTHANDSEEK1 = "/hs"
SLASH_HEARTHANDSEEK2 = "/hseek"
SLASH_HEARTHANDSEEK3 = "/hearthandseek"

SlashCmdList["HEARTHANDSEEK"] = function(msg)
    msg = strtrim(msg or "")
    local cmd, rest = msg:match("^(%S+)%s*(.*)")
    cmd = cmd and cmd:lower() or ""

    if cmd == "" or cmd == "catalog" or cmd == "cat" or cmd == "browse" then
        -- Toggle the Catalog browser
        if NS.UI.ToggleCatalog then
            NS.UI.ToggleCatalog()
        else
            NS.Utils.PrintMessage("Catalog not yet initialized.")
        end

    elseif cmd == "clear" then
        local subCmd = rest:match("^(%S+)")
        subCmd = subCmd and subCmd:lower() or ""

        if subCmd == "favorites" then
            wipe(NS.favorites)
            NS.Utils.PrintMessage("All favorites cleared.")
            if NS.UI.UpdateSidebarCounts then
                NS.UI.UpdateSidebarCounts()
            end
        else
            NS.Utils.PrintMessage("Clear commands: favorites")
        end

    elseif cmd == "dump" then
        if not NS.DEV_MODE then
            NS.Utils.PrintMessage("Unknown command. Type /hs help for options.")
            return
        end
        local subCmd = rest:match("^(%S+)")
        subCmd = subCmd and subCmd:lower() or ""

        if subCmd == "catalog" or subCmd == "" then
            if NS.CatalogDumper.DumpCatalog then
                NS.CatalogDumper.DumpCatalog()
            else
                NS.Utils.PrintMessage("CatalogDumper module not loaded.")
            end
        elseif subCmd == "bosses" then
            if NS.CatalogDumper.DumpBossFloorMaps then
                NS.CatalogDumper.DumpBossFloorMaps()
            else
                NS.Utils.PrintMessage("CatalogDumper module not loaded.")
            end
        elseif subCmd == "zones" then
            local ztc = NS.CatalogData and NS.CatalogData.ZoneToContinentMap
            if not ztc then
                NS.Utils.PrintMessage("Error: ZoneToContinentMap not loaded.")
                return
            end
            local allZones = {}
            for z in pairs(ztc) do
                allZones[z] = true
            end
            local de = NS.CatalogData and NS.CatalogData.DungeonEntrances
            if de then
                for _, ent in pairs(de) do
                    if ent.zone and ent.zone ~= "" then
                        allZones[ent.zone] = true
                    end
                end
            end
            local results = {}
            local resolved, unresolved = 0, 0
            for z in pairs(allZones) do
                local mapID = NS.UI.GetZoneMapID and NS.UI.GetZoneMapID(z)
                if mapID then
                    results[z] = mapID
                    resolved = resolved + 1
                else
                    results[z] = 0
                    unresolved = unresolved + 1
                end
            end
            HearthAndSeekDB.zoneDump = results
            NS.Utils.PrintMessage(string.format(
                "Zone dump: %d resolved, %d unresolved. Saved to HearthAndSeekDB.zoneDump.",
                resolved, unresolved))
            NS.Utils.PrintMessage("Run /reload to persist, then use parse scripts to extract.")
        else
            NS.Utils.PrintMessage("Dump commands: catalog, bosses, zones")
        end

    elseif cmd == "debug" then
        if not NS.DEV_MODE then
            NS.Utils.PrintMessage("Unknown command. Type /hs help for options.")
            return
        end
        local subCmd, arg = rest:match("^(%S+)%s*(.*)")
        subCmd = subCmd and subCmd:lower() or ""

        if subCmd == "faction" then
            local faction = strtrim(arg):lower()
            if faction == "alliance" then
                NS.DebugFaction = "Alliance"
                NS.Utils.PrintMessage("Debug: faction override set to Alliance")
            elseif faction == "horde" then
                NS.DebugFaction = "Horde"
                NS.Utils.PrintMessage("Debug: faction override set to Horde")
            elseif faction == "default" or faction == "" then
                NS.DebugFaction = nil
                NS.Utils.PrintMessage("Debug: faction override cleared (using real faction)")
            else
                NS.Utils.PrintMessage("Usage: /hs debug faction alliance|horde|default")
            end
            -- Refresh current detail panel to reflect faction change
            if NS.UI and NS.UI.CatalogDetail_ShowItem
                and NS.UI._currentDetailItem then
                NS.UI.CatalogDetail_ShowItem(NS.UI._currentDetailItem)
            end
        else
            NS.Utils.PrintMessage("Debug commands: faction <alliance|horde|default>")
        end

    elseif cmd == "help" then
        NS.Utils.PrintMessage("Commands:")
        NS.Utils.PrintMessage("  /hs - Toggle catalog browser")
        NS.Utils.PrintMessage("  /hs clear favorites - Clear all favorited items")
        if NS.DEV_MODE then
            NS.Utils.PrintMessage("  /hs dump catalog - Dump catalog to SavedVariables")
            NS.Utils.PrintMessage("  /hs dump bosses - Dump boss floor maps")
            NS.Utils.PrintMessage("  /hs dump zones - Dump zone mapID mappings")
            NS.Utils.PrintMessage("  /hs debug faction <f> - Override faction")
        end

    else
        NS.Utils.PrintMessage("Unknown command. Type /hs help for options.")
    end
end
