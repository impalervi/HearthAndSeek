---
name: publish-addon
description: Validate, package, and deploy HearthAndSeek addon to WoW for testing
user_invocable: true
---

# Publish Addon

Validate the addon structure, create a release zip, and deploy to the WoW addons folder for in-game testing.

## Steps

### 1. Validate included files

Check that ALL files needed for the addon to work are accounted for in both the publish script (`scripts/publish.ps1`) and the deploy script (`scripts/deploy.sh`).

**a) Read the TOC file** (`HearthAndSeek.toc`) and extract every file path listed (`.lua` and `.xml` files). Verify each one exists on disk. These are the files WoW will try to load — if any are missing, the addon will break.

**b) Determine which directories contain those files.** The standard set is:
- `HearthAndSeek.toc`
- `Core/`
- `Data/`
- `Modules/`
- `UI/`
- `Libs/`
- `Media/`

**c) Cross-check the publish script** (`scripts/publish.ps1`): read its fallback `$includePaths` array and the `deploy.config.example.json` include list. Verify they cover all directories from step (b). Flag any directory that contains addon files but is missing from the include list.

**d) Cross-check the deploy script** (`scripts/deploy.sh`): read its `DIRS` array. Verify it covers the same set of directories. Flag any mismatches between deploy.sh, publish.ps1, and the actual directory structure.

**e) If any discrepancies are found**, STOP and report them to the user. Do NOT proceed until the scripts are updated.

### 2. Run the publish script to create the release zip

Run the PowerShell publish script:
```
powershell -ExecutionPolicy Bypass -File d:/Programming/WoWAddons/HearthAndSeek/scripts/publish.ps1
```

This creates a zip at `d:\Programming\WoWAddons\HearthAndSeek\dist\HearthAndSeek-v{VERSION}.zip`.

Verify the zip was created successfully and report its path and size.

### 3. Deploy to WoW Addons directory

Completely remove the previous HearthAndSeek addon folder and extract the new archive:

```bash
# Remove the old addon completely
rm -rf "D:/Games/World of Warcraft/_retail_/Interface/AddOns/HearthAndSeek"

# Extract the new archive (the zip contains a HearthAndSeek/ folder at root)
cd "D:/Games/World of Warcraft/_retail_/Interface/AddOns" && unzip -o "d:/Programming/WoWAddons/HearthAndSeek/dist/HearthAndSeek-v{VERSION}.zip"
```

Verify the extraction succeeded by listing the deployed addon directory.

### 4. Wait for user validation

Tell the user:
- The addon has been published and deployed
- The version number and zip location
- Ask them to `/reload` in-game and validate the changes
- Wait for their confirmation before proceeding with any further steps (like merging to main or pushing)
