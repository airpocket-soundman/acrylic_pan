"""Compare direct 12-area classification with area labels derived from XY models."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import warnings

import joblib
import numpy as np
from scipy.stats import binomtest
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier
from sklearn.preprocessing import StandardScaler

from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference
from .pc_position_grid_runtime import (
    CLASS_COUNT,
    DEFAULT_SEEDS,
    DEFAULT_SESSION_IDS,
    PANEL_HEIGHT_MM,
    PANEL_WIDTH_MM,
    _balanced_density_indices,
    _density_model,
    _model,
    extract_grid_features,
    load_position_dataset,
)
from .real_model_pipeline import DEFAULT_ALPHA, FeatureScaler, extract_hybrid_features, fit_beta
from .sampling_experiment import classification_metrics, regression_metrics


def _area_ids(xy_mm: np.ndarray) -> np.ndarray:
    """Map 400 x 300 mm coordinates to row-major 100 mm area IDs."""
    clipped = np.clip(
        np.asarray(xy_mm, dtype=np.float64),
        (0.0, 0.0),
        (PANEL_WIDTH_MM - 1e-6, PANEL_HEIGHT_MM - 1e-6),
    )
    return (
        np.floor(clipped[:, 1] / 100.0).astype(np.int64) * 4
        + np.floor(clipped[:, 0] / 100.0).astype(np.int64)
    )


def _direct_area_model(seed: int) -> MLPClassifier:
    return MLPClassifier(
        hidden_layer_sizes=(384, 192, 96), activation="relu", solver="adam",
        alpha=1e-3, batch_size=128, learning_rate_init=1e-3, max_iter=350,
        early_stopping=True, validation_fraction=0.15, n_iter_no_change=20,
        random_state=seed,
    )


def _paired_comparison(expected: np.ndarray, direct: np.ndarray, candidate: np.ndarray) -> dict:
    direct_correct = direct == expected
    candidate_correct = candidate == expected
    direct_only = int(np.sum(direct_correct & ~candidate_correct))
    candidate_only = int(np.sum(~direct_correct & candidate_correct))
    discordant = direct_only + candidate_only
    return {
        "direct_only_correct": direct_only,
        "candidate_only_correct": candidate_only,
        "net_direct_additional_correct": direct_only - candidate_only,
        "accuracy_delta_direct_minus_candidate_points": float(
            100.0 * (np.mean(direct_correct) - np.mean(candidate_correct))
        ),
        "mcnemar_exact_p": float(
            binomtest(min(direct_only, candidate_only), discordant, 0.5).pvalue
            if discordant else 1.0
        ),
    }


def run(sessions_root: Path, output: Path, seeds: tuple[int, ...] = DEFAULT_SEEDS,
        model_output: Path | None = None) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    train = dataset.repetitions % 5 != 0
    test = ~train
    expected = dataset.labels[test]
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    # Device-compatible fixed-projection ELM, retrained on every non-holdout event.
    edge_scaler = FeatureScaler.fit(edge[train])
    alpha = load_official_sim_alpha(DEFAULT_ALPHA)
    if alpha.shape != (edge.shape[1], 32):
        raise ValueError(f"unexpected Solist alpha shape: {alpha.shape}")
    beta = fit_beta(edge_scaler.transform(edge[train]), dataset.labels[train], alpha,
                    ridge=1e-3, class_count=CLASS_COUNT)
    edge_prediction = np.argmax(
        mcu_reference(edge_scaler.transform(edge[test]), alpha, beta), axis=1
    )

    # Unrestricted direct classifier: same rich input, split, architecture and seeds as XY.
    area_indices = _balanced_density_indices(dataset.labels, train, seeds[0])
    direct_scaler = StandardScaler().fit(rich[area_indices])
    direct_probabilities = []
    direct_iterations = []
    for seed in seeds:
        model = _direct_area_model(seed)
        model.fit(direct_scaler.transform(rich[area_indices]), dataset.labels[area_indices])
        direct_probabilities.append(model.predict_proba(direct_scaler.transform(rich[test])))
        direct_iterations.append(int(model.n_iter_))
    direct_prediction = np.argmax(np.mean(direct_probabilities, axis=0), axis=1)

    # Literal XY regression; convert its ensemble mean coordinate back to a 4x3 area.
    xy_scaler = StandardScaler().fit(rich[train])
    normalized_xy = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    xy_predictions = []
    xy_iterations = []
    for seed in seeds:
        model = _model(seed)
        model.fit(xy_scaler.transform(rich[train]), normalized_xy[train])
        predicted = np.clip(model.predict(xy_scaler.transform(rich[test])), 0.0, 1.0)
        xy_predictions.append(predicted * (PANEL_WIDTH_MM, PANEL_HEIGHT_MM))
        xy_iterations.append(int(model.n_iter_))
    xy_prediction = np.mean(xy_predictions, axis=0)
    xy_area_prediction = _area_ids(xy_prediction)

    # Current UI method: a 60-position probability distribution and its expected XY.
    support_xy = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support_xy)}
    position_labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm])
    position_indices = _balanced_density_indices(position_labels, train, seeds[0])
    position_scaler = StandardScaler().fit(rich[position_indices])
    position_probabilities = []
    position_iterations = []
    for seed in seeds:
        model = _density_model(seed)
        model.fit(position_scaler.transform(rich[position_indices]), position_labels[position_indices])
        position_probabilities.append(model.predict_proba(position_scaler.transform(rich[test])))
        position_iterations.append(int(model.n_iter_))
    mean_position_probability = np.mean(position_probabilities, axis=0)
    probability_xy = mean_position_probability @ support_xy
    probability_area_prediction = _area_ids(probability_xy)
    support_areas = _area_ids(support_xy)
    area_probability = np.stack([
        mean_position_probability[:, support_areas == class_id].sum(axis=1)
        for class_id in range(CLASS_COUNT)
    ], axis=1)
    probability_sum_prediction = np.argmax(area_probability, axis=1)

    centre = np.asarray([name == "center" for name in dataset.point_names[test]])
    methods = {
        "edge_direct_12class": edge_prediction,
        "pc_direct_12class": direct_prediction,
        "xy_regression_to_area": xy_area_prediction,
        "probability_expected_xy_to_area": probability_area_prediction,
        "probability_mass_to_area": probability_sum_prediction,
    }
    metrics = {}
    for name, prediction in methods.items():
        metrics[name] = {
            **classification_metrics(expected, prediction, CLASS_COUNT),
            "correct": int(np.sum(prediction == expected)),
            "centre_accuracy": float(np.mean(prediction[centre] == expected[centre])),
            "corner_accuracy": float(np.mean(prediction[~centre] == expected[~centre])),
        }
    report = {
        "method": "same leakage-free repetition holdout for direct and coordinate-derived area classification",
        "session_ids": list(DEFAULT_SESSION_IDS),
        "dataset_sha256": dataset.dataset_sha256,
        "sample_count": int(len(dataset.samples)),
        "train_count": int(train.sum()),
        "test_count": int(test.sum()),
        "holdout_rule": "repetition_modulo_5_equals_0",
        "holdout_centre_count": int(centre.sum()),
        "holdout_corner_count": int((~centre).sum()),
        "seeds": list(seeds),
        "iterations": {
            "pc_direct_12class": direct_iterations,
            "xy_regression": xy_iterations,
            "probability_60position": position_iterations,
        },
        "metrics": metrics,
        "xy_regression_metrics": regression_metrics(dataset.xy_mm[test], xy_prediction),
        "paired_vs_pc_direct": {
            name: _paired_comparison(expected, direct_prediction, prediction)
            for name, prediction in methods.items() if name != "pc_direct_12class"
        },
        "interpretation": (
            "PC direct classification is the primary best-12-class comparator. "
            "XY regression is converted with 100 mm cell boundaries. Probability-mass "
            "aggregation is reported separately because it does not pass through one XY point."
        ),
    }
    if model_output is not None:
        all_rows = np.ones(len(dataset.labels), dtype=bool)
        final_indices = _balanced_density_indices(dataset.labels, all_rows, seeds[0])
        final_scaler = StandardScaler().fit(rich[final_indices])
        final_models = []
        final_iterations = []
        transformed = final_scaler.transform(rich[final_indices])
        for seed in seeds:
            model = _direct_area_model(seed)
            model.fit(transformed, dataset.labels[final_indices])
            final_models.append(model)
            final_iterations.append(int(model.n_iter_))
        bundle = {
            "scaler": final_scaler,
            "models": final_models,
            "contract": {
                "panel_profile_id": "400x300x5",
                "sample_rate_hz": 25_600,
                "sample_count": 512,
                "trigger_index": 64,
                "feature_mode": "pc_rich_20ms_v1",
                "class_count": CLASS_COUNT,
                "class_order": list(range(CLASS_COUNT)),
            },
            "training": {
                "session_ids": list(DEFAULT_SESSION_IDS),
                "sample_count": int(len(dataset.samples)),
                "balanced_training_count": int(len(final_indices)),
                "dataset_sha256": dataset.dataset_sha256,
                "seeds": list(seeds),
                "iterations": final_iterations,
            },
            "validation": metrics["pc_direct_12class"],
            "method": "pc_mlp_direct_12class_from_60_measured_positions",
        }
        model_output.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(bundle, model_output, compress=3)
        model_sha256 = hashlib.sha256(model_output.read_bytes()).hexdigest()
        report["final_pc_direct_model"] = {
            "path": str(model_output),
            "sha256": model_sha256,
            **bundle["training"],
        }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output", type=Path,
        default=Path("artifacts/area_classification_comparison_20260823/evaluation_report.json"),
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument(
        "--model-output", type=Path,
        default=Path("artifacts/pc_area_classifier_400x300x5/area_classifier.joblib"),
    )
    args = parser.parse_args()
    report = run(args.sessions, args.output, tuple(args.seeds), args.model_output)
    for name, metrics in report["metrics"].items():
        print(f"{name}: {metrics['accuracy']:.6f} ({metrics['correct']}/{report['test_count']})")
    print(f"report={args.output}")


if __name__ == "__main__":
    main()
