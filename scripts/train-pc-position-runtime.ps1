[CmdletBinding()]
param(
    [string]$Sessions = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/pc_position_runtime"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m sim.pc_position_runtime --sessions $Sessions --output-dir $OutputDirectory
    if ($LASTEXITCODE -ne 0) { throw "PC position model training failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
