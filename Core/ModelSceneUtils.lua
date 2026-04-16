-------------------------------------------------------------------------------
-- HearthAndSeek: ModelSceneUtils.lua
-- Shared helper for loading a decor actor into a ModelScene, with a reliable
-- fallback when the item's preferred uiModelSceneID doesn't expose a
-- "decor"-tagged actor or fails to transition.
-------------------------------------------------------------------------------
local _, NS = ...

NS.ModelSceneUtils = NS.ModelSceneUtils or {}

local DEFAULT_DECOR_SCENE_ID = 859

--- LoadDecorScene: transitions `scene` to the preferred scene ID, then
--- attempts to return its "decor"-tagged actor. Falls back to the default
--- decor scene (859) if the preferred scene fails to transition or has no
--- decor actor.
---
--- The caller is responsible for calling scene:ClearScene() before invoking
--- this, and for calling SetModelByFileID / Show() on the returned actor.
---
--- @param scene        table       the ModelScene frame to populate
--- @param preferredID  number|nil  the item's uiModelSceneID (may be nil)
--- @return table|nil               the decor actor, or nil if both fail
function NS.ModelSceneUtils.LoadDecorScene(scene, preferredID)
    if not scene or not scene.TransitionToModelSceneID then return nil end

    local sceneID = preferredID or DEFAULT_DECOR_SCENE_ID

    local function tryScene(id)
        local ok = pcall(function()
            scene:TransitionToModelSceneID(
                id,
                CAMERA_TRANSITION_TYPE_IMMEDIATE,
                CAMERA_MODIFICATION_TYPE_DISCARD,
                true)
        end)
        if not ok then return nil end
        return scene:GetActorByTag("decor")
    end

    local actor = tryScene(sceneID)
    if actor then return actor end

    -- Fallback: retry with the default decor scene, which is known to expose
    -- a "decor"-tagged actor. Clear the scene first to discard any partial
    -- state from the failed attempt.
    if sceneID ~= DEFAULT_DECOR_SCENE_ID then
        scene:ClearScene()
        actor = tryScene(DEFAULT_DECOR_SCENE_ID)
    end
    return actor
end
