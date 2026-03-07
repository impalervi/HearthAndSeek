-------------------------------------------------------------------------------
-- HearthAndSeek: WhatsNew.lua
-- Version-aware "What's New" callout system using Blizzard's HelpTip API.
-- Shows feature callouts anchored to UI elements on first catalog open
-- after install or update.
-------------------------------------------------------------------------------
local addonName, NS = ...
NS.UI = NS.UI or {}

-------------------------------------------------------------------------------
-- WHAT'S NEW ENTRIES
--
-- Add a new version block at the TOP of the table (newest first).
-- Each entry has:
--   text        = "Title\n\nDescription"      -- shown in the HelpTip bubble
--   anchorTo    = "searchBox"                  -- key registered via RegisterWhatsNewAnchor
--   targetPoint = "BottomEdgeCenter"            -- key from HelpTip.Point.*
--   alignment   = "Center"                     -- key from HelpTip.Alignment.*
--   offsetX/Y   = 0                            -- optional fine-tuning
--
-- Available anchors (registered in CatalogFrame.lua):
--   searchBox, sidebar, settingsBtn
--   Add more via NS.UI.RegisterWhatsNewAnchor("key", frame)
--   NOTE: Avoid anchoring to frames inside a ScrollFrame — HelpTip parents
--   its callout to the anchor, so ScrollFrame clipping will hide it.
--   NOTE: Consecutive entries on the same anchor are supported — the
--   transitioning flag prevents onHideCallback from ending the sequence.
--
-- Available targetPoints:
--   TopEdgeLeft, TopEdgeCenter, TopEdgeRight,
--   BottomEdgeLeft, BottomEdgeCenter, BottomEdgeRight,
--   RightEdgeTop, RightEdgeCenter, RightEdgeBottom,
--   LeftEdgeTop, LeftEdgeCenter, LeftEdgeBottom
--
-- Available alignments: Left, Center, Right (horizontal) / Top, Center, Bottom (vertical)
--
-- Buttons: "Next" on non-last entries, "Got It" on the last entry.
-- Close (X) button skips all remaining entries.
--
-- Testing: set NS.DEV_MODE = true in Core/Constants.lua → callouts show
-- every /reload, ignoring seen-version tracking.
-------------------------------------------------------------------------------
local WHATS_NEW = {
    -- Newest version first. Each block = one addon release.
    {
        version = "1.3.0",
        entries = {
            {
                text = "|cffffd200What's New|r\n\n|cffffd200Aesthetic & Culture Filters|r\nBrowse decorations by theme — Sacred, Tavern, Macabre, Elven, and more. Find decor that fits the home you're building.",
                anchorTo = "sidebar",
                targetPoint = "RightEdgeCenter",
                alignment = "Center",
            },
            {
                text = "|cffffd200What's New|r\n\n|cffffd200Customizable Filter Layout|r\nCollapse and reorder filter sections with the arrow buttons. Your layout is saved across sessions. Reset anytime in Settings.",
                anchorTo = "sidebar",
                targetPoint = "RightEdgeTop",
                alignment = "Center",
            },
        },
    },
    {
        version = "1.2.0",
        entries = {
            {
                text = "|cffffd200What's New|r\n\n|cffffd200Category Filters|r\nFilter decorations by category and subcategory, matching the in-game Housing Catalog.",
                anchorTo = "sidebar",
                targetPoint = "RightEdgeCenter",
                alignment = "Center",
            },
            {
                text = "|cffffd200What's New|r\n\n|cffffd200Enhanced Search|r\nSearch now matches vendor names, zone names, source types, and keywords — not just item names.",
                anchorTo = "searchBox",
                targetPoint = "BottomEdgeCenter",
                alignment = "Center",
            },
            {
                text = "|cffffd200What's New|r\n\n|cffffd200Resizable Icons|r\nAdjust grid icon size with the new slider in Settings (gear icon).",
                anchorTo = "settingsBtn",
                targetPoint = "BottomEdgeCenter",
                alignment = "Right",
            },
        },
    },
}

