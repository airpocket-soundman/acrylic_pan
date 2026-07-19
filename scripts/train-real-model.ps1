[CmdletBinding()]
param(
    [string]$Sessions = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/real_model",
    [string]$Header = "firmware/AcrylicPanCollector/generated/apan_8class_model.h",
    [string]$Alpha = "D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy",
    [double]$Ridge = 0.1
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m sim.real_model_pipeline --sessions $Sessions --output-dir $OutputDirectory `
        --header $Header --alpha $Alpha --ridge $Ridge
    if ($LASTEXITCODE -ne 0) { throw "Real model training failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
