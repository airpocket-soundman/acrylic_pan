"""Modal time response and FFT at the single centre accelerometer."""

from __future__ import annotations

import numpy as np


def acceleration_response(
    frequencies_hz: np.ndarray,
    signed_participation: np.ndarray,
    sample_rate_hz: float = 6400.0,
    sample_count: int = 2048,
    damping_ratio: float = 0.012,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return time, acceleration, FFT frequencies and single-sided magnitude.

    ``signed_participation`` has shape (hit, mode) and contains
    phi(hit) * phi(sensor) for a unit Z-direction impulse.  Modal vectors are
    expected to be mass normalized.
    """
    time = np.arange(sample_count) / sample_rate_hz
    omega = 2 * np.pi * np.asarray(frequencies_hz)
    decay = damping_ratio * omega
    damped = omega * np.sqrt(max(1.0 - damping_ratio**2, 1e-12))
    sin_term = np.sin(damped[:, None] * time)
    cos_term = np.cos(damped[:, None] * time)
    envelope = np.exp(-decay[:, None] * time)
    # Second derivative of exp(-a t) sin(b t) / b.
    modal_acceleration = envelope * (
        ((decay**2 - damped**2) / damped)[:, None] * sin_term
        - (2 * decay)[:, None] * cos_term
    )
    acceleration = np.asarray(signed_participation) @ modal_acceleration
    window = np.hanning(sample_count)
    spectrum = np.abs(np.fft.rfft(acceleration * window[None, :], axis=1))
    spectrum *= 2.0 / max(window.sum(), 1e-12)
    fft_frequency = np.fft.rfftfreq(sample_count, 1.0 / sample_rate_hz)
    return time, acceleration, fft_frequency, spectrum


def serializable_sensor_data(
    notes,
    hits,
    frequencies_hz,
    signed_participation,
    model: str,
    sample_rate_hz: float = 6400.0,
    sample_count: int = 2048,
    damping_ratio: float = 0.012,
):
    time, acceleration, fft_frequency, spectrum = acceleration_response(
        np.asarray(frequencies_hz), np.asarray(signed_participation),
        sample_rate_hz, sample_count, damping_ratio,
    )
    wave_scale = max(float(np.max(np.abs(acceleration))), 1e-15)
    fft_scale = max(float(np.max(spectrum)), 1e-15)
    return {
        "model": model,
        "sample_rate_hz": sample_rate_hz,
        "sample_count": sample_count,
        "duration_ms": sample_count / sample_rate_hz * 1000,
        "damping_ratio": damping_ratio,
        "quantity": "centre sensor Z acceleration after unit Z impulse",
        "units": "normalized arbitrary amplitude; common scale across 8 hits",
        "time_ms": np.round(time * 1000, 4).tolist(),
        "frequency_hz": np.round(fft_frequency, 4).tolist(),
        "hits": [
            {
                "note": note, "x_mm": int(hit[0] * 1000), "y_mm": int(hit[1] * 1000),
                "waveform": np.round(wave / wave_scale, 6).tolist(),
                "fft": np.round(spec / fft_scale, 6).tolist(),
                "peak_relative": round(float(np.max(np.abs(wave)) / wave_scale), 6),
            }
            for note, hit, wave, spec in zip(notes, hits, acceleration, spectrum)
        ],
    }
