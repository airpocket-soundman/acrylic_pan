"""Evaluate on-site ODL calibration of the deployed 12-area Solist-AI model.

Each acquisition session is held out in turn (leave-one-session-out).  The
factory model is trained exactly like the deployed max-data model (seed-1
alpha, ridge 1, beta stored as bfloat16).  A few guided hits per class from the
held-out session then update beta with the OS-ELM recursion that Solist-AI's
ODL uses, and the rest of that session is evaluated.

Starting from the factory beta and ``P0 = w (G_f + lambda I)^-1`` (``G_f`` is
the factory hidden-layer Gram matrix), the recursion reproduces the batch fit
that weights every calibration hit ``w`` times relative to a factory event.
The bfloat16 path approximates ``ODL_StartTrain`` on AxlCORE by rounding the
hidden vector, P and beta after every update; the exact on-chip operation order
is not published, so it is an approximation, not a bit-exact model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference, quantize_bfloat16
from .pc_position_grid_runtime import (
    CENTRE_SESSION_IDS,
    DEFAULT_SESSION_IDS,
    load_position_dataset,
)
from .real_model_pipeline import DEFAULT_ALPHA, FeatureScaler, extract_hybrid_features, fit_beta

CLASS_COUNT = 12
FACTORY_RIDGE = 1.0
POOL_PER_CLASS = 10
CALIBRATION_PER_CLASS = (1, 2, 3, 5, 10)
SEEDS = (0, 1, 2, 3, 4)
PRECISIONS = ("float64", "float32", "float32_bf16beta", "bfloat16")
CALIBRATION_WEIGHTS = (1.0, 10.0, 30.0, 100.0, 300.0)
PRIOR_RIDGES = (1.0, 10.0)


def hidden_layer(features: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Hard-sigmoid hidden layer with the bfloat16 input/weight boundaries of the MCU."""
    xq = quantize_bfloat16(features).astype(np.float64)
    aq = quantize_bfloat16(alpha).astype(np.float64)
    return np.clip(0.2 * (xq @ aq) + 0.5, 0.0, 1.0)


def initial_p(factory_hidden: np.ndarray, prior_ridge: float, weight: float) -> np.ndarray:
    """P0 so that each later update counts ``weight`` factory events."""
    if prior_ridge <= 0 or weight <= 0:
        raise ValueError("prior ridge and calibration weight must be positive")
    gram = factory_hidden.T @ factory_hidden + prior_ridge * np.eye(factory_hidden.shape[1])
    return weight * np.linalg.inv(gram)


def os_elm_update(beta: np.ndarray, p: np.ndarray, hidden: np.ndarray, labels: np.ndarray,
                  precision: str = "float64") -> tuple[np.ndarray, np.ndarray]:
    """Apply one OS-ELM (forgetting factor 1) update per calibration hit.

    ``float32_bf16beta`` mirrors the firmware: float32 arithmetic and P, while
    beta stays in the AxlCORE as bfloat16 and is rounded after every hit.
    """
    if precision == "float32_bf16beta":
        beta = quantize_bfloat16(np.asarray(beta, dtype=np.float32))
        p = np.asarray(p, dtype=np.float32)
        targets = np.eye(beta.shape[1], dtype=np.float32)
        for h, label in zip(np.asarray(hidden, dtype=np.float32), labels):
            ph = p @ h
            gain = ph / np.float32(1.0 + h @ ph)
            p = p - np.outer(gain, ph)
            p = np.float32(0.5) * (p + p.T)
            beta = quantize_bfloat16(beta + np.outer(gain, targets[int(label)] - h @ beta))
        return beta.astype(np.float64), p.astype(np.float64)
    if precision == "float64":
        cast = lambda value: np.asarray(value, dtype=np.float64)
    elif precision == "float32":
        cast = lambda value: np.asarray(value, dtype=np.float32)
    elif precision == "bfloat16":
        cast = lambda value: quantize_bfloat16(np.asarray(value, dtype=np.float32))
    else:
        raise ValueError(f"unsupported precision: {precision}")
    beta, p = cast(beta), cast(p)
    targets = np.eye(beta.shape[1], dtype=beta.dtype)
    for h, label in zip(hidden, labels):
        h = cast(h)
        ph = cast(p @ h)
        p = cast(p - np.outer(ph, ph) / (1.0 + h @ ph))
        error = cast(targets[int(label)] - h @ beta)
        beta = cast(beta + np.outer(cast(p @ h), error))
    return beta.astype(np.float64), p.astype(np.float64)


