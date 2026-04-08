-------------------------------------------------------------------------------
-- HearthAndSeek: Constants.lua
-- Enums, color definitions, sizing constants, and other shared values.
-------------------------------------------------------------------------------
local addonName, NS = ...

-------------------------------------------------------------------------------
-- Dev Mode: set to true to enable /hseek dump and /hseek debug commands.
-- MUST be false for releases. Only enable during data pipeline work.
-------------------------------------------------------------------------------
NS.DEV_MODE = false

-------------------------------------------------------------------------------
-- Addon metadata
-------------------------------------------------------------------------------
NS.ADDON_NAME    = "HearthAndSeek"
NS.ADDON_VERSION = "1.4.4"
NS.ADDON_PREFIX  = "|cff00ccff[Hearth & Seek]|r "

-------------------------------------------------------------------------------
-- Catalog UI Constants
-------------------------------------------------------------------------------
NS.CatalogSizing = {
    FrameWidth          = 1100,
    FrameHeight         = 750,
    SidebarWidth        = 200,
    DetailPanelWidth    = 330,
    GridItemSize        = 110,
    GridItemSpacing     = 10,
    GridColumns         = 4,
    GridRows            = 5,
    ItemsPerPage        = 20,
    SearchBoxWidth      = 260,
    ModelViewerHeight   = 240,
}

NS.QualityColors = {
    [0] = { 0.62, 0.62, 0.62, 1 },
    [1] = { 1.00, 1.00, 1.00, 1 },
    [2] = { 0.12, 1.00, 0.00, 1 },
    [3] = { 0.00, 0.44, 0.87, 1 },
    [4] = { 0.64, 0.21, 0.93, 1 },
    [5] = { 1.00, 0.50, 0.00, 1 },
}



NS.QualityNames = {
    [0] = "Poor",
    [1] = "Common",
    [2] = "Uncommon",
    [3] = "Rare",
    [4] = "Epic",
    [5] = "Legendary",
}

NS.QualityOrder = { 1, 2, 3, 4, 5, 0 }

NS.SourceIcons = {
    Vendor      = "Interface\\Icons\\INV_Misc_Bag_07",
    Quest       = "Interface\\GossipFrame\\AvailableQuestIcon",
    Achievement = "Interface\\Icons\\Achievement_Level_100",
    Prey        = "Interface\\Icons\\INV_Misc_Bone_HumanSkull_01",
    Profession  = "Interface\\Icons\\Trade_Tailoring",
    Drop        = "Interface\\Icons\\Achievement_Boss_Blackhand",
    Treasure    = "Interface\\Icons\\INV_Misc_Map02",
    Shop        = "Interface\\Icons\\WoW_Store",
    Other       = "Interface\\Icons\\INV_Misc_QuestionMark",
}

NS.SourceColors = {
    Vendor      = { 0.40, 0.70, 1.00, 1.00 },
    Quest       = { 1.00, 0.82, 0.00, 1.00 },
    Achievement = { 0.90, 0.80, 0.20, 1.00 },
    Prey        = { 0.85, 0.20, 0.20, 1.00 },
    Profession  = { 0.60, 0.40, 0.20, 1.00 },
    Drop        = { 0.80, 0.40, 0.80, 1.00 },
    Treasure    = { 0.60, 0.90, 0.60, 1.00 },
    Shop        = { 0.30, 0.80, 1.00, 1.00 },
    Other       = { 0.60, 0.60, 0.60, 1.00 },
}

-------------------------------------------------------------------------------
-- Expansion colors (hex strings for inline color codes)
-------------------------------------------------------------------------------
NS.ExpansionColors = {
    ["Classic"]                 = "CC8800",
    ["The Burning Crusade"]     = "1EFF00",
    ["Wrath of the Lich King"]  = "69CCF0",
    ["Cataclysm"]              = "FF4444",
    ["Mists of Pandaria"]      = "00FF96",
    ["Warlords of Draenor"]    = "B32D2D",
    ["Legion"]                 = "198C19",
    ["Battle for Azeroth"]     = "668FD6",
    ["Shadowlands"]            = "AA6666",
    ["Dragonflight"]           = "DDAA00",
    ["The War Within"]         = "CC6600",
    ["Midnight"]               = "9955CC",
    ["Neighborhoods"]          = "FFFFFF",
    ["Unknown"]                = "888888",
}

