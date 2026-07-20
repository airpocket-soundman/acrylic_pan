"""Train a measured-vibration Acrylic Pan Solist-AI classification model.

The evaluation split is by acquisition session, never by individual event.
The final model uses the official Simulator seed-1 projection captured in the
IchiPing validation repository and trains only beta, matching Solist-AI ELM.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .dummy_model_pipeline import (
    load_official_sim_alpha,
    mcu_reference,
    to_bfloat16_bits,
)
from .solist_dataset import (
    FeatureScaler,
    export_solist_csv,
    load_recorded_sessions,
    one_hot,
)

INPUT_COUNT = 128
FFT_FEATURE_COUNT = 0
TIME_FEATURE_COUNT = INPUT_COUNT
SAMPLE_COUNT = 512
TRIGGER_INDEX = 64
POSTTRIGGER_COUNT = SAMPLE_COUNT - TRIGGER_INDEX
HIDDEN_COUNT = 32
CLASS_COUNT = 8
RIDGE = 0.1
SCALE_ALPHA_BF16 = 0x3E52
DEFAULT_ALPHA = Path(r"D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy")


def time_sample_indices() -> np.ndarray:
    """Indices within the 448-sample post-trigger region used by the model."""
    return np.rint(np.linspace(0, POSTTRIGGER_COUNT - 1, TIME_FEATURE_COUNT)).astype(np.int64)


def extract_hybrid_features(samples: np.ndarray) -> np.ndarray:
    """Return 128 normalized post-trigger samples.

    The first target firmware deliberately uses time-domain features only.
    ROHM's hardware FFT reports a single-sided bfloat16 amplitude spectrum
    whose scaling differs from NumPy's unnormalised rFFT.  Keeping the first
    deployed model FFT-free makes the PC, exported CSV and MCU preprocessing
    contract exactly reproducible.  A calibrated FFT model can be added later.
    """
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.ndim != 1 or waveform.size < SAMPLE_COUNT:
        raise ValueError(f"waveform must contain at least {SAMPLE_COUNT} samples")
    # Collection captures may be longer than the live inference window.  Use
    # exactly the same first 512 samples that the firmware sees in inference
    # mode so training and deployment have an identical feature contract.
    waveform = waveform[:SAMPLE_COUNT]
    baseline = waveform[:TRIGGER_INDEX].mean()
    posttrigger = waveform[TRIGGER_INDEX:] - baseline
    peak = max(float(np.max(np.abs(posttrigger))), 1.0)
    time_features = posttrigger[time_sample_indices()] / peak
    return time_features.astype(np.float32)


def load_hybrid_dataset(source: Path, class_count: int = CLASS_COUNT,
                        session_ids: tuple[str, ...] = ()):
    """Load validated Recorder sessions, replacing FFT-only features."""
    dataset = load_recorded_sessions(source, class_count=class_count)
    selected = np.ones(len(dataset.labels), dtype=bool)
    if session_ids:
        selected = np.isin(dataset.session_ids, np.asarray(session_ids))
        missing = sorted(set(session_ids) - set(dataset.session_ids[selected].tolist()))
        if missing:
            raise ValueError(f"requested sessions were not found: {missing}")
    labels = dataset.labels[selected]
    if set(labels.tolist()) != set(range(class_count)):
        raise ValueError(f"selected dataset must contain every class 0..{class_count - 1}")
    paths = tuple(path for path, keep in zip(dataset.event_paths, selected) if keep)
    selected_dataset = type(dataset)(
        dataset.features[selected], labels, dataset.session_ids[selected], paths
    )
    features = []
    for path in selected_dataset.event_paths:
        with np.load(path, allow_pickle=False) as event:
            samples = np.asarray(event["samples"])
            trigger = int(np.asarray(event["trigger_index"]).reshape(-1)[0])
            if trigger != TRIGGER_INDEX:
                raise ValueError(f"{path}: trigger_index {trigger} is not {TRIGGER_INDEX}")
            features.append(extract_hybrid_features(samples))
    return selected_dataset, np.stack(features)


def fit_beta(features: np.ndarray, labels: np.ndarray, alpha: np.ndarray,
             ridge: float = RIDGE, class_count: int = CLASS_COUNT) -> np.ndarray:
    """Fit unweighted one-hot beta exactly as the Simulator MSE ELM."""
    hidden = np.clip(0.2 * (features @ alpha) + 0.5, 0.0, 1.0).astype(np.float64)
    targets = np.eye(class_count, dtype=np.float64)[labels]
    gram = hidden.T @ hidden + ridge * np.eye(hidden.shape[1])
    return np.linalg.solve(gram, hidden.T @ targets).astype(np.float32)


def confusion_matrix(expected: np.ndarray, predicted: np.ndarray,
                     class_count: int = CLASS_COUNT) -> np.ndarray:
    matrix = np.zeros((class_count, class_count), dtype=np.int64)
    for target, actual in zip(expected, predicted):
        matrix[int(target), int(actual)] += 1
    return matrix


def evaluate_fold(features: np.ndarray, labels: np.ndarray, session_ids: np.ndarray,
                  train_session: str, test_session: str, alpha: np.ndarray,
                  ridge: float = RIDGE, class_count: int = CLASS_COUNT
                  ) -> tuple[dict[str, Any], FeatureScaler, np.ndarray]:
    train = session_ids == train_session
    test = session_ids == test_session
    scaler = FeatureScaler.fit(features[train])
    train_x = scaler.transform(features[train])
    test_x = scaler.transform(features[test])
    beta = fit_beta(train_x, labels[train], alpha, ridge, class_count)
    scores = mcu_reference(test_x, alpha, beta)
    predicted = np.argmax(scores, axis=1)
    matrix = confusion_matrix(labels[test], predicted, class_count)
    recalls = np.diag(matrix) / np.maximum(matrix.sum(axis=1), 1)
    result = {
        "train_session": train_session,
        "test_session": test_session,
        "train_count": int(train.sum()),
        "test_count": int(test.sum()),
        "accuracy": float(np.mean(predicted == labels[test])),
        "per_class_recall": recalls.astype(float).tolist(),
        "confusion_matrix": matrix.tolist(),
    }
    return result, scaler, beta


def _c_bfloat_array(name: str, values: np.ndarray, declaration: str | None = None) -> str:
    bits = to_bfloat16_bits(values).reshape(-1)
    lines = []
    for offset in range(0, len(bits), 12):
        lines.append("    " + ", ".join(
            f"0x{int(value):04X}" for value in bits[offset : offset + 12]
        ) + ",")
    suffix = declaration or f"{name}[{len(bits)}]"
    return f"static const int16_t {suffix} = {{\n" + "\n".join(lines) + "\n};\n"


def _c_float_array(name: str, values: np.ndarray) -> str:
    flat = np.asarray(values, dtype=np.float32).reshape(-1)
    lines = []
    for offset in range(0, len(flat), 6):
        literals = []
        for value in flat[offset : offset + 6]:
            literal = f"{float(value):.9g}"
            if "." not in literal and "e" not in literal.lower():
                literal += ".0"
            literals.append(literal + "F")
        lines.append("    " + ", ".join(literals) + ",")
    return f"static const float {name}[{len(flat)}] = {{\n" + "\n".join(lines) + "\n};\n"


def export_header(path: Path, alpha: np.ndarray, beta: np.ndarray,
                  scaler: FeatureScaler, golden_inputs: np.ndarray,
                  class_count: int = CLASS_COUNT) -> None:
    indices = time_sample_indices()
    content = """/* Generated by python -m sim.real_model_pipeline. Do not edit. */
