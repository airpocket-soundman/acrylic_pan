"""Distil the PC probability model into a Solist-compatible 60-position model."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from scipy.special import softmax
from sklearn.preprocessing import StandardScaler

from .dummy_model_pipeline import (
    load_official_sim_alpha, quantize_bfloat16, to_bfloat16_bits,
)
from .pc_position_grid_runtime import (
    DEFAULT_SESSION_IDS, _balanced_density_indices, _density_model,
    extract_grid_features, load_position_dataset,
)
from .real_model_pipeline import extract_hybrid_features, time_sample_indices
from .sampling_experiment import regression_metrics

INPUT_FEATURE_COUNT = 127
ENGINE_INPUT_COUNT = 128
ENGINE_HIDDEN_COUNT = 32
POSITION_COUNT = 60
ENGINE_OUTPUT_COUNT = 12
DISTILLATION_WEIGHT = 0.25
SOFTMAX_TEMPERATURE = 0.05
SEED = 1
TEACHER_SEEDS = (1, 7, 21)


def _teacher_probabilities(features: np.ndarray, labels: np.ndarray,
                           train: np.ndarray, query: np.ndarray) -> np.ndarray:
    indices = _balanced_density_indices(labels, train, SEED)
    scaler = StandardScaler().fit(features[indices])
    scaled_train = scaler.transform(features[indices])
    scaled_query = scaler.transform(features[query])
    probabilities = []
    for seed in TEACHER_SEEDS:
        model = _density_model(seed)
        model.fit(scaled_train, labels[indices])
        probabilities.append(model.predict_proba(scaled_query))
    result = np.mean(probabilities, axis=0)
    return result / result.sum(axis=1, keepdims=True)


def _engine_inputs(features: np.ndarray, scaler: StandardScaler) -> np.ndarray:
    values = np.ones((len(features), ENGINE_INPUT_COUNT), dtype=np.float32)
    values[:, :INPUT_FEATURE_COUNT] = scaler.transform(features[:, :INPUT_FEATURE_COUNT])
    return values


def _fit_soft_targets(inputs: np.ndarray, targets: np.ndarray, epochs: int = 160
                      ) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(SEED)
    # This Solist library generates Alpha from seed/scale in ODL_Initialize;
    # its ODL_SetWeightAlpha symbol is a no-op. Use the exact Simulator Alpha.
    alpha = load_official_sim_alpha().astype(np.float32)
    beta = rng.normal(0.0, 0.08, (ENGINE_HIDDEN_COUNT, POSITION_COUNT)).astype(np.float32)
    targets = np.asarray(targets, dtype=np.float32)
    if targets.shape != (len(inputs), POSITION_COUNT):
        raise ValueError("soft targets must have shape (samples, 60)")
    first = np.zeros_like(beta)
    second = np.zeros_like(beta)
    step = 0
    for _ in range(epochs):
        order = rng.permutation(len(inputs))
        for start in range(0, len(inputs), 128):
            batch = order[start:start + 128]
            x = inputs[batch]
            hidden_pre = 0.2 * (x @ alpha) + 0.5
            hidden = np.clip(hidden_pre, 0.0, 1.0)
            logits = (hidden @ beta) / SOFTMAX_TEMPERATURE
            probability = softmax(logits, axis=1)
            output_grad = ((probability - targets[batch]) /
                           (len(batch) * SOFTMAX_TEMPERATURE))
            gradient = hidden.T @ output_grad
            step += 1
            first = 0.9 * first + 0.1 * gradient
            second = 0.999 * second + 0.001 * gradient * gradient
            beta -= 5e-4 * (first / (1.0 - 0.9 ** step)) / (
                np.sqrt(second / (1.0 - 0.999 ** step)) + 1e-8)
            np.clip(beta, -0.5, 0.5, out=beta)
    return alpha, beta


def _fit_student(inputs: np.ndarray, labels: np.ndarray, teacher: np.ndarray,
                 distillation_weight: float, epochs: int = 160
                 ) -> tuple[np.ndarray, np.ndarray]:
    targets = ((1.0 - distillation_weight) * np.eye(POSITION_COUNT, dtype=np.float32)[labels]
               + distillation_weight * teacher.astype(np.float32))
    return _fit_soft_targets(inputs, targets, epochs)


def bfloat_logits(inputs: np.ndarray, alpha: np.ndarray, beta: np.ndarray) -> np.ndarray:
    values = quantize_bfloat16(np.asarray(inputs, dtype=np.float32))
    hidden = np.clip(0.2 * (values @ quantize_bfloat16(alpha)) + 0.5, 0.0, 1.0)
    return quantize_bfloat16(quantize_bfloat16(hidden) @ quantize_bfloat16(beta))


def _metrics(expected_xy: np.ndarray, labels: np.ndarray, inputs: np.ndarray,
             alpha: np.ndarray, beta: np.ndarray, support: np.ndarray) -> dict:
    probability = softmax(bfloat_logits(inputs, alpha, beta) /
                          SOFTMAX_TEMPERATURE, axis=1)
    result = regression_metrics(expected_xy, probability @ support)
    result["top1_position_accuracy"] = float(np.mean(np.argmax(probability, axis=1) == labels))
    return result


def _float_array(name: str, values: np.ndarray) -> str:
    flat = np.asarray(values).reshape(-1)
    rows = ["    " + ", ".join(f"{float(v):.9g}F" for v in flat[i:i + 8]) + ","
            for i in range(0, len(flat), 8)]
    return f"static const float {name}[{len(flat)}] = {{\n" + "\n".join(rows) + "\n};\n"


def _bf16_array(name: str, values: np.ndarray) -> str:
    bits = to_bfloat16_bits(values).reshape(-1)
    rows = ["    " + ", ".join(f"0x{int(v):04X}" for v in bits[i:i + 12]) + ","
            for i in range(0, len(bits), 12)]
    return f"static const int16_t {name}[{len(bits)}] = {{\n" + "\n".join(rows) + "\n};\n"


def write_header(path: Path, scaler: StandardScaler, alpha: np.ndarray,
                 beta: np.ndarray, support_xy: np.ndarray,
                 golden_inputs: np.ndarray, golden_logits: np.ndarray) -> None:
    content = """/* Generated by python -m sim.device_position_probability. Do not edit. */
