# Firmware variants

- `collector-baseline`: the validated pre-AI collector snapshot. It provides
  UART capture, waveform transfer, LCD `test`, and three-LED startup checks.
- `../AcrylicPanCollector`: the current AI demo overlay. It retains every
  collector command and adds the deterministic eight-class Solist-AI self-test.

The repository stores project-owned overlays rather than redistributing the
vendor sample and libraries. Use each variant with the locally installed ROHM
project as described in `../AcrylicPanCollector/README.md`.
