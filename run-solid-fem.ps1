[CmdletBinding()]
param(
    [string]$Image = "acrylic-pan-solid-fem:local",
    [string]$Container = "acrylic-pan-solid-fem-run"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$output = Join-Path $root "web\assets\simulation"
New-Item -ItemType Directory -Force -Path $output | Out-Null

if (docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $Container }) {
    throw "Container '$Container' already exists. It is not removed automatically."
}

docker build --tag $Image --file (Join-Path $root "Dockerfile") $root
if ($LASTEXITCODE -ne 0) { throw "Docker image build failed." }

docker run --rm `
    --name $Container `
    --network none `
    --read-only `
    --tmpfs /tmp:rw,nosuid,nodev,size=256m `
    --env PYTHONDONTWRITEBYTECODE=1 `
    --env MPLCONFIGDIR=/tmp/matplotlib `
    --volume "${output}:/workspace/web/assets/simulation" `
    $Image
if ($LASTEXITCODE -ne 0) { throw "FEM container failed." }
