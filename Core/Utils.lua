-------------------------------------------------------------------------------
-- HearthAndSeek: Utils.lua
-- Shared utility functions used across the addon.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.Utils = {}

-------------------------------------------------------------------------------
-- FormatCoords: Formats map coordinates as "XX.X, YY.Y"
-- @param x number (0-1 or 0-100 scale)
-- @param y number (0-1 or 0-100 scale)
-- @return string formatted coordinate string
-------------------------------------------------------------------------------
function NS.Utils.FormatCoords(x, y)
    if not x or not y then return "??, ??" end
    -- If coords are in 0-1 scale, convert to 0-100
    if x <= 1 and y <= 1 then
        x = x * 100
        y = y * 100
    end
    return string.format("%.1f, %.1f", x, y)
end

-------------------------------------------------------------------------------
-- GetPlayerMapPosition: Returns the player's current map position.
-- @return mapID, x, y  or nil if unavailable
-------------------------------------------------------------------------------
function NS.Utils.GetPlayerMapPosition()
    local mapID = C_Map.GetBestMapForUnit("player")
    if not mapID then return nil end

    local pos = C_Map.GetPlayerMapPosition(mapID, "player")
    if not pos then return mapID, nil, nil end

    local x, y = pos:GetXY()
    return mapID, x, y
end

-------------------------------------------------------------------------------
-- PrintMessage: Prints a prefixed chat message.
-- @param msg string  The message to display
-------------------------------------------------------------------------------
function NS.Utils.PrintMessage(msg)
    print(NS.ADDON_PREFIX .. tostring(msg))
end

-------------------------------------------------------------------------------
-- HexToRGB: Converts a hex color string to RGBA values (0-1).
-- @param hex string  e.g. "FF8800" or "#FF8800"
-- @return r, g, b, a
-------------------------------------------------------------------------------
function NS.Utils.HexToRGB(hex)
    hex = hex:gsub("#", "")
    local r = tonumber(hex:sub(1, 2), 16) / 255
    local g = tonumber(hex:sub(3, 4), 16) / 255
    local b = tonumber(hex:sub(5, 6), 16) / 255
    return r, g, b, 1.0
end

-------------------------------------------------------------------------------
-- DistanceBetween: Calculates 2D distance between two coordinate pairs.
-- Coordinates should be in the same scale (both 0-100 or both 0-1).
-- @param x1, y1, x2, y2 number
-- @return number distance
-------------------------------------------------------------------------------
function NS.Utils.DistanceBetween(x1, y1, x2, y2)
    if not (x1 and y1 and x2 and y2) then return math.huge end
    local dx = x2 - x1
    local dy = y2 - y1
    return math.sqrt(dx * dx + dy * dy)
end

-------------------------------------------------------------------------------
-- TableCount: Returns the number of entries in a table (works for non-array tables).
-- @param t table
-- @return number
-------------------------------------------------------------------------------
function NS.Utils.TableCount(t)
    local count = 0
    for _ in pairs(t) do
        count = count + 1
    end
    return count
end

