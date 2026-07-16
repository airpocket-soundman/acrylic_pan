[CmdletBinding()]
param(
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

$candidates = @(
    'C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp\application\SolistAI_Sim_SLV10004.exe',
    'C:\Program Files (x86)\ROHM\SolistAI_Sim_SLV10004sp\application\SolistAI_Sim_SLV10004.exe'
)

$simulator = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $simulator) {
    throw @'
Solist-AI Sim SLV1.00.04 が見つかりません。
教師あり学習対応版をインストールしてから再実行してください。
既定の検索先: C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp\application
'@
}

$file = Get-Item -LiteralPath $simulator
$runtime = 'C:\Program Files\MATLAB\MATLAB Runtime\R2024a'
if (-not (Test-Path -LiteralPath $runtime)) {
    throw "MATLAB Runtime R2024a が見つかりません: $runtime"
}

Write-Host "Solist-AI Sim: $($file.FullName)"
Write-Host "Version:       $($file.VersionInfo.FileVersion)"
Write-Host "MATLAB Runtime: $runtime"

if ($CheckOnly) {
    Write-Host 'Status:        ready'
    return
}

Start-Process -FilePath $file.FullName -WorkingDirectory $file.DirectoryName
