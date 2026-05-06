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
--       _order = { "My Bedroom", "Beach House", ... },  -- creation order
--       ["My Bedroom"] = { [decorID]=true, [decorID]=true, ... },
--       ["Beach House"] = { ... },
--     }
--
-- Names starting with "_" are reserved for sentinels (currently just
-- `_order`). Public callers should never see them.
-------------------------------------------------------------------------------
local _, NS = ...

NS.Collections = NS.Collections or {}
local Collections = NS.Collections

-------------------------------------------------------------------------------
-- Configuration
-------------------------------------------------------------------------------
Collections.MAX_COUNT     = 20    -- soft cap; Create() refuses past this
Collections.MAX_NAME_LEN  = 20    -- fits the manager row's name column without truncation

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
    saved.collections._order = saved.collections._order or {}
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
    -- Append orphaned keys (back of order)
    for name, val in pairs(Collections._db) do
        if name ~= "_order" and type(val) == "table" and not present[name] then
            table.insert(Collections._db._order, name)
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
