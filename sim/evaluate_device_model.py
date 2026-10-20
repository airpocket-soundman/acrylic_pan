"""Evaluate a saved 128-32-60 device position model on recorded sessions.

    python -m sim.evaluate_device_model --model models/support-1point/artifacts/device_position_probability_400x300x5/model.npz \
        --sessions data/raw/sessions_2point --session-ids all

Reports the position top-1 accuracy, the 12-area accuracy and the distance of the expected coordinate for
all rows, for the rows of repetition % 5 == 0 (the hold-out of the training script) and for centre / corner points.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.special import softmax

from .dummy_model_pipeline import quantize_bfloat16
from .pc_position_grid_runtime import load_position_dataset, parse_session_ids
from .real_model_pipeline import extract_hybrid_features

TEMPERATURE = 0.05
INPUT_FEATURES = 127


def _float(bits: np.ndarray) -> np.ndarray:
    return (bits.astype(np.uint32) << 16).view(np.float32)


def evaluate(model_path: Path, sessions_root: Path, session_ids: tuple[str, ...]) -> dict:
    dataset = load_position_dataset(sessions_root, session_ids)
    with np.load(model_path, allow_pickle=False) as model:
        alpha, beta = _float(model["alpha_bfloat16"]), _float(model["beta_bfloat16"])
        mean, scale = model["feature_mean"], model["feature_scale"]
        support = model["support_xy_mm"].astype(np.float64)
    features = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    inputs = np.ones((len(features), alpha.shape[0]), dtype=np.float32)
    inputs[:, :INPUT_FEATURES] = (features[:, :INPUT_FEATURES] - mean) / scale
    hidden = np.clip(0.2 * (quantize_bfloat16(inputs) @ quantize_bfloat16(alpha)) + 0.5, 0.0, 1.0)
    logits = quantize_bfloat16(quantize_bfloat16(hidden) @ quantize_bfloat16(beta))
    probability = softmax(logits / TEMPERATURE, axis=1)

    truth = np.argmin(np.linalg.norm(dataset.xy_mm[:, None, :] - support[None, :, :], axis=2), axis=1)
    expected = probability @ support
    distance = np.linalg.norm(expected - dataset.xy_mm, axis=1)
    predicted = np.argmax(probability, axis=1)
    area = lambda xy: (np.clip(xy[:, 1] // 100, 0, 2) * 4 + np.clip(xy[:, 0] // 100, 0, 3)).astype(int)
    area_hit = area(support[predicted]) == area(dataset.xy_mm)
    centre = np.asarray(dataset.point_names) == "center"
    holdout = dataset.repetitions % 5 == 0

    def summary(mask: np.ndarray) -> dict:
        return {
            "count": int(mask.sum()),
            "top1_position_accuracy": round(float(np.mean(predicted[mask] == truth[mask])), 4),
            "area_accuracy": round(float(np.mean(area_hit[mask])), 4),
            "mean_distance_mm": round(float(np.mean(distance[mask])), 2),
            "median_distance_mm": round(float(np.median(distance[mask])), 2),
            "within_25mm": round(float(np.mean(distance[mask] <= 25.0)), 4),
        }

    return {"model": str(model_path), "sessions": list(session_ids),
            "all": summary(np.ones(len(distance), dtype=bool)), "holdout_rep_mod5": summary(holdout),
            "centre": summary(centre), "corner": summary(~centre)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--session-ids", default="default")
    arguments = parser.parse_args()
    ids = parse_session_ids(arguments.session_ids, arguments.sessions)
    print(json.dumps(evaluate(arguments.model, arguments.sessions, ids), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
