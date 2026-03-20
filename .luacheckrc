-- Luacheck config for HearthAndSeek WoW addon
-- Run: bash scripts/lint.sh

std = "lua51"
max_line_length = false  -- WoW addon code often has long lines

-- Suppress common WoW addon patterns
ignore = {
    "211/addonName",  -- unused variable 'addonName' (standard WoW boilerplate)
    "212/self",       -- unused argument 'self' (WoW callback signatures)
}

-- Exclude generated/vendored files
exclude_files = {
    "Data/",
    "Libs/",
}

-- WoW API read-only globals
read_globals = {
    -- C_* namespace APIs
    "C_AddOns",
    "C_EncounterJournal",
    "C_HousingCatalog",
    "C_Item",
    "C_Map",
    "C_QuestLog",
    "C_SuperTrack",
    "C_Timer",
    "UiMapPoint",

    -- Encounter Journal
    "EJ_GetCurrentTier",
    "EJ_GetEncounterInfoByIndex",
    "EJ_GetInstanceByIndex",
    "EJ_GetInstanceInfo",
    "EJ_GetNumTiers",
    "EJ_SelectInstance",
    "EJ_SelectTier",

    -- Global frames/objects
    "AchievementFrame",
    "GameFontHighlightSmall",
    "GameTooltip",
    "MerchantFrame",
    "ProfessionsCustomerOrdersFrame",
    "UIParent",

    -- Global functions
    "_G",
    "ChatEdit_GetActiveWindow",
    "ChatEdit_InsertLink",
    "CopyTable",
    "CreateFrame",
    "DressUpItemLink",
    "GetAchievementInfo",
    "GetCursorPosition",
    "GetItemIcon",
    "GetItemInfo",
    "GetMerchantItemID",
    "GetMerchantNumItems",
    "GetRealmName",
    "GetTime",
    "hooksecurefunc",
    "InCombatLockdown",
    "IsAltKeyDown",
    "IsControlKeyDown",
    "IsShiftKeyDown",
    "OpenAchievementFrameToAchievement",
    "ResetCursor",
    "SearchBoxTemplate_OnTextChanged",
    "SetCursor",
    "ShowUIPanel",
    "ToggleAchievementFrame",
    "UnitFactionGroup",
    "UnitName",

    -- Blizzard Lua extensions
    "strtrim",
    "tinsert",
    "wipe",

    -- Enum namespace
    "Enum",

    -- HelpTip system
    "HelpTip",

    -- Tooltip system
    "TooltipDataProcessor",

    -- Blizzard mixins
    "ProfessionsCustomerTableCellItemNameMixin",

    -- Blizzard frame update functions
    "MerchantFrame_Update",

    -- WoW constants
    "CAMERA_MODIFICATION_TYPE_DISCARD",
    "CAMERA_TRANSITION_TYPE_IMMEDIATE",
    "HOUSING_DECOR_PLACEMENT_COST_FORMAT",
    "MERCHANT_ITEMS_PER_PAGE",

    -- Libraries
    "LibStub",
}

-- Globals the addon defines (read-write)
globals = {
    "HearthAndSeekDB",
    "SLASH_HEARTHANDSEEK1",
    "SLASH_HEARTHANDSEEK2",
    "SLASH_HEARTHANDSEEK3",
    "SlashCmdList",
    "UISpecialFrames",
    "HearthAndSeek_CatalogItem_OnLoad",
}
