param(
    [string]$ConfigPath = (Join-Path (Join-Path $PSScriptRoot '..') 'deploy.config.json'),
    [string]$TocPath = (Join-Path (Join-Path $PSScriptRoot '..') 'HearthAndSeek.toc')
)

$ErrorActionPreference = 'Stop'

function Read-DeployConfig {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Deploy config not found at '$Path'. Copy deploy.config.example.json to deploy.config.json and set include paths."
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "Deploy config '$Path' is empty."
    }

    return ($raw | ConvertFrom-Json)
}

function Get-AddonVersion {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "TOC file not found at '$Path'."
    }

    $version = $null
    foreach ($line in Get-Content -LiteralPath $Path) {
        $m = [regex]::Match($line, '^\s*##\s*Version\s*:\s*(.+?)\s*$')
        if ($m.Success) {
            $version = $m.Groups[1].Value.Trim()
            break
        }
    }

    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Could not find '## Version:' in '$Path'."
    }

    if ($version -match '^[vV](.+)$') {
        $version = $Matches[1]
    }

    foreach ($ch in [System.IO.Path]::GetInvalidFileNameChars()) {
        $version = $version.Replace($ch, '-')
    }

    return $version
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$addonFolderName = 'HearthAndSeek'
$config = Read-DeployConfig -Path $ConfigPath
$version = Get-AddonVersion -Path $TocPath

$includePaths = @($config.include)
if ($includePaths.Count -eq 0) {
    $includePaths = @(
        'HearthAndSeek.toc',
        'Core',
        'Data',
        'Modules',
        'UI',
        'Libs',
        'Media'
    )
}

$stagingRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("$addonFolderName-publish-" + [Guid]::NewGuid().ToString('N'))
$stagingAddonRoot = Join-Path $stagingRoot $addonFolderName
$distDir = Join-Path $repoRoot 'dist'
if (-not (Test-Path -LiteralPath $distDir)) {
    New-Item -ItemType Directory -Path $distDir -Force | Out-Null
}
$zipName = "$addonFolderName-v$version.zip"
$zipPath = Join-Path $distDir $zipName

try {
    New-Item -ItemType Directory -Path $stagingAddonRoot -Force | Out-Null

    Write-Host "Staging HearthAndSeek v$version files from: $repoRoot"
    foreach ($relativePath in $includePaths) {
        $sourcePath = Join-Path $repoRoot $relativePath
        if (-not (Test-Path -LiteralPath $sourcePath)) {
            throw "Configured path '$relativePath' does not exist in repo."
        }

        $destinationPath = Join-Path $stagingAddonRoot $relativePath
        $destinationDir = Split-Path -Path $destinationPath -Parent
        if (-not [string]::IsNullOrWhiteSpace($destinationDir)) {
            New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
        }

        if (Test-Path -LiteralPath $sourcePath -PathType Container) {
            Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Recurse -Force
        }
        else {
            Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
        }

        Write-Host "  staged: $relativePath"
    }

    if (Test-Path -LiteralPath $zipPath) {
        Remove-Item -LiteralPath $zipPath -Force
    }

    # ZIP spec mandates forward-slash path separators in entry names.
    # PowerShell 5.1's `Compress-Archive` and .NET's
    # `ZipFile.CreateFromDirectory` BOTH write backslashes on Windows
    # (they use `Path.DirectorySeparatorChar`). Windows unzippers tolerate
    # that, but macOS / Linux unzippers treat the backslashes as literal
    # filename characters — extracting produces useless files like
    # `HearthAndSeek\Core\Init.lua` in the AddOns folder root. WoW can't
    # find any of them and the addon silently fails to load.
    #
    # The reliable fix: open the zip manually and write entries via
    # `CreateEntryFromFile` with forward-slash relativePaths constructed
    # explicitly. Verified to produce portable archives that extract
    # correctly on macOS Finder + `unzip` and on Windows.
    # FileSystem transitively loads System.IO.Compression on PS 5.1.
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $sourceRoot = Join-Path $stagingRoot $addonFolderName
    $sourceParent = Split-Path -Path $sourceRoot -Parent
    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::Open($zipPath, 'Create')
        # `-File` skips empty directories. WoW addons never need them in
        # the zip (no semantic empty dirs in the deploy.config allowlist),
        # but if a future include path is an empty folder it'll be omitted
        # silently — would need an explicit `CreateEntry` fallback then.
        # Skip PNG source artifacts — WoW never loads them at runtime, they
        # only exist in the repo as source for regenerating BLP/TGA exports.
        Get-ChildItem -LiteralPath $sourceRoot -Recurse -File |
            Where-Object { $_.Extension -ne '.png' } | ForEach-Object {
            $relativePath = $_.FullName.Substring($sourceParent.Length).
                TrimStart('\', '/').Replace('\', '/')
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive, $_.FullName, $relativePath,
                [System.IO.Compression.CompressionLevel]::Optimal) | Out-Null
        }
    }
    finally {
        if ($archive) { $archive.Dispose() }
    }
    Write-Host "Created package: $zipPath"
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