-- Continent name → expansion name (for cross-continent color coding)
NS.ContinentExpansion = {
    ["Eastern Kingdoms"]    = "Classic",
    ["Kalimdor"]            = "Classic",
    ["Outland"]             = "The Burning Crusade",
    ["Northrend"]           = "Wrath of the Lich King",
    ["The Maelstrom"]       = "Cataclysm",
    ["Pandaria"]            = "Mists of Pandaria",
    ["Draenor"]             = "Warlords of Draenor",
    ["Broken Isles"]        = "Legion",
    ["Argus"]               = "Legion",
    ["Zandalar"]            = "Battle for Azeroth",
    ["Kul Tiras"]           = "Battle for Azeroth",
    ["Mechagon"]            = "Battle for Azeroth",
    ["The Shadowlands"]     = "Shadowlands",
    ["Dragon Isles"]        = "Dragonflight",
    ["Khaz Algar"]          = "The War Within",
    ["Quel'Thalas"]         = "Midnight",
    ["The Voidstorm"]       = "Midnight",
    ["Harandar"]            = "Midnight",
    ["Neighborhoods"]       = "Neighborhoods",
}

-------------------------------------------------------------------------------
-- Zidormi zones: destination zone → Zidormi NPC location
-- The Zidormi NPC may be in a different zone than the destination (e.g.,
-- Quel'Thalas zones all share the Thalassian Pass Zidormi).
-- Coords are approximate and may need in-game verification.
-------------------------------------------------------------------------------
NS.ZidormiZones = {
    -- BfA war-changed zones
    ["Darkshore"]               = { npcID = 141489, x = 48.4, y = 25.0, npcZone = "Darkshore" },
    ["Tirisfal Glades"]         = { npcID = 141488, x = 69.4, y = 62.8, npcZone = "Tirisfal Glades" },
    ["Arathi Highlands"]        = { npcID = 141649, x = 38.2, y = 90.0, npcZone = "Arathi Highlands" },
    -- Cataclysm / pre-event zones
    ["Blasted Lands"]           = { npcID = 88206,  x = 48.2, y = 7.2,  npcZone = "Blasted Lands" },
    ["Silithus"]                = { npcID = 128607, x = 78.8, y = 22.0, npcZone = "Silithus" },
    -- N'Zoth assault zones
    ["Uldum"]                   = { npcID = 162419, x = 56.0, y = 35.2, npcZone = "Uldum" },
    ["Vale of Eternal Blossoms"]= { npcID = 163463, x = 81.0, y = 29.6, npcZone = "Vale of Eternal Blossoms" },
    -- Older revamps
    ["Dustwallow Marsh"]        = { npcID = 63546,  x = 55.8, y = 49.6, npcZone = "Dustwallow Marsh" },
    ["Eastern Plaguelands"]     = { npcID = 0,      x = 53.8, y = 8.8,  npcZone = "Eastern Plaguelands" }, -- NPC ID unconfirmed
    -- Midnight: Quel'Thalas zones share Zidormi at Thalassian Pass
    -- NPC ID and exact coords need in-game verification
    ["Eversong Woods"]          = { npcID = 0, x = 53.8, y = 8.8, npcZone = "Eastern Plaguelands" },
    ["Ghostlands"]              = { npcID = 0, x = 53.8, y = 8.8, npcZone = "Eastern Plaguelands" },
    ["Silvermoon City"]         = { npcID = 0, x = 53.8, y = 8.8, npcZone = "Eastern Plaguelands" },
    ["Isle of Quel'Danas"]      = { npcID = 0, x = 53.8, y = 8.8, npcZone = "Eastern Plaguelands" },
}

NS.ContinentColors = {
    ["Eastern Kingdoms"]    = "CC8800",
    ["Kalimdor"]            = "CC8800",
    ["Northrend"]           = "69CCF0",
    ["Pandaria"]            = "00FF96",
    ["Draenor"]             = "B32D2D",
    ["Broken Isles"]        = "198C19",
    ["Zandalar"]            = "668FD6",
    ["Kul Tiras"]           = "668FD6",
    ["Mechagon"]            = "668FD6",
    ["The Shadowlands"]     = "AA6666",
    ["Dragon Isles"]        = "DDAA00",
    ["Khaz Algar"]          = "CC6600",
    ["Quel'Thalas"]         = "9955CC",
    ["Neighborhoods"]       = "FFFFFF",
    ["Unknown"]             = "888888",
}

NS.ProfessionIcons = {
    Alchemy         = "Interface\\Icons\\Trade_Alchemy",
    Blacksmithing   = "Interface\\Icons\\Trade_BlackSmithing",
    Cooking         = "Interface\\Icons\\INV_Misc_Food_15",
    Enchanting      = "Interface\\Icons\\Trade_Engraving",
    Engineering     = "Interface\\Icons\\Trade_Engineering",
    Inscription     = "Interface\\Icons\\INV_Inscription_Tradeskill01",
    Jewelcrafting   = "Interface\\Icons\\INV_Misc_Gem_01",
    Leatherworking  = "Interface\\Icons\\Trade_LeatherWorking",
    Tailoring       = "Interface\\Icons\\Trade_Tailoring",
}

-- Boss floor maps are generated into NS.CatalogData.BossFloorMaps
-- by the data pipeline. See Tools/scraper/README.md.
