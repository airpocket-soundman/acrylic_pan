[CmdletBinding()]
param(
    [double]$ThicknessMm = 3.0,
    [string]$Image = "acrylic-pan-calculix:2.20",
    [string]$Container = "acrylic-pan-calculix-xy-grid"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$output = Join-Path $root "web\assets\simulation\calculix-xy-grid"
New-Item -ItemType Directory -Force -Path $output | Out-Null

docker build --tag $Image --file (Join-Path $root "Dockerfile.calculix") $root
if ($LASTEXITCODE -ne 0) { throw "CalculiX image build failed." }

docker run --rm --name $Container --network none --read-only `
    --tmpfs /tmp:rw,nosuid,nodev,size=512m `
    --env MPLCONFIGDIR=/tmp/matplotlib `
    --env XDG_CACHE_HOME=/tmp/cache `
    --mount "type=bind,src=$output,dst=/work" `
    --entrypoint bash $Image /solver/calculix/run-xy-grid.sh /work $ThicknessMm
if ($LASTEXITCODE -ne 0) { throw "CalculiX XY-grid analysis failed." }

Write-Host "XY-grid comparison results: $output"
