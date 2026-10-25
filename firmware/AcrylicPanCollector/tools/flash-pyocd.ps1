param(
    [string]$Hex = 'C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_odl\Debug\AIVibrationInference.hex',
    [string]$Pack = 'C:\Users\yamas\Desktop\solist-ai\ML63Q2500_DFP_r.1.1.0\ROHM.ML63Q25x7_DFP.1.1.0.pack',
    [string]$Probe = '14OZPOHJLAY5E',
    [string]$OpenOcd = 'C:\LAPIS\LEXIDE\gdb\openocd_arm.exe',
    [string]$Objcopy = 'C:\LAPIS\LEXIDE\BuildTools\Ver.20260317\Bin\llvm-objcopy.exe'
)

$ErrorActionPreference = 'Stop'
foreach ($path in @($Hex, $Pack, $OpenOcd, $Objcopy)) {
    if (-not (Test-Path -LiteralPath $path)) { throw "Required file not found: $path" }
}

# The ROHM ELF/HEX uses the physical flash load address 0x10000000, while the
# CMSIS pack exposes an IROM alias at 0.  pyOCD therefore skips the main HEX
# segment.  LEXIDE's ROHM OpenOCD flash driver understands the physical bank.
# Build a contiguous flash-only binary so RAM heap/stack sections in the HEX
# are not mistaken for programmable data.
$debug = Split-Path -Parent $Hex
$elf = Join-Path $debug 'AIVibrationInference.elf'
$binary = Join-Path $debug 'AIVibrationInference.flash.bin'
if (-not (Test-Path -LiteralPath $elf)) { throw "ELF not found beside HEX: $elf" }
& $Objcopy --output-target=binary --only-section=.text --only-section=.ARM.exidx `
    --only-section=.copy.table --only-section=.data $elf $binary
if ($LASTEXITCODE -ne 0) { throw "Flash binary conversion failed with exit code $LASTEXITCODE" }

$cfgRoot = Join-Path $env:TEMP 'acrylic_pan_openocd_cfg'
New-Item -ItemType Directory -Path $cfgRoot -Force | Out-Null
& tar -xf $Pack -C $cfgRoot 'Cfg/ml63q25x7.cfg'
if ($LASTEXITCODE -ne 0) { throw 'Unable to extract the target configuration from the CMSIS pack.' }
$targetCfg = Join-Path $cfgRoot 'Cfg\ml63q25x7.cfg'

& $OpenOcd -s 'C:\LAPIS\LEXIDE\Cfg' -f 'cmsis-dap.cfg' `
    -c "adapter serial $Probe" -f $targetCfg -c 'init' -c 'reset halt' `
    -c "program $($binary.Replace('\', '/')) 0x10000000 verify reset exit"
if ($LASTEXITCODE -ne 0) { throw "Flash failed with exit code $LASTEXITCODE" }
Write-Host "Programmed and verified: $binary"
