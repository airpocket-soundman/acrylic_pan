"""Build Acrylic Pan datasets and official Solist-AI Simulator CSV files.

Each CSV row is ``features + targets``.  Eight-class classification uses
one-hot targets.  The same exporter accepts numeric target matrices such as
normalized x/y/strength for a future regression model.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import re

import numpy as np

from .solist_elm import SolistELM, accuracy

DEFAULT_CLASS_COUNT = 8
DEFAULT_FEATURE_COUNT = 128
DEFAULT_MAX_CELLS = 1_000_000
LABEL_PATTERN = re.compile(r"(?:class|label|area)[_-]?(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class FeatureScaler:
    mean: np.ndarray
    scale: np.ndarray

    @classmethod
    def fit(cls, features: np.ndarray) -> "FeatureScaler":
        mean = features.mean(axis=0)
        std = features.std(axis=0)
        return cls(mean, np.where(std < 1e-8, 1.0, std))

    def transform(self, features: np.ndarray) -> np.ndarray:
        return ((features - self.mean) / self.scale).astype(np.float32)


@dataclass(frozen=True)
class SessionDataset:
    """Labelled events with acquisition-session groups kept for safe splitting."""

    features: np.ndarray
    labels: np.ndarray
    session_ids: np.ndarray
    event_paths: tuple[Path, ...]


@dataclass(frozen=True)
class GuidedRunSummary:
    session_id: str
    point_count: int
    repetitions: int
    event_count: int


def extract_fft_features(samples: np.ndarray, feature_count: int = DEFAULT_FEATURE_COUNT) -> np.ndarray:
    """Return log-magnitude FFT features, excluding the DC bin."""
    waveform = np.asarray(samples, dtype=np.float64)
    if waveform.ndim != 1 or waveform.size < 2 * (feature_count + 1):
        raise ValueError(f"waveform needs at least {2 * (feature_count + 1)} samples")
    centered = waveform - waveform.mean()
    magnitude = np.abs(np.fft.rfft(centered * np.hanning(waveform.size)))[1 : feature_count + 1]
    return np.log1p(magnitude).astype(np.float32)


def _scalar(npz: np.lib.npyio.NpzFile, names: tuple[str, ...]) -> int | None:
    for name in names:
        if name in npz:
            value = np.asarray(npz[name])
            if value.size != 1:
                raise ValueError(f"{name} must be scalar")
            return int(value.reshape(-1)[0])
    return None


def _label_from_path(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = LABEL_PATTERN.search(part)
        if match:
            return int(match.group(1))
    return None


def load_npz_events(
    source: Path,
    feature_count: int = DEFAULT_FEATURE_COUNT,
    class_count: int = DEFAULT_CLASS_COUNT,
) -> tuple[np.ndarray, np.ndarray]:
    """Load monitor NPZ events carrying ``class_id``/``label`` metadata.

    A label may alternatively be encoded in a parent directory or filename as
    ``class_0`` through ``class_7``.  Unlabelled events are rejected.
    """
    paths = sorted(source.rglob("*.npz")) if source.is_dir() else [source]
    if not paths or not all(path.exists() for path in paths):
        raise FileNotFoundError(source)
    features, labels = [], []
    for path in paths:
        with np.load(path, allow_pickle=False) as event:
            if "samples" not in event:
                raise ValueError(f"{path}: missing samples")
            label = _scalar(event, ("class_id", "label", "area"))
            if label is None:
                label = _label_from_path(path)
            if label is None:
                raise ValueError(f"{path}: missing class_id/label metadata")
            if not 0 <= label < class_count:
                raise ValueError(f"{path}: label {label} is outside 0..{class_count - 1}")
            features.append(extract_fft_features(event["samples"], feature_count))
            labels.append(label)
    return np.stack(features), np.asarray(labels, dtype=np.int64)


def load_recorded_sessions(
    source: Path,
    feature_count: int = DEFAULT_FEATURE_COUNT,
    class_count: int = DEFAULT_CLASS_COUNT,
    *,
    require_all_classes: bool = False,
) -> SessionDataset:
    """Load Recorder v1 sessions and validate manifest/NPZ label consistency.

    ``manifest.jsonl`` is the event index and the NPZ is the numeric payload.
    Unlabelled captures are rejected so they cannot silently enter training.
    Session IDs are returned for leakage-free train/test splitting.
    """
    source = Path(source)
    candidates = [source] if (source / "session.json").is_file() else sorted(
        path.parent for path in source.rglob("session.json")
    )
    if not candidates:
        raise FileNotFoundError(f"no Recorder sessions under {source}")

    features: list[np.ndarray] = []
    labels: list[int] = []
    session_ids: list[str] = []
    event_paths: list[Path] = []
    seen_session_ids: set[str] = set()
    for session_dir in candidates:
        metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        if metadata.get("format") != "acrylic-pan-session-v1":
            raise ValueError(f"{session_dir}: unsupported session format")
        session_id = str(metadata.get("session_id", ""))
        if not session_id or session_id in seen_session_ids:
            raise ValueError(f"{session_dir}: missing or duplicate session_id")
        seen_session_ids.add(session_id)
        manifest = session_dir / "manifest.jsonl"
        if not manifest.is_file():
            raise ValueError(f"{session_dir}: missing manifest.jsonl")
        rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line]
        if int(metadata.get("event_count", -1)) != len(rows):
            raise ValueError(f"{session_dir}: session event_count does not match manifest")
        for row in rows:
            label = row.get("class_id")
            if not isinstance(label, int) or isinstance(label, bool) or not 0 <= label < class_count:
                raise ValueError(f"{session_dir}: event has missing/invalid class_id")
            relative = Path(str(row.get("file", "")))
            event_path = (session_dir / relative).resolve()
            try:
                event_path.relative_to(session_dir.resolve())
            except ValueError as error:
                raise ValueError(f"{session_dir}: event path escapes session") from error
            if not event_path.is_file():
                raise ValueError(f"{session_dir}: missing event file {relative}")
            with np.load(event_path, allow_pickle=False) as event:
                npz_label = _scalar(event, ("class_id",))
                if npz_label != label:
                    raise ValueError(f"{event_path}: manifest/NPZ class_id mismatch")
                if int(np.asarray(event["sample_rate_hz"]).reshape(-1)[0]) != int(row["sample_rate_hz"]):
                    raise ValueError(f"{event_path}: manifest/NPZ sample_rate_hz mismatch")
                features.append(extract_fft_features(event["samples"], feature_count))
            labels.append(label)
            session_ids.append(session_id)
            event_paths.append(event_path)
    if not features:
        raise ValueError("recorded sessions contain no events")
    label_array = np.asarray(labels, dtype=np.int64)
    if require_all_classes and set(label_array.tolist()) != set(range(class_count)):
        raise ValueError(f"dataset must contain every class 0..{class_count - 1}")
    return SessionDataset(
        np.stack(features), label_array, np.asarray(session_ids), tuple(event_paths)
    )


def validate_guided_collection(
    source: Path,
    *,
    class_count: int = DEFAULT_CLASS_COUNT,
    point_count: int | None = None,
    repetitions: int | None = None,
) -> tuple[GuidedRunSummary, ...]:
    """Validate complete guided acquisition runs.

    ``point_count`` is 1 for the area-centre series or 4 for the 50 mm grid
    series of docs/design.md section 3.
    """
    if point_count is not None and point_count not in (1, 4):
        raise ValueError("point_count must be 1 or 4")
    source = Path(source)
    session_dirs = [source] if (source / "session.json").is_file() else sorted(
        path.parent for path in source.rglob("session.json")
    )
    if not session_dirs:
        raise FileNotFoundError(f"no Recorder sessions under {source}")
    summaries: list[GuidedRunSummary] = []
    required = (
        "target_class_id", "target_point_id", "target_point_name",
        "target_x_mm", "target_y_mm", "offset_x_mm", "offset_y_mm", "repetition",
    )
    for session_dir in session_dirs:
        metadata = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        session_id = str(metadata.get("session_id", ""))
        rows = [json.loads(line) for line in
                (session_dir / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line]
        entries: dict[tuple[int, int, int], tuple[str, float, float, float, float]] = {}
        # Keyed per area: docs/design.md moves the two grid points under the
        # clamp, so one point ID legitimately has different offsets per area.
        point_definitions: dict[tuple[int, int], tuple[str, float, float]] = {}
        point_names: dict[int, str] = {}
        class_centers: dict[int, tuple[float, float]] = {}
        for row in rows:
            annotations = row.get("annotations")
            if not isinstance(annotations, dict) or any(name not in annotations for name in required):
                raise ValueError(f"{session_dir}: guided event is missing position annotations")
            target_class = annotations["target_class_id"]
            target_point = annotations["target_point_id"]
            repetition = annotations["repetition"]
            if any(isinstance(value, bool) or not isinstance(value, int)
                   for value in (target_class, target_point, repetition)):
                raise ValueError(f"{session_dir}: class, point and repetition must be integers")
            if not 0 <= target_class < class_count or row.get("class_id") != target_class:
                raise ValueError(f"{session_dir}: target_class_id does not match class_id")
            if target_point < 0 or repetition < 1:
                raise ValueError(f"{session_dir}: invalid point or repetition")
            name = annotations["target_point_name"]
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"{session_dir}: target_point_name must be non-empty")
            try:
                x, y, dx, dy = (float(annotations[key]) for key in
                                ("target_x_mm", "target_y_mm", "offset_x_mm", "offset_y_mm"))
            except (TypeError, ValueError) as error:
                raise ValueError(f"{session_dir}: position values must be numeric") from error
            if not np.isfinite((x, y, dx, dy)).all():
                raise ValueError(f"{session_dir}: position values must be finite")
            key = (target_class, target_point, repetition)
            if key in entries:
                raise ValueError(f"{session_dir}: duplicate class/point/repetition")
            entries[key] = (name, x, y, dx, dy)
            definition = (name, dx, dy)
            if point_definitions.setdefault((target_class, target_point), definition) != definition:
                raise ValueError(f"{session_dir}: point definition changes within run")
            if point_names.setdefault(target_point, name) != name:
                raise ValueError(f"{session_dir}: point name changes within run")
            center = (x - dx, y - dy)
            old_center = class_centers.setdefault(target_class, center)
            if not np.allclose(center, old_center, rtol=0, atol=1e-6):
                raise ValueError(f"{session_dir}: inconsistent area center coordinates")

        expected_points = point_count or len(point_names)
        if expected_points not in (1, 4) or set(point_names) != set(range(expected_points)):
            raise ValueError(f"{session_dir}: point IDs must completely cover 0..{expected_points - 1}")
        expected_repetitions = repetitions or max((key[2] for key in entries), default=0)
        if expected_repetitions < 1:
            raise ValueError(f"{session_dir}: no guided events")
        expected_keys = {
            (class_id, point_id, repeat)
            for class_id in range(class_count)
            for point_id in range(expected_points)
            for repeat in range(1, expected_repetitions + 1)
        }
        if set(entries) != expected_keys:
            missing = len(expected_keys - set(entries))
            extra = len(set(entries) - expected_keys)
            raise ValueError(f"{session_dir}: incomplete guided run (missing={missing}, extra={extra})")
        summaries.append(GuidedRunSummary(
            session_id, expected_points, expected_repetitions, len(entries)
        ))
    return tuple(summaries)


def split_dataset_by_session(
    dataset: SessionDataset, test_fraction: float, seed: int,
    class_count: int = DEFAULT_CLASS_COUNT,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Split complete multi-class acquisition runs without event leakage."""
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between zero and one")
    rng = np.random.default_rng(seed)
    all_labels = set(np.unique(dataset.labels).tolist())
    required_labels = set(range(class_count))
    if all_labels != required_labels:
        raise ValueError(f"dataset must contain every class 0..{class_count - 1}")
    sessions = np.unique(dataset.session_ids)
    if len(sessions) < 2:
        raise ValueError("dataset needs at least two complete acquisition sessions")
    for session_id in sessions:
        session_labels = set(dataset.labels[dataset.session_ids == session_id].tolist())
        if session_labels != all_labels:
            raise ValueError(f"session {session_id} does not contain every dataset class")
    shuffled = rng.permutation(sessions)
    test_count = max(1, int(round(len(shuffled) * test_fraction)))
    test_count = min(test_count, len(shuffled) - 1)
    test_sessions = {str(value) for value in shuffled[:test_count]}
    train_sessions = {str(value) for value in shuffled[test_count:]}
    train_mask = np.isin(dataset.session_ids, list(train_sessions))
    test_mask = np.isin(dataset.session_ids, list(test_sessions))
    if np.any(train_mask & test_mask) or not np.all(train_mask | test_mask):
        raise RuntimeError("invalid session split")
    if set(dataset.labels[train_mask].tolist()) != all_labels or set(dataset.labels[test_mask].tolist()) != all_labels:
        raise RuntimeError("train and test must both contain every class")
    return (
        dataset.features[train_mask], dataset.labels[train_mask],
        dataset.features[test_mask], dataset.labels[test_mask],
    )


