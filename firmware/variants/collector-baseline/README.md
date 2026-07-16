# Collector baseline snapshot

This is the source snapshot used for the first successful COM3 vibration
capture test, before AI model code was added. It is kept as a regression and
recovery point.

Features: 512 Z-axis samples at 25.6 kHz, forced capture, impact trigger with
128-sample pre-trigger ring buffer, APAN COBS/CRC transport, LCD `test`, and
LED1/LED2/LED3 startup indication.

The maintained installer remains in `../../AcrylicPanCollector/tools`. The
current overlay is a compatible superset, so this snapshot is not copied by
that installer automatically.
