[CmdletBinding()]
param(
    [string]$Sessions = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/solist_xy_staged",
    [string]$Header = "firmware/AcrylicPanCollector/generated/apan_xy_staged_model.h"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m sim.solist_xy_staged --sessions $Sessions --output-dir $OutputDirectory --header $Header
    if ($LASTEXITCODE -ne 0) { throw "Staged Solist XY training failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
