[CmdletBinding()]
param([switch]$NoBrowser, [switch]$InstallDependencies)

$arguments = @{ Page = "index.html" }
if ($NoBrowser) { $arguments.NoBrowser = $true }
if ($InstallDependencies) { $arguments.InstallDependencies = $true }
& (Join-Path $PSScriptRoot "run-monitor.ps1") @arguments
