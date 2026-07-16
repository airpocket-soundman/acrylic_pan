# AcrylicPanCollector firmware overlay

This directory contains project-owned collection logic only. The installed
`AIVibrationInference` sample contains DATA TECNO and ROHM copyrighted files
without one project-wide redistribution licence, so those files are not copied
into this repository.

## Preserved variants

The original capture-only source snapshot is preserved under
`../variants/collector-baseline`. This directory is the AI demo variant: it
retains all capture commands and adds a fixed eight-class inference test.

`AI_SELFTEST` (`0x14`) accepts one test-case byte from 0 through 7.
`AI_RESULT` (`0x21`) returns `<BBH8f`: case number, CPU-side argmax class,
reserved zero, and all eight raw Solist-AI output scores. The embedded model is
128 inputs, 32 hidden nodes, and 8 outputs with hard sigmoid/MSE. Its alpha was
captured from the official Solist-AI Simulator seed-1 model; alpha, beta, and
the eight qualification inputs are all stored as bfloat16.

The completed COM3 qualification result is documented in
`../../docs/ai-dummy-validation.md`.

## Capture behaviour

- KX134 Z-axis at 25,600 samples/s
- continuous 512-sample vendor double buffers
- jerk detection continues across block boundaries
- 128 samples (5 ms) of circular pre-trigger history
- trigger sample plus 383 later samples, giving 512 samples (20 ms) total
- sensor is stopped only after the complete event exists
- APAN version 1 `EVENT_DATA`, CRC32 and COBS framing at 115,200 bit/s
- default raw-count thresholds: jerk 2000, absolute level 800.  The jerk
  threshold includes margin above the 1351-LSB maximum adjacent difference
  measured during a ten-event stationary-board false-trigger investigation.

UART1 accepts newline-terminated ASCII commands at 115200 8-N-1:

- `PING` returns `PONG APAN/1`.
- `STATUS` returns `STATUS IDLE`, `STATUS ARMED`, or `STATUS TX`.
- `CAPTURE` and `GET_STATIC` return `ACK CAPTURE`, then the next complete
  512-sample Z block as a binary APAN `EVENT_DATA` frame (trigger index 0).
- unknown commands return `NACK UNKNOWN`.

Command replies are ASCII for easy terminal diagnosis. Waveforms always use the
CRC-protected binary framing consumed by the PC monitor.

The same operations are available as APAN binary requests: `HELLO` 0x01,
`STATUS` 0x02, `START` 0x10, `STOP` 0x11, and `CAPTURE` 0x13. Replies retain the
request sequence. `ACK` 0x70 contains the one-byte request type; `NACK` 0x71
contains request type and reason (1 busy, 2 unsupported, 3 invalid). Binary
`STATUS` payload is little-endian `<BBHHI>`: state, flags, sample count, trigger
index, and sample rate. States are 0 idle, 1 forced-capture armed, 2 transmit,
and 3 stopped.

`STATUS.flags` reports the startup UI self-test: bit 0 LCD `test` draw success,
bits 1, 2, and 3 are the actual LED1, LED2, and LED3 output states. A successful
startup therefore returns `flags = 0x0f`. The 5 V regulator and LCD backlight
are enabled before drawing `test` on the first line.

The PC decoder is `pc/acrylic_pan_monitor/protocol.py`.

The collector boots stopped. `CAPTURE` starts exactly one sensor block and
returns to stopped state after queuing `EVENT_DATA`, so repeated PC-controlled
measurements are deterministic. `START` arms the next impact capture.
Each transition from stopped to `START` clears stale history and primes a new
128-sample ring history.  The PC must wait for the complete `EVENT_DATA` frame
to be decoded and saved before sending the next `START`.

Run `tools/test-host.ps1` to exercise a trigger exactly on a 512-sample block
boundary and decode the C-produced packet with the Python PC implementation.

## Create a private LEXIDE project

Run from PowerShell:

```powershell
.\firmware\AcrylicPanCollector\tools\install-overlay.ps1
.\firmware\AcrylicPanCollector\tools\build-private-project.ps1
```

The installer clones the locally installed sample into
`C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_private`, replaces only
the clone's main entry point, adds `S_AcrylicPan`, and selects UART 115200. It
refuses to overwrite an existing destination and never edits the original
`AIVibrationInference` project.

The build script does not start Eclipse or Java. It invokes the LEXIDE-provided
`make.exe` and compiler tools directly using a private copy of the generated
make metadata. Do not run `make clean`: LEXIDE treats its `.res` compiler option
files as generated outputs but a plain make invocation cannot recreate them.
The resulting image is
`...\AcrylicPanCollector_private\Debug\AIVibrationInference.hex` (the retained artifact
name comes from the vendor build metadata).

The repository-level `scripts/flash-firmware.ps1` programs the image through
MCU-Link CMSIS-DAP with LEXIDE's ROHM-enabled `openocd_arm.exe`. It creates a
flash-only binary from the ELF, erases, programs, verifies every flash byte,
then resets and runs the target. `tools/flash-pyocd.ps1` is an alternative.

## Current transport limitation

One encoded event is about 1.06 kB, or roughly 93 ms at 115200 8-N-1. Sampling
is paused while that frame is transmitted. This preserves the selected event
but deliberately does not promise capture of impacts occurring during UART
transmission. A later high-baud-rate or queued transport milestone can remove
that dead time.
