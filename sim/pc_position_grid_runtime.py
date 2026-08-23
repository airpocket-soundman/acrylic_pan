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
from sklearn.model_selection import GroupKFold
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

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
GRID_SESSION_ID = "20260823_081435_223491a5"
DEFAULT_SESSION_IDS = (*CENTRE_SESSION_IDS, GRID_SESSION_ID)
DEFAULT_SEEDS = (1, 7, 21)
HIDDEN_LAYERS = (384, 192, 96)


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


def _parameter_count(input_count: int) -> int:
    sizes = (input_count, *HIDDEN_LAYERS, 2)
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


def run(sessions_root: Path, output_dir: Path, seeds: tuple[int, ...] = DEFAULT_SEEDS) -> dict:
    if not seeds or len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be non-empty and unique")
    dataset = load_position_dataset(sessions_root)
    features = np.stack([extract_grid_features(row) for row in dataset.samples])
    warnings.filterwarnings("ignore", category=ConvergenceWarning)

    # The grid exists in one acquisition session.  Holding out complete
    # repetition numbers keeps every coordinate represented in both sides
    # without letting repeated strikes of a target cross the fold boundary.
    grid = dataset.session_ids == GRID_SESSION_ID
    centre = ~grid
    grid_indices = np.flatnonzero(grid)
    grid_prediction = np.empty((int(grid.sum()), 2), dtype=np.float64)
    grid_folds = []
    splitter = GroupKFold(n_splits=5)
    for fold, (grid_train_local, grid_test_local) in enumerate(
        splitter.split(grid_indices, groups=dataset.repetitions[grid]), start=1
    ):
        train = centre.copy()
        train[grid_indices[grid_train_local]] = True
        test_indices = grid_indices[grid_test_local]
        test = np.zeros(len(dataset.samples), dtype=bool)
        test[test_indices] = True
        predicted, iterations = _fit_predict(features, dataset.xy_mm, train, test, seeds[0])
        grid_prediction[grid_test_local] = predicted
        grid_folds.append({
            "fold": fold,
            "held_out_repetitions": sorted(set(dataset.repetitions[test].astype(int).tolist())),
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "iterations": iterations,
            **regression_metrics(dataset.xy_mm[test], predicted),
        })
    grid_metrics = regression_metrics(dataset.xy_mm[grid], grid_prediction)

    centre_prediction = np.empty((int(centre.sum()), 2), dtype=np.float64)
    centre_indices = np.flatnonzero(centre)
    centre_folds = []
    for session_id in CENTRE_SESSION_IDS:
        test = dataset.session_ids == session_id
        train = ~test
        predicted, iterations = _fit_predict(features, dataset.xy_mm, train, test, seeds[0])
        centre_prediction[np.searchsorted(centre_indices, np.flatnonzero(test))] = predicted
        centre_folds.append({
            "test_session": session_id,
            "train_count": int(train.sum()),
            "test_count": int(test.sum()),
            "iterations": iterations,
            **regression_metrics(dataset.xy_mm[test], predicted),
        })
    centre_metrics = regression_metrics(dataset.xy_mm[centre], centre_prediction)
    uncertainty = _uncertainty(dataset.xy_mm[grid], grid_prediction)

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

    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "position_ensemble.joblib"
    scope = (
        "400×300×5 mmの12中心点と、50 mm格子相当の四隅48点で学習したPC専用XYモデルです。"
        "四隅評価は同一収録セッション内で反復番号を分離しており、別日セッションへの汎化は未検証です。"
    )
    bundle = {
        "scaler": scaler,
        "models": models,
        "contract": {
            "panel_profile_id": PANEL_ID,
            "sample_rate_hz": SAMPLE_RATE_HZ,
            "sample_count": SAMPLE_COUNT,
            "trigger_index": TRIGGER_INDEX,
            "feature_mode": FEATURE_MODE,
            "panel_width_mm": PANEL_WIDTH_MM,
            "panel_height_mm": PANEL_HEIGHT_MM,
        },
        "validation": grid_metrics,
        "uncertainty": uncertainty,
        "method": "pc_large_mlp_xy_grid_calibrated_gaussian",
        "scope": scope,
    }
    joblib.dump(bundle, bundle_path, compress=3)
    report = {
        "experiment": "pc_position_400x300x5_grid_v1",
        "dataset_sha256": dataset.dataset_sha256,
        "session_ids": list(DEFAULT_SESSION_IDS),
        "sample_count": int(len(dataset.samples)),
        "centre_sample_count": int(centre.sum()),
        "grid_sample_count": int(grid.sum()),
        "unique_coordinates": int(len(np.unique(dataset.xy_mm, axis=0))),
        "feature_count": int(features.shape[1]),
        "architecture": [int(features.shape[1]), *HIDDEN_LAYERS, 2],
        "trainable_parameters_per_model": _parameter_count(features.shape[1]),
        "runtime_seeds": list(seeds),
        "final_iterations": final_iterations,
        "grid_grouped_validation": grid_metrics,
        "grid_folds": grid_folds,
        "centre_loso_validation": centre_metrics,
        "centre_folds": centre_folds,
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
        grid_expected_xy_mm=dataset.xy_mm[grid],
        grid_predicted_xy_mm=grid_prediction,
        centre_expected_xy_mm=dataset.xy_mm[centre],
        centre_predicted_xy_mm=centre_prediction,
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
    args = parser.parse_args()
    report = run(args.sessions, args.output_dir, tuple(args.seeds))
    grid = report["grid_grouped_validation"]
    centre = report["centre_loso_validation"]
    print(f"samples={report['sample_count']}; unique_coordinates={report['unique_coordinates']}")
    print(f"grid grouped mean distance={grid['mean_distance_mm']:.2f} mm")
    print(f"centre LOSO mean distance={centre['mean_distance_mm']:.2f} mm")
    print(f"model={report['model']}")


if __name__ == "__main__":
    main()
