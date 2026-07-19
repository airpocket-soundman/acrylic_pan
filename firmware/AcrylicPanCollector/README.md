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

Live inference first sends a compact `AI_RESULT` (`0x21`, case `0xFF`) so an
instrument client receives the class and eight scores without waiting for the
waveform. `INFERENCE_EVENT` (`0x22`) follows with the event metadata, the same
predicted class and scores, and the exact 512 raw Z-axis samples used by the
model. Instrument mode (`mode=2`) sends only the compact priority result and
rearms immediately, trading waveform/FFT telemetry for the shortest repeat-hit
interval. The normal inference page continues to use `mode=1` and retains the
waveform.

In instrument mode the firmware owns the retrigger guard. `SET_CONFIG` (`0x12`)
accepts a little-endian uint16 interval from 0 through 500 ms. Impacts inside
that interval are rearmed without emitting `AI_RESULT` and without changing the
LCD or LEDs. Consequently every result delivered to the browser is one audible
event, and the browser area, sound, LCD and three-bit LED display stay on the
same accepted hit.

After each live inference the board LCD shows the classified area center and
the measured model inference time. The first line is `X050 Y050 AREA1` style
(millimetres on the 400 x 200 mm panel); the second is `INFER 012.34ms` style.
The current 8-class model does not regress coordinates, so X/Y are the center
coordinates of the selected area. LCD drawing is deferred until the compact AI
result has left the UART, preserving sound-response latency.

The three board LEDs show the inferred area as a three-bit value. The physical
left-to-right order is LED1 (MSB), LED2, LED3 (LSB); the zero-based model
class is displayed, so area 1 is `000`, area 2 is `001`, through area 8 as
`111`. The startup self-test still lights all three LEDs until the first live
inference result replaces that pattern.

The PC derives the display FFT from those
samples, so the highlighted area, waveform, and spectrum always belong to the
same impact. FFT is display-only and is not an input to the time128 v1 model.

The completed COM3 qualification result is documented in
`../../docs/ai-dummy-validation.md`.

## Capture behaviour

- KX134 Z-axis at 25,600 samples/s, 32 g range (1024 LSB/g)
- continuous 512-sample vendor double buffers
- jerk detection continues across block boundaries
- 64 samples (2.5 ms) of circular pre-trigger history
- collection mode: 2,048 samples (80 ms), with 1,983 samples after the trigger
- inference mode: the deployed v1 model retains 512 samples (20 ms)
- sensor is stopped only after the complete event exists
- APAN version 1 `EVENT_CHUNK`, CRC32 and COBS framing at 115,200 bit/s;
  a 2,048-sample collection is sent as four independently checked 512-sample chunks
- default raw-count thresholds: jerk 1000, absolute level 200. A physically
  scaled jerk value of 500 caused a stationary false trigger because raw-count
  noise did not fall in proportion to sensitivity. A 1000-LSB threshold keeps
  margin above the observed 760-LSB maximum stationary adjacent difference;
  the target board then completed a 30-second armed wait with zero triggers.

UART1 accepts newline-terminated ASCII commands at 115200 8-N-1:

- `PING` returns `PONG APAN/1`.
- `STATUS` returns `STATUS IDLE`, `STATUS ARMED`, or `STATUS TX`.
- `CAPTURE` and `GET_STATIC` return `ACK CAPTURE`, then 2,048 Z samples as four
  binary APAN `EVENT_CHUNK` frames (trigger index 0) in collection mode.
- unknown commands return `NACK UNKNOWN`.

Command replies are ASCII for easy terminal diagnosis. Waveforms always use the
CRC-protected binary framing consumed by the PC monitor.

The same operations are available as APAN binary requests: `HELLO` 0x01,
`STATUS` 0x02, `START` 0x10, `STOP` 0x11, `CAPTURE` 0x13, `AI_SELFTEST` 0x14,
and `SET_MODE` 0x15. `SET_MODE` payload 0 selects collection, 1 selects normal
inference, and 2 selects low-latency instrument inference. Mode changes are
accepted only while stopped. `START` arms impact
capture and then sends `EVENT_DATA` in collection mode or `INFERENCE_EVENT` in
inference mode. `CAPTURE` is accepted only in collection mode. Replies retain the
request sequence. `ACK` 0x70 contains the one-byte request type; `NACK` 0x71
contains request type and reason (1 busy, 2 unsupported, 3 invalid). Binary
`STATUS` payload is little-endian `<BBHHIB>`: state, flags, sample count,
trigger index, sample rate, and operating mode. States are 0 idle, 1
forced-capture armed, 2 transmit, and 3 stopped.

`STATUS.flags` reports the startup UI self-test: bit 0 LCD `test` draw success,
bits 1, 2, and 3 are the actual LED1, LED2, and LED3 output states. A successful
startup therefore returns `flags = 0x0f`. The 5 V regulator and LCD backlight
are enabled before drawing `test` on the first line.

The PC decoder is `pc/acrylic_pan_monitor/protocol.py`.

The collector boots stopped. `CAPTURE` starts exactly one sensor block and
returns to stopped state after queuing `EVENT_DATA`, so repeated PC-controlled
measurements are deterministic. `START` arms the next impact capture.
Each transition from stopped to `START` clears stale history and primes a new
64-sample ring history.  The PC must wait for the complete `EVENT_DATA` frame
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
