$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$repo = Split-Path -Parent (Split-Path -Parent $root)
$vcvars = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path -LiteralPath $vcvars)) { throw "Visual C++ environment not found: $vcvars" }

$build = Join-Path $env:TEMP 'acrylic_pan_firmware_test'
New-Item -ItemType Directory -Path $build -Force | Out-Null
$exe = Join-Path $build 'test_capture.exe'
$packet = Join-Path $build 'event.apan'
$testSource = Join-Path $root 'tests\test_capture.c'
$captureSource = Join-Path $root 'src\apan_capture.c'
$protocolSource = Join-Path $root 'src\apan_protocol.c'
$include = Join-Path $root 'include'

$compile = 'call "' + $vcvars + '" >nul && cl /nologo /W4 /WX /D_CRT_SECURE_NO_WARNINGS /std:c11 /I"' + $include + '" "' + `
    $testSource + '" "' + $captureSource + '" "' + $protocolSource + '" /Fe:"' + $exe + '"'
cmd.exe /d /c $compile
if ($LASTEXITCODE -ne 0) { throw "Host C build failed: $LASTEXITCODE" }
& $exe $packet
if ($LASTEXITCODE -ne 0) { throw "Host capture test failed: $LASTEXITCODE" }
Push-Location $repo
try {
    $previousPythonPath = $env:PYTHONPATH
    $env:PYTHONPATH = $repo
    python (Join-Path $root 'tests\verify_frame.py') $packet
    if ($LASTEXITCODE -ne 0) { throw "Python protocol verification failed: $LASTEXITCODE" }
} finally {
    $env:PYTHONPATH = $previousPythonPath
    Pop-Location
}