def calibration_pool(labels: np.ndarray, repetitions: np.ndarray, point_names: np.ndarray,
                     members: np.ndarray, per_class: int = POOL_PER_CLASS) -> dict[int, np.ndarray]:
    """First guided hits of each class in one session, cycling through its points."""
    pool = {}
    for class_id in range(CLASS_COUNT):
        indices = members[labels[members] == class_id]
        if len(indices) <= per_class:
            raise ValueError(f"class {class_id} needs more than {per_class} events")
        order = np.lexsort((point_names[indices], repetitions[indices]))
        pool[class_id] = indices[order[:per_class]]
    return pool


def draw_calibration(pool: dict[int, np.ndarray], count: int, seed: int) -> np.ndarray:
    """Random hits from the pool, ordered as guided rounds through all classes."""
    rng = np.random.default_rng(seed)
    chosen = {class_id: rng.choice(indices, size=count, replace=False)
              for class_id, indices in pool.items()}
    return np.asarray([chosen[class_id][round_index]
                       for round_index in range(count) for class_id in sorted(chosen)])


def _accuracy(features: np.ndarray, labels: np.ndarray, alpha: np.ndarray,
              beta: np.ndarray) -> float:
    if not np.isfinite(beta).all():
        return 0.0
    predicted = np.argmax(mcu_reference(features, alpha, beta), axis=1)
    return float(np.mean(predicted == labels))


def _load_features(sessions_root: Path, cache: Path):
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    if cache.is_file():
        with np.load(cache, allow_pickle=False) as stored:
            if str(stored["dataset_sha256"]) == dataset.dataset_sha256:
                return dataset, np.asarray(stored["features"])
    features = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(cache, features=features, dataset_sha256=dataset.dataset_sha256)
    return dataset, features


def evaluate_session(features: np.ndarray, labels: np.ndarray, repetitions: np.ndarray,
                     point_names: np.ndarray, session_ids: np.ndarray, session_id: str,
                     alpha: np.ndarray) -> list[dict[str, Any]]:
    """Return one row per (method, calibration count, seed) for one held-out session."""
    held_out = np.flatnonzero(session_ids == session_id)
    train = session_ids != session_id
    scaler = FeatureScaler.fit(features[train])
    factory_x = scaler.transform(features[train])
    beta0 = quantize_bfloat16(
        fit_beta(factory_x, labels[train], alpha, FACTORY_RIDGE, CLASS_COUNT)
    ).astype(np.float64)
    factory_hidden = hidden_layer(factory_x, alpha)
    priors = {
        (ridge, weight): initial_p(factory_hidden, ridge, weight)
        for ridge in PRIOR_RIDGES for weight in CALIBRATION_WEIGHTS
    }

    pool = calibration_pool(labels, repetitions, point_names, held_out)
    evaluation = np.setdiff1d(held_out, np.concatenate(list(pool.values())))
    eval_x = scaler.transform(features[evaluation])
    eval_y = labels[evaluation]
    rows = [{"method": "none", "calibration_per_class": 0, "seed": None,
             "accuracy": _accuracy(eval_x, eval_y, alpha, beta0)}]

    for count in CALIBRATION_PER_CLASS:
        for seed in SEEDS:
            chosen = draw_calibration(pool, count, seed)
            base = {"calibration_per_class": count, "seed": seed}
            cal_y = labels[chosen]
            cal_hidden = hidden_layer(scaler.transform(features[chosen]), alpha)

            # Label-free: re-centre the input standardisation on the calibration hits.
            recentred = FeatureScaler(features[chosen].mean(axis=0), scaler.scale)
            rows.append({**base, "method": "recenter", "accuracy": _accuracy(
                recentred.transform(features[evaluation]), eval_y, alpha, beta0)})

            # Calibration hits alone, without the factory prior.
            own = fit_beta(scaler.transform(features[chosen]), cal_y, alpha,
                           FACTORY_RIDGE, CLASS_COUNT)
            rows.append({**base, "method": "calibration_only", "accuracy": _accuracy(
                eval_x, eval_y, alpha, own)})

            for (ridge, weight), p0 in priors.items():
                for precision in PRECISIONS:
                    beta, _ = os_elm_update(beta0, p0, cal_hidden, cal_y, precision)
                    rows.append({
                        **base, "method": "odl", "precision": precision,
                        "prior_ridge": ridge, "calibration_weight": weight,
                        "accuracy": _accuracy(eval_x, eval_y, alpha, beta),
                    })
    for row in rows:
        row.update({"session_id": session_id, "evaluation_count": int(len(evaluation))})
    return rows


def _condition_key(row: dict[str, Any]) -> tuple:
    return (row["method"], row.get("precision"), row.get("prior_ridge"),
            row.get("calibration_weight"), row["calibration_per_class"])


