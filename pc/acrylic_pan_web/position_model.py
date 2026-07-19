"""Optional PC-side XY ensemble and probability-distribution metadata."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import joblib
import numpy as np

from pc.acrylic_pan_monitor.protocol import EventData
from sim.pc_position_runtime import extract_live_features

PANEL_SIZE_MM = np.asarray((400.0, 200.0), dtype=np.float64)
AREA_CENTRES_MM = np.asarray(
    [(x, y) for y in (50.0, 150.0) for x in (50.0, 150.0, 250.0, 350.0)],
    dtype=np.float64,
)
DEFAULT_MODEL_PATH = Path(__file__).resolve().parents[2] / "artifacts/pc_position_runtime/position_ensemble.joblib"


def class_probabilities(outputs: tuple[float, ...] | list[float]) -> np.ndarray:
    scores = np.asarray(outputs, dtype=np.float64)
    if scores.shape != (8,) or not np.isfinite(scores).all():
        return np.full(8, 1.0 / 8.0)
    # Solist outputs are scores rather than calibrated logits.  A moderate
    # temperature preserves secondary spatial hypotheses for the heat map.
    temperature = 0.18
    shifted = (scores - scores.max()) / temperature
    probability = np.exp(np.clip(shifted, -40.0, 0.0))
    return probability / probability.sum()


@lru_cache(maxsize=4)
def load_bundle(path: str) -> dict[str, Any] | None:
    model_path = Path(path)
    if not model_path.is_file():
        return None
    return joblib.load(model_path)


class PositionEstimator:
    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH) -> None:
        self.model_path = Path(model_path).resolve()

    @property
    def available(self) -> bool:
        return load_bundle(str(self.model_path)) is not None

    def predict(self, event: EventData, outputs: tuple[float, ...] | list[float],
                predicted_class: int) -> dict[str, Any]:
        probabilities = class_probabilities(outputs)
        entropy = float(-np.sum(probabilities * np.log(np.maximum(probabilities, 1e-12))) / np.log(8.0))
        bundle = load_bundle(str(self.model_path))
        model_positions: np.ndarray | None = None
        if bundle is not None:
            contract = bundle["contract"]
            if (
                event.sample_rate_hz == int(contract["sample_rate_hz"])
                and len(event.samples) == int(contract["sample_count"])
                and event.trigger_index == int(contract["trigger_index"])
            ):
                feature = extract_live_features(np.asarray(event.samples, dtype=np.float64))[None, :]
                scaled = bundle["scaler"].transform(feature)
                model_positions = np.stack([
                    np.clip(model.predict(scaled)[0], 0.0, 1.0) * PANEL_SIZE_MM
                    for model in bundle["models"]
                ])

        if model_positions is None:
            centre = AREA_CENTRES_MM[int(np.clip(predicted_class, 0, 7))]
            spread = np.asarray((0.0, 0.0))
            covariance = None
            confidence_level = 0.0
            empirical_coverage = 0.0
            ellipse_axes = np.asarray((0.0, 0.0))
            ellipse_angle = 0.0
            method = "area_probability_fallback"
        else:
            centre = model_positions.mean(axis=0)
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
            method = "pc_mlp_xy_calibrated_gaussian"

        sigma = (
            np.sqrt(np.diag(covariance))
            if covariance is not None else np.asarray((0.0, 0.0))
        )
        correlation = (
            float(np.clip(covariance[0, 1] / max(sigma[0] * sigma[1], 1e-9), -0.99, 0.99))
            if covariance is not None else 0.0
        )
        classification_confidence = float(
            np.clip((1.0 - entropy) * np.exp(-np.linalg.norm(spread) / 35.0), 0.0, 1.0)
        )
        return {
            "x_mm": float(centre[0]),
            "y_mm": float(centre[1]),
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
            "ensemble_positions_mm": (
                model_positions.astype(float).tolist() if model_positions is not None else []
            ),
            "ensemble_spread_mm": spread.astype(float).tolist(),
            "model_available": model_positions is not None,
            "method": method,
            "scope": (
                "XY回帰の平均座標と、LOSO実測誤差で90%被覆に校正した不確実性分布です。"
                "モデル間分散も加算しています。8中心点教師からの補間であり、"
                "任意位置の実測精度は未検証です。"
                if model_positions is not None else
                "PC座標モデルを利用できないため、不確実性分布は表示していません。"
            ),
        }