#ifndef APAN_CLASS_MODEL_H
#define APAN_CLASS_MODEL_H
#include <stdint.h>
#define APAN_MODEL_INPUT_SIZE 128
#define APAN_MODEL_HIDDEN_SIZE 32
#define APAN_MODEL_OUTPUT_SIZE {class_count}
#define APAN_MODEL_FFT_FEATURE_COUNT 0
#define APAN_MODEL_TIME_FEATURE_COUNT 128
#define APAN_MODEL_SAMPLE_COUNT 512
#define APAN_MODEL_TRIGGER_INDEX 64
#define APAN_MODEL_ACTIVATION 1
#define APAN_MODEL_LOSS 1
#define APAN_MODEL_SEED 1
#define APAN_MODEL_SCALE_ALPHA_BF16 ((int16_t)0x3E52)
""".format(class_count=class_count)
    content += "static const uint16_t apan_model_time_indices[128] = {\n    "
    content += ", ".join(str(int(value)) for value in indices) + "\n};\n"
    content += _c_float_array("apan_model_feature_mean", scaler.mean)
    content += _c_float_array("apan_model_feature_scale", scaler.scale)
    content += _c_bfloat_array("apan_model_alpha", alpha)
    content += _c_bfloat_array("apan_model_beta", beta)
    content += _c_bfloat_array(
        "apan_model_golden_inputs", golden_inputs,
        f"apan_model_golden_inputs[{class_count}][APAN_MODEL_INPUT_SIZE]",
    )
    content += "#endif\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="ascii")


def generate(source: Path, output_dir: Path, header: Path,
             alpha_path: Path = DEFAULT_ALPHA, ridge: float = RIDGE,
             class_count: int = CLASS_COUNT,
             session_ids: tuple[str, ...] = ()) -> dict[str, Any]:
    dataset, features = load_hybrid_dataset(source, class_count, session_ids)
    sessions = sorted(str(value) for value in np.unique(dataset.session_ids))
    if len(sessions) < 2:
        raise ValueError("at least two acquisition sessions are required")
    alpha = load_official_sim_alpha(alpha_path)
    if alpha.shape != (INPUT_COUNT, HIDDEN_COUNT):
        raise ValueError(f"alpha must have shape {(INPUT_COUNT, HIDDEN_COUNT)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    folds = []
    for fold_index, (train_session, test_session) in enumerate(
        ((sessions[0], sessions[1]), (sessions[1], sessions[0])), start=1
    ):
        result, scaler, _ = evaluate_fold(
            features, dataset.labels, dataset.session_ids,
            train_session, test_session, alpha, ridge, class_count,
        )
        folds.append(result)
        train = dataset.session_ids == train_session
        test = dataset.session_ids == test_session
        np.savez(output_dir / f"fold{fold_index}_scaler.npz", mean=scaler.mean, scale=scaler.scale)
        export_solist_csv(
            output_dir / f"fold{fold_index}_train_{class_count}class.csv",
            scaler.transform(features[train]), one_hot(dataset.labels[train], class_count),
        )
        export_solist_csv(
            output_dir / f"fold{fold_index}_test_{class_count}class.csv",
            scaler.transform(features[test]), one_hot(dataset.labels[test], class_count),
        )

    final_scaler = FeatureScaler.fit(features)
    final_x = final_scaler.transform(features)
    final_beta = fit_beta(final_x, dataset.labels, alpha, ridge, class_count)
    final_scores = mcu_reference(final_x, alpha, final_beta)
    final_predictions = np.argmax(final_scores, axis=1)
    final_matrix = confusion_matrix(dataset.labels, final_predictions, class_count)

    golden_indices = []
    for class_id in range(class_count):
        members = np.flatnonzero(dataset.labels == class_id)
        centroid = final_x[members].mean(axis=0)
        golden_indices.append(int(members[np.argmin(np.sum((final_x[members] - centroid) ** 2, axis=1))]))
    golden_inputs = final_x[golden_indices]
    golden_scores = mcu_reference(golden_inputs, alpha, final_beta)

    np.savez(
        output_dir / "model.npz", alpha=alpha, beta=final_beta,
        alpha_bf16=to_bfloat16_bits(alpha), beta_bf16=to_bfloat16_bits(final_beta),
        feature_mean=final_scaler.mean, feature_scale=final_scaler.scale,
        time_indices=time_sample_indices(),
    )
    np.savez(output_dir / "feature_scaler.npz", mean=final_scaler.mean, scale=final_scaler.scale)
    export_solist_csv(
        output_dir / f"final_train_{class_count}class.csv", final_x,
        one_hot(dataset.labels, class_count)
    )
    export_solist_csv(
        output_dir / f"final_parity_{class_count}class.csv", golden_inputs,
        one_hot(np.arange(class_count, dtype=np.int64), class_count),
    )
    export_header(header, alpha, final_beta, final_scaler, golden_inputs, class_count)

    model_hash = hashlib.sha256((output_dir / "model.npz").read_bytes()).hexdigest()
    report = {
        "model": f"acrylic_pan_time128_h32_{class_count}class_v1",
        "sessions": sessions,
        "sample_count": int(len(dataset.labels)),
        "class_counts": np.bincount(dataset.labels, minlength=class_count).astype(int).tolist(),
        "feature_contract": {
            "input_count": INPUT_COUNT,
            "fft_bins": FFT_FEATURE_COUNT,
            "time_samples": TIME_FEATURE_COUNT,
            "trigger_index": TRIGGER_INDEX,
            "posttrigger_indices": time_sample_indices().astype(int).tolist(),
            "time_normalization": "subtract pretrigger mean, divide by posttrigger absolute peak",
            "standardization": "per-feature mean/std fitted on training sessions",
        },
        "solist": {
            "hidden_count": HIDDEN_COUNT,
            "output_count": class_count,
            "activation": "hard_sigmoid",
            "loss": "mse",
            "ridge_l2": ridge,
            "seed": 1,
            "alpha_origin": str(alpha_path),
            "precision_evaluation": "bfloat16 input/weights/hidden/output boundaries",
        },
        "session_folds": folds,
        "mean_session_accuracy": float(np.mean([fold["accuracy"] for fold in folds])),
        "final_training_accuracy": float(np.mean(final_predictions == dataset.labels)),
        "final_training_confusion_matrix": final_matrix.tolist(),
        "golden_cases": [
            {
                "case_id": class_id,
                "source_event": str(dataset.event_paths[index]),
                "expected_class": class_id,
                "predicted_class": int(np.argmax(golden_scores[class_id])),
                "outputs": golden_scores[class_id].astype(float).tolist(),
            }
            for class_id, index in enumerate(golden_indices)
        ],
        "model_sha256": model_hash,
        "header": str(header),
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/real_model"))
    parser.add_argument(
        "--header", type=Path,
        default=Path("firmware/AcrylicPanCollector/generated/apan_8class_model.h"),
    )
    parser.add_argument("--alpha", type=Path, default=DEFAULT_ALPHA)
    parser.add_argument("--ridge", type=float, default=RIDGE)
    parser.add_argument("--class-count", type=int, default=CLASS_COUNT)
    parser.add_argument("--session-id", action="append", default=[])
    args = parser.parse_args()
    report = generate(args.sessions, args.output_dir, args.header, args.alpha, args.ridge,
                      args.class_count, tuple(args.session_id))
    print(f"samples={report['sample_count']}; class_counts={report['class_counts']}")
    for fold in report["session_folds"]:
        print(
            f"{fold['train_session']} -> {fold['test_session']}: "
            f"accuracy={fold['accuracy']:.4f}"
        )
    print(f"mean_session_accuracy={report['mean_session_accuracy']:.4f}")
    print(f"final_training_accuracy={report['final_training_accuracy']:.4f}")
    print(f"report={args.output_dir / 'training_report.json'}")
    print(f"header={args.header}")


if __name__ == "__main__":
    main()
