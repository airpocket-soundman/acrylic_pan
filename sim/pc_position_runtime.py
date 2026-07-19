"""Train the PC position model used by the 512-sample live inference UI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

import joblib
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.preprocessing import StandardScaler

from .pc_xy_regression import DEFAULT_SEEDS, _direct_model, parameter_count
from .sampling_experiment import (
    DEFAULT_SESSION_IDS,
    PANEL_HEIGHT_MM,
    PANEL_WIDTH_MM,
    extract_features,
    load_experiment_dataset,
    make_loso_folds,
    regression_metrics,
)

SAMPLE_RATE_HZ = 25_600
SAMPLE_COUNT = 512
TRIGGER_INDEX = 64
FEATURE_MODE = "hybrid"


def extract_live_features(samples: np.ndarray) -> np.ndarray:
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.shape != (SAMPLE_COUNT,):
        raise ValueError(f"live waveform must contain {SAMPLE_COUNT} samples")
    return extract_features(waveform, SAMPLE_RATE_HZ, TRIGGER_INDEX, FEATURE_MODE)


def run(sessions_root: Path, output_dir: Path, seeds: tuple[int, ...]) -> dict:
    dataset = load_experiment_dataset(sessions_root, DEFAULT_SESSION_IDS)
    features = np.stack([extract_live_features(waveform[:SAMPLE_COUNT]) for waveform in dataset.samples])
    targets = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    # Seed 1 LOSO is retained as a conservative compatibility check for the
    # exact live 20 ms waveform contract.  The runtime ensemble is then fitted
    # on all four sessions below.
    loso_prediction = np.empty_like(dataset.xy_mm)
    folds = []
    for train_sessions, test_session in make_loso_folds(dataset.session_ids):
        train = np.isin(dataset.session_ids, train_sessions)
        test = dataset.session_ids == test_session
        scaler = StandardScaler().fit(features[train])
        model = _direct_model(seeds[0])
        model.fit(scaler.transform(features[train]), targets[train])
        predicted = np.clip(model.predict(scaler.transform(features[test])), 0.0, 1.0)
        predicted *= (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
        loso_prediction[test] = predicted
        folds.append({
            "test_session": test_session,
            "test_count": int(test.sum()),
            **regression_metrics(dataset.xy_mm[test], predicted),
        })
    metrics = regression_metrics(dataset.xy_mm, loso_prediction)
    residuals = loso_prediction - dataset.xy_mm
    residual_covariance = np.cov(residuals, rowvar=False, ddof=1)
    residual_covariance = (residual_covariance + residual_covariance.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(residual_covariance)
    residual_covariance = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0)) @ eigenvectors.T
    inverse_covariance = np.linalg.inv(residual_covariance)
    mahalanobis_squared = np.einsum(
        "ni,ij,nj->n", residuals, inverse_covariance, residuals
    )
    confidence_level = 0.90
    gaussian_threshold = -2.0 * np.log(1.0 - confidence_level)
    calibration_scale = float(
        np.quantile(mahalanobis_squared, confidence_level) / gaussian_threshold
    )
    calibrated_covariance = residual_covariance * max(calibration_scale, 1e-6)
    calibrated_inverse = np.linalg.inv(calibrated_covariance)
    calibrated_d2 = np.einsum(
        "ni,ij,nj->n", residuals, calibrated_inverse, residuals
    )
    uncertainty = {
        "method": "loso_residual_covariance",
        "confidence_level": confidence_level,
        "chi_square_threshold": gaussian_threshold,
        "calibration_scale": calibration_scale,
        "empirical_coverage": float(np.mean(calibrated_d2 <= gaussian_threshold)),
        "residual_covariance_mm2": residual_covariance.tolist(),
        "calibrated_covariance_mm2": calibrated_covariance.tolist(),
    }

    scaler = StandardScaler().fit(features)
    scaled = scaler.transform(features)
    models = []
    for seed in seeds:
        model = _direct_model(seed)
        model.fit(scaled, targets)
        models.append(model)

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "position_ensemble.joblib"
    bundle = {
        "scaler": scaler,
        "models": models,
        "contract": {
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "sample_count": SAMPLE_COUNT,
            "trigger_index": TRIGGER_INDEX,
            "feature_mode": FEATURE_MODE,
            "panel_width_mm": PANEL_WIDTH_MM,
            "panel_height_mm": PANEL_HEIGHT_MM,
        },
        "validation": metrics,
        "uncertainty": uncertainty,
        "scope": "eight centre coordinates only; arbitrary-position output is interpolation",
    }
    joblib.dump(bundle, bundle_path, compress=3)
    report = {
        "experiment": "pc_live_position_20ms_v1",
        "dataset_sha256": dataset.dataset_sha256,
        "sample_count": int(len(dataset.labels)),
        "session_ids": list(DEFAULT_SESSION_IDS),
        "contract": bundle["contract"],
        "architecture": [128, 256, 128, 64, 2],
        "trainable_parameters": parameter_count(128, (256, 128, 64), 2),
        "runtime_seeds": list(seeds),
        "validation_seed": seeds[0],
        "loso_metrics": metrics,
        "uncertainty": uncertainty,
        "folds": folds,
        "scope": bundle["scope"],
        "model": str(bundle_path),
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/pc_position_runtime"))
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir, tuple(args.seeds))
    print(f"LOSO mean distance={report['loso_metrics']['mean_distance_mm']:.2f} mm")
    print(f"model={report['model']}")


if __name__ == "__main__":
    main()
