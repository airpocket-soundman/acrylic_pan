"""Compare larger Solist-style 60-position models on the same holdout.

This is a PC-only capacity experiment.  The 32-node baseline uses the exact
official seed-1 Alpha deployed by the project.  Larger candidates retain those
32 columns and append bfloat16-quantized uniform random columns with the same
range.  Several append seeds are evaluated so that a lucky random projection
is not mistaken for an architectural improvement.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np
from scipy.special import softmax
from sklearn.preprocessing import StandardScaler

from .device_position_probability import (
    DISTILLATION_WEIGHT,
    INPUT_FEATURE_COUNT,
    POSITION_COUNT,
    SOFTMAX_TEMPERATURE,
    _engine_inputs,
    _teacher_probabilities,
    bfloat_logits,
)
from .dummy_model_pipeline import load_official_sim_alpha, quantize_bfloat16
from .pc_position_grid_runtime import (
    DEFAULT_SESSION_IDS,
    extract_grid_features,
    load_position_dataset,
    parse_session_ids,
)
from .real_model_pipeline import extract_hybrid_features


def _expanded_alpha(hidden: int, seed: int) -> np.ndarray:
    baseline = load_official_sim_alpha()
    if hidden < baseline.shape[1] or hidden > 64:
        raise ValueError("hidden must be between 32 and the Solist limit 64")
    if hidden == baseline.shape[1]:
        return baseline
    rng = np.random.default_rng(seed)
    scale = float(np.max(np.abs(baseline)))
    extra = rng.uniform(-scale, scale, (baseline.shape[0], hidden - baseline.shape[1]))
    return np.concatenate((baseline, quantize_bfloat16(extra.astype(np.float32))), axis=1)


def _fit(inputs: np.ndarray, targets: np.ndarray, alpha: np.ndarray,
         epochs: int) -> np.ndarray:
    rng = np.random.default_rng(1)
    beta = rng.normal(0.0, 0.08, (alpha.shape[1], POSITION_COUNT)).astype(np.float32)
    first = np.zeros_like(beta)
    second = np.zeros_like(beta)
    step = 0
    for _ in range(epochs):
        order = rng.permutation(len(inputs))
        for start in range(0, len(inputs), 128):
            batch = order[start:start + 128]
            hidden = np.clip(0.2 * (inputs[batch] @ alpha) + 0.5, 0.0, 1.0)
            probability = softmax((hidden @ beta) / SOFTMAX_TEMPERATURE, axis=1)
            gradient = hidden.T @ ((probability - targets[batch]) /
                                   (len(batch) * SOFTMAX_TEMPERATURE))
            step += 1
            first = 0.9 * first + 0.1 * gradient
            second = 0.999 * second + 0.001 * gradient * gradient
            beta -= 5e-4 * (first / (1.0 - 0.9 ** step)) / (
                np.sqrt(second / (1.0 - 0.999 ** step)) + 1e-8)
            np.clip(beta, -0.5, 0.5, out=beta)
    return beta


def _area(xy: np.ndarray) -> np.ndarray:
    return (np.clip(xy[:, 1] // 100, 0, 2) * 4 +
            np.clip(xy[:, 0] // 100, 0, 3)).astype(np.int64)


def _evaluate(expected_xy: np.ndarray, labels: np.ndarray, inputs: np.ndarray,
              alpha: np.ndarray, beta: np.ndarray, support: np.ndarray) -> dict:
    probability = softmax(bfloat_logits(inputs, alpha, beta) / SOFTMAX_TEMPERATURE, axis=1)
    best = np.argmax(probability, axis=1)
    expected = probability @ support
    distance = np.linalg.norm(expected - expected_xy, axis=1)
    return {
        "top1_position_accuracy": float(np.mean(best == labels)),
        "area_accuracy": float(np.mean(_area(support[best]) == _area(expected_xy))),
        "mean_distance_mm": float(np.mean(distance)),
        "median_distance_mm": float(np.median(distance)),
        "within_25mm": float(np.mean(distance <= 25.0)),
    }


def run(sessions_root: Path, session_ids: tuple[str, ...], hidden_sizes: tuple[int, ...],
        seeds: tuple[int, ...], epochs: int) -> dict:
    dataset = load_position_dataset(sessions_root, session_ids)
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    train = dataset.repetitions % 5 != 0
    test = ~train
    teacher = _teacher_probabilities(rich, labels, train, train)
    targets = ((1.0 - DISTILLATION_WEIGHT) * np.eye(POSITION_COUNT, dtype=np.float32)[labels[train]]
               + DISTILLATION_WEIGHT * teacher.astype(np.float32))
    scaler = StandardScaler().fit(edge[train, :INPUT_FEATURE_COUNT])
    train_inputs = _engine_inputs(edge[train], scaler)
    test_inputs = _engine_inputs(edge[test], scaler)

    rows = []
    for hidden in hidden_sizes:
        run_seeds = (seeds[0],) if hidden == 32 else seeds
        for seed in run_seeds:
            alpha = _expanded_alpha(hidden, seed)
            started = perf_counter()
            beta = _fit(train_inputs, targets, alpha, epochs)
            metrics = _evaluate(dataset.xy_mm[test], labels[test], test_inputs,
                                alpha, beta, support)
            rows.append({
                "hidden": hidden,
                "append_seed": seed,
                "parameters": int(128 * hidden + hidden * POSITION_COUNT),
                "beta_parameters": int(hidden * POSITION_COUNT),
                "bfloat16_model_bytes": int((128 * hidden + hidden * POSITION_COUNT) * 2),
                "fit_seconds": perf_counter() - started,
                **metrics,
            })

    summary = []
    for hidden in hidden_sizes:
        selected = [row for row in rows if row["hidden"] == hidden]
        item = {"hidden": hidden, "runs": len(selected),
                "parameters": selected[0]["parameters"],
                "bfloat16_model_bytes": selected[0]["bfloat16_model_bytes"]}
        for key in ("top1_position_accuracy", "area_accuracy", "mean_distance_mm",
                    "median_distance_mm", "within_25mm"):
            values = np.asarray([row[key] for row in selected])
            item[key + "_mean"] = float(values.mean())
            item[key + "_min"] = float(values.min())
            item[key + "_max"] = float(values.max())
        summary.append(item)
    return {
        "experiment": "pc_only_solist_hidden_size_comparison",
        "session_ids": list(session_ids),
        "sample_count": int(len(dataset.samples)),
        "train_count": int(train.sum()),
        "test_count": int(test.sum()),
        "holdout_rule": "repetition_modulo_5_equals_0",
        "epochs": epochs,
        "distillation_weight": DISTILLATION_WEIGHT,
        "alpha_note": "32 official columns plus same-range random BF16 columns; larger sizes are simulation candidates",
        "rows": rows,
        "summary": summary,
    }


def run_loso(sessions_root: Path, session_ids: tuple[str, ...],
             hidden_sizes: tuple[int, ...], seeds: tuple[int, ...], epochs: int) -> dict:
    """Evaluate capacity on sessions that are completely absent from training."""
    dataset = load_position_dataset(sessions_root, session_ids)
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    owners = np.asarray([session_ids.index(name) for name in dataset.session_ids])
    rows = []
    for fold, session in enumerate(session_ids):
        test = owners == fold
        train = ~test
        scaler = StandardScaler().fit(edge[train, :INPUT_FEATURE_COUNT])
        train_inputs = _engine_inputs(edge[train], scaler)
        test_inputs = _engine_inputs(edge[test], scaler)
        targets = np.eye(POSITION_COUNT, dtype=np.float32)[labels[train]]
        for hidden in hidden_sizes:
            run_seeds = (seeds[0],) if hidden == 32 else seeds
            for seed in run_seeds:
                alpha = _expanded_alpha(hidden, seed)
                beta = _fit(train_inputs, targets, alpha, epochs)
                rows.append({
                    "session": session,
                    "test_count": int(test.sum()),
                    "hidden": hidden,
                    "append_seed": seed,
                    **_evaluate(dataset.xy_mm[test], labels[test], test_inputs,
                                alpha, beta, support),
                })

    per_seed = []
    for hidden in hidden_sizes:
        run_seeds = (seeds[0],) if hidden == 32 else seeds
        for seed in run_seeds:
            selected = [row for row in rows
                        if row["hidden"] == hidden and row["append_seed"] == seed]
            total = sum(row["test_count"] for row in selected)
            item = {"hidden": hidden, "append_seed": seed, "test_count": total}
            for key in ("top1_position_accuracy", "area_accuracy", "mean_distance_mm",
                        "median_distance_mm", "within_25mm"):
                item[key] = float(sum(row[key] * row["test_count"] for row in selected) / total)
            per_seed.append(item)
    summary = []
    for hidden in hidden_sizes:
        selected = [row for row in per_seed if row["hidden"] == hidden]
        item = {"hidden": hidden, "runs": len(selected),
                "parameters": int(188 * hidden),
                "bfloat16_model_bytes": int(376 * hidden)}
        for key in ("top1_position_accuracy", "area_accuracy", "mean_distance_mm",
                    "median_distance_mm", "within_25mm"):
            values = np.asarray([row[key] for row in selected])
            item[key + "_mean"] = float(values.mean())
            item[key + "_min"] = float(values.min())
            item[key + "_max"] = float(values.max())
        summary.append(item)
    return {
        "experiment": "pc_only_solist_hidden_size_loso",
        "session_ids": list(session_ids),
        "sample_count": int(len(dataset.samples)),
        "folds": len(session_ids),
        "epochs": epochs,
        "targets": "one_hot; no teacher data from the held-out session",
        "alpha_note": "32 official columns plus same-range random BF16 columns; larger sizes are simulation candidates",
        "rows": rows,
        "per_seed": per_seed,
        "summary": summary,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions_2point"))
    parser.add_argument("--session-ids", default="default")
    parser.add_argument("--hidden-sizes", default="32,48,64")
    parser.add_argument("--seeds", default="7,21,42,84,168")
    parser.add_argument("--epochs", type=int, default=160)
    parser.add_argument("--output", type=Path,
                        default=Path("artifacts/position_model_size_20261024/report.json"))
    parser.add_argument("--loso", action="store_true",
                        help="leave one complete recording session out per fold")
    args = parser.parse_args()
    ids = parse_session_ids(args.session_ids, args.sessions)
    sizes = tuple(int(value) for value in args.hidden_sizes.split(","))
    seeds = tuple(int(value) for value in args.seeds.split(","))
    result = (run_loso(args.sessions, ids, sizes, seeds, args.epochs) if args.loso
              else run(args.sessions, ids, sizes, seeds, args.epochs))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
