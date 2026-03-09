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

    Compress-Archive -Path (Join-Path $stagingRoot $addonFolderName) -DestinationPath $zipPath -Force
    Write-Host "Created package: $zipPath"
}
finally {
    if (Test-Path -LiteralPath $stagingRoot) {
        Remove-Item -LiteralPath $stagingRoot -Recurse -Force
    }
}
