[CmdletBinding()]
param([switch]$FakeBoard, [int]$Port = 8765)

# Exhibit pages on this PC.  -FakeBoard runs without the sensor board (strikes are simulated for the learning page).
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
if ($FakeBoard) {
    & $python (Join-Path $PSScriptRoot "exhibit_fake_server.py") --port $Port
} else {
    & $python -m pc.acrylic_pan_web --port $Port --auto-connect --page exhibit/play.html
}
