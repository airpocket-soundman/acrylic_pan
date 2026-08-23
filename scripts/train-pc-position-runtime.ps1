[CmdletBinding()]
param(
    [string]$Sessions = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/pc_position_runtime_400x300x5",
    [string]$Python = "python",
    [string]$BaselineModel = "artifacts/pc_position_runtime_400x300x5/position_ensemble_1grid_20260823.joblib"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    $arguments = @(
        "-m", "sim.pc_position_grid_runtime",
        "--sessions", $Sessions,
        "--output-dir", $OutputDirectory
    )
    if (Test-Path -LiteralPath $BaselineModel) {
        $arguments += @("--baseline-model", $BaselineModel)
    }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PC position model training failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
