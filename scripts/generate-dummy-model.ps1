[CmdletBinding()]
param(
    [string]$OutputDirectory = "data/dummy_model",
    [string]$Header = "firmware/AcrylicPanCollector/generated/apan_dummy_model.h",
    [int]$Seed = 20260716,
    [string]$Alpha = "D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Push-Location $root
try {
    python -m sim.dummy_model_pipeline --output-dir $OutputDirectory --header $Header --seed $Seed --alpha $Alpha
    if ($LASTEXITCODE -ne 0) { throw "Dummy model generation failed ($LASTEXITCODE)" }
}
finally {
    Pop-Location
}