def make_synthetic_events(
    samples_per_class: int = 80,
    sample_count: int = 512,
    sample_rate_hz: int = 25_600,
    class_count: int = DEFAULT_CLASS_COUNT,
    feature_count: int = DEFAULT_FEATURE_COUNT,
    seed: int = 20260716,
) -> tuple[np.ndarray, np.ndarray]:
    """Generate deterministic damped-impact waveforms for pipeline tests only."""
    if samples_per_class <= 0 or sample_count < 2 * (feature_count + 1):
        raise ValueError("invalid synthetic dataset size")
    rng = np.random.default_rng(seed)
    time = np.arange(sample_count) / sample_rate_hz
    features, labels = [], []
    for label in range(class_count):
        base_frequency = 450.0 + 260.0 * label
        for _ in range(samples_per_class):
            frequency = base_frequency * rng.uniform(0.97, 1.03)
            phase = rng.uniform(-0.15, 0.15)
            force = rng.uniform(0.65, 1.35)
            signal = force * np.exp(-time * rng.uniform(150.0, 220.0))
            signal *= np.sin(2.0 * np.pi * frequency * time + phase)
            signal += 0.32 * force * np.exp(-time * 90.0) * np.sin(2.0 * np.pi * (frequency * 1.83) * time)
            signal += rng.normal(0.0, 0.025, sample_count)
            features.append(extract_fft_features(signal, feature_count))
            labels.append(label)
    order = rng.permutation(len(labels))
    return np.asarray(features)[order], np.asarray(labels, dtype=np.int64)[order]


