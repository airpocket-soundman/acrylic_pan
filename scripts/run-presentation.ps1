[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$Port = 8780,
    [switch]$NoBrowser
)

# Serves web/ over HTTP. The main and presenter windows must share one origin to stay in sync,
# so open the deck through this server rather than as file:// pages.
$ErrorActionPreference = "Stop"
$root = Join-Path (Split-Path -Parent $PSScriptRoot) "web"
$url = "http://127.0.0.1:$Port/presentation/index.html"
Write-Host "Presentation: $url"
Write-Host "Live demo   : start the instrument server (scripts\run-monitor.ps1) on port 8765 first."
if (-not $NoBrowser) { Start-Process $url }
& $Python (Join-Path $PSScriptRoot "serve_presentation.py") --port $Port --bind 127.0.0.1 --directory $root