def summarise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Seed-averaged accuracy per session, then macro-averaged over sessions."""
    grouped: dict[tuple, dict[str, list[float]]] = {}
    for row in rows:
        grouped.setdefault(_condition_key(row), {}).setdefault(
            row["session_id"], []).append(row["accuracy"])
    baseline = {
        session_id: values[0]
        for session_id, values in grouped[("none", None, None, None, 0)].items()
    }
    summary = []
    for key, sessions in grouped.items():
        session_means = {session_id: float(np.mean(values))
                         for session_id, values in sessions.items()}
        worst = {session_id: float(np.min(values)) for session_id, values in sessions.items()}
        centre = [value for session_id, value in session_means.items()
                  if session_id in CENTRE_SESSION_IDS]
        corner = [value for session_id, value in session_means.items()
                  if session_id not in CENTRE_SESSION_IDS]
        method, precision, ridge, weight, count = key
        summary.append({
            "method": method, "precision": precision, "prior_ridge": ridge,
            "calibration_weight": weight, "calibration_per_class": count,
            "mean_accuracy": float(np.mean(list(session_means.values()))),
            "centre_session_accuracy": float(np.mean(centre)),
            "corner_session_accuracy": float(np.mean(corner)),
            "worst_seed_mean_accuracy": float(np.mean(list(worst.values()))),
            "sessions_worse_than_none": int(sum(
                session_means[session_id] < baseline[session_id] - 1e-12
                for session_id in session_means
            )),
            "session_accuracy": session_means,
        })
    return sorted(summary, key=lambda item: (
        item["calibration_per_class"], -item["mean_accuracy"]))


def run(sessions_root: Path, output_dir: Path, alpha_path: Path = DEFAULT_ALPHA) -> dict:
    dataset, features = _load_features(sessions_root, output_dir / "features_cache.npz")
    labels = np.asarray(dataset.labels, dtype=np.int64)
    point_names = np.asarray(dataset.point_names)
    alpha = load_official_sim_alpha(alpha_path)
    rows = []
    for session_id in DEFAULT_SESSION_IDS:
        rows.extend(evaluate_session(
            features, labels, dataset.repetitions, point_names,
            dataset.session_ids, session_id, alpha,
        ))
    report = {
        "experiment": "odl_onsite_calibration_12class_v1",
        "dataset_sha256": dataset.dataset_sha256,
        "alpha_sha256": hashlib.sha256(np.ascontiguousarray(alpha).tobytes()).hexdigest(),
        "sample_count": int(len(labels)),
        "session_ids": list(DEFAULT_SESSION_IDS),
        "protocol": {
            "split": "leave one acquisition session out",
            "factory_model": "seed-1 alpha, hard sigmoid, ridge 1, beta rounded to bfloat16",
            "calibration_pool": (
                f"first {POOL_PER_CLASS} guided hits per class in the held-out session, "
                "cycling through that class's points"
            ),
            "evaluation": "every held-out-session event outside the calibration pool",
            "calibration_order": "guided rounds: one hit per class per round",
            "calibration_per_class": list(CALIBRATION_PER_CLASS),
            "seeds": list(SEEDS),
            "odl": {
                "update": "OS-ELM recursive least squares, forgetting factor 1",
                "initial_p": "calibration_weight (G_factory + prior_ridge I)^-1",
                "precisions": list(PRECISIONS),
                "calibration_weights": list(CALIBRATION_WEIGHTS),
                "prior_ridges": list(PRIOR_RIDGES),
                "bfloat16_note": (
                    "approximates ODL_StartTrain by rounding h, P and beta after every "
                    "step; not bit-exact with AxlCORE"
                ),
            },
            "recenter": "label-free: feature mean replaced by the calibration hits' mean",
            "calibration_only": "ridge-1 beta fitted on the calibration hits alone",
        },
        "summary": summarise(rows),
        "runs": rows,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "calibration_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/odl_calibration_experiment_20260927"),
    )
    parser.add_argument("--alpha", type=Path, default=DEFAULT_ALPHA)
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir, args.alpha)
    for item in report["summary"]:
        if item["method"] == "odl" and item["precision"] != "float64":
            continue
        print(
            f"k={item['calibration_per_class']:2d} {item['method']:16s} "
            f"lambda={item['prior_ridge']} w={item['calibration_weight']} "
            f"mean={item['mean_accuracy']:.4f} centre={item['centre_session_accuracy']:.4f} "
            f"corner={item['corner_session_accuracy']:.4f} "
            f"worse={item['sessions_worse_than_none']}"
        )


if __name__ == "__main__":
    main()
