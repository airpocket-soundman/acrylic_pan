param(
    [string]$Hex = 'C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_private\Debug\AIVibrationInference.hex',
    [string]$Pack = 'C:\Users\yamas\Desktop\solist-ai\ML63Q2500_DFP_r.1.1.0\ROHM.ML63Q25x7_DFP.1.1.0.pack',
    [string]$Probe = '14OZPOHJLAY5E'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Hex)) { throw "HEX not found: $Hex" }
if (-not (Test-Path -LiteralPath $Pack)) { throw "CMSIS pack not found: $Pack" }

$venv = Join-Path $env:TEMP 'acrylic_pan_pyocd'
$python = Join-Path $venv 'Scripts\python.exe'
$pyocd = Join-Path $venv 'Scripts\pyocd.exe'
if (-not (Test-Path -LiteralPath $python)) {
    python -m venv $venv
}
if (-not (Test-Path -LiteralPath $pyocd)) {
    & $python -m pip install --disable-pip-version-check pyocd
    if ($LASTEXITCODE -ne 0) { throw 'Unable to install pyOCD.' }
}

# pyOCD verifies programmed data by default. This command changes target flash.
& $pyocd load --pack $Pack -t ml63q25x7 -u $Probe $Hex
if ($LASTEXITCODE -ne 0) { throw "Flash failed with exit code $LASTEXITCODE" }
Write-Host "Programmed and verified: $Hex"
