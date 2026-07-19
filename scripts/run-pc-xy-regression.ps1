[CmdletBinding()]
param(
    [string]$Sessions = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/pc_xy_regression",
    [string]$WebOutputDirectory = "web/assets/experiment/pc-xy-regression"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m sim.pc_xy_regression --sessions $Sessions --output-dir $OutputDirectory `
        --web-output-dir $WebOutputDirectory
    if ($LASTEXITCODE -ne 0) { throw "PC XY regression experiment failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
