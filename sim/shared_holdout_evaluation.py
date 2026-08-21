"""Compare growing session sets on one fixed, leakage-free event holdout."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.stats import binomtest

from .real_model_pipeline import (
    DEFAULT_ALPHA,
    FeatureScaler,
    confusion_matrix,
    fit_beta,
    load_hybrid_dataset,
    load_official_sim_alpha,
    mcu_reference,
)

DEFAULT_SEED = 20260821


def select_stratified_holdout(
    labels: np.ndarray,
    session_ids: np.ndarray,
    event_paths: tuple[Path, ...],
    sessions: tuple[str, ...],
    class_count: int,
    per_class: int,
    seed: int = DEFAULT_SEED,
) -> np.ndarray:
    """Select the same deterministic event holdout from every session/class."""
    if per_class < 1:
        raise ValueError("holdout per class must be positive")
    mask = np.zeros(len(labels), dtype=bool)
    for session_id in sessions:
        for class_id in range(class_count):
            members = np.flatnonzero((session_ids == session_id) & (labels == class_id))
            if len(members) <= per_class:
                raise ValueError(
                    f"session {session_id} class {class_id} needs more than "
                    f"{per_class} events"
                )
            ranked = sorted(
                members,
                key=lambda index: hashlib.sha256(
                    f"{seed}|{session_id}|{event_paths[index].name}".encode("utf-8")
                ).digest(),
            )
            mask[ranked[:per_class]] = True
    return mask


def evaluate_growth(
    source: Path,
    sessions: tuple[str, ...],
    output: Path,
    *,
    alpha_path: Path = DEFAULT_ALPHA,
    ridge: float = 0.001,
    class_count: int = 12,
    holdout_per_class: int = 10,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    if len(sessions) < 2 or len(set(sessions)) != len(sessions):
        raise ValueError("provide at least two unique sessions in growth order")
    dataset, features = load_hybrid_dataset(source, class_count, sessions)
    holdout = select_stratified_holdout(
        dataset.labels, dataset.session_ids, dataset.event_paths,
        sessions, class_count, holdout_per_class, seed,
    )
    expected = dataset.labels[holdout]
    evaluation_sessions = dataset.session_ids[holdout]
    alpha = load_official_sim_alpha(alpha_path)
    selected_keys = sorted(
        f"{dataset.session_ids[index]}|{dataset.labels[index]}|{dataset.event_paths[index].name}"
        for index in np.flatnonzero(holdout)
    )
    holdout_hash = hashlib.sha256("\n".join(selected_keys).encode("utf-8")).hexdigest()

    model_results = []
    model_predictions = []
    for session_count in range(2, len(sessions) + 1):
        training_sessions = sessions[:session_count]
        train = (~holdout) & np.isin(dataset.session_ids, np.asarray(training_sessions))
        scaler = FeatureScaler.fit(features[train])
        train_x = scaler.transform(features[train])
        test_x = scaler.transform(features[holdout])
        beta = fit_beta(train_x, dataset.labels[train], alpha, ridge, class_count)
        predicted = np.argmax(mcu_reference(test_x, alpha, beta), axis=1)
        model_predictions.append(predicted)
        matrix = confusion_matrix(expected, predicted, class_count)
        recalls = np.diag(matrix) / np.maximum(matrix.sum(axis=1), 1)
        session_accuracy = {
            session_id: float(np.mean(
                predicted[evaluation_sessions == session_id]
                == expected[evaluation_sessions == session_id]
            ))
            for session_id in sessions
        }
        model_results.append({
            "session_count": session_count,
            "training_sessions": list(training_sessions),
            "training_count": int(train.sum()),
            "correct": int(np.sum(predicted == expected)),
            "accuracy": float(np.mean(predicted == expected)),
            "session_accuracy": session_accuracy,
            "per_class_recall": recalls.astype(float).tolist(),
            "confusion_matrix": matrix.tolist(),
        })

    pairwise_changes = []
    for previous_result, current_result, previous, current in zip(
        model_results, model_results[1:], model_predictions, model_predictions[1:]
    ):
        previous_correct = previous == expected
        current_correct = current == expected
        improved = int(np.sum((~previous_correct) & current_correct))
        regressed = int(np.sum(previous_correct & (~current_correct)))
        discordant = improved + regressed
        pairwise_changes.append({
            "from_session_count": previous_result["session_count"],
            "to_session_count": current_result["session_count"],
            "improved_events": improved,
            "regressed_events": regressed,
            "net_additional_correct": improved - regressed,
            "accuracy_delta_points": float(
                (current_result["accuracy"] - previous_result["accuracy"]) * 100.0
            ),
            "mcnemar_exact_p": float(
                binomtest(improved, discordant, 0.5).pvalue if discordant else 1.0
            ),
        })

    report = {
        "method": "fixed stratified event holdout before every training run",
        "sessions": list(sessions),
        "sample_count": int(len(dataset.labels)),
        "class_count": class_count,
        "holdout_per_session_per_class": holdout_per_class,
        "holdout_count": int(holdout.sum()),
        "holdout_class_counts": np.bincount(expected, minlength=class_count).astype(int).tolist(),
        "holdout_seed": seed,
        "holdout_sha256": holdout_hash,
        "ridge_l2": ridge,
        "models": model_results,
        "pairwise_changes": pairwise_changes,
        "interpretation": (
            "Every event in the shared holdout is excluded before fitting. "
            "Because models can still train on other events from the same acquisition session, "
            "this measures fixed event generalization, not strict unseen-session generalization."
        ),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--session-id", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alpha", type=Path, default=DEFAULT_ALPHA)
    parser.add_argument("--ridge", type=float, default=0.001)
    parser.add_argument("--class-count", type=int, default=12)
    parser.add_argument("--holdout-per-class", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    report = evaluate_growth(
        args.sessions, tuple(args.session_id), args.output,
        alpha_path=args.alpha, ridge=args.ridge, class_count=args.class_count,
        holdout_per_class=args.holdout_per_class, seed=args.seed,
    )
    print(f"holdout={report['holdout_count']}; sha256={report['holdout_sha256']}")
    for model in report["models"]:
        print(
            f"sessions={model['session_count']}; train={model['training_count']}; "
            f"accuracy={model['accuracy']:.6f}; correct={model['correct']}"
        )


if __name__ == "__main__":
    main()
