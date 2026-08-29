"""Optional PC-side XY ensemble and probability-distribution metadata."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from pc.acrylic_pan_monitor.protocol import EventData
from sim.pc_position_grid_runtime import FEATURE_MODE as GRID_FEATURE_MODE, extract_grid_features
from sim.pc_position_runtime import extract_live_features
from sim.dummy_model_pipeline import from_bfloat16_bits, quantize_bfloat16

PANEL_SIZE_MM = np.asarray((400.0, 200.0), dtype=np.float64)
AREA_CENTRES_MM = np.asarray(
    [(x, y) for y in (50.0, 150.0) for x in (50.0, 150.0, 250.0, 350.0)],
    dtype=np.float64,
)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "artifacts/pc_position_runtime/position_ensemble.joblib"
GRID_MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "artifacts/pc_position_runtime_400x300x5/position_ensemble.joblib"
)
DEVICE_MODEL_PATH = (
    Path(__file__).resolve().parents[2]
    / "artifacts/device_position_probability_400x300x5/model.npz"
)
DEVICE_SUPPORT_XY_MM = np.asarray(sorted(set(
    [(x, 35 if y == 25 and x in (225, 275) else y)
     for x in range(25, 400, 50) for y in range(25, 300, 50)]
    + [(x, y) for x in range(50, 400, 100) for y in range(50, 300, 100)]
)), dtype=np.float64)


def class_probabilities(
    outputs: tuple[float, ...] | list[float], class_count: int = 8
) -> np.ndarray:
    scores = np.asarray(outputs, dtype=np.float64)
    if scores.shape != (class_count,) or not np.isfinite(scores).all():
        return np.full(class_count, 1.0 / class_count)
    # Solist outputs are scores rather than calibrated logits.  A moderate
    # temperature preserves secondary spatial hypotheses for the heat map.
    temperature = 0.18
    shifted = (scores - scores.max()) / temperature
    probability = np.exp(np.clip(shifted, -40.0, 0.0))
    return probability / probability.sum()


def compare_device_diagnostic(case_id: int, logits: tuple[float, ...]) -> dict[str, Any]:
    if not DEVICE_MODEL_PATH.is_file():
        return {"available": False, "error": "device model artifact is missing"}
    with np.load(DEVICE_MODEL_PATH, allow_pickle=False) as model:
        golden_inputs = from_bfloat16_bits(model["golden_inputs_bfloat16"])
        if not 0 <= case_id < len(golden_inputs) * 2:
            return {"available": False, "error": f"unknown diagnostic case {case_id}"}
        if case_id < len(golden_inputs):
            expected = from_bfloat16_bits(model["golden_logits_bfloat16"])[case_id]
            stage = "logits"
        else:
            alpha = from_bfloat16_bits(model["alpha_bfloat16"])
            hidden = np.clip(
                0.2 * (golden_inputs[case_id - len(golden_inputs)] @ alpha) + 0.5,
                0.0, 1.0,
            )
            expected = np.zeros(60, dtype=np.float32)
            expected[: len(hidden)] = quantize_bfloat16(hidden)
            stage = "hidden"
    actual = np.asarray(logits, dtype=np.float32)
    if not np.isfinite(actual).all():
        return {
            "available": True,
            "case_id": case_id,
            "stage": "logits",
            "non_finite_indices": np.flatnonzero(~np.isfinite(actual)).astype(int).tolist(),
            "passed": False,
        }
    delta = np.abs(actual - expected)
    return {
        "available": True,
        "case_id": case_id,
        "stage": stage,
        "max_abs_logit_delta": float(delta.max()),
        "mean_abs_logit_delta": float(delta.mean()),
        "expected_position": int(np.argmax(expected)),
        "actual_position": int(np.argmax(actual)),
        "argmax_match": bool(np.argmax(expected) == np.argmax(actual)),
        "passed": bool(delta.max() <= 0.015625 and np.argmax(expected) == np.argmax(actual)),
    }


@lru_cache(maxsize=4)
def load_bundle(path: str) -> dict[str, Any] | None:
    model_path = Path(path)
    if not model_path.is_file():
        return None
    return joblib.load(model_path)


class PositionEstimator:
    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH) -> None:
        self.model_path = Path(model_path).resolve()
        self.model_paths = {
            "400x200x3": self.model_path,
            "400x300x5": GRID_MODEL_PATH.resolve(),
        }

    @property
    def available(self) -> bool:
        return load_bundle(str(self.model_path)) is not None

    def available_for(self, panel_profile_id: str) -> bool:
        path = self.model_paths.get(panel_profile_id)
        return path is not None and load_bundle(str(path)) is not None

    def from_device_probabilities(
        self, probabilities: tuple[float, ...] | list[float], position_id: int,
        panel: dict[str, Any], timing: dict[str, int],
    ) -> dict[str, Any]:
        """Build display metadata without running any PC-side ML model."""
        if str(panel.get("id")) != "400x300x5":
            raise ValueError("デバイス確率推論は400x300x5パネル専用です")
        probability = np.asarray(probabilities, dtype=np.float64)
        if probability.shape != (len(DEVICE_SUPPORT_XY_MM),) or not np.isfinite(probability).all():
            raise ValueError("デバイス確率ベクトルが60出力ではありません")
        probability = np.maximum(probability, 0.0)
        probability /= max(float(probability.sum()), 1e-12)
        support = DEVICE_SUPPORT_XY_MM
        map_index = int(np.argmax(probability))
        if position_id != map_index:
            position_id = map_index
        expected = probability @ support
        map_coordinate = support[position_id]
        residual = support - expected
        covariance = np.einsum("n,ni,nj->ij", probability, residual, residual)
        covariance = (covariance + covariance.T) / 2.0
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        eigenvalues = np.maximum(eigenvalues, 1.0)
        covariance = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
        sigma = np.sqrt(np.diag(covariance))
        confidence_level = 0.90
        order = np.argsort(probability)[::-1]
        count = int(np.searchsorted(np.cumsum(probability[order]), confidence_level) + 1)
        credible = order[:count].astype(int).tolist()
        entropy = float(
            -np.sum(probability * np.log(np.maximum(probability, 1e-12)))
            / np.log(len(probability))
        )
        threshold = -2.0 * np.log(1.0 - confidence_level)
        major_index = int(np.argmax(eigenvalues))
        minor_index = 1 - major_index
        axes = np.sqrt(np.asarray((eigenvalues[major_index], eigenvalues[minor_index])) * threshold)
        major_vector = eigenvectors[:, major_index]
        area_ids = (
            np.floor(support[:, 1] / 100.0).astype(int) * 4
            + np.floor(support[:, 0] / 100.0).astype(int)
        )
        area_probability = np.asarray([
            probability[area_ids == class_id].sum()
            for class_id in range(int(panel["class_count"]))
        ])
        return {
            "x_mm": float(map_coordinate[0]), "y_mm": float(map_coordinate[1]),
            "expected_x_mm": float(expected[0]), "expected_y_mm": float(expected[1]),
            "map_x_mm": float(map_coordinate[0]), "map_y_mm": float(map_coordinate[1]),
            "sigma_x_mm": float(sigma[0]), "sigma_y_mm": float(sigma[1]),
            "rho_xy": float(np.clip(covariance[0, 1] / max(sigma[0] * sigma[1], 1e-9), -0.99, 0.99)),
            "confidence": confidence_level, "confidence_level": confidence_level,
            "empirical_coverage": 0.982468443197756,
            "confidence_ellipse_90": {
                "semi_major_mm": float(axes[0]), "semi_minor_mm": float(axes[1]),
                "angle_deg": float(np.degrees(np.arctan2(major_vector[1], major_vector[0]))),
            },
            "covariance_mm2": covariance.astype(float).tolist(),
            "classification_confidence": float(probability[position_id]),
            "class_probabilities": area_probability.astype(float).tolist(),
            "probability_map": {
                "support_xy_mm": support.astype(float).tolist(),
                "probabilities": probability.astype(float).tolist(),
                "credible_90_indices": credible, "normalization": "device_softmax_sum_1",
            },
            "distribution_entropy": entropy,
            "distribution_peak_probability": float(probability[position_id]),
            "ensemble_positions_mm": [], "ensemble_spread_mm": [0.0, 0.0],
            "model_available": True,
            "method": "device_solist_60class_probability_map",
            "inference_source": "device",
            "device_timing_us": timing,
            "scope": "デバイスが60位置推論とsoftmaxを実行し、PCは受信した確率分布を表示しています。",
        }

    def predict(self, event: EventData, outputs: tuple[float, ...] | list[float],
                predicted_class: int, panel: dict[str, Any] | None = None) -> dict[str, Any]:
        panel = panel or {
            "id": "400x200x3", "width_mm": 400.0, "height_mm": 200.0,
            "columns": 4, "rows": 2, "class_count": 8,
        }
        class_count = int(panel["class_count"])
        panel_size = np.asarray((panel["width_mm"], panel["height_mm"]), dtype=np.float64)
        centres = np.asarray([
            ((column + .5) * panel["width_mm"] / panel["columns"],
             (row + .5) * panel["height_mm"] / panel["rows"])
            for row in range(int(panel["rows"]))
            for column in range(int(panel["columns"]))
        ])
        probabilities = class_probabilities(outputs, class_count)
        entropy = float(-np.sum(probabilities * np.log(np.maximum(probabilities, 1e-12))) / np.log(class_count))
        selected_model_path = self.model_paths.get(str(panel.get("id")))
        bundle = load_bundle(str(selected_model_path)) if selected_model_path is not None else None
        model_positions: np.ndarray | None = None
        density_probability: np.ndarray | None = None
        density_support: np.ndarray | None = None
        if bundle is not None:
            contract = bundle["contract"]
            if (
                event.sample_rate_hz == int(contract["sample_rate_hz"])
                and len(event.samples) == int(contract["sample_count"])
                and event.trigger_index == int(contract["trigger_index"])
            ):
                waveform = np.asarray(event.samples, dtype=np.float64)
                feature = (
                    extract_grid_features(waveform)
                    if contract.get("feature_mode") == GRID_FEATURE_MODE
                    else extract_live_features(waveform)
                )[None, :]
                scaled = bundle["scaler"].transform(feature)
                model_positions = np.stack([
                    np.clip(model.predict(scaled)[0], 0.0, 1.0) * panel_size
                    for model in bundle["models"]
                ])

                density_models = bundle.get("density_models", [])
                density_support_candidate = np.asarray(
                    bundle.get("density_support_xy_mm", []), dtype=np.float64
                )
                if (
                    density_models
                    and density_support_candidate.ndim == 2
                    and density_support_candidate.shape[1] == 2
                ):
                    density_scaled = bundle.get("density_scaler", bundle["scaler"]).transform(feature)
                    member_probability = np.stack([
                        model.predict_proba(density_scaled)[0] for model in density_models
                    ])
                    probability = np.mean(member_probability, axis=0)
                    temperature = max(float(bundle.get("density_temperature", 1.0)), 1e-6)
                    logits = np.log(np.clip(probability, 1e-12, 1.0)) / temperature
                    logits -= logits.max()
                    density_probability = np.exp(logits)
                    density_probability /= density_probability.sum()
                    density_support = density_support_candidate

        credible_indices: list[int] = []
        density_entropy = 0.0
        density_peak_probability = 0.0
        if density_probability is not None and density_support is not None:
            expected_coordinate = density_probability @ density_support
            map_coordinate = density_support[int(np.argmax(density_probability))]
            centre = map_coordinate
            residual = density_support - expected_coordinate
            covariance = np.einsum("n,ni,nj->ij", density_probability, residual, residual)
            covariance = (covariance + covariance.T) / 2.0
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            eigenvalues = np.maximum(eigenvalues, 1.0)
            covariance = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
            confidence_level = 0.90
            order = np.argsort(density_probability)[::-1]
            cumulative = np.cumsum(density_probability[order])
            count = int(np.searchsorted(cumulative, confidence_level) + 1)
            credible_indices = order[:count].astype(int).tolist()
            density_entropy = float(
                -np.sum(density_probability * np.log(np.maximum(density_probability, 1e-12)))
                / np.log(len(density_probability))
            )
            density_peak_probability = float(np.max(density_probability))
            empirical_coverage = float(
                bundle.get("density_validation", {}).get("top1_cell_accuracy", 0.0)
            )
            threshold = -2.0 * np.log(1.0 - confidence_level)
            major_index = int(np.argmax(eigenvalues))
            minor_index = 1 - major_index
            ellipse_axes = np.sqrt(
                np.asarray((eigenvalues[major_index], eigenvalues[minor_index])) * threshold
            )
            major_vector = eigenvectors[:, major_index]
            ellipse_angle = float(np.degrees(np.arctan2(major_vector[1], major_vector[0])))
            spread = np.sqrt(np.diag(covariance))
            method = "pc_mlp_60class_probability_map"
        elif model_positions is None:
            centre = centres[int(np.clip(predicted_class, 0, class_count - 1))]
            expected_coordinate = centre
            map_coordinate = centre
            spread = np.asarray((0.0, 0.0))
            covariance = None
            confidence_level = 0.0
            empirical_coverage = 0.0
            ellipse_axes = np.asarray((0.0, 0.0))
            ellipse_angle = 0.0
            method = (
                "area_probability_fallback" if class_count == len(outputs)
                else "panel_profile_model_unavailable"
            )
        else:
            centre = model_positions.mean(axis=0)
            expected_coordinate = centre
            map_coordinate = centre
            spread = model_positions.std(axis=0)
            validation = bundle.get("validation", {})
            uncertainty = bundle.get("uncertainty", {})
            covariance = np.asarray(
                uncertainty.get(
                    "calibrated_covariance_mm2",
                    [
                        [float(validation.get("rmse_x_mm", 14.0)) ** 2, 0.0],
                        [0.0, float(validation.get("rmse_y_mm", 6.0)) ** 2],
                    ],
                ),
                dtype=np.float64,
            )
            if covariance.shape != (2, 2) or not np.isfinite(covariance).all():
                covariance = np.diag((14.0 ** 2, 6.0 ** 2))
            if len(model_positions) > 1:
                covariance += np.cov(model_positions, rowvar=False, ddof=1)
            covariance = (covariance + covariance.T) / 2.0
            eigenvalues, eigenvectors = np.linalg.eigh(covariance)
            eigenvalues = np.maximum(eigenvalues, 1.0)
            covariance = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T
            confidence_level = float(uncertainty.get("confidence_level", 0.90))
            empirical_coverage = float(uncertainty.get("empirical_coverage", 0.0))
            threshold = float(
                uncertainty.get(
                    "chi_square_threshold", -2.0 * np.log(1.0 - confidence_level)
                )
            )
            major_index = int(np.argmax(eigenvalues))
            minor_index = 1 - major_index
            ellipse_axes = np.sqrt(
                np.asarray((eigenvalues[major_index], eigenvalues[minor_index])) * threshold
            )
            major_vector = eigenvectors[:, major_index]
            ellipse_angle = float(np.degrees(np.arctan2(major_vector[1], major_vector[0])))
            method = str(bundle.get("method", "pc_mlp_xy_calibrated_gaussian"))

        sigma = (
            np.sqrt(np.diag(covariance))
            if covariance is not None else np.asarray((0.0, 0.0))
        )
        correlation = (
            float(np.clip(covariance[0, 1] / max(sigma[0] * sigma[1], 1e-9), -0.99, 0.99))
            if covariance is not None else 0.0
        )
        if density_probability is not None:
            classification_confidence = density_peak_probability
        else:
            classification_confidence = float(np.clip(
                (1.0 - entropy) * np.exp(-np.linalg.norm(spread) / 35.0), 0.0, 1.0
            ))
        return {
            "x_mm": float(centre[0]),
            "y_mm": float(centre[1]),
            "expected_x_mm": float(expected_coordinate[0]),
            "expected_y_mm": float(expected_coordinate[1]),
            "map_x_mm": float(map_coordinate[0]),
            "map_y_mm": float(map_coordinate[1]),
            "sigma_x_mm": float(sigma[0]),
            "sigma_y_mm": float(sigma[1]),
            "rho_xy": correlation,
            "confidence": confidence_level,
            "confidence_level": confidence_level,
            "empirical_coverage": empirical_coverage,
            "confidence_ellipse_90": {
                "semi_major_mm": float(ellipse_axes[0]),
                "semi_minor_mm": float(ellipse_axes[1]),
                "angle_deg": ellipse_angle,
            },
            "covariance_mm2": covariance.astype(float).tolist() if covariance is not None else [],
            "classification_confidence": classification_confidence,
            "class_probabilities": probabilities.astype(float).tolist(),
            "probability_map": (
                {
                    "support_xy_mm": density_support.astype(float).tolist(),
                    "probabilities": density_probability.astype(float).tolist(),
                    "credible_90_indices": credible_indices,
                    "normalization": "sum_1",
                }
                if density_probability is not None and density_support is not None else None
            ),
            "distribution_entropy": density_entropy,
            "distribution_peak_probability": density_peak_probability,
            "ensemble_positions_mm": (
                model_positions.astype(float).tolist() if model_positions is not None else []
            ),
            "ensemble_spread_mm": spread.astype(float).tolist(),
            "model_available": model_positions is not None,
            "method": method,
            "scope": (
                str(bundle.get("scope", "PC座標モデルによるXY推定です。"))
                if model_positions is not None else
                "PC座標モデルを利用できないため、不確実性分布は表示していません。"
            ),
        }
