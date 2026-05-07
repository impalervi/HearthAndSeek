-------------------------------------------------------------------------------
-- HearthAndSeek: Collections.lua
--
-- User-defined named decor collections (think "playlists" — a small set of
-- decorIDs grouped under a name). Account-wide; siblings of `favorites`,
-- not a replacement for it. The star icon stays as the quick-toggle for
-- the existing favorites set.
--
-- SavedVariables shape (top-level `HearthAndSeekDB.collections`):
--
--     {
--       _order  = { "My Bedroom", "Beach House", ... },     -- creation order
--       _colors = { ["My Bedroom"] = { r, g, b }, ... },    -- per-collection display color (parallel to membership)
--       ["My Bedroom"] = { [decorID]=true, [decorID]=true, ... },
--       ["Beach House"] = { ... },
--     }
--
-- Names starting with "_" are reserved for sentinels (`_order`,
-- `_colors`). Public callers should never see them.
-------------------------------------------------------------------------------
local _, NS = ...

NS.Collections = NS.Collections or {}
local Collections = NS.Collections

-------------------------------------------------------------------------------
-- Configuration
-------------------------------------------------------------------------------
Collections.MAX_COUNT     = 20    -- soft cap; Create() refuses past this
Collections.MAX_NAME_LEN  = 20    -- fits the manager row's name column without truncation

-- Default display color for a collection that has never been recolored.
-- Soft cyan, matching the original hardcoded shade so existing collections
-- look unchanged after the colour-picker feature shipped.
Collections.DEFAULT_COLOR = { 0.55, 0.80, 1.00 }

-- Public-facing failure reasons returned by Create / Rename
Collections.ERR_EMPTY     = "empty"     -- name is "" after trim
Collections.ERR_RESERVED  = "reserved"  -- starts with "_"
Collections.ERR_TOO_LONG  = "too_long"
Collections.ERR_DUPLICATE = "duplicate"
Collections.ERR_AT_CAP    = "at_cap"
Collections.ERR_NOT_FOUND = "not_found"
Collections.ERR_NO_DB     = "no_db"     -- called before Init()

-------------------------------------------------------------------------------
-- Internals
-------------------------------------------------------------------------------

--- Trim leading/trailing whitespace.
local function trim(s)
    if type(s) ~= "string" then return "" end
    return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

--- Validate a candidate collection name. Returns nil on success, or one of
--- the ERR_* strings on failure. Pass `existingName` when validating a
--- rename so the existing name doesn't trigger ERR_DUPLICATE on itself.
local function validateName(name, existingName)
    name = trim(name)
    if name == "" then return Collections.ERR_EMPTY end
    if name:sub(1, 1) == "_" then return Collections.ERR_RESERVED end
    if #name > Collections.MAX_NAME_LEN then return Collections.ERR_TOO_LONG end
    local db = Collections._db
    if db and db[name] and name ~= existingName then
        return Collections.ERR_DUPLICATE
    end
    return nil
end

--- Locate `name` in the order array. Returns the 1-based index or nil.
local function indexOf(name)
    local order = Collections._db and Collections._db._order
    if not order then return nil end
    for i, v in ipairs(order) do
        if v == name then return i end
    end
    return nil
end

-------------------------------------------------------------------------------
-- Init: bound to SavedVariables. Call from InitSavedVars after the
-- HearthAndSeekDB table is materialized.
-------------------------------------------------------------------------------
function Collections.Init(saved)
    if type(saved) ~= "table" then return end
    saved.collections = saved.collections or {}
    saved.collections._order  = saved.collections._order  or {}
    saved.collections._colors = saved.collections._colors or {}
    Collections._db = saved.collections

    -- Reconcile _order vs actual keys: drop missing names, append any
    -- name present as a key but not yet in _order. Defensive against
    -- manual edits to SavedVariables.
    local present = {}
    for _, name in ipairs(Collections._db._order) do
        present[name] = true
    end
    -- Remove _order entries whose backing table is gone
    for i = #Collections._db._order, 1, -1 do
        local n = Collections._db._order[i]
        if type(Collections._db[n]) ~= "table" then
            table.remove(Collections._db._order, i)
            present[n] = nil
        end
    end
    -- Append orphaned keys (back of order). Reserved sentinels (any key
    -- starting with "_") are skipped so `_colors` etc. don't get treated
    -- as collection names.
    for name, val in pairs(Collections._db) do
        if name:sub(1, 1) ~= "_" and type(val) == "table" and not present[name] then
            table.insert(Collections._db._order, name)
        end
    end

    -- Drop colour entries whose backing collection is gone (e.g. user
    -- hand-edited SavedVariables, or a Delete prior to this version).
    for name in pairs(Collections._db._colors) do
        if type(Collections._db[name]) ~= "table" then
            Collections._db._colors[name] = nil
        end
    end
end

-------------------------------------------------------------------------------
-- Read API
-------------------------------------------------------------------------------

--- Return an array of collection names in creation order.
function Collections.List()
    local db = Collections._db
    if not db then return {} end
    local out = {}
    for i, name in ipairs(db._order) do
        out[i] = name
    end
    return out
