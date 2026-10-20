"""Compare a 1-D CNN with the PC MLP and the Solist-AI ELM on the same data and the same hold-out.

Data: the 2-point-support sessions (60 positions).  Hold-out: every hit whose repetition number is a multiple
of five (the rule used by the device and PC training), so the three model families are evaluated on the same
720 hits.  The CNN needs PyTorch (not part of requirements.txt):

    python -m sim.compare_cnn_mlp_elm --sessions-root data/raw/sessions_2point
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from scipy.special import softmax
from sklearn.preprocessing import StandardScaler

from .device_position_probability import (
    ENGINE_HIDDEN_COUNT, ENGINE_INPUT_COUNT, INPUT_FEATURE_COUNT, POSITION_COUNT,
    SOFTMAX_TEMPERATURE, _engine_inputs, _fit_student, _teacher_probabilities, bfloat_logits,
)
from .pc_position_grid_runtime import (
    discover_session_ids, extract_grid_features, load_position_dataset,
)
from .real_model_pipeline import extract_hybrid_features
from .sampling_experiment import regression_metrics

TRIGGER_INDEX = 64
SAMPLE_RATE_HZ = 25600
SEEDS = (1, 7, 21)
EPOCHS = 120


def _top1_and_distance(probability: np.ndarray, labels: np.ndarray, support: np.ndarray, xy: np.ndarray) -> dict:
    result = regression_metrics(xy, probability @ support)
    result["top1_position_accuracy"] = float(np.mean(np.argmax(probability, axis=1) == labels))
    return result


def _time_series(samples: np.ndarray) -> np.ndarray:
    """The 448 samples after the trigger, baseline removed and scaled by the peak (the same view the ELM gets)."""
    centered = samples - samples[:, :TRIGGER_INDEX].mean(axis=1, keepdims=True)
    post = centered[:, TRIGGER_INDEX:]
    peak = np.maximum(np.abs(post).max(axis=1, keepdims=True), 1.0)
    return (post / peak).astype(np.float32)


def _build_models(torch, kind: str, length: int):
    nn = torch.nn
    if kind == "cnn_xs":
        return nn.Sequential(
            nn.Conv1d(1, 4, 9, stride=4, padding=4), nn.ReLU(),
            nn.Conv1d(4, 8, 9, stride=4, padding=4), nn.ReLU(), nn.AvgPool1d(2),
            nn.Flatten(), nn.Linear(8 * (length // 32), POSITION_COUNT))
    if kind == "cnn_s":
        return nn.Sequential(
            nn.Conv1d(1, 8, 9, stride=2, padding=4), nn.ReLU(),
            nn.Conv1d(8, 16, 9, stride=2, padding=4), nn.ReLU(),
            nn.Conv1d(16, 16, 9, stride=2, padding=4), nn.ReLU(), nn.AvgPool1d(4),
            nn.Flatten(), nn.Linear(16 * (length // 32), POSITION_COUNT))
    if kind == "cnn_l":
        return nn.Sequential(
            nn.Conv1d(1, 32, 9, stride=2, padding=4), nn.BatchNorm1d(32), nn.ReLU(),
            nn.Conv1d(32, 64, 9, stride=2, padding=4), nn.BatchNorm1d(64), nn.ReLU(),
            nn.Conv1d(64, 64, 9, stride=2, padding=4), nn.BatchNorm1d(64), nn.ReLU(),
            nn.Flatten(), nn.Linear(64 * (length // 8), 96), nn.ReLU(), nn.Linear(96, POSITION_COUNT))
    if kind == "mlp_elm_shape":   # the ELM's 128-32-60 with every layer trained
        return nn.Sequential(nn.Flatten(), nn.Linear(length, 32), nn.ReLU(), nn.Linear(32, POSITION_COUNT))
    if kind == "mlp_time":
        return nn.Sequential(
            nn.Flatten(), nn.Linear(length, 384), nn.ReLU(), nn.Linear(384, 192), nn.ReLU(),
            nn.Linear(192, 96), nn.ReLU(), nn.Linear(96, POSITION_COUNT))
    raise ValueError(kind)


def _train_torch(torch, kind: str, series: np.ndarray, labels: np.ndarray, train: np.ndarray,
                 test: np.ndarray, seed: int) -> tuple[np.ndarray, int, float]:
    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)
    model = _build_models(torch, kind, series.shape[1])
    parameters = sum(p.numel() for p in model.parameters())
    x_train = torch.from_numpy(series[train])[:, None, :]
    y_train = torch.from_numpy(labels[train])
    x_test = torch.from_numpy(series[test])[:, None, :]
    optimizer = torch.optim.Adam(model.parameters(), lr=2e-3, weight_decay=1e-5)
    steps = EPOCHS * int(np.ceil(len(x_train) / 64))
    schedule = torch.optim.lr_scheduler.OneCycleLR(optimizer, max_lr=3e-3, total_steps=steps)
    loss_fn = torch.nn.CrossEntropyLoss()
    started = time.time()
    for _ in range(EPOCHS):
        model.train()
        order = rng.permutation(len(x_train))
        for start in range(0, len(order), 64):
            batch = torch.from_numpy(order[start:start + 64])
            optimizer.zero_grad()
            loss_fn(model(x_train[batch]), y_train[batch]).backward()
            optimizer.step()
            schedule.step()
    model.eval()
    with torch.no_grad():
        probability = torch.softmax(model(x_test), dim=1).numpy()
    return probability, parameters, time.time() - started


def _evaluate(torch, dataset, labels: np.ndarray, support: np.ndarray, edge: np.ndarray, rich: np.ndarray,
              series: np.ndarray, train: np.ndarray, test: np.ndarray, verbose: bool = True) -> dict:
    """Train every model on ``train`` and score it on ``test``; returns one entry per model."""
    xy_test = dataset.xy_mm[test]
    labels_test = labels[test]
    models: dict = {}

    # Solist-AI ELM: fixed alpha, beta trained from the labels only (the deployed 128-32-60 model).
    scaler = StandardScaler().fit(edge[train, :INPUT_FEATURE_COUNT])
    inputs = _engine_inputs(edge, scaler)
    alpha, beta = _fit_student(inputs[train], labels[train], np.zeros((int(train.sum()), POSITION_COUNT)), 0.0)
    probability = softmax(bfloat_logits(inputs[test], alpha, beta) / SOFTMAX_TEMPERATURE, axis=1)
    models["solist_elm"] = {
        "structure": f"{ENGINE_INPUT_COUNT}-{ENGINE_HIDDEN_COUNT}-{POSITION_COUNT} (fixed alpha, trained beta)",
        "input": "127 points picked from the 448 samples after the trigger (+1 bias input)",
        "parameters": int(alpha.size + beta.size), "trained_parameters": int(beta.size),
        "bytes": int((alpha.size + beta.size) * 2), "dtype": "bfloat16",
        "runs": [_top1_and_distance(probability, labels_test, support, xy_test)],
    }

    # PC MLP: 714 features (time, spectrum, statistics), 3-seed ensemble, as shipped.
    probability = _teacher_probabilities(rich, labels, train, test)
    per_model = 714 * 384 + 384 + 384 * 192 + 192 + 192 * 96 + 96 + 96 * POSITION_COUNT + POSITION_COUNT
    models["pc_mlp"] = {
        "structure": "714-384-192-96-60 x 3 seeds (ensemble)",
        "input": "714 features: 448 time points, 256 spectrum bins, 10 statistics",
        "parameters": 3 * per_model, "parameters_per_model": per_model, "bytes": 3 * per_model * 4, "dtype": "float32",
        "runs": [_top1_and_distance(probability, labels_test, support, xy_test)],
    }

    # CNN and a plain MLP on the 448-sample time series (no spectrum, no statistics).
    for kind, structure in (
        ("cnn_xs", "Conv(1-4,s4) - Conv(4-8,s4) - Pool - Linear 60"),
        ("cnn_s", "Conv(1-8,s2) - Conv(8-16,s2) - Conv(16-16,s2) - Pool - Linear 60"),
        ("cnn_l", "Conv(1-32,s2) - Conv(32-64,s2) - Conv(64-64,s2) - Linear 96 - Linear 60 (BatchNorm)"),
        ("mlp_time", "448-384-192-96-60 (the same layers as the PC MLP, time series only)"),
        ("mlp_elm_shape", "128-32-60, all layers trained (the ELM's shape and inputs)"),
    ):
        runs = []
        parameters = 0
        seconds = 0.0
        for seed in SEEDS:
            inputs_for_kind = inputs.astype(np.float32) if kind == "mlp_elm_shape" else series
            probability, parameters, elapsed = _train_torch(torch, kind, inputs_for_kind, labels, train, test, seed)
            runs.append(_top1_and_distance(probability, labels_test, support, xy_test))
            seconds += elapsed
            if verbose:
                print(f"{kind} seed={seed}: top1={runs[-1]['top1_position_accuracy']:.4f} "
                      f"mean={runs[-1]['mean_distance_mm']:.2f} mm ({elapsed:.0f}s)", flush=True)
        models[kind] = {
            "structure": structure,
            "input": ("the ELM's 128 inputs" if kind == "mlp_elm_shape"
                      else "448 time samples after the trigger, peak-normalised"),
            "parameters": int(parameters), "bytes": int(parameters * 4), "dtype": "float32",
            "epochs": EPOCHS, "seeds": list(SEEDS), "runs": runs, "train_seconds": round(seconds, 1),
        }
    for entry in models.values():
        accuracy = [r["top1_position_accuracy"] for r in entry["runs"]]
        distance = [r["mean_distance_mm"] for r in entry["runs"]]
        entry["top1_mean"] = float(np.mean(accuracy))
        entry["top1_min"] = float(np.min(accuracy))
        entry["top1_max"] = float(np.max(accuracy))
        entry["mean_distance_mm_mean"] = float(np.mean(distance))
    return models


def run(sessions_root: Path, output: Path, loso: bool = False) -> dict:
    import torch

    session_ids = discover_session_ids(sessions_root)
    dataset = load_position_dataset(sessions_root, session_ids)
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    series = _time_series(dataset.samples)
    train = dataset.repetitions % 5 != 0
    report: dict = {
        "sessions": list(session_ids), "samples": int(len(labels)), "train": int(train.sum()),
        "test": int((~train).sum()), "holdout_rule": "repetition_modulo_5_equals_0",
        "dataset_sha256": dataset.dataset_sha256,
    }
    report["models"] = _evaluate(torch, dataset, labels, support, edge, rich, series, train, ~train)
    for name, entry in report["models"].items():
        print(f"{name}: {entry['parameters']:,} params, {entry['bytes'] / 1024:.1f} KiB, "
              f"top1 {entry['top1_mean']:.4f}, mean distance {entry['mean_distance_mm_mean']:.2f} mm", flush=True)

    if loso:
        # Leave one whole session out: the test hits come from a session the model has never seen.
        folds = []
        for session in session_ids:
            test = dataset.session_ids == session
            fold = {"session": session, "test": int(test.sum()),
                    "models": _evaluate(torch, dataset, labels, support, edge, rich, series, ~test, test, verbose=False)}
            folds.append(fold)
            print(f"LOSO {session} ({int(test.sum())} hits): " + ", ".join(
                f"{name} {entry['top1_mean']:.3f}" for name, entry in fold["models"].items()), flush=True)
        report["loso"] = folds
        for name in report["models"]:
            weights = np.asarray([fold["test"] for fold in folds], dtype=float)
            values = np.asarray([fold["models"][name]["top1_mean"] for fold in folds])
            report["models"][name]["loso_top1_weighted"] = float((weights * values).sum() / weights.sum())
            report["models"][name]["loso_top1_min"] = float(values.min())
            print(f"LOSO {name}: weighted top1 {report['models'][name]['loso_top1_weighted']:.4f}, "
                  f"worst session {values.min():.3f}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions-root", type=Path, default=Path("data/raw/sessions_2point"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/cnn_comparison/results.json"))
    parser.add_argument("--loso", action="store_true", help="also evaluate leave-one-session-out")
    args = parser.parse_args()
    run(args.sessions_root, args.output, args.loso)


if __name__ == "__main__":
    main()
