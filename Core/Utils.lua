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
