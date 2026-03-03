-------------------------------------------------------------------------------
-- HearthAndSeek: Navigation.lua
-- Waypoint integration using C_Map.SetUserWaypoint() with supertracking.
-------------------------------------------------------------------------------
local addonName, NS = ...

NS.Navigation = NS.Navigation or {}
local Nav = NS.Navigation

-------------------------------------------------------------------------------
-- Internal state
-------------------------------------------------------------------------------
local activeWaypoint = nil  -- Tracks the current waypoint for cleanup

-------------------------------------------------------------------------------
-- SetWaypoint: Sets a navigation waypoint on the map.
--
-- @param mapID number   Blizzard uiMapID for the target zone
-- @param x     number   X coordinate (0-100 scale)
-- @param y     number   Y coordinate (0-100 scale)
-- @param title string   Waypoint label/tooltip text
-------------------------------------------------------------------------------
function Nav.SetWaypoint(mapID, x, y, title)
    if not mapID or not x or not y then
        NS.Utils.PrintMessage("Cannot set waypoint: missing coordinates.")
        return
    end

    -- Normalize coords to 0-1 scale if they're in 0-100
    local nx = x > 1 and x / 100 or x
    local ny = y > 1 and y / 100 or y

    -- Clear any existing waypoint first
    Nav.ClearWaypoint()

    title = title or "Hearth & Seek"

    -- Set the Blizzard native map pin + supertrack arrow
    local point = UiMapPoint.CreateFromCoordinates(mapID, nx, ny)
    if point then
        C_Map.SetUserWaypoint(point)
        C_SuperTrack.SetSuperTrackedUserWaypoint(true)
        activeWaypoint = "blizzard"
    end
end

-------------------------------------------------------------------------------
-- ClearWaypoint: Removes the current active waypoint.
-------------------------------------------------------------------------------
function Nav.ClearWaypoint()
    if not activeWaypoint then return end

    if C_Map.HasUserWaypoint and C_Map.HasUserWaypoint() then
        C_Map.ClearUserWaypoint()
    end
    if C_SuperTrack and C_SuperTrack.SetSuperTrackedUserWaypoint then
        C_SuperTrack.SetSuperTrackedUserWaypoint(false)
    end

    activeWaypoint = nil
end

-------------------------------------------------------------------------------
-- SetWaypointForStep: Convenience function that takes a step table and
-- sets the waypoint using its location data.
-- @param step table  A step from the guide pack
-------------------------------------------------------------------------------
function Nav.SetWaypointForStep(step)
    if not step then return end

    local x, y
    if step.coords then
        x = step.coords[1]
        y = step.coords[2]
    end

    local title = step.label or "Hearth & Seek"
    if step.zone then
        title = title .. " (" .. step.zone .. ")"
    end

    Nav.SetWaypoint(step.mapID, x, y, title)
end

-------------------------------------------------------------------------------
-- IsWaypointActive: Returns whether we currently have an active waypoint.
-- @return boolean
-------------------------------------------------------------------------------
function Nav.IsWaypointActive()
    return activeWaypoint ~= nil
end