#ifndef APAN_POSITION_PROBABILITY_MODEL_H
#define APAN_POSITION_PROBABILITY_MODEL_H
#include <stdint.h>
#define APAN_POSITION_FEATURE_COUNT 127U
#define APAN_POSITION_INPUT_SIZE 128U
#define APAN_POSITION_HIDDEN_SIZE 32U
#define APAN_POSITION_ENGINE_OUTPUT_SIZE 12U
#define APAN_POSITION_OUTPUT_SIZE 60U
#define APAN_POSITION_HEAD_COUNT 5U
#define APAN_POSITION_ACTIVATION 1
#define APAN_POSITION_LOSS 1
#define APAN_POSITION_SEED 1U
#define APAN_POSITION_SCALE_ALPHA_BF16 ((int16_t)0x3E52)
#define APAN_POSITION_SOFTMAX_INVERSE_TEMPERATURE 20.0F
#define APAN_POSITION_GOLDEN_COUNT 4U
"""
    content += _float_array("apan_position_feature_mean", scaler.mean_)
    content += _float_array("apan_position_feature_scale", scaler.scale_)
    content += "static const uint16_t apan_position_time_indices[127] = {\n    "
    content += ", ".join(f"{int(v)}U" for v in time_sample_indices()[:127]) + "\n};\n"
    beta_heads = np.concatenate([
        beta[:, start:start + ENGINE_OUTPUT_COUNT].reshape(-1)
        for start in range(0, POSITION_COUNT, ENGINE_OUTPUT_COUNT)
    ])
    content += _bf16_array("apan_position_beta", beta_heads)
    content += _bf16_array("apan_position_golden_inputs", golden_inputs)
    content += _bf16_array("apan_position_golden_logits", golden_logits)
    content += "static const uint16_t apan_position_support_xy_mm[60][2] = {\n"
    content += "\n".join(f"    {{{int(round(x))}U, {int(round(y))}U}}," for x, y in support_xy)
    content += "\n};\n#endif\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def run(sessions_root: Path, output_dir: Path, header: Path) -> dict:
    dataset = load_position_dataset(sessions_root, DEFAULT_SESSION_IDS)
    edge = np.stack([extract_hybrid_features(row) for row in dataset.samples])
    rich = np.stack([extract_grid_features(row) for row in dataset.samples])
    support = np.unique(dataset.xy_mm, axis=0)
    lookup = {tuple(row): index for index, row in enumerate(support)}
    labels = np.asarray([lookup[tuple(row)] for row in dataset.xy_mm], dtype=np.int64)
    train = dataset.repetitions % 5 != 0
    test = ~train

    validation_teacher = _teacher_probabilities(rich, labels, train, train)
    validation_scaler = StandardScaler().fit(edge[train, :INPUT_FEATURE_COUNT])
    validation_inputs = _engine_inputs(edge[train], validation_scaler)
    direct_alpha, direct_beta = _fit_student(validation_inputs, labels[train],
                                             validation_teacher, 0.0)
    distilled_alpha, distilled_beta = _fit_student(
        validation_inputs, labels[train], validation_teacher, DISTILLATION_WEIGHT)
    test_inputs = _engine_inputs(edge[test], validation_scaler)
    comparison = {
        "direct": _metrics(dataset.xy_mm[test], labels[test], test_inputs,
                           direct_alpha, direct_beta, support),
        "distilled": _metrics(dataset.xy_mm[test], labels[test], test_inputs,
                              distilled_alpha, distilled_beta, support),
    }

    all_rows = np.ones(len(edge), dtype=bool)
    final_teacher = _teacher_probabilities(rich, labels, all_rows, all_rows)
    final_scaler = StandardScaler().fit(edge[:, :INPUT_FEATURE_COUNT])
    final_inputs = _engine_inputs(edge, final_scaler)
    final_alpha, final_beta = _fit_student(final_inputs, labels, final_teacher,
                                          DISTILLATION_WEIGHT)
    golden_indices = np.linspace(0, len(edge) - 1, 4, dtype=np.int64)
    golden_inputs = quantize_bfloat16(final_inputs[golden_indices])
    golden_logits = bfloat_logits(golden_inputs, final_alpha, final_beta)

    output_dir.mkdir(parents=True, exist_ok=True)
    model_path = output_dir / "model.npz"
    np.savez_compressed(
        model_path, alpha_bfloat16=to_bfloat16_bits(final_alpha),
        beta_bfloat16=to_bfloat16_bits(final_beta),
        feature_mean=final_scaler.mean_.astype(np.float32),
        feature_scale=final_scaler.scale_.astype(np.float32),
        time_indices=time_sample_indices()[:INPUT_FEATURE_COUNT].astype(np.uint16),
        support_xy_mm=support.astype(np.float32), golden_indices=golden_indices,
        golden_inputs_bfloat16=to_bfloat16_bits(golden_inputs),
        golden_logits_bfloat16=to_bfloat16_bits(golden_logits))
    write_header(header, final_scaler, final_alpha, final_beta, support,
                 golden_inputs, golden_logits)
    report = {
        "model": "solist_fixed_alpha_distilled_beta_128x32x60_v3",
        "dataset_sha256": dataset.dataset_sha256,
        "session_ids": list(DEFAULT_SESSION_IDS), "sample_count": int(len(dataset.samples)),
        "train_count": int(train.sum()), "test_count": int(test.sum()),
        "holdout_rule": "repetition_modulo_5_equals_0",
        "architecture": [ENGINE_INPUT_COUNT, ENGINE_HIDDEN_COUNT, POSITION_COUNT],
        "distillation_weight": DISTILLATION_WEIGHT,
        "softmax_temperature": SOFTMAX_TEMPERATURE,
        "teacher": "PC 714-feature 60-class MLP ensemble",
        "alpha": "fixed ROHM Simulator seed=1 projection; Beta-only training",
        "precision": "Solist hard-sigmoid with bfloat16 boundaries",
        "validation_comparison": comparison, "validation": comparison["distilled"],
        "deployed_beta_bytes": int(final_beta.size * 2),
        "fixed_generated_alpha_bytes": int(final_alpha.size * 2),
        "model_sha256": hashlib.sha256(model_path.read_bytes()).hexdigest(),
        "header": str(header), "transport": "device softmax over 60 logits",
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("artifacts/device_position_probability_400x300x5"))
    parser.add_argument("--header", type=Path,
                        default=Path("firmware/AcrylicPanCollector/generated/apan_position_probability_model.h"))
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir, args.header)
    print(json.dumps(report["validation_comparison"], ensure_ascii=False))


if __name__ == "__main__":
    main()
