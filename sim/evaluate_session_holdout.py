"""Leave-one-session-out evaluation: how well does a model trained on the other sessions do on an unseen session?

    python -m sim.evaluate_session_holdout --sessions data/raw/sessions_2point --session-ids all

Reports, per held-out session, the 12-class accuracy (ridge ELM, as deployed) and, for the 60-position
128-32-60 device model, the top-1 position accuracy, 12-area accuracy and the expected-coordinate distance
(the student trained on one-hot labels only, without the PC teacher, so that it runs in seconds).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from scipy.special import softmax
from sklearn.preprocessing import StandardScaler

from .device_position_probability import (
    INPUT_FEATURE_COUNT, POSITION_COUNT, SOFTMAX_TEMPERATURE, _engine_inputs, _fit_student, bfloat_logits,
)
from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference
from .pc_position_grid_runtime import load_position_dataset, parse_session_ids
from .real_model_pipeline import DEFAULT_ALPHA, FeatureScaler, extract_hybrid_features, fit_beta


def _area(xy: np.ndarray) -> np.ndarray:
    return (np.clip(xy[:, 1] // 100, 0, 2) * 4 + np.clip(xy[:, 0] // 100, 0, 3)).astype(int)


def evaluate(sessions_root: Path, session_ids: tuple[str, ...], ridge: float = 1.0) -> dict:
    dataset = load_position_dataset(sessions_root, session_ids)
    features = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    classes = np.asarray(dataset.labels, dtype=np.int64)
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    positions = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    owner = np.asarray([session_ids.index(name) for name in dataset.session_ids])
    centre = np.asarray(dataset.point_names) == "center"
    alpha12 = load_official_sim_alpha(DEFAULT_ALPHA)
    rows = []
    for index, name in enumerate(session_ids):
        test = owner == index
        train = ~test
        scaler = FeatureScaler.fit(features[train])
        beta = fit_beta(scaler.transform(features[train]), classes[train], alpha12, ridge=ridge, class_count=12)
        predicted = np.argmax(mcu_reference(scaler.transform(features[test]), alpha12, beta), axis=1)

        standard = StandardScaler().fit(features[train, :INPUT_FEATURE_COUNT])
        alpha, beta60 = _fit_student(_engine_inputs(features[train], standard), positions[train],
                                     np.zeros((int(train.sum()), POSITION_COUNT), dtype=np.float32), 0.0)
        probability = softmax(bfloat_logits(_engine_inputs(features[test], standard), alpha, beta60)
                              / SOFTMAX_TEMPERATURE, axis=1)
        best = np.argmax(probability, axis=1)
        distance = np.linalg.norm(probability @ support - dataset.xy_mm[test], axis=1)
        rows.append({
            "session": name, "kind": "center" if centre[test].all() else "corner", "count": int(test.sum()),
            "class12_accuracy": round(float(np.mean(predicted == classes[test])), 4),
            "position60_top1": round(float(np.mean(best == positions[test])), 4),
            "position60_area_accuracy": round(float(np.mean(_area(support[best]) == _area(dataset.xy_mm[test]))), 4),
            "mean_distance_mm": round(float(np.mean(distance)), 2),
            "within_25mm": round(float(np.mean(distance <= 25.0)), 4),
        })
    return {"sessions": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--session-ids", default="default")
    parser.add_argument("--ridge", type=float, default=1.0)
    arguments = parser.parse_args()
    ids = parse_session_ids(arguments.session_ids, arguments.sessions)
    print(json.dumps(evaluate(arguments.sessions, ids, arguments.ridge), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
