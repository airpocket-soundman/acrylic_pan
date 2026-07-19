[CmdletBinding()]
param(
    [string]$Image = "acrylic-pan-calculix:2.20",
    [string]$Container = "acrylic-pan-calculix-thickness-5mm"
)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$threeMm = Join-Path $root "web\assets\simulation\calculix-highfreq"
$fiveMm = Join-Path $root "tmp\calculix-thickness-5mm"
$output = Join-Path $root "web\assets\simulation\calculix-thickness-comparison"
New-Item -ItemType Directory -Force -Path $fiveMm, $output | Out-Null

if (-not (Test-Path (Join-Path $threeMm "highfreq-results.json"))) {
    throw "Run .\run-calculix-highfreq.ps1 -ThicknessMm 3 first."
}
if (docker ps -a --format "{{.Names}}" | Where-Object { $_ -eq $Container }) {
    throw "Container '$Container' already exists. It is not removed automatically."
}

docker build --tag $Image --file (Join-Path $root "Dockerfile.calculix") $root
if ($LASTEXITCODE -ne 0) { throw "CalculiX image build failed." }

docker run --rm --name $Container --network none --read-only `
    --tmpfs /tmp:rw,nosuid,nodev,size=512m `
    --env MPLCONFIGDIR=/tmp/matplotlib `
    --env XDG_CACHE_HOME=/tmp/cache `
    --mount "type=bind,src=$fiveMm,dst=/work" `
    --entrypoint bash $Image /solver/calculix/run-highfreq.sh /work 5
if ($LASTEXITCODE -ne 0) { throw "CalculiX 5 mm high-frequency analysis failed." }

docker run --rm --name "$Container-compare" --network none --read-only `
    --tmpfs /tmp:rw,nosuid,nodev,size=256m `
    --env MPLCONFIGDIR=/tmp/matplotlib `
    --mount "type=bind,src=$threeMm,dst=/three,readonly" `
    --mount "type=bind,src=$fiveMm,dst=/five,readonly" `
    --mount "type=bind,src=$output,dst=/output" `
    --entrypoint python3 $Image /solver/calculix/compare_thickness.py `
    --three-mm /three --five-mm /five --output /output
if ($LASTEXITCODE -ne 0) { throw "Thickness comparison postprocess failed." }

Write-Host "Thickness comparison results: $output"
