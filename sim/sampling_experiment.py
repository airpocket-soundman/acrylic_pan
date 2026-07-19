"""Compare sampling rates for Acrylic Pan classification and XY regression.

The source recordings stay at 25.6 kHz/2,048 samples.  Lower-rate variants
are produced with an anti-alias FIR (``scipy.signal.resample_poly``), never by
plain slicing.  Evaluation is leave-one-acquisition-session-out, and the
official Simulator seed-1 alpha plus bfloat16 boundaries are used so the
numeric path matches the deployed Solist-AI reference as closely as possible.

This module deliberately writes experiment artifacts only.  It never replaces
the firmware's deployed model header automatically.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import scipy
from scipy.signal import resample_poly

from .dummy_model_pipeline import (
    load_official_sim_alpha,
    quantize_bfloat16,
    to_bfloat16_bits,
)
from .solist_dataset import FeatureScaler, export_solist_csv, one_hot
from .solist_elm import activation


SOURCE_RATE_HZ = 25_600
SOURCE_SAMPLE_COUNT = 2_048
SOURCE_TRIGGER_INDEX = 64
INPUT_COUNT = 128
HIDDEN_COUNT = 32
CLASS_COUNT = 8
PANEL_WIDTH_MM = 400.0
PANEL_HEIGHT_MM = 200.0
AREA_CENTRES_MM = np.asarray(
    [(x, y) for y in (50.0, 150.0) for x in (50.0, 150.0, 250.0, 350.0)],
    dtype=np.float64,
)
RIDGE = 0.1
CLASSIFICATION_ACTIVATION = "hard_sigmoid"
XY_ACTIVATION = "sigmoid"
DEFAULT_ALPHA = Path(r"D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy")
DEFAULT_SESSION_IDS = (
    "20260718_074916_84b183d2",
    "20260718_080557_9524983c",
    "20260718_081909_9dd2d785",
    "20260718_084143_ac8e6d56",
)


@dataclass(frozen=True)
class RateSpec:
    target_rate_hz: int
    factor: int
    output_count: int
    trigger_index: int


RATE_SPECS = (
    RateSpec(25_600, 1, 2_048, 64),
    RateSpec(12_800, 2, 1_024, 32),
    RateSpec(6_400, 4, 512, 16),
)
FEATURE_MODES = ("time", "fft", "hybrid")


@dataclass(frozen=True)
class ExperimentDataset:
    samples: np.ndarray
    labels: np.ndarray
    xy_mm: np.ndarray
    session_ids: np.ndarray
    event_paths: tuple[Path, ...]
    dataset_sha256: str


def downsample_waveform(samples: np.ndarray, source_rate_hz: int, spec: RateSpec) -> np.ndarray:
    """Return one anti-aliased waveform at ``spec.target_rate_hz``."""
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.ndim != 1 or waveform.size != SOURCE_SAMPLE_COUNT:
        raise ValueError(f"waveform must contain {SOURCE_SAMPLE_COUNT} samples")
    if source_rate_hz != SOURCE_RATE_HZ:
        raise ValueError(f"source rate must be {SOURCE_RATE_HZ} Hz")
    if spec.factor not in (1, 2, 4) or source_rate_hz // spec.factor != spec.target_rate_hz:
        raise ValueError("invalid sampling-rate specification")
    if spec.output_count != SOURCE_SAMPLE_COUNT // spec.factor:
        raise ValueError("invalid output sample count")
    if spec.trigger_index != SOURCE_TRIGGER_INDEX // spec.factor:
        raise ValueError("invalid trigger index")
    if spec.factor == 1:
        return waveform.copy()
    result = resample_poly(
        waveform, up=1, down=spec.factor,
        window=("kaiser", 8.0), padtype="line",
    )
    if result.size != spec.output_count:
        raise RuntimeError("resampler returned an unexpected sample count")
    return result


def _band_rms(spectrum: np.ndarray, count: int) -> np.ndarray:
    edges = np.linspace(0, spectrum.size, count + 1).astype(np.int64)
    values = []
    for start, stop in zip(edges[:-1], edges[1:]):
        stop = max(int(stop), int(start) + 1)
        values.append(float(np.sqrt(np.mean(spectrum[int(start):stop] ** 2))))
    return np.asarray(values, dtype=np.float64)


def extract_features(
    samples: np.ndarray,
    sample_rate_hz: int,
    trigger_index: int,
    mode: str,
) -> np.ndarray:
    """Extract 128 force-normalized time, FFT, or hybrid features."""
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.ndim != 1 or waveform.size < 256:
        raise ValueError("waveform must be a one-dimensional event")
    if mode not in FEATURE_MODES:
        raise ValueError(f"unsupported feature mode: {mode}")
    if sample_rate_hz <= 0 or not 1 <= trigger_index < waveform.size:
        raise ValueError("invalid sampling metadata")
    baseline = float(np.mean(waveform[:trigger_index]))
    centered = waveform - baseline
    posttrigger = centered[trigger_index:]
    peak = max(float(np.max(np.abs(posttrigger))), 1.0)

    time_count = INPUT_COUNT if mode == "time" else INPUT_COUNT // 2
    time_indices = np.rint(
        np.linspace(0, posttrigger.size - 1, time_count)
    ).astype(np.int64)
    time_features = posttrigger[time_indices] / peak
    if mode == "time":
        return time_features.astype(np.float32)

    positive_spectrum = np.abs(
        np.fft.rfft(centered * np.hanning(centered.size))
    )[1:]
    fft_count = INPUT_COUNT if mode == "fft" else INPUT_COUNT // 2
    fft_features = np.log1p(_band_rms(positive_spectrum, fft_count) / peak)
    result = fft_features if mode == "fft" else np.concatenate((time_features, fft_features))
    if result.shape != (INPUT_COUNT,) or not np.isfinite(result).all():
        raise RuntimeError("feature extraction returned an invalid vector")
    return result.astype(np.float32)


def fit_numeric_beta(
    features: np.ndarray,
    targets: np.ndarray,
    alpha: np.ndarray,
    ridge: float = RIDGE,
    activation_name: str = CLASSIFICATION_ACTIVATION,
) -> np.ndarray:
    """Fit hidden-to-output weights for one-hot or continuous targets."""
    x = np.asarray(features, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64)
    projection = np.asarray(alpha, dtype=np.float64)
    if x.ndim != 2 or y.ndim != 2 or x.shape[0] != y.shape[0]:
        raise ValueError("features and targets must be 2-D with equal row counts")
    if projection.shape != (x.shape[1], HIDDEN_COUNT):
        raise ValueError(f"alpha must have shape {(x.shape[1], HIDDEN_COUNT)}")
    if y.shape[1] < 1 or ridge < 0 or not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError("invalid training arrays")
    hidden = activation(activation_name, x @ projection)
    gram = hidden.T @ hidden + ridge * np.eye(hidden.shape[1])
    return np.linalg.solve(gram, hidden.T @ y).astype(np.float32)


def _solist_reference(
    features: np.ndarray,
    alpha: np.ndarray,
    beta: np.ndarray,
    activation_name: str,
) -> np.ndarray:
    """Evaluate with the same bfloat16 boundaries used on the MCU path."""
    xq = quantize_bfloat16(features)
    aq = quantize_bfloat16(alpha)
    bq = quantize_bfloat16(beta)
    hidden = quantize_bfloat16(activation(activation_name, xq @ aq))
    return quantize_bfloat16(hidden @ bq)


def classification_metrics(
    expected: np.ndarray, predicted: np.ndarray, class_count: int = CLASS_COUNT,
) -> dict[str, Any]:
    expected = np.asarray(expected, dtype=np.int64)
    predicted = np.asarray(predicted, dtype=np.int64)
    if expected.shape != predicted.shape or expected.ndim != 1 or expected.size == 0:
        raise ValueError("classification arrays must have the same non-empty shape")
    matrix = np.zeros((class_count, class_count), dtype=np.int64)
    for target, actual in zip(expected, predicted):
        if not 0 <= target < class_count or not 0 <= actual < class_count:
            raise ValueError("classification value is outside the configured classes")
        matrix[target, actual] += 1
    recall = np.diag(matrix) / np.maximum(matrix.sum(axis=1), 1)
    precision = np.diag(matrix) / np.maximum(matrix.sum(axis=0), 1)
    f1 = 2.0 * precision * recall / np.maximum(precision + recall, 1e-12)
    return {
        "accuracy": float(np.mean(expected == predicted)),
        "balanced_accuracy": float(np.mean(recall)),
        "macro_f1": float(np.mean(f1)),
        "per_class_recall": recall.astype(float).tolist(),
        "confusion_matrix": matrix.tolist(),
    }


def regression_metrics(expected_xy: np.ndarray, predicted_xy: np.ndarray) -> dict[str, Any]:
    expected = np.asarray(expected_xy, dtype=np.float64)
    predicted = np.asarray(predicted_xy, dtype=np.float64)
    if expected.shape != predicted.shape or expected.ndim != 2 or expected.shape[1] != 2:
        raise ValueError("coordinate arrays must both have shape (samples, 2)")
    error = predicted - expected
    distance = np.linalg.norm(error, axis=1)
    return {
        "mae_x_mm": float(np.mean(np.abs(error[:, 0]))),
        "mae_y_mm": float(np.mean(np.abs(error[:, 1]))),
        "rmse_x_mm": float(np.sqrt(np.mean(error[:, 0] ** 2))),
        "rmse_y_mm": float(np.sqrt(np.mean(error[:, 1] ** 2))),
        "mean_distance_mm": float(np.mean(distance)),
        "median_distance_mm": float(np.median(distance)),
        "p90_distance_mm": float(np.percentile(distance, 90)),
        "within_25mm": float(np.mean(distance <= 25.0)),
        "within_50mm": float(np.mean(distance <= 50.0)),
    }


def make_loso_folds(session_ids: np.ndarray) -> tuple[tuple[tuple[str, ...], str], ...]:
    sessions = tuple(sorted(str(value) for value in np.unique(session_ids)))
    if len(sessions) < 2:
        raise ValueError("LOSO evaluation requires at least two sessions")
    return tuple((tuple(value for value in sessions if value != test), test) for test in sessions)


def _load_scalar(npz: np.lib.npyio.NpzFile, name: str) -> int:
    value = np.asarray(npz[name])
    if value.size != 1:
        raise ValueError(f"{name} must be scalar")
    return int(value.reshape(-1)[0])


def load_experiment_dataset(root: Path, session_ids: Iterable[str]) -> ExperimentDataset:
    """Load exactly the explicitly named long-capture sessions."""
    root = Path(root)
    names = tuple(str(value) for value in session_ids)
    if len(names) < 2 or len(set(names)) != len(names):
        raise ValueError("at least two distinct session IDs are required")
    samples: list[np.ndarray] = []
    labels: list[int] = []
    coordinates: list[tuple[float, float]] = []
    groups: list[str] = []
    paths: list[Path] = []
    digest = hashlib.sha256()
    for session_id in names:
        directory = root / session_id
        metadata_path = directory / "session.json"
        manifest_path = directory / "manifest.jsonl"
        if not metadata_path.is_file() or not manifest_path.is_file():
            raise FileNotFoundError(directory)
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("format") != "acrylic-pan-session-v1":
            raise ValueError(f"{session_id}: unsupported session format")
        rows = [
            json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if metadata.get("event_count") != len(rows):
            raise ValueError(f"{session_id}: event_count does not match manifest")
        digest.update(metadata_path.read_bytes())
        digest.update(manifest_path.read_bytes())
        session_classes: set[int] = set()
        for row in rows:
            label = row.get("class_id")
            annotations = row.get("annotations")
            if not isinstance(label, int) or not 0 <= label < CLASS_COUNT:
                raise ValueError(f"{session_id}: invalid class label")
            if not isinstance(annotations, dict):
                raise ValueError(f"{session_id}: missing guided annotations")
            try:
                x_mm = float(annotations["target_x_mm"])
                y_mm = float(annotations["target_y_mm"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{session_id}: invalid XY annotation") from error
            if not np.isfinite((x_mm, y_mm)).all() or not (
                0.0 <= x_mm <= PANEL_WIDTH_MM and 0.0 <= y_mm <= PANEL_HEIGHT_MM
            ):
                raise ValueError(f"{session_id}: XY annotation is outside the panel")
            event_path = (directory / str(row.get("file", ""))).resolve()
            try:
                event_path.relative_to(directory.resolve())
            except ValueError as error:
                raise ValueError(f"{session_id}: event path escapes session") from error
            with np.load(event_path, allow_pickle=False) as event:
                waveform = np.asarray(event["samples"], dtype=np.float64)
                if waveform.shape != (SOURCE_SAMPLE_COUNT,):
                    raise ValueError(f"{event_path}: expected {SOURCE_SAMPLE_COUNT} samples")
                if _load_scalar(event, "sample_rate_hz") != SOURCE_RATE_HZ:
                    raise ValueError(f"{event_path}: unexpected sample rate")
                if _load_scalar(event, "trigger_index") != SOURCE_TRIGGER_INDEX:
                    raise ValueError(f"{event_path}: unexpected trigger index")
                if _load_scalar(event, "class_id") != label:
                    raise ValueError(f"{event_path}: NPZ/manifest label mismatch")
            digest.update(waveform.astype(np.int16).tobytes())
            samples.append(waveform)
            labels.append(label)
            coordinates.append((x_mm, y_mm))
            groups.append(session_id)
            paths.append(event_path)
            session_classes.add(label)
        if session_classes != set(range(CLASS_COUNT)):
            raise ValueError(f"{session_id}: session does not contain all eight classes")
    return ExperimentDataset(
        samples=np.stack(samples),
        labels=np.asarray(labels, dtype=np.int64),
        xy_mm=np.asarray(coordinates, dtype=np.float64),
        session_ids=np.asarray(groups),
        event_paths=tuple(paths),
        dataset_sha256=digest.hexdigest(),
    )


def _features_for(dataset: ExperimentDataset, spec: RateSpec, mode: str) -> np.ndarray:
    result = []
    for waveform in dataset.samples:
        downsampled = downsample_waveform(waveform, SOURCE_RATE_HZ, spec)
        result.append(extract_features(downsampled, spec.target_rate_hz, spec.trigger_index, mode))
    return np.stack(result)


def _cell_ids(xy_mm: np.ndarray) -> np.ndarray:
    clipped = np.clip(
        np.asarray(xy_mm, dtype=np.float64),
        (0.0, 0.0),
        (PANEL_WIDTH_MM - 1e-6, PANEL_HEIGHT_MM - 1e-6),
    )
    column = np.floor(clipped[:, 0] / 100.0).astype(np.int64)
    row = np.floor(clipped[:, 1] / 100.0).astype(np.int64)
    return row * 4 + column


def _weighted_average(items: list[dict[str, Any]], key: str, weight_key: str) -> float:
    weight = np.asarray([item[weight_key] for item in items], dtype=np.float64)
    value = np.asarray([item[key] for item in items], dtype=np.float64)
    return float(np.sum(value * weight) / np.sum(weight))


def _evaluate_condition(
    dataset: ExperimentDataset,
    features: np.ndarray,
    alpha: np.ndarray,
    ridge: float,
    output_dir: Path,
) -> dict[str, Any]:
    classification_folds: list[dict[str, Any]] = []
    regression_folds: list[dict[str, Any]] = []
    for fold_index, (train_sessions, test_session) in enumerate(
        make_loso_folds(dataset.session_ids), start=1
    ):
        train = np.isin(dataset.session_ids, train_sessions)
        test = dataset.session_ids == test_session
        scaler = FeatureScaler.fit(features[train])
        train_x = scaler.transform(features[train])
        test_x = scaler.transform(features[test])

        class_beta = fit_numeric_beta(
            train_x, one_hot(dataset.labels[train]), alpha, ridge,
            CLASSIFICATION_ACTIVATION,
        )
        class_scores = _solist_reference(
            test_x, alpha, class_beta, CLASSIFICATION_ACTIVATION
        )
        class_predicted = np.argmax(class_scores, axis=1)
        class_result = classification_metrics(dataset.labels[test], class_predicted)
        class_coordinate_metrics = regression_metrics(
            dataset.xy_mm[test], AREA_CENTRES_MM[class_predicted]
        )
        class_result.update({
            "fold": fold_index,
            "train_sessions": list(train_sessions),
            "test_session": test_session,
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "coordinate_from_class": class_coordinate_metrics,
        })
        classification_folds.append(class_result)

        normalized_xy = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
        xy_beta = fit_numeric_beta(
            train_x, normalized_xy[train], alpha, ridge, XY_ACTIVATION
        )
        predicted_xy = _solist_reference(
            test_x, alpha, xy_beta, XY_ACTIVATION
        ) * (
            PANEL_WIDTH_MM, PANEL_HEIGHT_MM
        )
        xy_result = regression_metrics(dataset.xy_mm[test], predicted_xy)
        xy_result.update({
            "fold": fold_index,
            "train_sessions": list(train_sessions),
            "test_session": test_session,
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "area_accuracy": float(np.mean(_cell_ids(predicted_xy) == dataset.labels[test])),
        })
        regression_folds.append(xy_result)

        fold_dir = output_dir / f"fold{fold_index}_{test_session}"
        fold_dir.mkdir(parents=True, exist_ok=True)
        np.savez(
            fold_dir / "classification_model.npz",
            alpha=alpha, beta=class_beta, feature_mean=scaler.mean, feature_scale=scaler.scale,
        )
        np.savez(
            fold_dir / "xy_model.npz",
            alpha=alpha, beta=xy_beta, feature_mean=scaler.mean, feature_scale=scaler.scale,
        )

    final_scaler = FeatureScaler.fit(features)
    final_x = final_scaler.transform(features)
    final_class_beta = fit_numeric_beta(
        final_x, one_hot(dataset.labels), alpha, ridge, CLASSIFICATION_ACTIVATION
    )
    final_xy_beta = fit_numeric_beta(
        final_x,
        dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM),
        alpha,
        ridge,
        XY_ACTIVATION,
    )
    classification_dir = output_dir / "classification"
    regression_dir = output_dir / "xy"
    classification_dir.mkdir(parents=True, exist_ok=True)
    regression_dir.mkdir(parents=True, exist_ok=True)
    np.savez(
        classification_dir / "model.npz",
        alpha=alpha,
        beta=final_class_beta,
        alpha_bf16=to_bfloat16_bits(alpha),
        beta_bf16=to_bfloat16_bits(final_class_beta),
        feature_mean=final_scaler.mean,
        feature_scale=final_scaler.scale,
    )
    np.savez(
        regression_dir / "model.npz",
        alpha=alpha,
        beta=final_xy_beta,
        alpha_bf16=to_bfloat16_bits(alpha),
        beta_bf16=to_bfloat16_bits(final_xy_beta),
        feature_mean=final_scaler.mean,
        feature_scale=final_scaler.scale,
        panel_size_mm=np.asarray((PANEL_WIDTH_MM, PANEL_HEIGHT_MM)),
    )
    export_solist_csv(
        classification_dir / "final_train.csv", final_x, one_hot(dataset.labels)
    )
    export_solist_csv(
        regression_dir / "final_train.csv",
        final_x,
        dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM),
    )

    final_class_prediction = np.argmax(
        _solist_reference(
            final_x, alpha, final_class_beta, CLASSIFICATION_ACTIVATION
        ), axis=1
    )
    final_xy_prediction = _solist_reference(
        final_x, alpha, final_xy_beta, XY_ACTIVATION
    ) * (
        PANEL_WIDTH_MM, PANEL_HEIGHT_MM
    )
    return {
        "classification": {
            "folds": classification_folds,
            "mean_accuracy": _weighted_average(classification_folds, "accuracy", "test_count"),
            "mean_balanced_accuracy": float(np.mean([
                fold["balanced_accuracy"] for fold in classification_folds
            ])),
            "mean_macro_f1": float(np.mean([
                fold["macro_f1"] for fold in classification_folds
            ])),
            "mean_coordinate_distance_mm": _weighted_average(
                [
                    {"value": fold["coordinate_from_class"]["mean_distance_mm"],
                     "test_count": fold["test_count"]}
                    for fold in classification_folds
                ],
                "value", "test_count",
            ),
            "final_training_metrics": classification_metrics(
                dataset.labels, final_class_prediction
            ),
        },
        "xy": {
            "folds": regression_folds,
            "mean_mae_x_mm": _weighted_average(regression_folds, "mae_x_mm", "test_count"),
            "mean_mae_y_mm": _weighted_average(regression_folds, "mae_y_mm", "test_count"),
            "mean_distance_mm": _weighted_average(
                regression_folds, "mean_distance_mm", "test_count"
            ),
            "mean_area_accuracy": _weighted_average(
                regression_folds, "area_accuracy", "test_count"
            ),
            "mean_within_25mm": _weighted_average(
                regression_folds, "within_25mm", "test_count"
            ),
            "mean_within_50mm": _weighted_average(
                regression_folds, "within_50mm", "test_count"
            ),
            "final_training_metrics": {
                **regression_metrics(dataset.xy_mm, final_xy_prediction),
                "area_accuracy": float(np.mean(
                    _cell_ids(final_xy_prediction) == dataset.labels
                )),
            },
        },
    }


def _export_official_simulator_package(
    dataset: ExperimentDataset,
    spec: RateSpec,
    mode: str,
    alpha: np.ndarray,
    ridge: float,
    task: str,
    destination: Path,
) -> None:
    """Export fold CSVs/settings for manual use in the official GUI."""
    features = _features_for(dataset, spec, mode)
    destination.mkdir(parents=True, exist_ok=True)
    normalized_xy = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    for fold_index, (train_sessions, test_session) in enumerate(
        make_loso_folds(dataset.session_ids), start=1
    ):
        train = np.isin(dataset.session_ids, train_sessions)
        test = dataset.session_ids == test_session
        scaler = FeatureScaler.fit(features[train])
        train_x = scaler.transform(features[train])
        test_x = scaler.transform(features[test])
        targets = one_hot(dataset.labels) if task == "classification" else normalized_xy
        fold = destination / f"fold{fold_index}_{test_session}"
        fold.mkdir(parents=True, exist_ok=True)
        export_solist_csv(fold / "train.csv", train_x, targets[train])
        export_solist_csv(fold / "test.csv", test_x, targets[test])
        task_activation = (
            CLASSIFICATION_ACTIVATION if task == "classification" else XY_ACTIVATION
        )
        beta = fit_numeric_beta(
            train_x, targets[train], alpha, ridge, task_activation
        )
        np.savez(
            fold / "reference_model.npz",
            alpha=alpha, beta=beta, feature_mean=scaler.mean, feature_scale=scaler.scale,
        )
    settings = {
        "task": task,
        "sample_rate_hz": spec.target_rate_hz,
        "sample_count": spec.output_count,
        "trigger_index": spec.trigger_index,
        "feature_mode": mode,
        "input_nodes": INPUT_COUNT,
        "hidden_nodes": HIDDEN_COUNT,
        "output_nodes": CLASS_COUNT if task == "classification" else 2,
        "activation": (
            CLASSIFICATION_ACTIVATION if task == "classification" else XY_ACTIVATION
        ),
        "loss": "mse",
        "seed": 1,
        "ridge_l2": ridge,
        "target": "one_hot_8class" if task == "classification" else "x/400,y/200",
        "note": "The official Simulator is GUI-only; run each train/test pair manually.",
    }
    (destination / "simulator_settings.json").write_text(
        json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def run_experiment(
    sessions_root: Path,
    session_ids: Iterable[str],
    output_dir: Path,
    alpha_path: Path = DEFAULT_ALPHA,
    ridge: float = RIDGE,
    feature_modes: Iterable[str] = FEATURE_MODES,
) -> dict[str, Any]:
    dataset = load_experiment_dataset(sessions_root, session_ids)
    alpha = load_official_sim_alpha(alpha_path)
    if alpha.shape != (INPUT_COUNT, HIDDEN_COUNT):
        raise ValueError(f"official alpha must have shape {(INPUT_COUNT, HIDDEN_COUNT)}")
    modes = tuple(feature_modes)
    if not modes or any(mode not in FEATURE_MODES for mode in modes):
        raise ValueError("invalid feature mode selection")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    conditions: list[dict[str, Any]] = []
    for spec in RATE_SPECS:
        for mode in modes:
            condition_dir = output_dir / f"{spec.target_rate_hz}hz_{mode}"
            features = _features_for(dataset, spec, mode)
            result = _evaluate_condition(
                dataset, features, alpha, ridge, condition_dir
            )
            conditions.append({
                "sample_rate_hz": spec.target_rate_hz,
                "decimation_factor": spec.factor,
                "sample_count": spec.output_count,
                "trigger_index": spec.trigger_index,
                "feature_mode": mode,
                "input_count": INPUT_COUNT,
                **result,
            })
    best_classification = max(
        conditions, key=lambda item: item["classification"]["mean_accuracy"]
    )
    best_xy = min(conditions, key=lambda item: item["xy"]["mean_distance_mm"])
    specs_by_rate = {spec.target_rate_hz: spec for spec in RATE_SPECS}
    simulator_root = output_dir / "official_simulator_packages"
    _export_official_simulator_package(
        dataset,
        specs_by_rate[best_classification["sample_rate_hz"]],
        best_classification["feature_mode"],
        alpha, ridge, "classification",
        simulator_root / "classification",
    )
    _export_official_simulator_package(
        dataset,
        specs_by_rate[best_xy["sample_rate_hz"]],
        best_xy["feature_mode"],
        alpha, ridge, "xy",
        simulator_root / "xy",
    )
    report = {
        "experiment": "acrylic_pan_sampling_and_xy_v1",
        "session_ids": list(session_ids),
        "sample_count": int(dataset.labels.size),
        "class_counts": np.bincount(dataset.labels, minlength=CLASS_COUNT).astype(int).tolist(),
        "dataset_sha256": dataset.dataset_sha256,
        "source_contract": {
            "sample_rate_hz": SOURCE_RATE_HZ,
            "sample_count": SOURCE_SAMPLE_COUNT,
            "trigger_index": SOURCE_TRIGGER_INDEX,
        },
        "resampling": {
            "implementation": "scipy.signal.resample_poly",
            "scipy_version": scipy.__version__,
            "window": ["kaiser", 8.0],
            "padtype": "line",
            "note": "offline decimation does not reproduce ADC-rate-dependent analog noise",
        },
        "solist": {
            "input_count": INPUT_COUNT,
            "hidden_count": HIDDEN_COUNT,
            "classification_output_count": CLASS_COUNT,
            "xy_output_count": 2,
            "classification_activation": CLASSIFICATION_ACTIVATION,
            "xy_activation": XY_ACTIVATION,
            "loss": "mse",
            "ridge": ridge,
            "seed": 1,
            "alpha_origin": str(alpha_path),
            "evaluation_precision": "bfloat16 boundaries",
        },
        "xy_scope": (
            "Training targets are the eight area centres only; this evaluates coarse "
            "coordinate regression, not interpolation to unseen hit positions."
        ),
        "conditions": conditions,
        "best_classification": {
            "sample_rate_hz": best_classification["sample_rate_hz"],
            "feature_mode": best_classification["feature_mode"],
            **best_classification["classification"],
        },
        "best_xy": {
            "sample_rate_hz": best_xy["sample_rate_hz"],
            "feature_mode": best_xy["feature_mode"],
            **best_xy["xy"],
        },
        "official_simulator_packages": {
            "classification": str(simulator_root / "classification"),
            "xy": str(simulator_root / "xy"),
            "execution": "manual GUI; no supported headless CLI was found",
        },
    }
    (output_dir / "comparison_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions-root", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--session-id", action="append", dest="session_ids")
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/sampling_experiment_20260718"),
    )
    parser.add_argument("--alpha", type=Path, default=DEFAULT_ALPHA)
    parser.add_argument("--ridge", type=float, default=RIDGE)
    parser.add_argument("--feature-mode", action="append", dest="feature_modes")
    args = parser.parse_args()
    session_ids = tuple(args.session_ids or DEFAULT_SESSION_IDS)
    modes = tuple(args.feature_modes or FEATURE_MODES)
    report = run_experiment(
        args.sessions_root, session_ids, args.output_dir, args.alpha, args.ridge, modes
    )
    print(f"samples={report['sample_count']}; class_counts={report['class_counts']}")
    for condition in report["conditions"]:
        print(
            f"{condition['sample_rate_hz']:5d} Hz {condition['feature_mode']:6s}: "
            f"class={condition['classification']['mean_accuracy']:.4f}; "
            f"xy_distance={condition['xy']['mean_distance_mm']:.2f} mm; "
            f"xy_area={condition['xy']['mean_area_accuracy']:.4f}"
        )
    print(
        "best classification: "
        f"{report['best_classification']['sample_rate_hz']} Hz/"
        f"{report['best_classification']['feature_mode']} "
        f"accuracy={report['best_classification']['mean_accuracy']:.4f}"
    )
    print(
        "best XY: "
        f"{report['best_xy']['sample_rate_hz']} Hz/{report['best_xy']['feature_mode']} "
        f"distance={report['best_xy']['mean_distance_mm']:.2f} mm"
    )
    print(f"report={args.output_dir / 'comparison_report.json'}")


if __name__ == "__main__":
    main()
