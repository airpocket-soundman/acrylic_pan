# Dummy Solist-AI model validation

Validated on 2026-07-16 before collecting real acrylic-panel data.

## Model

- topology: 128 inputs, 32 hidden nodes, 8 outputs
- activation/loss: hard sigmoid / MSE
- output decision: eight raw scores from Solist-AI, followed by CPU argmax
- model data: bfloat16 alpha and beta
- test data: eight deterministic synthetic cases, one per class

The alpha matrix is the first 128 rows of the seed-1 matrix previously
captured from ROHM Solist-AI Simulator SLV1.00.04. The current run did not
automate the official Simulator GUI; its PC golden values are a reproducible
BF16 reference calculation using that official-Simulator alpha. This
distinction is intentional and must be retained in later reports.

## Hardware result

- board UART: COM3 at 115200 bit/s
- programmer: MCU-Link CMSIS-DAP
- firmware size: text 32,330 / data 1,304 / bss 8,104 bytes
- class result: 8/8 matched
- score values compared: 64/64
- maximum absolute score difference: 0.03125
- acceptance limit: 0.035 absolute, 5% relative

The PC reference quantizes layer boundaries, while the ML63Q25x7 accelerator
also rounds within its multiply-accumulate path. Therefore exact bit equality
is not expected; class agreement plus the bounded score error is the smoke-test
criterion.

Machine-readable results are in `../data/dummy_model/board_comparison.json`.

## Interfaces

- request `AI_SELFTEST` (`0x14`), payload: one case number from 0 to 7
- response `AI_RESULT` (`0x21`), payload: little-endian `<BBH8f`
- response fields: case number, argmax class, reserved zero, eight raw scores

The PC AI demo also loads the exact 128-element normalized input for the
returned case from `golden_outputs.json`. It displays that synthetic input as
a waveform and shows its DC-removed, Hann-windowed FFT. The graph is explicitly
labelled as normalized dummy-model input, not a physical accelerometer waveform.

## Applications retained

- AI demo: `scripts/run-ai-demo.ps1`, page `/`
- initial vibration collector: `scripts/run-collector-monitor.ps1`, page
  `/collector.html`
- original collector firmware snapshot:
  `firmware/variants/collector-baseline`
- AI demo firmware overlay: `firmware/AcrylicPanCollector`