-------------------------------------------------------------------------------
-- Anchor registry: string key → frame reference
-------------------------------------------------------------------------------
local ANCHOR_TARGETS = {}

function NS.UI.RegisterWhatsNewAnchor(key, frame)
    ANCHOR_TARGETS[key] = frame
end

-------------------------------------------------------------------------------
-- Version comparison utilities
-------------------------------------------------------------------------------

--- Parse "1.2.3" into {1, 2, 3}. Returns nil on invalid input.
local function ParseVersion(str)
    if not str or str == "" then return nil end
    local major, minor, patch = str:match("^(%d+)%.(%d+)%.(%d+)$")
    if not major then return nil end
    return { tonumber(major), tonumber(minor), tonumber(patch) }
end

--- Compare two version strings. Returns -1 (a < b), 0 (a == b), or 1 (a > b).
local function CompareVersions(a, b)
    local va = ParseVersion(a)
    local vb = ParseVersion(b)
    if not va or not vb then return 0 end
    for i = 1, 3 do
        if va[i] < vb[i] then return -1 end
        if va[i] > vb[i] then return  1 end
    end
    return 0
end

-------------------------------------------------------------------------------
-- State
-------------------------------------------------------------------------------
local currentEntries = nil
local currentIndex = 0
local transitioning = false  -- guards onHideCallback during same-anchor transitions

-------------------------------------------------------------------------------
-- Determine which entries to show (or nil if none)
-------------------------------------------------------------------------------
local function GetPendingEntries()
    local db = NS.db and NS.db.whatsNew
    if not db then return nil end

    local currentVersion = NS.ADDON_VERSION

    -- DEV_MODE: always show current version entries for testing
    if NS.DEV_MODE then
        for _, block in ipairs(WHATS_NEW) do
            if block.version == currentVersion then
                return block.entries
            end
        end
        return nil
    end

    -- Settings: user disabled callouts
    if NS.db.settings and NS.db.settings.showWhatsNew == false then
        return nil
    end

    local lastSeen = db.lastSeenVersion

    -- First install (nil): show current version entries
    if not lastSeen then
        for _, block in ipairs(WHATS_NEW) do
            if block.version == currentVersion then
                return block.entries
            end
        end
        return nil
    end

    -- Already seen current version or newer
    if CompareVersions(lastSeen, currentVersion) >= 0 then
        return nil
    end

    -- Update: show current version entries
    for _, block in ipairs(WHATS_NEW) do
        if block.version == currentVersion then
            return block.entries
        end
    end
    return nil
end

-------------------------------------------------------------------------------
-- Display logic
-------------------------------------------------------------------------------

local function FinishWhatsNew()
    if NS.db and NS.db.whatsNew then
        NS.db.whatsNew.lastSeenVersion = NS.ADDON_VERSION
    end
    currentEntries = nil
    currentIndex = 0
end

--- Find the active HelpTip frame parented to a specific anchor.
local function FindHelpTipFrame(anchor)
    if not HelpTip.framePool then return nil end
    for frame in HelpTip.framePool:EnumerateActive() do
        if frame:GetParent() == anchor then
            return frame
        end
    end
    return nil
end

