"""Compare spatial Gaussian soft targets with the deployed MCU position model."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from sklearn.preprocessing import StandardScaler

from .device_position_probability import (
    DISTILLATION_WEIGHT,
    INPUT_FEATURE_COUNT,
    POSITION_COUNT,
    _engine_inputs,
    _fit_soft_targets,
    _metrics,
    _teacher_probabilities,
)
from .pc_position_grid_runtime import (
    DEFAULT_SESSION_IDS,
    extract_grid_features,
    load_position_dataset,
)
from .real_model_pipeline import extract_hybrid_features

SIGMAS_MM = (20.0, 35.0, 50.0, 75.0)
GAUSSIAN_WEIGHTS = (0.15, 0.35, 0.60)


def _gaussian_targets(labels: np.ndarray, support: np.ndarray,
                      sigma_mm: float) -> np.ndarray:
    centres = support[np.asarray(labels, dtype=np.int64)]
    squared_distance = np.sum(
        (centres[:, None, :] - support[None, :, :]) ** 2, axis=2
    )
    values = np.exp(-0.5 * squared_distance / (sigma_mm * sigma_mm))
    return (values / values.sum(axis=1, keepdims=True)).astype(np.float32)


def run(sessions_root: Path, output_dir: Path) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    train = np.asarray(dataset.repetitions) % 5 != 0
    test = ~train

    teacher = _teacher_probabilities(rich, labels, train, train)
    scaler = StandardScaler().fit(edge[train, :INPUT_FEATURE_COUNT])
    train_inputs = _engine_inputs(edge[train], scaler)
    test_inputs = _engine_inputs(edge[test], scaler)
    one_hot = np.eye(POSITION_COUNT, dtype=np.float32)[labels[train]]

    baseline_targets = ((1.0 - DISTILLATION_WEIGHT) * one_hot
                        + DISTILLATION_WEIGHT * teacher)
    alpha, beta = _fit_soft_targets(train_inputs, baseline_targets)
    baseline = {
        "name": "deployed_target_recipe",
        "gaussian_weight": 0.0,
        "sigma_mm": None,
        **_metrics(dataset.xy_mm[test], labels[test], test_inputs,
                   alpha, beta, support),
    }

    candidates = []
    for sigma_mm in SIGMAS_MM:
        gaussian = _gaussian_targets(labels[train], support, sigma_mm)
        for gaussian_weight in GAUSSIAN_WEIGHTS:
            one_hot_weight = 1.0 - DISTILLATION_WEIGHT - gaussian_weight
            targets = (one_hot_weight * one_hot
                       + DISTILLATION_WEIGHT * teacher
                       + gaussian_weight * gaussian)
            alpha, beta = _fit_soft_targets(train_inputs, targets)
            candidates.append({
                "name": "spatial_gaussian",
                "gaussian_weight": gaussian_weight,
                "one_hot_weight": one_hot_weight,
                "teacher_weight": DISTILLATION_WEIGHT,
                "sigma_mm": sigma_mm,
                **_metrics(dataset.xy_mm[test], labels[test], test_inputs,
                           alpha, beta, support),
            })

    best_distance = min(candidates, key=lambda item: item["mean_distance_mm"])
    best_top1 = max(candidates, key=lambda item: item["top1_position_accuracy"])
    report = {
        "experiment": "solist_60position_spatial_gaussian_targets_v1",
        "sample_count": int(len(edge)),
        "train_count": int(train.sum()),
        "test_count": int(test.sum()),
        "holdout_rule": "repetition_modulo_5_equals_0",
        "architecture": [128, 32, 60],
        "baseline": baseline,
        "candidates": candidates,
        "best_mean_distance": best_distance,
        "best_top1": best_top1,
        "scope_warning": (
            "All evaluation hits are at the same 60 support coordinates. "
            "This does not validate interpolation at unseen coordinates."
        ),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "evaluation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/device_position_gaussian_400x300x5"),
    )
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir)
    print(json.dumps({
        "baseline": report["baseline"],
        "best_mean_distance": report["best_mean_distance"],
        "best_top1": report["best_top1"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
