[CmdletBinding()]
param(
    [string]$SessionsRoot = "data/raw/sessions",
    [string]$OutputDirectory = "artifacts/sampling_experiment_20260718",
    [string]$Alpha = "D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy",
    [double]$Ridge = 0.1,
    [string[]]$SessionIds = @(
        "20260718_074916_84b183d2",
        "20260718_080557_9524983c",
        "20260718_081909_9dd2d785",
        "20260718_084143_ac8e6d56"
    )
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$arguments = @(
    "-m", "sim.sampling_experiment",
    "--sessions-root", $SessionsRoot,
    "--output-dir", $OutputDirectory,
    "--alpha", $Alpha,
    "--ridge", $Ridge
)
foreach ($sessionId in $SessionIds) {
    $arguments += @("--session-id", $sessionId)
}

Push-Location $root
try {
    & python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Sampling experiment failed ($LASTEXITCODE)"
    }
}
finally {
    Pop-Location
}