--- Ensure the HelpTip frame and its built-in buttons render above the catalog.
--- HelpTip parents its callout to the anchor frame, inheriting its strata.
--- The OkayButton and CloseButton are defined in HelpTipTemplate but may not
--- render visibly if the strata is too low relative to the catalog UI.
local function EnsureHelpTipVisible(anchor)
    local frame = FindHelpTipFrame(anchor)
    if not frame then return end

    frame:SetFrameStrata("FULLSCREEN_DIALOG")
    frame:SetFrameLevel(500)

    -- Raise and force-show the built-in action button (Next / Got It)
    if frame.OkayButton then
        frame.OkayButton:SetFrameStrata("FULLSCREEN_DIALOG")
        frame.OkayButton:SetFrameLevel(501)
        if not frame.OkayButton:IsShown() then
            frame.OkayButton:Show()
        end
    end

    -- Raise and force-show the built-in close button (X)
    if frame.CloseButton then
        frame.CloseButton:SetFrameStrata("FULLSCREEN_DIALOG")
        frame.CloseButton:SetFrameLevel(502)
        if not frame.CloseButton:IsShown() then
            frame.CloseButton:Show()
        end
        -- Override OnClick: Blizzard's default calls Acknowledge (which advances
        -- to next entry). We want X to skip the entire What's New flow instead.
        frame.CloseButton:SetScript("OnClick", function()
            transitioning = true   -- suppress onHideCallback during X dismiss
            FinishWhatsNew()
            HelpTip:HideAll(anchor)
            transitioning = false
        end)
    end
end

--- Show the next available entry (skipping missing anchors), or finish.
local function ShowNextEntry()
    if not currentEntries then return end

    -- Loop to skip entries with missing anchors
    while currentIndex < #currentEntries do
        currentIndex = currentIndex + 1
        local entry = currentEntries[currentIndex]
        local anchor = entry and ANCHOR_TARGETS[entry.anchorTo]

        if anchor then
            local isLast = (currentIndex == #currentEntries)

            -- Inject page counter after title: "What's New (1/3)"
            local counter = " (" .. currentIndex .. "/" .. #currentEntries .. ")"
            local displayText = entry.text:gsub("|cffffd200What's New|r",
                "|cffffd200What's New|r" .. counter, 1)

            local point = HelpTip.Point[entry.targetPoint] or HelpTip.Point.BottomEdgeCenter
            local align = HelpTip.Alignment[entry.alignment] or HelpTip.Alignment.Center

            -- Set transitioning flag before Show — if the next entry uses the
            -- same anchor, HelpTip:Show hides the current tip first, firing
            -- onHideCallback with acknowledged=false. The flag prevents that
            -- from prematurely ending the whole sequence.
            transitioning = true
            HelpTip:Show(anchor, {
                text = displayText,
                buttonStyle = isLast and HelpTip.ButtonStyle.GotIt
                    or HelpTip.ButtonStyle.Next,
                targetPoint = point,
                alignment = align,
                offsetX = entry.offsetX or 0,
                offsetY = entry.offsetY or 0,
                useParentStrata = false,
                onAcknowledgeCallback = function()
                    ShowNextEntry()
                end,
                onHideCallback = function(acknowledged)
                    if not acknowledged and not transitioning then
                        FinishWhatsNew()
                    end
                end,
            })
            transitioning = false

            EnsureHelpTipVisible(anchor)

            return  -- callout displayed, wait for user action
        end
        -- else: anchor missing, continue loop to try next entry
    end

    -- No more entries (or all remaining had missing anchors)
    FinishWhatsNew()
end

-------------------------------------------------------------------------------
-- Public API
-------------------------------------------------------------------------------

--- Auto-trigger: called once on first catalog show after login.
function NS.UI.TryShowWhatsNew()
    local entries = GetPendingEntries()
    if not entries or #entries == 0 then
        -- First install with no entries for this version: still mark as seen
        if NS.db and NS.db.whatsNew and not NS.db.whatsNew.lastSeenVersion then
            NS.db.whatsNew.lastSeenVersion = NS.ADDON_VERSION
        end
        return
    end
    currentEntries = entries
    currentIndex = 0
    ShowNextEntry()
end

--- Dismiss active callouts (called when catalog frame hides mid-sequence).
function NS.UI.DismissWhatsNew()
    if currentEntries then
        transitioning = true   -- suppress onHideCallback during teardown
        FinishWhatsNew()
        for _, anchor in pairs(ANCHOR_TARGETS) do
            HelpTip:HideAll(anchor)
        end
        transitioning = false
    end
end
