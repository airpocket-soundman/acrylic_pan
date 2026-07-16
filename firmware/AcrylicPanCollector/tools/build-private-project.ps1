param(
    [string]$Project = 'C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_private'
)

$ErrorActionPreference = 'Stop'
$make = 'C:\LAPIS\LEXIDE\Utilities\Bin\make.exe'
if (-not (Test-Path -LiteralPath $make)) { throw "LEXIDE make not found: $make" }
if (-not (Test-Path -LiteralPath $Project)) { throw "Project not found: $Project" }
$debug = Join-Path $Project 'Debug'
if (-not (Test-Path -LiteralPath (Join-Path $debug 'makefile'))) {
    throw "Generated makefile not found: $debug"
}

Push-Location $debug
try {
    & $make all -j
    if ($LASTEXITCODE -ne 0) { throw "LEXIDE make failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}

$hex = Join-Path $Project 'Debug\AIVibrationInference.hex'
if (-not (Test-Path -LiteralPath $hex)) { throw "Build completed but HEX was not found: $hex" }
Write-Host "Built: $hex"