def one_hot(labels: np.ndarray, class_count: int = DEFAULT_CLASS_COUNT) -> np.ndarray:
    labels = np.asarray(labels)
    if labels.ndim != 1 or not np.issubdtype(labels.dtype, np.integer):
        raise ValueError("labels must be a 1-D integer array")
    if np.any(labels < 0) or np.any(labels >= class_count):
        raise ValueError("label outside class range")
    return np.eye(class_count, dtype=np.float32)[labels]


def export_solist_csv(
    output: Path,
    features: np.ndarray,
    targets: np.ndarray,
    *,
    max_cells: int = DEFAULT_MAX_CELLS,
    limit_rows: bool = False,
) -> int:
    """Export Simulator CSV and return the number of written data rows.

    Cell accounting conservatively includes the header row.  By default
    oversize data is rejected; ``limit_rows=True`` safely truncates complete
    rows to the maximum that fits.
    """
    features = np.asarray(features)
    targets = np.asarray(targets)
    if features.ndim != 2 or targets.ndim != 2 or len(features) != len(targets):
        raise ValueError("features and targets must be 2-D with equal row counts")
    if len(features) == 0 or not np.isfinite(features).all() or not np.isfinite(targets).all():
        raise ValueError("dataset must be non-empty and finite")
    columns = features.shape[1] + targets.shape[1]
    if 2 * columns > max_cells:
        raise ValueError("header plus one data row exceeds the cell limit")
    row_count = len(features)
    total_cells = (row_count + 1) * columns
    if total_cells > max_cells:
        if not limit_rows:
            raise ValueError(f"CSV would contain {total_cells:,} cells (limit {max_cells:,})")
        row_count = max_cells // columns - 1
    output.parent.mkdir(parents=True, exist_ok=True)
    header = [f"input_{index}" for index in range(features.shape[1])]
    header += [f"target_{index}" for index in range(targets.shape[1])]
    with output.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(header)
        for feature_row, target_row in zip(features[:row_count], targets[:row_count]):
            writer.writerow(np.concatenate((feature_row, target_row)).tolist())
    return row_count


