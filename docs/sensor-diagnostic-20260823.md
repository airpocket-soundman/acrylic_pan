# KX134 sensor diagnostic — 2026-08-23

## Symptom

The PC can exchange APAN control frames with the ML63Q2557 over COM3, but no
impact event or sensor sample frame is received. Both normal inference and
forced collection capture remain armed indefinitely.

## Diagnostic firmware

The current `AcrylicPanCollector_xy_staged` project was copied to the private
local project below and instrumented without changing the normal project.

- Project: `C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanSensorDiag`
- Diagnostic HEX: `Debug\AIVibrationInference.hex`
- Diagnostic HEX SHA-256: `4EF43F160CEE9EC6F243F6562EDFA69BD147B5614F85486CDF6B142264960974`
- Normal HEX SHA-256: `56B80C41C96DB4A9E66F8CF0086B045E1E20FDD1DACFD7453800B3A58614973F`

The diagnostic STATUS extension reports configuration write/read failures,
KX134 `WHO_AM_I`, register readbacks, DRDY count, raw sample count, the last raw
sample, completed sensor blocks, and relevant GPIO states.

## Results

Software configuration writes and readbacks all succeeded:

- sensor selection: MEMS (`1`)
- axis: Z (`2`)
- sample-rate code: `15` (25.6 kHz)
- block size: 512 samples
- LPF: enabled (`1`)
- real-time vendor transfer: disabled (`0`)
- configuration write failure mask: `0x00`
- configuration read failure mask: `0x00`

After forced capture and one second of acquisition time:

- sensor-running flag: `1`
- expected `WHO_AM_I`: `0x46`
- measured `WHO_AM_I`: `0xFF`
- CNTL1 readback: `0xFF`
- ODCNTL readback: `0xFF`
- INC1 readback: `0xFF`
- INC4 readback: `0xFF`
- DRDY interrupts: `0`
- raw samples: `0`
- completed 512-sample blocks: `0`
- P2 input: `0xFF` (DRDY/P2.2 remains high)
- P4 input: `0xEE`
- P4 output: `0x60` (5 V regulator enable/P4.6 is high)

Power-cycling the board and toggling the MCU's 5 V regulator enable output
OFF→ON did not change the result.

## Conclusion

The application configuration layer and UART protocol work. SPI transfers
complete at the MCU, but every tested KX134 register reads `0xFF`, the DRDY line
never produces an edge, and no sample is captured. This is characteristic of a
sensor that is not driving MISO. The likely causes are, in order to check:

1. missing sensor-board supply or ground;
2. loose, reversed, or open sensor/SPI connector or cable;
3. open CS, SCLK, or MISO trace;
4. failed KX134 or sensor board.

The regulator control output being high does not prove that the supply voltage
is present at the sensor. Measure the sensor-board supply at the connector and
at the device, then verify ground and cable continuity. If supply and continuity
are correct, replacement of the KX134 board/device is justified.

Keep the diagnostic firmware installed until the connector, supply, or sensor
has been serviced. A successful repair must return `WHO_AM_I=0x46`, increasing
DRDY/raw-sample counters, and non-constant samples around the static-gravity
level. After confirmation, restore the normal HEX from
`AcrylicPanCollector_xy_staged` using `scripts/flash-firmware.ps1`.