-------------------------------------------------------------------------------
-- Source ordering and labels for multi-source items.
--
-- Some decor items can be acquired through multiple paths — typically an
-- Achievement + a Vendor (either "reward from achievement, redeemable at
-- vendor" or "sold by vendor, requires achievement to unlock"). Both
-- flavours must render consistently, and the chosen convention (based on
-- 145 vs 39 catalog prevalence on 2026-04-22) is:
--
--   1. Sort all detected sources by SOURCE_PRIORITY (gating steps first).
--   2. Render each label in its own SourceColors palette entry — so
--      "+ Achievement" is the same yellow as a primary Achievement
--      badge, and "+ Vendor" the same blue as a primary Vendor badge.
--
-- This replaces ad-hoc if/elseif chains that inconsistently ordered and
-- colored the "+ Secondary" label across different item cases.
-------------------------------------------------------------------------------

-- Gating-first ordering: whichever source is more prerequisite-shaped
-- shows first. Mirror of Tools/scraper/output_catalog_lua.SOURCE_PRIORITY.
NS.SourcePriority = {
    "Quest", "Achievement", "Prey", "Profession",
    "Drop", "Treasure", "Vendor", "Shop", "Other",
}

--- Collect all source types present on an item, in canonical order.
---
--- Scans the item's primary ``sourceType`` plus the well-known secondary
--- fields (``vendorUnlockAchievement``, ``vendorName``, ``treasureX/Y``
--- etc.), de-duplicates, and returns them sorted by ``NS.SourcePriority``.
--- Each entry records whether it was the primary source so callers can
--- still distinguish (e.g. for acquisition text below the badge).
---
--- @param item table  A CatalogData item entry.
--- @return table[]    Array of ``{ type, isPrimary }`` entries.
function NS.Utils.GetItemSources(item)
    if not item then return {} end
    local present = {}
    local primaryType = item.sourceType
    if primaryType and primaryType ~= "" then
        present[primaryType] = { type = primaryType, isPrimary = true }
    end

    -- Achievement secondary: when the item has a vendorUnlockAchievement
    -- (Case B: vendor gated by achievement), or when the primary source
    -- isn't Achievement but the item has an achievementName.
    if primaryType ~= "Achievement" then
        if (item.vendorUnlockAchievement and item.vendorUnlockAchievement ~= "")
                or (item.achievementName and item.achievementName ~= "") then
            present["Achievement"] = present["Achievement"]
                or { type = "Achievement", isPrimary = false }
        end
    end

    -- Vendor secondary: when the primary isn't Vendor but the item has a
    -- vendorName (Case A: achievement reward redeemable at vendor).
    if primaryType ~= "Vendor"
            and item.vendorName and item.vendorName ~= "" then
        present["Vendor"] = present["Vendor"]
            or { type = "Vendor", isPrimary = false }
    end

    -- Treasure secondary: when the primary isn't Treasure but the item
    -- has treasure coordinates (picked up from a world object).
    if primaryType ~= "Treasure"
            and item.treasureX and item.treasureY then
        present["Treasure"] = present["Treasure"]
            or { type = "Treasure", isPrimary = false }
    end

    -- Structured additionalSources array (populated by the generator from
    -- Wowhead enrichment + source-merging). Each entry is
    -- ``{ sourceType = "...", sourceDetail = "..." }``. These are the
    -- secondary-source signals that don't map to a single scalar field —
    -- e.g. Vendor-primary items that are also rewards from a Quest
    -- (Decor Treasure Hunt), or multi-vendor items. Add any source type
    -- not already present so the badge reflects everything.
    if type(item.additionalSources) == "table" then
        for _, alt in ipairs(item.additionalSources) do
            local alt_t = alt and alt.sourceType
            if alt_t and alt_t ~= "" and not present[alt_t] then
                present[alt_t] = { type = alt_t, isPrimary = false }
            end
        end
    end

    -- Order by canonical priority
    local ordered = {}
    for _, t in ipairs(NS.SourcePriority) do
        if present[t] then ordered[#ordered + 1] = present[t] end
    end
    return ordered
end

--- Serialize a sources list (from GetItemSources) into an addon-friendly
--- colored-text string, e.g. "|cffe6cc33Achievement|r + |cff66b3ffVendor|r".
--- Colors come from ``NS.SourceColors`` so secondary labels match their
--- primary equivalents.
---
--- @param sources  table[] output of GetItemSources
--- @return string  colored concatenation, or "Unknown" if empty.
function NS.Utils.FormatSourcesText(sources)
    if not sources or #sources == 0 then return "Unknown" end
    local parts = {}
    for i, src in ipairs(sources) do
        local c = NS.SourceColors and NS.SourceColors[src.type]
            or (NS.SourceColors and NS.SourceColors.Other)
            or { 0.6, 0.6, 0.6, 1 }
        local hex = string.format("%02x%02x%02x",
            math.floor(c[1] * 255 + 0.5),
            math.floor(c[2] * 255 + 0.5),
            math.floor(c[3] * 255 + 0.5))
        local sep = i == 1 and "" or " |cff888888+|r "
        parts[#parts + 1] = sep .. "|cff" .. hex .. src.type .. "|r"
    end
    return table.concat(parts)
end
