param(
    [string]$WowDir,
    [string]$ConfigPath = (Join-Path (Join-Path $PSScriptRoot '..') 'deploy.config.json')
)

$ErrorActionPreference = 'Stop'

function Read-DeployConfig {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Deploy config not found at '$Path'. Copy deploy.config.example.json to deploy.config.json and set wowDir."
    }

    $raw = Get-Content -LiteralPath $Path -Raw
    if ([string]::IsNullOrWhiteSpace($raw)) {
        throw "Deploy config '$Path' is empty."
    }

    return ($raw | ConvertFrom-Json)
}

function Sync-Directory {
    param(
        [string]$SourceDir,
        [string]$DestinationDir
    )

    New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null

    robocopy $SourceDir $DestinationDir /E /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null
    $code = $LASTEXITCODE
    if ($code -ge 8) {
        throw "Directory sync failed for '$SourceDir' -> '$DestinationDir' (robocopy exit code $code)."
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$config = Read-DeployConfig -Path $ConfigPath

if ([string]::IsNullOrWhiteSpace($WowDir)) {
    $WowDir = $config.wowDir
}

if ([string]::IsNullOrWhiteSpace($WowDir)) {
    throw 'Missing WoW directory. Pass -WowDir or set wowDir in deploy.config.json.'
}

$addonRelativePath = $config.addonRelativePath
if ([string]::IsNullOrWhiteSpace($addonRelativePath)) {
    $addonRelativePath = 'Interface\AddOns\HearthAndSeek'
}

$includePaths = @($config.include)
if ($includePaths.Count -eq 0) {
    $includePaths = @(
        'HearthAndSeek.toc',
        'Core',
        'Data',
        'Modules',
        'UI',
        'Libs',
        'Media',
        'Changelogs'
    )
}

$destinationRoot = Join-Path $WowDir $addonRelativePath
New-Item -ItemType Directory -Path $destinationRoot -Force | Out-Null

Write-Host "Deploying HearthAndSeek to: $destinationRoot"

foreach ($relativePath in $includePaths) {
    $sourcePath = Join-Path $repoRoot $relativePath
    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Configured path '$relativePath' does not exist in repo."
    }

    $destinationPath = Join-Path $destinationRoot $relativePath

    if (Test-Path -LiteralPath $sourcePath -PathType Container) {
        Sync-Directory -SourceDir $sourcePath -DestinationDir $destinationPath
    }
    else {
        $destinationDir = Split-Path -Path $destinationPath -Parent
        if (-not [string]::IsNullOrWhiteSpace($destinationDir)) {
            New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
        }
        Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
    }

    Write-Host "  copied: $relativePath"
}

Write-Host 'HearthAndSeek deployment complete.'
