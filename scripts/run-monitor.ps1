[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$HostAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 8765,
    [string]$Output = "data/raw/sessions",
    [ValidateSet("index.html", "collector.html", "position.html", "instrument.html")]
    [string]$Page = "index.html",
    [switch]$NoBrowser,
    [switch]$InstallDependencies
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$requirements = Join-Path $repoRoot "requirements.txt"

try {
    & $Python --version
} catch {
    throw "Python could not be started. Install Python 3.10+ or select python.exe with -Python."
}
if ($LASTEXITCODE -ne 0) { throw "Python failed with exit code $LASTEXITCODE." }

if ($InstallDependencies) {
    & $Python -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { throw "Installing Python dependencies failed." }
}

& $Python -c "import joblib, numpy, serial, sklearn"
if ($LASTEXITCODE -ne 0) {
    throw "Required Python packages are missing. Run again with -InstallDependencies."
}

$url = "http://${HostAddress}:$Port/"
$arguments = @(
    "-m", "pc.acrylic_pan_web",
    "--host", $HostAddress,
    "--port", "$Port",
    "--output", $Output,
    "--page", $Page
)
if ($NoBrowser) { $arguments += "--no-browser" }

Write-Host "Acrylic Pan monitor: $url"
Write-Host "Stop the server with Ctrl+C."
Push-Location $repoRoot
try {
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$repoRoot;$env:PYTHONPATH" } else { $repoRoot }
    & $Python @arguments
    if ($LASTEXITCODE -ne 0) { throw "PC monitor exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