def split_dataset(features: np.ndarray, labels: np.ndarray, test_fraction: float, seed: int):
    if not 0.0 < test_fraction < 1.0:
        raise ValueError("test_fraction must be between zero and one")
    rng = np.random.default_rng(seed)
    train_indices, test_indices = [], []
    for label in np.unique(labels):
        indices = np.flatnonzero(labels == label)
        indices = rng.permutation(indices)
        test_count = max(1, int(round(len(indices) * test_fraction)))
        if test_count >= len(indices):
            raise ValueError("each class needs at least two samples")
        test_indices.extend(indices[:test_count])
        train_indices.extend(indices[test_count:])
    return (features[train_indices], labels[train_indices], features[test_indices], labels[test_indices])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--npz", type=Path, help="NPZ event file or directory; omit for synthetic data")
    parser.add_argument("--output-dir", type=Path, default=Path("data/solist_sim"))
    parser.add_argument("--samples-per-class", type=int, default=80)
    parser.add_argument("--features", type=int, default=DEFAULT_FEATURE_COUNT)
    parser.add_argument("--hidden", type=int, default=64)
    parser.add_argument("--seed", type=int, default=20260716)
    parser.add_argument("--test-fraction", type=float, default=0.25)
    parser.add_argument("--limit-rows", action="store_true", help="truncate CSV instead of rejecting >1M cells")
    args = parser.parse_args()

    if args.npz:
        features, labels = load_npz_events(args.npz, args.features)
        source = "NPZ events"
    else:
        features, labels = make_synthetic_events(
            args.samples_per_class, feature_count=args.features, seed=args.seed
        )
        source = "synthetic feasibility data"
    train_x, train_y, test_x, test_y = split_dataset(features, labels, args.test_fraction, args.seed)
    scaler = FeatureScaler.fit(train_x)
    train_x, test_x = scaler.transform(train_x), scaler.transform(test_x)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    np.savez(args.output_dir / "feature_scaler.npz", mean=scaler.mean, scale=scaler.scale)
    train_rows = export_solist_csv(
        args.output_dir / "train_8class.csv", train_x, one_hot(train_y), limit_rows=args.limit_rows
    )
    test_rows = export_solist_csv(
        args.output_dir / "test_8class.csv", test_x, one_hot(test_y), limit_rows=args.limit_rows
    )
    model = SolistELM(args.hidden, seed=args.seed).fit(train_x, train_y)
    score = accuracy(test_y, model.predict(test_x))
    print(f"source={source}; features={args.features}; train={train_rows}; test={test_rows}")
    print(f"reference ELM accuracy={score:.4f} (official Simulator validation still required)")


if __name__ == "__main__":
    main()
