"""Estimate 400 x 300 position accuracy after an MPU-9250-like conversion.

This is an optimistic offline sensor-substitution experiment.  It applies the
MPU-9250 accelerometer's widest normal-mode bandwidth and output rate to the
recorded KX134 waveforms, then retrains on the converted data.  It cannot
reproduce package/mount transfer functions, clock jitter, or real sensor noise.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt
from sklearn.exceptions import ConvergenceWarning
from sklearn.preprocessing import StandardScaler

from .pc_position_grid_runtime import (
    DEFAULT_SESSION_IDS,
    GRID_SESSION_IDS,
    PANEL_HEIGHT_MM,
    PANEL_WIDTH_MM,
    SAMPLE_RATE_HZ,
    SOURCE_SAMPLE_COUNT,
    _balanced_density_indices,
    _calibrate_temperature,
    _density_model,
    _fit_predict,
    _temperature_scaled,
    density_metrics,
    load_position_dataset,
)
from .sampling_experiment import regression_metrics

MPU9250_RATE_HZ = 4_000
MPU9250_BANDWIDTH_HZ = 1_130.0
MPU9250_RANGE_G = 16.0
KX134_COUNTS_PER_G = 1_024.0  # Recorded source sessions used the +/-32 g range.
MPU9250_COUNTS_PER_G = 2_048.0
MPU9250_TRIGGER_INDEX = 10  # 64 / 25.6 kHz = 10 / 4 kHz = 2.5 ms.


def _event_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for session_id in DEFAULT_SESSION_IDS:
        directory = Path(root).resolve() / session_id
        rows = [
            json.loads(line)
            for line in (directory / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        paths.extend(directory / str(row["file"]) for row in rows)
    return paths


def load_source_waveforms(root: Path) -> np.ndarray:
    rows: list[np.ndarray] = []
    for path in _event_paths(root):
        with np.load(path, allow_pickle=False) as event:
            waveform = np.asarray(event["samples"], dtype=np.float32)
        if waveform.shape != (SOURCE_SAMPLE_COUNT,):
            raise ValueError(f"{path}: expected {SOURCE_SAMPLE_COUNT} samples")
        rows.append(waveform)
    return np.stack(rows)


def convert_to_mpu9250(source: np.ndarray, batch_size: int = 128) -> tuple[np.ndarray, dict]:
    """Low-pass, resample, saturate, and quantize KX134 counts as MPU-9250 counts."""
    values = np.asarray(source, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != SOURCE_SAMPLE_COUNT:
        raise ValueError("source must have shape (events, 2048)")
    sos = butter(4, MPU9250_BANDWIDTH_HZ, btype="lowpass", fs=SAMPLE_RATE_HZ, output="sos")
    converted: list[np.ndarray] = []
    clipped_events = 0
    clipped_samples = 0
    total_samples = 0
    peak_g: list[np.ndarray] = []
    positive_limit_g = np.iinfo(np.int16).max / MPU9250_COUNTS_PER_G
    for start in range(0, len(values), batch_size):
        batch_g = values[start:start + batch_size].astype(np.float64) / KX134_COUNTS_PER_G
        bandwidth_limited = sosfiltfilt(sos, batch_g, axis=1, padtype="odd")
        sampled_g = resample_poly(
            bandwidth_limited, up=5, down=32, axis=1,
            window=("kaiser", 8.0), padtype="line",
        )
        if sampled_g.shape[1] != 320:
            raise RuntimeError("MPU-9250 resampler did not return 320 samples")
        magnitude = np.abs(sampled_g)
        peak_g.append(np.max(magnitude, axis=1))
        clipped_events += int(np.sum(np.any(magnitude >= MPU9250_RANGE_G, axis=1)))
        clipped_samples += int(np.sum(magnitude >= MPU9250_RANGE_G))
        total_samples += int(sampled_g.size)
        quantized = np.rint(
            np.clip(sampled_g, -MPU9250_RANGE_G, positive_limit_g)
            * MPU9250_COUNTS_PER_G
        ).astype(np.int16)
        converted.append(quantized)
    peaks = np.concatenate(peak_g)
    return np.concatenate(converted), {
        "event_count": int(len(values)),
        "clipped_event_count": clipped_events,
        "clipped_event_fraction": float(clipped_events / len(values)),
        "clipped_sample_count": clipped_samples,
        "clipped_sample_fraction": float(clipped_samples / total_samples),
        "preclip_peak_g_percentiles": {
            key: float(np.percentile(peaks, percentile))
            for key, percentile in (("p50", 50), ("p90", 90), ("p95", 95), ("p99", 99), ("max", 100))
        },
    }


def extract_rich_features(samples: np.ndarray, trigger_index: int = MPU9250_TRIGGER_INDEX) -> np.ndarray:
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.ndim != 1 or not 1 <= trigger_index < len(waveform):
        raise ValueError("invalid MPU-9250 waveform")
    baseline = float(np.mean(waveform[:trigger_index]))
    centered = waveform - baseline
    post = centered[trigger_index:]
    peak = max(float(np.max(np.abs(post))), 1.0)
    rms = max(float(np.sqrt(np.mean(post ** 2))), 1.0)
    normalized_time = post / peak
    spectrum = np.abs(np.fft.rfft(centered * np.hanning(len(centered))))[1:]
    normalized_spectrum = np.log1p(spectrum / peak)
    absolute = np.abs(post)
    energy = absolute ** 2
    quarter_energy = np.asarray([np.sum(part) for part in np.array_split(energy, 4)])
    quarter_energy /= max(float(np.sum(energy)), 1.0)
    scalars = np.asarray([
        np.log1p(peak), np.log1p(rms), peak / rms,
        float(np.argmax(absolute)) / max(len(post) - 1, 1),
        float(np.max(post)) / peak, float(-np.min(post)) / peak,
        *quarter_energy,
    ])
    result = np.concatenate((normalized_time, normalized_spectrum, scalars))
    if not np.isfinite(result).all():
        raise RuntimeError("MPU-9250 feature extraction failed")
    return result.astype(np.float32)


def _area_ids(xy_mm: np.ndarray) -> np.ndarray:
    xy = np.asarray(xy_mm, dtype=np.float64)
    columns = np.clip((xy[:, 0] // 100.0).astype(np.int64), 0, 3)
    rows = np.clip((xy[:, 1] // 100.0).astype(np.int64), 0, 2)
    return rows * 4 + columns


def evaluate_condition(dataset, converted: np.ndarray, sample_count: int) -> dict:
    waveforms = converted[:, :sample_count]
    features = np.stack([extract_rich_features(row) for row in waveforms])
    grid = np.isin(dataset.session_ids, GRID_SESSION_IDS)
    common_test = grid & (dataset.repetitions % 5 == 0)
    direct_train = ~common_test
    direct_prediction, direct_iterations = _fit_predict(
        features, dataset.xy_mm, direct_train, common_test, 1
    )
    direct = {
        "test_count": int(common_test.sum()),
        "train_count": int(direct_train.sum()),
        "iterations": direct_iterations,
        **regression_metrics(dataset.xy_mm[common_test], direct_prediction),
        "area_accuracy": float(np.mean(
            _area_ids(direct_prediction) == dataset.labels[common_test]
        )),
    }

    support_xy = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support_xy)}
    density_labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    density_train = dataset.repetitions % 5 != 0
    density_test = ~density_train
    density_indices = _balanced_density_indices(density_labels, density_train, 1)
    scaler = StandardScaler().fit(features[density_indices])
    model = _density_model(1)
    model.fit(scaler.transform(features[density_indices]), density_labels[density_indices])
    raw_probability = model.predict_proba(scaler.transform(features[density_test]))
    temperature = _calibrate_temperature(raw_probability, density_labels[density_test])
    probability = _temperature_scaled(raw_probability, temperature)
    metrics = density_metrics(
        dataset.xy_mm[density_test], density_labels[density_test], probability, support_xy
    )
    map_xy = support_xy[np.argmax(probability, axis=1)]
    density = {
        "test_count": int(density_test.sum()),
        "balanced_train_count": int(len(density_indices)),
        "iterations": int(model.n_iter_),
        "temperature": temperature,
        **metrics,
        "area_accuracy": float(np.mean(
            _area_ids(map_xy) == dataset.labels[density_test]
        )),
    }
    return {
        "sample_rate_hz": MPU9250_RATE_HZ,
        "sample_count": sample_count,
        "trigger_index": MPU9250_TRIGGER_INDEX,
        "duration_ms": 1000.0 * sample_count / MPU9250_RATE_HZ,
        "feature_count": int(features.shape[1]),
        "direct_xy": direct,
        "density_60class": density,
    }


def run(sessions_root: Path, output_dir: Path) -> dict:
    dataset = load_position_dataset(sessions_root)
    source = load_source_waveforms(sessions_root)
    if len(source) != len(dataset.samples):
        raise RuntimeError("source waveform order does not match position dataset")
    converted, clipping = convert_to_mpu9250(source)
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    conditions = [
        evaluate_condition(dataset, converted, sample_count)
        for sample_count in (80, 320)
    ]
    baseline_path = Path("artifacts/pc_position_runtime_400x300x5/training_report.json")
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    report = {
        "experiment": "mpu9250_offline_position_upper_bound_v1",
        "dataset_sha256": dataset.dataset_sha256,
        "sample_count": int(len(dataset.samples)),
        "session_ids": list(DEFAULT_SESSION_IDS),
        "simulation": {
            "source_sensor": "KX134-1211 +/-32 g, 25.6 kHz",
            "target_sensor": "MPU-9250 accelerometer optimistic approximation",
            "lowpass": "4th-order zero-phase Butterworth",
            "bandwidth_hz": MPU9250_BANDWIDTH_HZ,
            "resampling": "scipy.signal.resample_poly up=5 down=32, Kaiser beta=8",
            "output_rate_hz": MPU9250_RATE_HZ,
            "full_scale_g": MPU9250_RANGE_G,
            "counts_per_g": MPU9250_COUNTS_PER_G,
            "limitations": [
                "Does not reproduce MPU-9250 package or mounting transfer function.",
                "Does not add MPU-9250 clock jitter, nonlinearity, cross-axis response, or fresh sensor noise.",
                "Zero-phase offline filtering makes this an optimistic upper-bound experiment.",
            ],
        },
        "clipping": clipping,
        "original_kx134_baseline": {
            "direct_xy": baseline["common_holdout_comparison"]["candidate_seven_grid_sessions"],
            "density_60class": baseline["density_validation"],
        },
        "conditions": conditions,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    np.savez_compressed(
        output_dir / "converted_mpu9250_summary.npz",
        first_12_waveforms=converted[:12],
        first_12_xy_mm=dataset.xy_mm[:12],
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/mpu9250_position_experiment_20260829"),
    )
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir)
    print(json.dumps({
        "clipping": report["clipping"],
        "conditions": report["conditions"],
        "report": str(args.output_dir / "comparison_report.json"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
