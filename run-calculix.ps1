[CmdletBinding()]
param(
    [string]$Image = "acrylic-pan-calculix:2.20",
    [string]$Container = "acrylic-pan-calculix-run"
)
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$output = Join-Path $root "web\assets\simulation\calculix"
New-Item -ItemType Directory -Force -Path $output | Out-Null
if (docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $Container }) {
    throw "Container '$Container' already exists. It is not removed automatically."
}
docker build --tag $Image --file (Join-Path $root "Dockerfile.calculix") $root
if ($LASTEXITCODE -ne 0) { throw "CalculiX image build failed." }
docker run --rm --name $Container --network none --read-only `
    --tmpfs /tmp:rw,nosuid,nodev,size=256m `
    --env MPLCONFIGDIR=/tmp/matplotlib `
    --env XDG_CACHE_HOME=/tmp/cache `
    --volume "${output}:/work" `
    --volume "${root}\web\assets\simulation:/reference:ro" $Image
if ($LASTEXITCODE -ne 0) { throw "CalculiX analysis failed." }
