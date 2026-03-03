# Zidormi Option B: Timeline Detection & Catalog Enrichment

## Overview

Option A (current) shows a "Visit Zidormi" button for any decoration in a known Zidormi zone. Option B enhances this with:
1. **Timeline detection**: Know which timeline the player is currently in
2. **Catalog enrichment**: Each decoration knows which timeline it requires
3. **Smart button**: Only show "Visit Zidormi" when the player is in the wrong timeline

## Part 1: In-Game Data Collection

### What We Need

For each Zidormi zone, we need the `C_Map.GetMapArtID()` value in **both** timelines (past and present). This is the only reliable way to detect which timeline the player is in.

### How to Collect

We'll add a `/dd zidormi` slash command that prints diagnostic info:

```lua
/dd zidormi
-- Output:
-- [DecorDrive] Zone: Darkshore (uiMapID: 62)
-- [DecorDrive] MapArtID: 12345
-- [DecorDrive] Timeline: unknown (visit Zidormi to switch and run again)
```

**Steps for each zone:**
1. Travel to the Zidormi zone
2. Run `/dd zidormi` — record the artID (this is the "current/present" timeline)
3. Talk to Zidormi and switch to the past timeline
4. Run `/dd zidormi` again — record the artID (this is the "past" timeline)
5. Talk to Zidormi to switch back

### Zones to Visit

| Zone | uiMapID | Priority | Notes |
|------|---------|----------|-------|
| Eversong Woods | 94 | HIGH | 41 decorations (Midnight) |
| Silvermoon City | 110 | HIGH | 78 decorations (Midnight) |
| Ghostlands | 95 | HIGH | 2 decorations |
| Isle of Quel'Danas | 122 | MEDIUM | 0 decorations currently |
| Vale of Eternal Blossoms | 390 | MEDIUM | 12 decorations |
| Eastern Plaguelands | 23 | MEDIUM | 7 decorations |
| Darkshore | 62 | LOW | 4 decorations |
| Blasted Lands | 17 | LOW | 1 decoration |
| Tirisfal Glades | 18 | LOW | 0 decorations |
| Arathi Highlands | 14 | LOW | 0 decorations |
| Silithus | 81 | LOW | 0 decorations |
| Uldum | 249 | LOW | 0 decorations |
| Dustwallow Marsh | 70 | LOW | 0 decorations |

### Also Collect: Zidormi NPC IDs

For the Quel'Thalas Zidormi (and any zones where NPC ID is currently 0), target the Zidormi NPC and run:
```lua
/run print(UnitGUID("target"), GetNPCID_from_GUID)
```
Or simply: `/dd zidormi npc` — we'll add this to print the target's NPC ID.

## Part 2: Timeline Art ID Table

After collecting, we store a table in Constants.lua:

```lua
NS.ZidormiArtIDs = {
    [62]  = { present = XXXXX, past = XXXXX }, -- Darkshore
    [94]  = { present = XXXXX, past = XXXXX }, -- Eversong Woods (Midnight vs TBC)
    [110] = { present = XXXXX, past = XXXXX }, -- Silvermoon City
    -- etc.
}
```

### Detection Function

```lua
function NS.GetCurrentTimeline(zoneMapID)
    local artData = NS.ZidormiArtIDs[zoneMapID]
    if not artData then return nil end -- not a Zidormi zone
    local currentArt = C_Map.GetMapArtID(zoneMapID)
    if currentArt == artData.present then return "present" end
    if currentArt == artData.past then return "past" end
    return "unknown" -- art ID doesn't match either (new patch?)
end
```

## Part 3: Catalog Data Enrichment

### Pipeline Change

In `enrich_catalog.py`, add a `requiredTimeline` field based on:

```python
ZIDORMI_ZONE_TIMELINE = {
    # zone_name -> { expansion -> timeline }
    "Darkshore": {
        "Battle for Azeroth": "present",  # BfA warfront drops need current Darkshore
        "default": "past",                 # older items need pre-BfA Darkshore
    },
    "Eversong Woods": {
        "Midnight": "present",             # Midnight items need revamped Quel'Thalas
        "The Burning Crusade": "past",
        "default": "past",
    },
    "Silvermoon City": {
        "Midnight": "present",
        "default": "past",
    },
    "Vale of Eternal Blossoms": {
        "Shadowlands": "present",          # N'Zoth assault items
        "Mists of Pandaria": "past",       # Pandaren vendor items need pristine Vale
        "default": "past",
    },
    # etc.
}
```

Rule: match `(item.zone, item.expansion)` → `requiredTimeline`. Default to `"present"` if no rule matches.

### CatalogData.lua Output

Add `requiredTimeline` field to each item entry:
```lua
{ decorID=123, name="...", ..., requiredTimeline="present" }
```

Items NOT in Zidormi zones get `requiredTimeline = nil` (omitted to save space).

## Part 4: Smart UI Behavior

### When to Show the Zidormi Button

```
if item is in Zidormi zone:
    if player is in the same zone:
        check C_Map.GetMapArtID() against known art IDs
        if player is in wrong timeline:
            show "Visit Zidormi" button (ORANGE - action needed)
        else:
            hide button (player is in correct timeline)
    else:
        show "Visit Zidormi" button (GRAY - informational)
        tooltip: "This zone has multiple timelines. You may need to
                  visit Zidormi to switch after arriving."
```

### Event Monitoring

Register `ZONE_CHANGED_NEW_AREA` to re-check timeline when player moves zones. If the detail panel is open and showing a Zidormi-zone item, refresh the button state.

## Implementation Order

1. Add `/dd zidormi` diagnostic command
2. Collect art IDs in-game (user task)
3. Add `NS.ZidormiArtIDs` table with collected data
4. Add `GetCurrentTimeline()` detection function
5. Add `requiredTimeline` to enrichment pipeline
6. Regenerate CatalogData.lua
7. Update ShowItem to use smart Zidormi logic
8. Add ZONE_CHANGED_NEW_AREA listener for live updates

## Notes

- Art IDs may change with WoW patches — will need re-verification after major updates
- Some zones may have more than 2 timelines in the future
- The `/dd zidormi` command doubles as a debug tool for users to report issues
