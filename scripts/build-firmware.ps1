[CmdletBinding()]
param(
    [string]$SourceProject = "C:\Users\yamas\lexide\workspace_omega_v2\AIVibrationInference",
    [string]$Configuration = "Debug",
    [string]$StagingRoot,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$makeExe = "C:\LAPIS\LEXIDE\Utilities\Bin\make.exe"

if (-not (Test-Path -LiteralPath $SourceProject -PathType Container)) {
    throw "LEXIDE project not found: $SourceProject"
}
if (-not (Test-Path -LiteralPath $makeExe -PathType Leaf)) {
    throw "LEXIDE make.exe not found: $makeExe"
}
if (-not $StagingRoot) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $StagingRoot = Join-Path $repoRoot ".local\firmware-build\$stamp"
}
$stagingFull = [System.IO.Path]::GetFullPath($StagingRoot)
if (Test-Path -LiteralPath $stagingFull) {
    throw "Staging path already exists. Select a new empty path: $stagingFull"
}

$projectName = Split-Path -Leaf $SourceProject
$stagedProject = Join-Path $stagingFull $projectName
New-Item -ItemType Directory -Path $stagedProject -Force | Out-Null
Write-Host "Creating a working copy so the source project is not modified."
Get-ChildItem -LiteralPath $SourceProject -Force | Copy-Item -Destination $stagedProject -Recurse -Force

$buildDir = Join-Path $stagedProject $Configuration
$makefile = Join-Path $buildDir "makefile"
if (-not (Test-Path -LiteralPath $makefile -PathType Leaf)) {
    throw "Generated makefile not found: $makefile. Generate the $Configuration build once in LEXIDE."
}

$lexidePaths = @(
    "C:\LAPIS\LEXIDE\Bin",
    "C:\LAPIS\LEXIDE\BuildTools\Ver.20260317\Bin",
    "C:\LAPIS\LEXIDE\Utilities\Bin"
)
$env:Path = ($lexidePaths -join ";") + ";" + $env:Path

Push-Location $buildDir
try {
    if ($Clean) {
        & $makeExe clean
        if ($LASTEXITCODE -ne 0) { throw "make clean failed with exit code $LASTEXITCODE." }
    }
    & $makeExe all -j
    if ($LASTEXITCODE -ne 0) { throw "Firmware build failed with exit code $LASTEXITCODE." }
} finally {
    Pop-Location
}

$hex = Get-ChildItem -LiteralPath $buildDir -Filter "*.hex" | Select-Object -First 1
if (-not $hex) { throw "Build completed but no HEX file was found in $buildDir." }
Write-Host "Build succeeded: $($hex.FullName)"
