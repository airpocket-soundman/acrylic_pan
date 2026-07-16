"""Signal processing used only for PC-side visualization."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .protocol import EventData


@dataclass(frozen=True)
class EventPlotData:
    time_ms: np.ndarray
    samples: np.ndarray
    frequency_hz: np.ndarray
    magnitude_db: np.ndarray
    trigger_time_ms: float


def prepare_plot_data(event: EventData) -> EventPlotData:
    if event.sample_rate_hz <= 0:
        raise ValueError("sample rate must be positive")
    samples = np.asarray(event.samples, dtype=np.float64)
    centered = samples - samples.mean()
    window = np.hanning(len(samples))
    spectrum = np.fft.rfft(centered * window)
    coherent_gain = max(window.sum() / 2.0, 1.0)
    magnitude = np.abs(spectrum) / coherent_gain
    magnitude_db = 20.0 * np.log10(np.maximum(magnitude, 1e-9))
    time_ms = np.arange(len(samples), dtype=np.float64) * 1000.0 / event.sample_rate_hz
    frequency_hz = np.fft.rfftfreq(len(samples), 1.0 / event.sample_rate_hz)
    return EventPlotData(
        time_ms,
        samples,
        frequency_hz,
        magnitude_db,
        event.trigger_index * 1000.0 / event.sample_rate_hz,
    )