end

--- Number of user-defined collections.
function Collections.Total()
    local db = Collections._db
    return db and #db._order or 0
end

--- Whether a collection with this exact name exists.
function Collections.Exists(name)
    local db = Collections._db
    return db and type(db[name]) == "table" and not name:match("^_")
end

--- Number of items in a collection. Returns 0 if the collection doesn't exist.
function Collections.Count(name)
    local db = Collections._db
    if not db or type(db[name]) ~= "table" then return 0 end
    local n = 0
    for _ in pairs(db[name]) do n = n + 1 end
    return n
end

--- Whether `decorID` belongs to `name`. Returns false on missing collection.
function Collections.Contains(name, decorID)
    local db = Collections._db
    if not db or type(db[name]) ~= "table" then return false end
    return db[name][decorID] == true
end

-------------------------------------------------------------------------------
-- Write API
-------------------------------------------------------------------------------

--- Create a new collection. Returns (true) on success, or (false, reason)
--- where reason is one of the ERR_* constants.
function Collections.Create(name)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    name = trim(name)
    local err = validateName(name)
    if err then return false, err end
    if #Collections._db._order >= Collections.MAX_COUNT then
        return false, Collections.ERR_AT_CAP
    end
    Collections._db[name] = {}
    table.insert(Collections._db._order, name)
    return true
end

--- Rename a collection. Preserves item membership and order position.
--- Returns (true) on success, or (false, reason).
function Collections.Rename(oldName, newName)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(oldName) then return false, Collections.ERR_NOT_FOUND end
    newName = trim(newName)
    if newName == oldName then return true end
    local err = validateName(newName, oldName)
    if err then return false, err end
    Collections._db[newName] = Collections._db[oldName]
    Collections._db[oldName] = nil
    local colours = Collections._db._colors
    if colours and colours[oldName] then
        colours[newName] = colours[oldName]
        colours[oldName] = nil
    end
    local idx = indexOf(oldName)
    if idx then Collections._db._order[idx] = newName end
    return true
end

--- Delete a collection (and all its item entries). Returns (true) on
--- success, or (false, reason).
function Collections.Delete(name)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(name) then return false, Collections.ERR_NOT_FOUND end
    Collections._db[name] = nil
    if Collections._db._colors then Collections._db._colors[name] = nil end
    local idx = indexOf(name)
    if idx then table.remove(Collections._db._order, idx) end
    return true
end

--- Add a decorID to a collection. Returns (true, true) when newly added,
--- (true, false) when already present, or (false, reason) on failure.
function Collections.AddItem(name, decorID)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(name) then return false, Collections.ERR_NOT_FOUND end
    if type(decorID) ~= "number" then return false, "bad_id" end
    local set = Collections._db[name]
    local was = set[decorID] == true
    set[decorID] = true
    return true, not was
end

--- Remove a decorID from a collection. Returns (true, true) when actually
--- removed, (true, false) when not present.
function Collections.RemoveItem(name, decorID)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(name) then return false, Collections.ERR_NOT_FOUND end
    if type(decorID) ~= "number" then return false, "bad_id" end
    local set = Collections._db[name]
    local was = set[decorID] == true
    set[decorID] = nil
    return true, was
end

--- Toggle membership: add if missing, remove if present. Returns the
--- final state (true = item is now in the collection).
function Collections.ToggleItem(name, decorID)
    if Collections.Contains(name, decorID) then
        Collections.RemoveItem(name, decorID)
        return false
    end
    Collections.AddItem(name, decorID)
    return true
end

-------------------------------------------------------------------------------
-- Color API: per-collection display tint shown next to the name in the
-- manager, the filter dropdown checkbox, and the active-filter footer pill.
-- Stored in a parallel `_colors` table so item membership stays a flat set.
-------------------------------------------------------------------------------

--- Return the collection's stored color as r, g, b in [0, 1].
--- Falls back to DEFAULT_COLOR when the collection has none set yet
--- (or doesn't exist — callers that care should check Exists first).
function Collections.GetColor(name)
    local db = Collections._db
    local stored = db and db._colors and db._colors[name]
    if stored then
        return stored[1], stored[2], stored[3]
    end
    local d = Collections.DEFAULT_COLOR
    return d[1], d[2], d[3]
end

--- Set the collection's display color. Silent no-op if the collection
--- doesn't exist or the DB isn't ready.
function Collections.SetColor(name, r, g, b)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(name) then return false, Collections.ERR_NOT_FOUND end
    Collections._db._colors = Collections._db._colors or {}
    Collections._db._colors[name] = { r, g, b }
    return true
end

--- Drop any custom color override on the collection. Subsequent
--- GetColor calls return DEFAULT_COLOR. No-op if no override is set.
function Collections.ResetColor(name)
    if not Collections._db then return false, Collections.ERR_NO_DB end
    if not Collections.Exists(name) then return false, Collections.ERR_NOT_FOUND end
    if Collections._db._colors then
        Collections._db._colors[name] = nil
    end
    return true
end
