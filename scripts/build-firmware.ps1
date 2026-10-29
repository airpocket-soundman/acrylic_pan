[CmdletBinding()]
param(
    [string]$SourceProject = "C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_lowlatency",
    [string]$Configuration = "Debug",
    [string]$StagingRoot,
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$makeExe = "C:\LAPIS\LEXIDE\Utilities\Bin\make.exe"

if (-not (Test-Path -LiteralPath $SourceProject -PathType Container)) {
    throw "LEXIDE project not found: $SourceProject"
}
if (-not (Test-Path -LiteralPath $makeExe -PathType Leaf)) {
    throw "LEXIDE make.exe not found: $makeExe"
}
if (-not $StagingRoot) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $StagingRoot = Join-Path $repoRoot ".local\firmware-build\$stamp"
}
$stagingFull = [System.IO.Path]::GetFullPath($StagingRoot)
if (Test-Path -LiteralPath $stagingFull) {
    throw "Staging path already exists. Select a new empty path: $stagingFull"
}

$projectName = Split-Path -Leaf $SourceProject
$stagedProject = Join-Path $stagingFull $projectName
New-Item -ItemType Directory -Path $stagedProject -Force | Out-Null
Write-Host "Creating a working copy so the source project is not modified."
Get-ChildItem -LiteralPath $SourceProject -Force | Copy-Item -Destination $stagedProject -Recurse -Force

# Refresh the repository-owned overlay in the disposable build copy.  This
# keeps an existing private LEXIDE project usable after new overlay sources or
# generated models are added, without mutating the saved project itself.
$stagedOverlay = Join-Path $stagedProject "S_AcrylicPan"
if (-not (Test-Path -LiteralPath $stagedOverlay -PathType Container)) {
    throw "The source project does not contain the Acrylic Pan overlay: $SourceProject. Use AcrylicPanCollector_lowlatency or create a private integration project with install-overlay.ps1."
}
$debugOverlay = Join-Path $stagedProject "$Configuration\S_AcrylicPan"
if (-not (Test-Path -LiteralPath $debugOverlay -PathType Container)) {
    throw "The source project has no generated $Configuration overlay metadata: $debugOverlay"
}

Copy-Item -Path (Join-Path $repoRoot "firmware\AcrylicPanCollector\include\*.h") -Destination $stagedOverlay -Force
Copy-Item -Path (Join-Path $repoRoot "firmware\AcrylicPanCollector\generated\*.h") -Destination $stagedOverlay -Force
Copy-Item -Path (Join-Path $repoRoot "firmware\AcrylicPanCollector\src\*.c") -Destination $stagedOverlay -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "firmware\AcrylicPanCollector\integration\apan_collector_app.c") -Destination $stagedOverlay -Force
# Always replace the vendor state-machine entry point.  Building the vendor
# main by mistake makes PSW3 control the LCD backlight and never starts the
# Acrylic Pan UART/inference application, even though its objects still link.
Copy-Item -LiteralPath (Join-Path $repoRoot "firmware\AcrylicPanCollector\integration\main_collector.c") `
    -Destination (Join-Path $stagedProject "S_System\main.c") -Force
Copy-Item -LiteralPath (Join-Path $repoRoot "firmware\AcrylicPanCollector\tools\S_AcrylicPan.subdir.mk") -Destination (Join-Path $debugOverlay "subdir.mk") -Force

# Projects created before an overlay source was added lack its compiler
# response file.  Derive it from apan_inference.res, which has the same options.
function Add-MissingOverlayResponses {
    $templateResponse = Join-Path $debugOverlay "apan_inference.res"
    Get-ChildItem -LiteralPath $stagedOverlay -Filter "*.c" | ForEach-Object {
        $response = Join-Path $debugOverlay ($_.BaseName + ".res")
        if (-not (Test-Path -LiteralPath $response)) {
            $responseText = [IO.File]::ReadAllText($templateResponse)
            $responseText = $responseText.Replace('apan_inference.asm', $_.BaseName + '.asm')
            $responseText = $responseText.Replace('apan_inference.c', $_.Name)
            [IO.File]::WriteAllText($response, $responseText, [Text.UTF8Encoding]::new($false))
        }
    }
}
Add-MissingOverlayResponses

$buildDir = Join-Path $stagedProject $Configuration
$makefile = Join-Path $buildDir "makefile"
if (-not (Test-Path -LiteralPath $makefile -PathType Leaf)) {
    throw "Generated makefile not found: $makefile. Generate the $Configuration build once in LEXIDE."
}

# Use the newest installed Build Tools and point the generated build files at it,
# so projects created with an older tools version still build.
$toolsVersion = Get-ChildItem -LiteralPath "C:\LAPIS\LEXIDE\BuildTools" -Directory -Filter "Ver.*" |
    Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty Name
if (-not $toolsVersion) { throw "LEXIDE Build Tools not found under C:\LAPIS\LEXIDE\BuildTools." }
Get-ChildItem -LiteralPath $buildDir -Recurse -File | Where-Object {
    $_.Extension -in @(".res", ".mk") -or $_.Name -eq "makefile"
} | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName)
    $updated = [regex]::Replace($text, "BuildTools/Ver\.\d+", "BuildTools/$toolsVersion")
    if ($updated -ne $text) { [IO.File]::WriteAllText($_.FullName, $updated, [Text.UTF8Encoding]::new($false)) }
}

$lexidePaths = @(
    "C:\LAPIS\LEXIDE\Bin",
    "C:\LAPIS\LEXIDE\BuildTools\$toolsVersion\Bin",
    "C:\LAPIS\LEXIDE\Utilities\Bin"
)
$env:Path = ($lexidePaths -join ";") + ";" + $env:Path

Push-Location $buildDir
try {
    if ($Clean) {
        & $makeExe clean
        if ($LASTEXITCODE -ne 0) { throw "make clean failed with exit code $LASTEXITCODE." }
        # LEXIDE's generated clean target also removes compiler response files,
        # although its make rules cannot regenerate them outside Eclipse.
        # Restore those immutable build settings from the untouched source
        # project before compiling the disposable copy.
        $sourceBuildDir = Join-Path $SourceProject $Configuration
        Get-ChildItem -LiteralPath $sourceBuildDir -Filter "*.res" -Recurse | ForEach-Object {
            $relative = $_.FullName.Substring($sourceBuildDir.Length).TrimStart('\')
            $destination = Join-Path $buildDir $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
        }
        Add-MissingOverlayResponses
    }
    # make prints a harmless DOSKEY complaint on stderr; judge success by the exit code.
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $makeExe all -j 2>&1 | Where-Object { $_ -notmatch "DOSKEY" }
    $makeExit = $LASTEXITCODE
    $ErrorActionPreference = $previousPreference
    if ($makeExit -ne 0) { throw "Firmware build failed with exit code $makeExit." }
} finally {
    Pop-Location
}

$hex = Get-ChildItem -LiteralPath $buildDir -Filter "*.hex" | Select-Object -First 1
if (-not $hex) { throw "Build completed but no HEX file was found in $buildDir." }
Write-Host "Build succeeded: $($hex.FullName)"
