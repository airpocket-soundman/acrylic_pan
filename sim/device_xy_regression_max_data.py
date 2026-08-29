"""Evaluate direct continuous XY regression under the current Solist MCU limits."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

from .compare_area_classification import _area_ids
from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference
from .pc_position_grid_runtime import DEFAULT_SESSION_IDS, load_position_dataset
from .real_model_pipeline import extract_hybrid_features
from .sampling_experiment import regression_metrics

RIDGES = (1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0, 100.0)
PANEL_SIZE_MM = np.asarray((400.0, 300.0), dtype=np.float32)
PANEL_CENTRE_MM = PANEL_SIZE_MM / 2.0


def _fit_beta(inputs: np.ndarray, targets: np.ndarray, alpha: np.ndarray,
              ridge: float) -> np.ndarray:
    hidden = np.clip(0.2 * (inputs @ alpha) + 0.5, 0.0, 1.0).astype(np.float64)
    gram = hidden.T @ hidden + ridge * np.eye(hidden.shape[1])
    return np.linalg.solve(gram, hidden.T @ targets).astype(np.float32)


def _hidden_float(inputs: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    return np.clip(0.2 * (inputs @ alpha) + 0.5, 0.0, 1.0)


def _decode(values: np.ndarray, encoding: str) -> np.ndarray:
    if encoding == "zero_one":
        return values * PANEL_SIZE_MM
    return values * PANEL_SIZE_MM + PANEL_CENTRE_MM


def _predict_mm(inputs: np.ndarray, alpha: np.ndarray, beta: np.ndarray,
                encoding: str) -> np.ndarray:
    return _decode(mcu_reference(inputs, alpha, beta), encoding)


def _metrics(expected: np.ndarray, predicted: np.ndarray) -> dict:
    result = regression_metrics(expected, predicted)
    result["area_accuracy"] = float(np.mean(_area_ids(expected) == _area_ids(predicted)))
    result["inside_panel"] = float(np.mean(
        (predicted[:, 0] >= 0.0) & (predicted[:, 0] <= PANEL_SIZE_MM[0])
        & (predicted[:, 1] >= 0.0) & (predicted[:, 1] <= PANEL_SIZE_MM[1])
    ))
    return result


def run(sessions_root: Path, output_dir: Path) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    features = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    expected = np.asarray(dataset.xy_mm, dtype=np.float32)
    train = np.asarray(dataset.repetitions) % 5 != 0
    test = ~train
    alpha = load_official_sim_alpha()

    candidates = []
    fitted = []
    for encoding in ("centred", "zero_one"):
        targets = ((expected - PANEL_CENTRE_MM) / PANEL_SIZE_MM
                   if encoding == "centred" else expected / PANEL_SIZE_MM)
        for ridge in RIDGES:
            scaler = StandardScaler().fit(features[train])
            train_inputs = scaler.transform(features[train]).astype(np.float32)
            beta = _fit_beta(train_inputs, targets[train], alpha, ridge)
            test_inputs = scaler.transform(features[test]).astype(np.float32)
            predicted = _predict_mm(test_inputs, alpha, beta, encoding)
            float_predicted = _decode(_hidden_float(test_inputs, alpha) @ beta, encoding)
            metrics = {
                "encoding": encoding,
                "ridge_l2": ridge,
                **_metrics(expected[test], predicted),
                "float_reference": _metrics(expected[test], float_predicted),
            }
            candidates.append(metrics)
            fitted.append((scaler, beta))

    best_index = min(range(len(candidates)),
                     key=lambda index: candidates[index]["mean_distance_mm"])
    selected = candidates[best_index]

    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "experiment": "solist_direct_xy_128_fixed32_2_max_data_v1",
        "session_ids": list(DEFAULT_SESSION_IDS),
        "sample_count": int(len(features)),
        "train_count": int(train.sum()),
        "test_count": int(test.sum()),
        "holdout_rule": "repetition_modulo_5_not_equal_0_for_train",
        "architecture": [128, 32, 2],
        "target": "continuous XY; centred and zero-to-one encodings compared",
        "precision": "bfloat16 input, alpha, hidden output, beta, and output",
        "alpha": "fixed ROHM Simulator seed=1 projection; Beta-only training",
        "ridge_candidates": candidates,
        "selected": selected,
        "comparison_note": (
            "This tests held-out repetitions at the same 60 measured coordinates. "
            "Continuous interpolation at unseen coordinates needs a separate dataset."
        ),
    }
    (output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/device_xy_regression_400x300x5_max_data"),
    )
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir)
    print(json.dumps(report["selected"], ensure_ascii=False))


if __name__ == "__main__":
    main()
