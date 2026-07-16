param(
    [string]$VendorProject = 'C:\Users\yamas\lexide\workspace_omega_v2\AIVibrationInference',
    [string]$Destination = 'C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_private'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$vendorResolved = (Resolve-Path -LiteralPath $VendorProject).Path
$destinationFull = [IO.Path]::GetFullPath($Destination)

if ($vendorResolved.TrimEnd('\') -eq $destinationFull.TrimEnd('\')) {
    throw 'Destination must not be the original vendor project.'
}
if (Test-Path -LiteralPath $destinationFull) {
    throw "Destination already exists: $destinationFull"
}

Copy-Item -LiteralPath $vendorResolved -Destination $destinationFull -Recurse

$projectFile = Join-Path $destinationFull '.project'
$cprojectFile = Join-Path $destinationFull '.cproject'
foreach ($file in @($projectFile, $cprojectFile)) {
    $text = [IO.File]::ReadAllText($file)
    $text = $text.Replace('AIVibrationInference', 'AcrylicPanCollector')
    [IO.File]::WriteAllText($file, $text, [Text.UTF8Encoding]::new($false))
}

$overlay = Join-Path $destinationFull 'S_AcrylicPan'
New-Item -ItemType Directory -Path $overlay | Out-Null
Copy-Item -Path (Join-Path $root 'include\*.h') -Destination $overlay
Copy-Item -Path (Join-Path $root 'generated\*.h') -Destination $overlay
Copy-Item -Path (Join-Path $root 'src\*.c') -Destination $overlay
Copy-Item -Path (Join-Path $root 'integration\apan_collector_app.c') -Destination $overlay
Copy-Item -LiteralPath (Join-Path $root 'integration\main_collector.c') `
    -Destination (Join-Path $destinationFull 'S_System\main.c') -Force

$uartFile = Join-Path $destinationFull 'S_Uartf\Uart1.c'
$uart = [IO.File]::ReadAllText($uartFile)
$uart = $uart -replace '(?m)^//#define UARTF1_PARAM_DLR\s*\( 0x0019U \)', '#define UARTF1_PARAM_DLR ( 0x0019U )'
$uart = $uart -replace '(?m)^#define UARTF1_PARAM_DLR\s*\( 0x012CU \)', '//#define UARTF1_PARAM_DLR ( 0x012CU )'
# The vendor driver waits for a TEMTI interrupt after the final byte is handed
# to the UART.  That interrupt is not generated reliably by this board for long
# frames.  Complete once the FIFO has accepted the final byte; a PC cannot issue
# the next request until it has received the complete delimited frame.
$completion = @'
			set_reg32( UARTF1->UAF0IER, UARTF_TEMTI_ENA);
			if( writeCtrlParam.callBack != NULL ){
				writeCtrlParam.callBack( writeCtrlParam.cnt, writeCtrlParam.errStat );
				writeCtrlParam.callBack = NULL;
			}
'@
$uart = $uart.Replace("`t`t`tset_reg32( UARTF1->UAF0IER, UARTF_TEMTI_ENA);", $completion.TrimEnd())
$finalByte = @'
		writeSingleData( &writeCtrlParam );
		if( writeCtrlParam.size == writeCtrlParam.cnt ){
			clear_reg32( UARTF1->UAF0IER, UARTF_ETBEI_ENA);
			if( writeCtrlParam.callBack != NULL ){
				writeCtrlParam.callBack( writeCtrlParam.cnt, writeCtrlParam.errStat );
				writeCtrlParam.callBack = NULL;
			}
			ret = (int32_t)( UARTF_R_TRANS_FIN );
		}
'@
$uart = $uart.Replace("`t`twriteSingleData( &writeCtrlParam );", $finalByte.TrimEnd())
[IO.File]::WriteAllText($uartFile, $uart, [Text.UTF8Encoding]::new($false))

# Reuse LEXIDE's generated make metadata so CLI builds do not start Eclipse/JRE.
$debug = Join-Path $destinationFull 'Debug'
$oldForward = $vendorResolved.Replace('\', '/')
$newForward = $destinationFull.Replace('\', '/')
Get-ChildItem -LiteralPath $debug -Recurse -File | Where-Object {
    $_.Extension -in @('.mk', '.res')
} | ForEach-Object {
    $text = [IO.File]::ReadAllText($_.FullName)
    $text = $text.Replace($oldForward, $newForward).Replace($vendorResolved, $destinationFull)
    [IO.File]::WriteAllText($_.FullName, $text, [Text.UTF8Encoding]::new($false))
}

$makefile = Join-Path $debug 'makefile'
$makeText = [IO.File]::ReadAllText($makefile)
$makeText = $makeText.Replace($oldForward, $newForward).Replace($vendorResolved, $destinationFull)
$makeText = $makeText.Replace('-include S_AI/subdir.mk', "-include S_AI/subdir.mk`r`n-include S_AcrylicPan/subdir.mk")
[IO.File]::WriteAllText($makefile, $makeText, [Text.UTF8Encoding]::new($false))

$debugOverlay = Join-Path $debug 'S_AcrylicPan'
New-Item -ItemType Directory -Path $debugOverlay | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'S_AcrylicPan.subdir.mk') `
    -Destination (Join-Path $debugOverlay 'subdir.mk')

$baseResponse = [IO.File]::ReadAllText((Join-Path $debug 'S_System\main.res'))
$includeOption = '-I"' + $newForward + '/S_AcrylicPan" '
foreach ($name in @('apan_capture', 'apan_protocol', 'apan_ai_selftest', 'apan_collector_app')) {
    $response = $baseResponse.Replace('[output_dir]"./S_System/"', '[output_dir]"./S_AcrylicPan/"')
    $response = $response.Replace('[output_filename]"main.asm"', ('[output_filename]"' + $name + '.asm"'))
    $response = $response.Replace('[file_c]"../S_System/main.c"', ('[file_c]"../S_AcrylicPan/' + $name + '.c"'))
    $response = $response.Replace('[option_lccarm]', ('[option_lccarm]' + $includeOption))
    [IO.File]::WriteAllText((Join-Path $debugOverlay ($name + '.res')), $response, [Text.UTF8Encoding]::new($false))
}

$mainResponseFile = Join-Path $debug 'S_System\main.res'
$mainResponse = [IO.File]::ReadAllText($mainResponseFile).Replace('[option_lccarm]', ('[option_lccarm]' + $includeOption))
[IO.File]::WriteAllText($mainResponseFile, $mainResponse, [Text.UTF8Encoding]::new($false))

Write-Host "Created private integration project: $destinationFull"
Write-Host 'The original vendor project was not modified.'
