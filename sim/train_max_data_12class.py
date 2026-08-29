"""Train the MCU 12-area classifier from every validated 5 mm-panel event."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .dummy_model_pipeline import load_official_sim_alpha, mcu_reference, to_bfloat16_bits
from .pc_position_grid_runtime import DEFAULT_SESSION_IDS, load_position_dataset
from .real_model_pipeline import (
    DEFAULT_ALPHA,
    FeatureScaler,
    export_header,
    extract_hybrid_features,
    fit_beta,
)
from .sampling_experiment import classification_metrics

CLASS_COUNT = 12
DEFAULT_RIDGES = (1e-4, 1e-3, 1e-2, 1e-1, 1.0, 10.0)


def _metrics(expected: np.ndarray, predicted: np.ndarray, point_names: np.ndarray) -> dict:
    centre = np.asarray(point_names) == "center"
    result = classification_metrics(expected, predicted, CLASS_COUNT)
    result.update({
        "correct": int(np.sum(expected == predicted)),
        "centre_accuracy": float(np.mean(expected[centre] == predicted[centre])),
        "corner_accuracy": float(np.mean(expected[~centre] == predicted[~centre])),
    })
    return result


def _predict(features: np.ndarray, scaler: FeatureScaler, alpha: np.ndarray,
             beta: np.ndarray) -> np.ndarray:
    return np.argmax(mcu_reference(scaler.transform(features), alpha, beta), axis=1)


def run(sessions_root: Path, output_dir: Path, header: Path,
        current_model: Path, ridges: tuple[float, ...] = DEFAULT_RIDGES) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    features = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    labels = np.asarray(dataset.labels, dtype=np.int64)
    train = dataset.repetitions % 5 != 0
    test = ~train
    alpha = load_official_sim_alpha(DEFAULT_ALPHA)

    candidates = []
    for ridge in ridges:
        scaler = FeatureScaler.fit(features[train])
        beta = fit_beta(scaler.transform(features[train]), labels[train], alpha,
                        ridge=ridge, class_count=CLASS_COUNT)
        prediction = _predict(features[test], scaler, alpha, beta)
        candidates.append({
            "ridge_l2": ridge,
            **_metrics(labels[test], prediction, np.asarray(dataset.point_names)[test]),
        })
    selected = max(candidates, key=lambda item: (item["balanced_accuracy"], item["accuracy"]))

    current = np.load(current_model)
    current_scaler = FeatureScaler(
        np.asarray(current["feature_mean"]), np.asarray(current["feature_scale"])
    )
    current_prediction = _predict(
        features[test], current_scaler, np.asarray(current["alpha"]),
        np.asarray(current["beta"]),
    )
    current_metrics = _metrics(
        labels[test], current_prediction, np.asarray(dataset.point_names)[test]
    )

    final_scaler = FeatureScaler.fit(features)
    final_x = final_scaler.transform(features)
    final_beta = fit_beta(final_x, labels, alpha, ridge=selected["ridge_l2"],
                          class_count=CLASS_COUNT)
    final_prediction = np.argmax(mcu_reference(final_x, alpha, final_beta), axis=1)
    golden_indices = []
    for class_id in range(CLASS_COUNT):
        members = np.flatnonzero(labels == class_id)
        centroid = final_x[members].mean(axis=0)
        golden_indices.append(int(members[np.argmin(np.sum((final_x[members] - centroid) ** 2, axis=1))]))
    golden_inputs = final_x[golden_indices]
    golden_outputs = mcu_reference(golden_inputs, alpha, final_beta)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez(
        model_path, alpha=alpha, beta=final_beta,
        alpha_bf16=to_bfloat16_bits(alpha), beta_bf16=to_bfloat16_bits(final_beta),
        feature_mean=final_scaler.mean, feature_scale=final_scaler.scale,
    )
    export_header(header, alpha, final_beta, final_scaler, golden_inputs, CLASS_COUNT)
    report = {
        "model": "acrylic_pan_time128_h32_12class_max_data_v2",
        "session_ids": list(DEFAULT_SESSION_IDS),
        "sample_count": int(len(labels)),
        "class_counts": np.bincount(labels, minlength=CLASS_COUNT).astype(int).tolist(),
        "dataset_sha256": dataset.dataset_sha256,
        "validation": {
            "rule": "repetition_modulo_5_equals_0",
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "ridge_candidates": candidates,
            "selected": selected,
            "previous_firmware_model_on_same_events": current_metrics,
            "comparison_warning": (
                "The previous model used all centre events from its four sessions, so its "
                "centre subset is not leakage-free under this later repetition holdout."
            ),
        },
        "final": {
            "training_count": int(len(labels)),
            "training_accuracy": float(np.mean(final_prediction == labels)),
            "ridge_l2": selected["ridge_l2"],
            "model_path": str(model_path),
            "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
            "header_path": str(header),
            "header_sha256": hashlib.sha256(header.read_bytes()).hexdigest(),
        },
        "golden_cases": [
            {
                "case_id": class_id,
                "expected_class": class_id,
                "predicted_class": int(np.argmax(golden_outputs[class_id])),
                "source_session": str(dataset.session_ids[index]),
                "source_repetition": int(dataset.repetitions[index]),
                "source_point": str(dataset.point_names[index]),
            }
            for class_id, index in enumerate(golden_indices)
        ],
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/real_model_400x300x5_12class_max_data_20260828"),
    )
    parser.add_argument(
        "--header", type=Path,
        default=Path("firmware/AcrylicPanCollector/generated/apan_12class_model.h"),
    )
    parser.add_argument(
        "--current-model", type=Path,
        default=Path("artifacts/real_model_300x400x5_12class_4sessions_20260821/model.npz"),
    )
    parser.add_argument("--ridge", type=float, action="append", default=[])
    args = parser.parse_args()
    report = run(
        args.sessions, args.output_dir, args.header, args.current_model,
        tuple(args.ridge) if args.ridge else DEFAULT_RIDGES,
    )
    selected = report["validation"]["selected"]
    print(f"samples={report['sample_count']}; class_counts={report['class_counts']}")
    print(
        f"holdout={selected['accuracy']:.6f}; balanced={selected['balanced_accuracy']:.6f}; "
        f"ridge={selected['ridge_l2']}"
    )
    print(f"final_training_accuracy={report['final']['training_accuracy']:.6f}")
    print(f"report={args.output_dir / 'training_report.json'}")


if __name__ == "__main__":
    main()
