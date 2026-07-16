[CmdletBinding()]
param(
    [string]$FirmwareProject = "C:\Users\yamas\lexide\workspace_omega_v2\AIVibrationInference"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$makeExe = "C:\LAPIS\LEXIDE\Utilities\Bin\make.exe"

function Find-Executable {
    param([string[]]$Candidates)
    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    return $null
}

$python = Find-Executable @(
    (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    (Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1)
)
$versionedJFlash = Get-ChildItem -Path "$env:ProgramFiles\SEGGER\JLink*\JFlash.exe" -File -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName -First 1
$jFlash = Find-Executable @(
    "$env:ProgramFiles\SEGGER\JLink\JFlash.exe",
    "${env:ProgramFiles(x86)}\SEGGER\JLink\JFlash.exe",
    $versionedJFlash
)
$uv4 = Find-Executable @(
    "${env:ProgramFiles(x86)}\Keil_v5\UV4\UV4.exe",
    "$env:ProgramFiles\Keil_v5\UV4\UV4.exe"
)

$checks = @(
    [pscustomobject]@{ Item = "Repository"; Ready = Test-Path -LiteralPath $repoRoot; Path = $repoRoot },
    [pscustomobject]@{ Item = "Python"; Ready = $null -ne $python; Path = $python },
    [pscustomobject]@{ Item = "LEXIDE make"; Ready = Test-Path -LiteralPath $makeExe; Path = $makeExe },
    [pscustomobject]@{ Item = "Firmware source"; Ready = Test-Path -LiteralPath $FirmwareProject; Path = $FirmwareProject },
    [pscustomobject]@{ Item = "J-Flash CLI"; Ready = $null -ne $jFlash; Path = $jFlash },
    [pscustomobject]@{ Item = "Keil uVision"; Ready = $null -ne $uv4; Path = $uv4 }
)
$checks | Format-Table -AutoSize

if ($python) {
    & $python -c "import numpy, serial; print('PC monitor dependencies: OK')"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "PC monitor packages are missing. Run scripts\run-monitor.ps1 -InstallDependencies."
    }
}

if (-not $jFlash -and -not $uv4) {
    Write-Warning "No flashing CLI was detected. Building is available, but command-line flashing is not ready."
}
