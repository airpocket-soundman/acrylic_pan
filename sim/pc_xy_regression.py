"""Evaluate unrestricted PC neural networks for measured XY regression.

The experiment deliberately reuses the same four centre-point sessions and
leave-one-session-out folds as :mod:`sim.sampling_experiment`.  It therefore
isolates model capacity from data coverage: the result describes decoding of
the eight recorded centres, not interpolation to an unseen coordinate.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import warnings

import joblib
import matplotlib.pyplot as plt
import numpy as np
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.preprocessing import StandardScaler

from .sampling_experiment import (
    AREA_CENTRES_MM,
    DEFAULT_SESSION_IDS,
    PANEL_HEIGHT_MM,
    PANEL_WIDTH_MM,
    RATE_SPECS,
    _cell_ids,
    _features_for,
    load_experiment_dataset,
    make_loso_folds,
    regression_metrics,
)

DIRECT_HIDDEN = (256, 128, 64)
PROBABILISTIC_HIDDEN = (256, 128)
DEFAULT_SEEDS = (1, 7, 21)
SOLIST_BASELINE_MM = 47.14857460360778


def parameter_count(input_count: int, hidden: tuple[int, ...], output_count: int) -> int:
    sizes = (input_count, *hidden, output_count)
    return int(sum((left + 1) * right for left, right in zip(sizes[:-1], sizes[1:])))


def _metric_summary(expected: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    result = regression_metrics(expected, predicted)
    result["area_accuracy"] = float(np.mean(_cell_ids(predicted) == _cell_ids(expected)))
    return result


def _direct_model(seed: int) -> MLPRegressor:
    return MLPRegressor(
        hidden_layer_sizes=DIRECT_HIDDEN,
        activation="relu",
        solver="adam",
        alpha=1e-3,
        batch_size=64,
        learning_rate_init=5e-4,
        max_iter=700,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=40,
        random_state=seed,
    )


def _probabilistic_model(seed: int) -> MLPClassifier:
    return MLPClassifier(
        hidden_layer_sizes=PROBABILISTIC_HIDDEN,
        activation="relu",
        solver="adam",
        alpha=1e-3,
        batch_size=64,
        learning_rate_init=5e-4,
        max_iter=700,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=40,
        random_state=seed,
    )


def _fold_record(
    fold: int,
    train_sessions: tuple[str, ...],
    test_session: str,
    train_count: int,
    test_count: int,
    metrics: dict[str, Any],
    iterations: int,
) -> dict[str, Any]:
    return {
        "fold": fold,
        "train_sessions": list(train_sessions),
        "test_session": test_session,
        "train_count": train_count,
        "test_count": test_count,
        "iterations": iterations,
        **metrics,
    }


def _make_figure(report: dict[str, Any], output: Path) -> None:
    direct = report["direct_xy_mlp"]["ensemble_metrics"]
    probabilistic = report["probabilistic_coordinate_mlp"]["ensemble_metrics"]
    labels = ["Solist ELM\n(direct XY)", "PC MLP\n(direct XY)", "PC MLP\n(probability→XY)"]
    values = [SOLIST_BASELINE_MM, direct["mean_distance_mm"], probabilistic["mean_distance_mm"]]
    colors = ["#8994a4", "#1f77b4", "#2ca58d"]
    figure, axis = plt.subplots(figsize=(8.8, 4.8))
    bars = axis.bar(labels, values, color=colors, width=0.62)
    axis.set_ylabel("LOSO mean distance error (mm)")
    axis.set_title("Measured centre points: same 1,534 events and four held-out sessions")
    axis.set_ylim(0, max(values) * 1.18)
    axis.grid(axis="y", alpha=0.25)
    for bar, value in zip(bars, values):
        axis.text(bar.get_x() + bar.get_width() / 2, value + 1.0, f"{value:.2f} mm", ha="center")
    axis.text(
        0.99, 0.97,
        "Centre-point decoding only; no unseen-coordinate interpolation",
        transform=axis.transAxes, ha="right", va="top", fontsize=9, color="#52606d",
    )
    figure.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output, format="svg", metadata={"Date": None})
    plt.close(figure)


def run(
    sessions_root: Path,
    output_dir: Path,
    web_output_dir: Path,
    seeds: tuple[int, ...] = DEFAULT_SEEDS,
) -> dict[str, Any]:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be non-empty and unique")
    dataset = load_experiment_dataset(sessions_root, DEFAULT_SESSION_IDS)
    spec = RATE_SPECS[-1]
    features = _features_for(dataset, spec, "hybrid")
    targets = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    folds = make_loso_folds(dataset.session_ids)
    output_dir.mkdir(parents=True, exist_ok=True)
    web_output_dir.mkdir(parents=True, exist_ok=True)

    direct_predictions = np.empty((len(seeds), len(dataset.labels), 2), dtype=np.float64)
    probabilistic_predictions = np.empty_like(direct_predictions)
    direct_runs: list[dict[str, Any]] = []
    probabilistic_runs: list[dict[str, Any]] = []

    warnings.filterwarnings("ignore", category=ConvergenceWarning)
    for seed_index, seed in enumerate(seeds):
        direct_fold_results: list[dict[str, Any]] = []
        probabilistic_fold_results: list[dict[str, Any]] = []
        for fold_index, (train_sessions, test_session) in enumerate(folds, start=1):
            train = np.isin(dataset.session_ids, train_sessions)
            test = dataset.session_ids == test_session
            scaler = StandardScaler().fit(features[train])
            train_x = scaler.transform(features[train])
            test_x = scaler.transform(features[test])

            direct = _direct_model(seed)
            direct.fit(train_x, targets[train])
            predicted = np.clip(direct.predict(test_x), 0.0, 1.0) * (
                PANEL_WIDTH_MM, PANEL_HEIGHT_MM
            )
            direct_predictions[seed_index, test] = predicted
            direct_fold_results.append(_fold_record(
                fold_index, train_sessions, test_session, int(train.sum()), int(test.sum()),
                _metric_summary(dataset.xy_mm[test], predicted), direct.n_iter_,
            ))

            probabilistic = _probabilistic_model(seed)
            probabilistic.fit(train_x, dataset.labels[train])
            probabilities = probabilistic.predict_proba(test_x)
            predicted = probabilities @ AREA_CENTRES_MM
            probabilistic_predictions[seed_index, test] = predicted
            probabilistic_fold_results.append(_fold_record(
                fold_index, train_sessions, test_session, int(train.sum()), int(test.sum()),
                _metric_summary(dataset.xy_mm[test], predicted), probabilistic.n_iter_,
            ))

        direct_runs.append({
            "seed": seed,
            "metrics": _metric_summary(dataset.xy_mm, direct_predictions[seed_index]),
            "folds": direct_fold_results,
        })
        probabilistic_runs.append({
            "seed": seed,
            "metrics": _metric_summary(dataset.xy_mm, probabilistic_predictions[seed_index]),
            "folds": probabilistic_fold_results,
        })

    direct_ensemble = direct_predictions.mean(axis=0)
    probabilistic_ensemble = probabilistic_predictions.mean(axis=0)
    direct_metrics = _metric_summary(dataset.xy_mm, direct_ensemble)
    probabilistic_metrics = _metric_summary(dataset.xy_mm, probabilistic_ensemble)

    # Fit deployable PC-side models on all four sessions.  Evaluation above is
    # kept separate and uses only out-of-session predictions.
    final_scaler = StandardScaler().fit(features)
    final_x = final_scaler.transform(features)
    final_models = []
    for seed in seeds:
        model = _direct_model(seed)
        model.fit(final_x, targets)
        final_models.append(model)
    joblib.dump(
        {"scaler": final_scaler, "models": final_models},
        output_dir / "direct_xy_ensemble.joblib",
        compress=3,
    )

    report: dict[str, Any] = {
        "experiment": "measured_centre_pc_xy_nn_v1",
        "scope": (
            "Eight recorded area centres only. This tests model-capacity-limited coordinate "
            "decoding and does not test interpolation to an unseen position."
        ),
        "dataset": {
            "session_ids": list(DEFAULT_SESSION_IDS),
            "sample_count": int(len(dataset.labels)),
            "class_counts": np.bincount(dataset.labels, minlength=8).astype(int).tolist(),
            "sha256": dataset.dataset_sha256,
            "split": "leave-one-acquisition-session-out, four folds",
        },
        "features": {
            "sample_rate_hz": spec.target_rate_hz,
            "input_count": int(features.shape[1]),
            "mode": "64 force-normalized time samples + 64 normalized FFT bands",
            "scaling": "StandardScaler fitted on each fold's training sessions only",
        },
        "solist_direct_xy_baseline": {
            "hidden": [32],
            "fixed_input_projection": True,
            "mean_distance_mm": SOLIST_BASELINE_MM,
        },
        "direct_xy_mlp": {
            "hidden": list(DIRECT_HIDDEN),
            "trainable_parameters": parameter_count(features.shape[1], DIRECT_HIDDEN, 2),
            "seeds": list(seeds),
            "runs": direct_runs,
            "ensemble_metrics": direct_metrics,
            "mean_single_seed_distance_mm": float(np.mean([
                run["metrics"]["mean_distance_mm"] for run in direct_runs
            ])),
            "improvement_vs_solist_percent": float(
                100.0 * (SOLIST_BASELINE_MM - direct_metrics["mean_distance_mm"])
                / SOLIST_BASELINE_MM
            ),
        },
        "probabilistic_coordinate_mlp": {
            "description": "Eight-class softmax probabilities multiplied by the eight centre coordinates",
            "hidden": list(PROBABILISTIC_HIDDEN),
            "trainable_parameters": parameter_count(features.shape[1], PROBABILISTIC_HIDDEN, 8),
            "seeds": list(seeds),
            "runs": probabilistic_runs,
            "ensemble_metrics": probabilistic_metrics,
        },
        "software": {"numpy": np.__version__, "scikit_learn": sklearn.__version__},
        "final_model": str(output_dir / "direct_xy_ensemble.joblib"),
    }
    result_path = web_output_dir / "pc-xy-regression-results.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    np.savez_compressed(
        output_dir / "loso_predictions.npz",
        expected_xy_mm=dataset.xy_mm,
        labels=dataset.labels,
        session_ids=dataset.session_ids,
        direct_predictions_mm=direct_predictions,
        direct_ensemble_mm=direct_ensemble,
        probabilistic_predictions_mm=probabilistic_predictions,
        probabilistic_ensemble_mm=probabilistic_ensemble,
        seeds=np.asarray(seeds),
    )
    _make_figure(report, web_output_dir / "pc-xy-regression-comparison.svg")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/pc_xy_regression"))
    parser.add_argument(
        "--web-output-dir", type=Path,
        default=Path("web/assets/experiment/pc-xy-regression"),
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir, args.web_output_dir, tuple(args.seeds))
    direct = report["direct_xy_mlp"]
    probabilistic = report["probabilistic_coordinate_mlp"]
    print(f"samples={report['dataset']['sample_count']}; folds=4; seeds={direct['seeds']}")
    print(f"Solist direct XY={SOLIST_BASELINE_MM:.2f} mm")
    print(f"PC direct XY ensemble={direct['ensemble_metrics']['mean_distance_mm']:.2f} mm")
    print(f"improvement={direct['improvement_vs_solist_percent']:.1f}%")
    print(
        "PC probability-to-XY ensemble="
        f"{probabilistic['ensemble_metrics']['mean_distance_mm']:.2f} mm"
    )


if __name__ == "__main__":
    main()
