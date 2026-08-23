"""Train an unrestricted PC XY model for the 400 x 300 x 5 mm panel.

The live firmware still supplies the first 512 samples (20 ms).  Unlike the
edge model, the PC model keeps nearly the complete post-trigger waveform and
the complete positive-frequency spectrum, and trains all neural-network
layers.  Centre recordings and the measured four-corners-per-area grid are
combined explicitly.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import warnings

import joblib
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPClassifier, MLPRegressor
from sklearn.preprocessing import StandardScaler
from scipy.optimize import minimize_scalar

from .sampling_experiment import extract_features, regression_metrics

PANEL_ID = "400x300x5"
PANEL_WIDTH_MM = 400.0
PANEL_HEIGHT_MM = 300.0
CLASS_COUNT = 12
SAMPLE_RATE_HZ = 25_600
SOURCE_SAMPLE_COUNT = 2_048
SAMPLE_COUNT = 512
TRIGGER_INDEX = 64
FEATURE_MODE = "pc_rich_20ms_v1"
CENTRE_SESSION_IDS = (
    "20260720_215533_aa9943ae",
    "20260720_220935_bd12a70b",
    "20260821_215225_8f38a3e4",
    "20260821_221547_4c753402",
)
GRID_SESSION_IDS = (
    "20260823_081435_223491a5",
    "20260823_082825_9374f169",
    "20260823_083716_e0336d95",
    "20260823_084554_6d26bd22",
)
BASELINE_GRID_SESSION_IDS = (
    "20260823_081435_223491a5",
    "20260823_083716_e0336d95",
    "20260823_084554_6d26bd22",
)
NEW_GRID_SESSION_ID = "20260823_082825_9374f169"
EXTERNAL_EVAL_SESSION_IDS: tuple[str, ...] = ()
GRID_SESSION_ID = GRID_SESSION_IDS[0]  # Compatibility name for the first grid set.
DEFAULT_SESSION_IDS = (*CENTRE_SESSION_IDS, *GRID_SESSION_IDS)
DEFAULT_SEEDS = (1, 7, 21)
HIDDEN_LAYERS = (384, 192, 96)
DENSITY_HIDDEN_LAYERS = (384, 192, 96)


@dataclass(frozen=True)
class PositionDataset:
    samples: np.ndarray
    xy_mm: np.ndarray
    labels: np.ndarray
    session_ids: np.ndarray
    repetitions: np.ndarray
    point_names: np.ndarray
    dataset_sha256: str


def _scalar(npz: np.lib.npyio.NpzFile, name: str) -> int:
    value = np.asarray(npz[name])
    if value.size != 1:
        raise ValueError(f"{name} must be scalar")
    return int(value.reshape(-1)[0])


def load_position_dataset(root: Path, session_ids: tuple[str, ...] = DEFAULT_SESSION_IDS) -> PositionDataset:
    root = Path(root).resolve()
    samples: list[np.ndarray] = []
    coordinates: list[tuple[float, float]] = []
    labels: list[int] = []
    sessions: list[str] = []
    repetitions: list[int] = []
    point_names: list[str] = []
    digest = hashlib.sha256()
    for session_id in session_ids:
        directory = (root / session_id).resolve()
        metadata_path = directory / "session.json"
        manifest_path = directory / "manifest.jsonl"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        panel_id = metadata.get("user_metadata", {}).get("panel_profile_id")
        if metadata.get("format") != "acrylic-pan-session-v1" or panel_id != PANEL_ID:
            raise ValueError(f"{session_id}: incompatible session or panel profile")
        rows = [
            json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if int(metadata.get("event_count", -1)) != len(rows):
            raise ValueError(f"{session_id}: event_count does not match manifest")
        digest.update(metadata_path.read_bytes())
        digest.update(manifest_path.read_bytes())
        session_classes: set[int] = set()
        for row in rows:
            label = row.get("class_id")
            annotation = row.get("annotations")
            if not isinstance(label, int) or not 0 <= label < CLASS_COUNT:
                raise ValueError(f"{session_id}: invalid class label")
            if not isinstance(annotation, dict):
                raise ValueError(f"{session_id}: missing guided annotation")
            x_mm = float(annotation["target_x_mm"])
            y_mm = float(annotation["target_y_mm"])
            repetition = int(annotation["repetition"])
            point_name = str(annotation.get("target_point_name", "center"))
            if not np.isfinite((x_mm, y_mm)).all() or not (
                0.0 <= x_mm <= PANEL_WIDTH_MM and 0.0 <= y_mm <= PANEL_HEIGHT_MM
            ):
                raise ValueError(f"{session_id}: coordinate is outside the panel")
            event_path = (directory / str(row["file"])).resolve()
            event_path.relative_to(directory)
            with np.load(event_path, allow_pickle=False) as event:
                waveform = np.asarray(event["samples"], dtype=np.float64)
                if waveform.shape != (SOURCE_SAMPLE_COUNT,):
                    raise ValueError(f"{event_path}: expected {SOURCE_SAMPLE_COUNT} samples")
                if _scalar(event, "sample_rate_hz") != SAMPLE_RATE_HZ:
                    raise ValueError(f"{event_path}: unexpected sample rate")
                if _scalar(event, "trigger_index") != TRIGGER_INDEX:
                    raise ValueError(f"{event_path}: unexpected trigger index")
                if _scalar(event, "class_id") != label:
                    raise ValueError(f"{event_path}: class label mismatch")
            digest.update(waveform.astype(np.int16).tobytes())
            samples.append(waveform[:SAMPLE_COUNT])
            coordinates.append((x_mm, y_mm))
            labels.append(label)
            sessions.append(session_id)
            repetitions.append(repetition)
            point_names.append(point_name)
            session_classes.add(label)
        if session_classes != set(range(CLASS_COUNT)):
            raise ValueError(f"{session_id}: session does not contain all 12 classes")
    return PositionDataset(
        samples=np.stack(samples),
        xy_mm=np.asarray(coordinates, dtype=np.float64),
        labels=np.asarray(labels, dtype=np.int64),
        session_ids=np.asarray(sessions),
        repetitions=np.asarray(repetitions, dtype=np.int64),
        point_names=np.asarray(point_names),
        dataset_sha256=digest.hexdigest(),
    )


def extract_grid_features(samples: np.ndarray) -> np.ndarray:
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.shape != (SAMPLE_COUNT,):
        raise ValueError(f"waveform must contain {SAMPLE_COUNT} samples")
    baseline = float(np.mean(waveform[:TRIGGER_INDEX]))
    centered = waveform - baseline
    post = centered[TRIGGER_INDEX:]
    peak = max(float(np.max(np.abs(post))), 1.0)
    rms = max(float(np.sqrt(np.mean(post ** 2))), 1.0)
    normalized_time = post / peak
    spectrum = np.abs(np.fft.rfft(centered * np.hanning(SAMPLE_COUNT)))[1:]
    normalized_spectrum = np.log1p(spectrum / peak)
    absolute = np.abs(post)
    energy = absolute ** 2
    quarter_energy = np.asarray([
        np.sum(part) for part in np.array_split(energy, 4)
    ]) / max(float(np.sum(energy)), 1.0)
    scalars = np.asarray([
        np.log1p(peak),
        np.log1p(rms),
        peak / rms,
        float(np.argmax(absolute)) / max(len(post) - 1, 1),
        float(np.max(post)) / peak,
        float(-np.min(post)) / peak,
        *quarter_energy,
    ])
    result = np.concatenate((normalized_time, normalized_spectrum, scalars))
    if result.shape != (714,) or not np.isfinite(result).all():
        raise RuntimeError("rich PC feature extraction failed")
    return result.astype(np.float32)


def _model(seed: int) -> MLPRegressor:
    return MLPRegressor(
        hidden_layer_sizes=HIDDEN_LAYERS,
        activation="relu",
        solver="adam",
        alpha=1e-3,
        batch_size=128,
        learning_rate_init=1e-3,
        max_iter=350,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=seed,
    )


def _density_model(seed: int) -> MLPClassifier:
    return MLPClassifier(
        hidden_layer_sizes=DENSITY_HIDDEN_LAYERS,
        activation="relu",
        solver="adam",
        alpha=1e-3,
        batch_size=128,
        learning_rate_init=1e-3,
        max_iter=350,
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=20,
        random_state=seed,
    )


def _temperature_scaled(probabilities: np.ndarray, temperature: float) -> np.ndarray:
    logits = np.log(np.clip(probabilities, 1e-12, 1.0)) / max(float(temperature), 1e-6)
    logits -= logits.max(axis=1, keepdims=True)
    scaled = np.exp(logits)
    return scaled / scaled.sum(axis=1, keepdims=True)


def _calibrate_temperature(probabilities: np.ndarray, labels: np.ndarray) -> float:
    def objective(temperature: float) -> float:
        calibrated = _temperature_scaled(probabilities, temperature)
        true_probability = calibrated[np.arange(len(labels)), labels]
        return float(-np.mean(np.log(np.clip(true_probability, 1e-12, 1.0))))

    result = minimize_scalar(objective, bounds=(0.25, 4.0), method="bounded")
    return float(result.x)


def density_metrics(expected_xy: np.ndarray, labels: np.ndarray,
                    probabilities: np.ndarray, support_xy: np.ndarray) -> dict:
    expected = probabilities @ support_xy
    map_xy = support_xy[np.argmax(probabilities, axis=1)]
    expected_distance = np.linalg.norm(expected - expected_xy, axis=1)
    map_distance = np.linalg.norm(map_xy - expected_xy, axis=1)
    support_distance = np.linalg.norm(
        support_xy[None, :, :] - expected_xy[:, None, :], axis=2
    )
    sorted_probability = np.sort(probabilities, axis=1)[:, ::-1]
    credible_cells = 1 + np.sum(np.cumsum(sorted_probability, axis=1) < 0.90, axis=1)
    confidence = probabilities.max(axis=1)
    correct = np.argmax(probabilities, axis=1) == labels
    ece = 0.0
    for lower in np.linspace(0.0, 0.9, 10):
        selected = (confidence >= lower) & (confidence < lower + 0.1)
        if np.any(selected):
            ece += float(np.mean(selected)) * abs(
                float(np.mean(confidence[selected])) - float(np.mean(correct[selected]))
            )
    return {
        "nll": float(-np.mean(np.log(np.clip(
            probabilities[np.arange(len(labels)), labels], 1e-12, 1.0
        )))),
        "brier_score": float(np.mean(np.sum(
            (probabilities - np.eye(probabilities.shape[1])[labels]) ** 2, axis=1
        ))),
        "top1_cell_accuracy": float(np.mean(correct)),
        "expected_mean_distance_mm": float(np.mean(expected_distance)),
        "expected_median_distance_mm": float(np.median(expected_distance)),
        "map_mean_distance_mm": float(np.mean(map_distance)),
        "probability_mass_within_25mm": float(np.mean(np.sum(
            probabilities * (support_distance <= 25.0), axis=1
        ))),
        "probability_mass_within_50mm": float(np.mean(np.sum(
            probabilities * (support_distance <= 50.0), axis=1
        ))),
        "mean_credible_90_cell_count": float(np.mean(credible_cells)),
        "expected_calibration_error": ece,
    }


def _balanced_density_indices(labels: np.ndarray, selected: np.ndarray,
                              seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    classes = np.unique(labels)
    per_class = [np.flatnonzero(selected & (labels == class_id)) for class_id in classes]
    if any(len(indices) == 0 for indices in per_class):
        raise ValueError("density split must contain every coordinate class")
    target_count = max(len(indices) for indices in per_class)
    balanced = []
    for indices in per_class:
        repeats, remainder = divmod(target_count, len(indices))
        expanded = np.tile(indices, repeats)
        if remainder:
            expanded = np.concatenate((expanded, rng.choice(indices, remainder, replace=False)))
        balanced.append(expanded)
    result = np.concatenate(balanced)
    rng.shuffle(result)
    return result


def _parameter_count(input_count: int, output_count: int = 2) -> int:
    sizes = (input_count, *HIDDEN_LAYERS, output_count)
    return int(sum((left + 1) * right for left, right in zip(sizes[:-1], sizes[1:])))


def _fit_predict(features: np.ndarray, targets: np.ndarray, train: np.ndarray,
                 test: np.ndarray, seed: int) -> tuple[np.ndarray, int]:
    scaler = StandardScaler().fit(features[train])
    model = _model(seed)
    normalized_targets = targets / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    model.fit(scaler.transform(features[train]), normalized_targets[train])
    predicted = np.clip(model.predict(scaler.transform(features[test])), 0.0, 1.0)
    return predicted * (PANEL_WIDTH_MM, PANEL_HEIGHT_MM), int(model.n_iter_)


def _uncertainty(expected: np.ndarray, predicted: np.ndarray) -> dict:
    residuals = predicted - expected
    covariance = np.cov(residuals, rowvar=False, ddof=1)
    covariance = (covariance + covariance.T) / 2.0
    eigenvalues, eigenvectors = np.linalg.eigh(covariance)
    covariance = eigenvectors @ np.diag(np.maximum(eigenvalues, 1.0)) @ eigenvectors.T
    inverse = np.linalg.inv(covariance)
    d2 = np.einsum("ni,ij,nj->n", residuals, inverse, residuals)
    level = 0.90
    threshold = -2.0 * np.log(1.0 - level)
    scale = float(np.quantile(d2, level) / threshold)
    calibrated = covariance * max(scale, 1e-6)
    calibrated_inverse = np.linalg.inv(calibrated)
    calibrated_d2 = np.einsum("ni,ij,nj->n", residuals, calibrated_inverse, residuals)
    return {
        "method": "grouped_repetition_residual_covariance",
        "confidence_level": level,
        "chi_square_threshold": threshold,
        "calibration_scale": scale,
        "empirical_coverage": float(np.mean(calibrated_d2 <= threshold)),
        "residual_covariance_mm2": covariance.tolist(),
        "calibrated_covariance_mm2": calibrated.tolist(),
    }


def _bundle_predict(bundle: dict, features: np.ndarray) -> np.ndarray:
    scaled = bundle["scaler"].transform(features)
    predictions = np.stack([
        np.clip(model.predict(scaled), 0.0, 1.0) * (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
        for model in bundle["models"]
    ])
    return predictions.mean(axis=0)


def evaluate_external_sessions(sessions_root: Path, baseline_model_path: Path,
                               candidate_model_path: Path) -> dict:
    dataset = load_position_dataset(sessions_root, EXTERNAL_EVAL_SESSION_IDS)
    features = np.stack([extract_grid_features(row) for row in dataset.samples])
    baseline = joblib.load(baseline_model_path)
    candidate = joblib.load(candidate_model_path)
    baseline_prediction = _bundle_predict(baseline, features)
    candidate_prediction = _bundle_predict(candidate, features)
    baseline_metrics = regression_metrics(dataset.xy_mm, baseline_prediction)
    candidate_metrics = regression_metrics(dataset.xy_mm, candidate_prediction)
    per_session = []
    for session_id in EXTERNAL_EVAL_SESSION_IDS:
        selected = dataset.session_ids == session_id
        per_session.append({
            "session_id": session_id,
            "sample_count": int(selected.sum()),
            "baseline": regression_metrics(dataset.xy_mm[selected], baseline_prediction[selected]),
            "candidate": regression_metrics(dataset.xy_mm[selected], candidate_prediction[selected]),
        })
    return {
        "session_ids": list(EXTERNAL_EVAL_SESSION_IDS),
        "sample_count": int(len(dataset.samples)),
        "dataset_sha256": dataset.dataset_sha256,
        "training_overlap": False,
        "baseline_model": str(baseline_model_path),
        "candidate_model": str(candidate_model_path),
        "baseline": baseline_metrics,
        "candidate": candidate_metrics,
        "mean_distance_improvement_mm": float(
            baseline_metrics["mean_distance_mm"] - candidate_metrics["mean_distance_mm"]
        ),
        "mean_distance_improvement_percent": float(
            100.0 * (baseline_metrics["mean_distance_mm"] - candidate_metrics["mean_distance_mm"])
            / baseline_metrics["mean_distance_mm"]
        ),
        "per_session": per_session,
    }


def run(sessions_root: Path, output_dir: Path, seeds: tuple[int, ...] = DEFAULT_SEEDS,
        baseline_model_path: Path | None = None) -> dict:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be non-empty and unique")
    dataset = load_position_dataset(sessions_root)
    features = np.stack([extract_grid_features(row) for row in dataset.samples])
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    grid = np.isin(dataset.session_ids, GRID_SESSION_IDS)
    centre = ~grid
    baseline_grid = np.isin(dataset.session_ids, BASELINE_GRID_SESSION_IDS)
    new_grid = dataset.session_ids == NEW_GRID_SESSION_ID

    previous_model_unseen = None
    if baseline_model_path is not None and Path(baseline_model_path).is_file():
        previous_bundle = joblib.load(baseline_model_path)
        previous_prediction = _bundle_predict(previous_bundle, features[new_grid])
        previous_model_unseen = {
            "model": str(baseline_model_path),
            "test_session": NEW_GRID_SESSION_ID,
            "test_count": int(new_grid.sum()),
            **regression_metrics(dataset.xy_mm[new_grid], previous_prediction),
        }

    # Repetitions 9 and 10 are a common coordinate-balanced test set. The
    # three-grid condition sees no training event from the new session, while
    # the four-grid condition sees repetitions 1..8 from all four sessions.
    common_test = grid & np.isin(dataset.repetitions, (9, 10))
    three_grid_train = centre | (baseline_grid & ~common_test)
    four_grid_train = ~common_test
    three_grid_prediction, three_grid_iterations = _fit_predict(
        features, dataset.xy_mm, three_grid_train, common_test, seeds[0]
    )
    four_grid_prediction, four_grid_iterations = _fit_predict(
        features, dataset.xy_mm, four_grid_train, common_test, seeds[0]
    )
    three_grid_metrics = regression_metrics(dataset.xy_mm[common_test], three_grid_prediction)
    four_grid_metrics = regression_metrics(dataset.xy_mm[common_test], four_grid_prediction)
    common_comparison = {
        "held_out_repetitions": [9, 10],
        "test_sessions": list(GRID_SESSION_IDS),
        "test_count": int(common_test.sum()),
        "three_grids": {
            "training_grid_sessions": list(BASELINE_GRID_SESSION_IDS),
            "train_count": int(three_grid_train.sum()),
            "iterations": three_grid_iterations,
            **three_grid_metrics,
        },
        "four_grids": {
            "training_grid_sessions": list(GRID_SESSION_IDS),
            "train_count": int(four_grid_train.sum()),
            "iterations": four_grid_iterations,
            **four_grid_metrics,
        },
        "mean_distance_improvement_mm": float(
            three_grid_metrics["mean_distance_mm"] - four_grid_metrics["mean_distance_mm"]
        ),
        "mean_distance_improvement_percent": float(
            100.0 * (three_grid_metrics["mean_distance_mm"] - four_grid_metrics["mean_distance_mm"])
            / three_grid_metrics["mean_distance_mm"]
        ),
    }
    uncertainty = _uncertainty(dataset.xy_mm[common_test], four_grid_prediction)

    density_support_xy = np.unique(dataset.xy_mm, axis=0)
    if len(density_support_xy) != 60:
        raise ValueError(f"expected 60 measured density classes, got {len(density_support_xy)}")
    density_lookup = {
        (float(coordinate[0]), float(coordinate[1])): index
        for index, coordinate in enumerate(density_support_xy)
    }
    density_labels = np.asarray([
        density_lookup[(float(coordinate[0]), float(coordinate[1]))]
        for coordinate in dataset.xy_mm
    ], dtype=np.int64)
    # Every session uses guided repetition numbers. Holding out each fifth
    # repetition gives a coordinate-balanced 20% split across centre and grid data.
    density_train = dataset.repetitions % 5 != 0
    density_test = ~density_train
    density_train_indices = _balanced_density_indices(density_labels, density_train, seeds[0])
    density_scaler = StandardScaler().fit(features[density_train_indices])
    density_validation_model = _density_model(seeds[0])
    density_validation_model.fit(
        density_scaler.transform(features[density_train_indices]),
        density_labels[density_train_indices],
    )
    raw_density_probability = density_validation_model.predict_proba(
        density_scaler.transform(features[density_test])
    )
    density_temperature = _calibrate_temperature(
        raw_density_probability, density_labels[density_test]
    )
    density_probability = _temperature_scaled(
        raw_density_probability, density_temperature
    )
    density_validation = {
        "held_out_repetition_rule": "repetition_modulo_5_equals_0",
        "test_count": int(density_test.sum()),
        "balanced_train_count": int(len(density_train_indices)),
        "support_cell_count": int(len(density_support_xy)),
        "temperature": density_temperature,
        "iterations": int(density_validation_model.n_iter_),
        **density_metrics(
            dataset.xy_mm[density_test],
            density_labels[density_test],
            density_probability,
            density_support_xy,
        ),
        "per_position_group": {
            "centre_12": density_metrics(
                dataset.xy_mm[density_test & centre],
                density_labels[density_test & centre],
                density_probability[centre[density_test]],
                density_support_xy,
            ),
            "grid_48": density_metrics(
                dataset.xy_mm[density_test & grid],
                density_labels[density_test & grid],
                density_probability[grid[density_test]],
                density_support_xy,
            ),
        },
    }

    scaler = StandardScaler().fit(features)
    scaled = scaler.transform(features)
    targets = dataset.xy_mm / (PANEL_WIDTH_MM, PANEL_HEIGHT_MM)
    models = []
    final_iterations = []
    for seed in seeds:
        model = _model(seed)
        model.fit(scaled, targets)
        models.append(model)
        final_iterations.append(int(model.n_iter_))

    density_models = []
    density_iterations = []
    density_all = np.ones(len(density_labels), dtype=bool)
    density_final_indices = _balanced_density_indices(density_labels, density_all, seeds[0])
    density_final_scaler = StandardScaler().fit(features[density_final_indices])
    density_scaled = density_final_scaler.transform(features[density_final_indices])
    for seed in seeds:
        model = _density_model(seed)
        model.fit(density_scaled, density_labels[density_final_indices])
        density_models.append(model)
        density_iterations.append(int(model.n_iter_))

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "position_ensemble.joblib"
    scope = (
        "400×300×5 mmの12中心＋四隅48点の計60座標について、各座標の条件付き確率を"
        "直接出力するPC専用確率マップモデルです。座標は60クラス分布の期待値として算出します。"
        "評価は全8セッションで反復番号が5の倍数のイベントを除外した共通holdoutです。"
        "四隅セッションをすべて学習へ使用したため、完全未学習の外部セッションは残っていません。"
    )
    bundle = {
        "scaler": scaler,
        "models": models,
        "density_models": density_models,
        "density_scaler": density_final_scaler,
        "density_support_xy_mm": density_support_xy.astype(float),
        "density_temperature": density_temperature,
        "density_validation": density_validation,
        "contract": {
            "panel_profile_id": PANEL_ID,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "sample_count": SAMPLE_COUNT,
            "trigger_index": TRIGGER_INDEX,
            "feature_mode": FEATURE_MODE,
            "panel_width_mm": PANEL_WIDTH_MM,
            "panel_height_mm": PANEL_HEIGHT_MM,
        },
        "validation": four_grid_metrics,
        "uncertainty": uncertainty,
        "method": "pc_mlp_60class_probability_map",
        "scope": scope,
    }
    joblib.dump(bundle, bundle_path, compress=3)
    external_evaluation = None
    if (
        EXTERNAL_EVAL_SESSION_IDS
        and baseline_model_path is not None
        and Path(baseline_model_path).is_file()
        and all(
            (Path(sessions_root) / session_id / "session.json").is_file()
            for session_id in EXTERNAL_EVAL_SESSION_IDS
        )
    ):
        external_evaluation = evaluate_external_sessions(
            sessions_root, Path(baseline_model_path), bundle_path
        )
        (output_dir / "external_unused_sessions_evaluation.json").write_text(
            json.dumps(external_evaluation, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report = {
        "experiment": "pc_position_400x300x5_grid_v4",
        "dataset_sha256": dataset.dataset_sha256,
        "session_ids": list(DEFAULT_SESSION_IDS),
        "sample_count": int(len(dataset.samples)),
        "centre_sample_count": int(centre.sum()),
        "grid_sample_count": int(grid.sum()),
        "grid_session_ids": list(GRID_SESSION_IDS),
        "grid_session_counts": {
            session_id: int(np.sum(dataset.session_ids == session_id))
            for session_id in GRID_SESSION_IDS
        },
        "unique_coordinates": int(len(np.unique(dataset.xy_mm, axis=0))),
        "feature_count": int(features.shape[1]),
        "architecture": [int(features.shape[1]), *HIDDEN_LAYERS, 2],
        "trainable_parameters_per_model": _parameter_count(features.shape[1]),
        "density_trainable_parameters_per_model": _parameter_count(
            features.shape[1], len(density_support_xy)
        ),
        "runtime_seeds": list(seeds),
        "final_iterations": final_iterations,
        "density_iterations": density_iterations,
        "density_balanced_final_count": int(len(density_final_indices)),
        "density_validation": density_validation,
        "previous_model_unseen_new_session": previous_model_unseen,
        "common_holdout_comparison": common_comparison,
        "external_unused_sessions_comparison": external_evaluation,
        "uncertainty": uncertainty,
        "contract": bundle["contract"],
        "scope": scope,
        "model": str(bundle_path),
    }
    (output_dir / "training_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    np.savez_compressed(
        output_dir / "validation_predictions.npz",
        common_expected_xy_mm=dataset.xy_mm[common_test],
        three_grid_predicted_xy_mm=three_grid_prediction,
        four_grid_predicted_xy_mm=four_grid_prediction,
        common_session_ids=dataset.session_ids[common_test],
        common_repetitions=dataset.repetitions[common_test],
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=Path, default=Path("data/raw/sessions"))
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/pc_position_runtime_400x300x5"),
    )
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    parser.add_argument("--baseline-model", type=Path)
    args = parser.parse_args()
    report = run(
        args.sessions, args.output_dir, tuple(args.seeds), args.baseline_model
    )
    comparison = report["common_holdout_comparison"]
    print(f"samples={report['sample_count']}; unique_coordinates={report['unique_coordinates']}")
    print(f"three-grid common holdout={comparison['three_grids']['mean_distance_mm']:.2f} mm")
    print(f"four-grid common holdout={comparison['four_grids']['mean_distance_mm']:.2f} mm")
    print(f"improvement={comparison['mean_distance_improvement_percent']:.1f}%")
    print(f"model={report['model']}")


if __name__ == "__main__":
    main()
