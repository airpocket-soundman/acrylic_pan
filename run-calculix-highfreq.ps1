param([double]$ThicknessMm = 3.0)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$output = Join-Path $root "web\assets\simulation\calculix-highfreq"
New-Item -ItemType Directory -Force $output | Out-Null

docker build -t acrylic-pan-calculix:2.20 -f (Join-Path $root "Dockerfile.calculix") $root
if ($LASTEXITCODE -ne 0) { throw "CalculiX image build failed" }

docker run --rm `
  --name acrylic-pan-calculix-highfreq `
  --network none `
  --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=512m `
  -e MPLCONFIGDIR=/tmp/matplotlib `
  -e XDG_CACHE_HOME=/tmp/cache `
  --mount "type=bind,src=$output,dst=/work" `
  --entrypoint bash `
  acrylic-pan-calculix:2.20 `
  /solver/calculix/run-highfreq.sh /work $ThicknessMm
if ($LASTEXITCODE -ne 0) { throw "CalculiX high-frequency analysis failed" }

Write-Host "High-frequency results: $output"
