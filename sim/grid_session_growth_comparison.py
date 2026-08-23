"""Measure the benefit of growing from four to seven corner-grid sessions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import warnings

import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.preprocessing import StandardScaler

from .compare_area_classification import _area_ids, _direct_area_model
from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference
from .pc_position_grid_runtime import (
    BASELINE_GRID_SESSION_IDS,
    CENTRE_SESSION_IDS,
    CLASS_COUNT,
    DEFAULT_SESSION_IDS,
    GRID_SESSION_IDS,
    _balanced_density_indices,
    _density_model,
    extract_grid_features,
    load_position_dataset,
)
from .real_model_pipeline import DEFAULT_ALPHA, FeatureScaler, extract_hybrid_features, fit_beta
from .sampling_experiment import classification_metrics


def _fit_direct(features: np.ndarray, labels: np.ndarray, train: np.ndarray,
                test: np.ndarray, seed: int) -> tuple[np.ndarray, int]:
    indices = _balanced_density_indices(labels, train, seed)
    scaler = StandardScaler().fit(features[indices])
    model = _direct_area_model(seed)
    model.fit(scaler.transform(features[indices]), labels[indices])
    return model.predict(scaler.transform(features[test])), int(model.n_iter_)


def _fit_density(features: np.ndarray, xy_mm: np.ndarray, train: np.ndarray,
                 test: np.ndarray, seed: int) -> tuple[np.ndarray, np.ndarray, int]:
    support = np.unique(xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in xy_mm])
    indices = _balanced_density_indices(labels, train, seed)
    scaler = StandardScaler().fit(features[indices])
    model = _density_model(seed)
    model.fit(scaler.transform(features[indices]), labels[indices])
    probability = model.predict_proba(scaler.transform(features[test]))
    support_areas = _area_ids(support)
    area_probability = np.stack([
        probability[:, support_areas == class_id].sum(axis=1)
        for class_id in range(CLASS_COUNT)
    ], axis=1)
    return np.argmax(area_probability, axis=1), np.argmax(probability, axis=1), int(model.n_iter_)


def _fit_edge(features: np.ndarray, labels: np.ndarray, train: np.ndarray,
              test: np.ndarray) -> np.ndarray:
    scaler = FeatureScaler.fit(features[train])
    alpha = load_official_sim_alpha(DEFAULT_ALPHA)
    beta = fit_beta(scaler.transform(features[train]), labels[train], alpha,
                    ridge=1e-3, class_count=CLASS_COUNT)
    return np.argmax(mcu_reference(scaler.transform(features[test]), alpha, beta), axis=1)


def _comparison(expected: np.ndarray, baseline: np.ndarray, candidate: np.ndarray) -> dict:
    baseline_correct = baseline == expected
    candidate_correct = candidate == expected
    return {
        "baseline": classification_metrics(expected, baseline, CLASS_COUNT),
        "candidate": classification_metrics(expected, candidate, CLASS_COUNT),
        "accuracy_delta_points": float(100 * (candidate_correct.mean() - baseline_correct.mean())),
        "baseline_only_correct": int(np.sum(baseline_correct & ~candidate_correct)),
        "candidate_only_correct": int(np.sum(~baseline_correct & candidate_correct)),
        "net_additional_correct": int(candidate_correct.sum() - baseline_correct.sum()),
    }


def run(sessions_root: Path, output: Path, seed: int = 1) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    grid = np.isin(dataset.session_ids, GRID_SESSION_IDS)
    baseline_sessions = np.isin(
        dataset.session_ids, (*CENTRE_SESSION_IDS, *BASELINE_GRID_SESSION_IDS)
    )
    test = grid & (dataset.repetitions % 5 == 0)
    baseline_train = baseline_sessions & ~test
    candidate_train = ~test
    expected = dataset.labels[test]
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    baseline_direct, baseline_direct_iterations = _fit_direct(
        rich, dataset.labels, baseline_train, test, seed
    )
    candidate_direct, candidate_direct_iterations = _fit_direct(
        rich, dataset.labels, candidate_train, test, seed
    )
    baseline_density, baseline_position, baseline_density_iterations = _fit_density(
        rich, dataset.xy_mm, baseline_train, test, seed
    )
    candidate_density, candidate_position, candidate_density_iterations = _fit_density(
        rich, dataset.xy_mm, candidate_train, test, seed
    )
    baseline_edge = _fit_edge(edge, dataset.labels, baseline_train, test)
    candidate_edge = _fit_edge(edge, dataset.labels, candidate_train, test)

    report = {
        "method": "fixed corner-grid holdout comparing original four and expanded seven sessions",
        "seed": seed,
        "test_rule": "grid_only_and_repetition_modulo_5_equals_0",
        "test_count": int(test.sum()),
        "baseline_training_sessions": list((*CENTRE_SESSION_IDS, *BASELINE_GRID_SESSION_IDS)),
        "candidate_training_sessions": list(DEFAULT_SESSION_IDS),
        "baseline_train_count": int(baseline_train.sum()),
        "candidate_train_count": int(candidate_train.sum()),
        "pc_direct_12class": {
            **_comparison(expected, baseline_direct, candidate_direct),
            "iterations": [baseline_direct_iterations, candidate_direct_iterations],
        },
        "probability_mass_12class": {
            **_comparison(expected, baseline_density, candidate_density),
            "iterations": [baseline_density_iterations, candidate_density_iterations],
        },
        "edge_direct_12class": _comparison(expected, baseline_edge, candidate_edge),
        "position_top1_accuracy": {
            "baseline": float(np.mean(baseline_position == np.asarray([
                {tuple(row): index for index, row in enumerate(np.unique(dataset.xy_mm, axis=0))}[tuple(row)]
                for row in dataset.xy_mm[test]
            ]))),
            "candidate": float(np.mean(candidate_position == np.asarray([
                {tuple(row): index for index, row in enumerate(np.unique(dataset.xy_mm, axis=0))}[tuple(row)]
                for row in dataset.xy_mm[test]
            ]))),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output", type=Path,
        default=Path("artifacts/grid_session_growth_20260823/evaluation_report.json"),
    )
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    report = run(args.sessions, args.output, args.seed)
    for name in ("pc_direct_12class", "probability_mass_12class", "edge_direct_12class"):
        item = report[name]
        print(f"{name}: {item['baseline']['accuracy']:.6f} -> "
              f"{item['candidate']['accuracy']:.6f} ({item['accuracy_delta_points']:+.3f} pt)")


if __name__ == "__main__":
    main()
