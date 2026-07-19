"""Evaluate XY regression from centre-only versus centre-plus-corner FEM data."""

from __future__ import annotations

import argparse
import json
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal
from sklearn.exceptions import ConvergenceWarning
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import StandardScaler

from postprocess import dynamic_displacement, frequencies_from_dat


FEATURE_COUNT = 128
HIDDEN_COUNT = 128
MODEL_SEEDS = tuple(range(1, 21))
MLP_SEEDS = tuple(range(1, 11))
RIDGE = 0.1


def shift_with_zeros(values: np.ndarray, shift: int) -> np.ndarray:
    result = np.zeros_like(values)
    if shift > 0:
        result[shift:] = values[:-shift]
    elif shift < 0:
        result[:shift] = values[-shift:]
    else:
        result[:] = values
    return result


def feature_vector(waveform: np.ndarray, sample_rate: float) -> np.ndarray:
    peak = max(float(np.max(np.abs(waveform))), 1e-20)
    normalized = waveform / peak
    indices = np.rint(np.linspace(0, len(normalized) - 1, FEATURE_COUNT // 2)).astype(int)
    time_features = normalized[indices]
    frequencies = np.fft.rfftfreq(len(normalized), 1.0 / sample_rate)
    spectrum = np.abs(np.fft.rfft(normalized * np.hanning(len(normalized))))
    edges = np.linspace(100.0, 5000.0, FEATURE_COUNT // 2 + 1)
    bands = []
    for left, right in zip(edges[:-1], edges[1:]):
        use = (frequencies >= left) & (frequencies < right)
        bands.append(np.sqrt(np.mean(spectrum[use] ** 2)) if np.any(use) else 0.0)
    bands = np.asarray(bands)
    bands /= max(float(np.linalg.norm(bands)), 1e-20)
    return np.concatenate((time_features, np.log10(np.maximum(bands, 1e-12))))


def augmented_dataset(points: list[dict], responses: dict[str, np.ndarray], count: int,
                      sample_rate: float, seed: int) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    features, targets = [], []
    time = np.arange(len(next(iter(responses.values())))) / sample_rate
    for point in points:
        source = responses[point["id"]]
        rms = max(float(np.sqrt(np.mean(source ** 2))), 1e-20)
        for _ in range(count):
            shifted = shift_with_zeros(source, int(rng.integers(-2, 3)))
            damping_delta = float(rng.uniform(-3.0, 3.0))
            varied = shifted * np.exp(-damping_delta * time)
            varied *= float(rng.lognormal(mean=0.0, sigma=0.18))
            varied += rng.normal(scale=rms * float(rng.uniform(0.002, 0.02)), size=len(source))
            features.append(feature_vector(varied, sample_rate))
            targets.append((point["x_mm"] / 400.0, point["y_mm"] / 200.0))
    return np.asarray(features), np.asarray(targets)


def regression_metrics(expected: np.ndarray, predicted: np.ndarray) -> dict[str, float]:
    error = predicted - expected
    distance = np.linalg.norm(error, axis=1)
    return {
        "mae_x_mm": float(np.mean(np.abs(error[:, 0]))),
        "mae_y_mm": float(np.mean(np.abs(error[:, 1]))),
        "mean_distance_mm": float(np.mean(distance)),
        "median_distance_mm": float(np.median(distance)),
        "p90_distance_mm": float(np.percentile(distance, 90)),
        "within_25mm": float(np.mean(distance <= 25.0)),
        "within_50mm": float(np.mean(distance <= 50.0)),
    }


def predict_elm(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray,
                seed: int) -> np.ndarray:
    mean = train_x.mean(axis=0)
    scale = train_x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    x = (train_x - mean) / scale
    test = (test_x - mean) / scale
    rng = np.random.default_rng(seed)
    alpha = rng.normal(scale=1.0 / np.sqrt(x.shape[1]), size=(x.shape[1], HIDDEN_COUNT))
    hidden = np.clip(0.2 * (x @ alpha) + 0.5, 0.0, 1.0)
    test_hidden = np.clip(0.2 * (test @ alpha) + 0.5, 0.0, 1.0)
    beta = np.linalg.solve(
        hidden.T @ hidden + RIDGE * np.eye(HIDDEN_COUNT), hidden.T @ train_y
    )
    normalized = np.clip(test_hidden @ beta, (0.0, 0.0), (1.0, 1.0))
    return normalized * np.asarray((400.0, 200.0))


def summarize_predictions(seed_values: tuple[int, ...], predicted_values: list[np.ndarray],
                          expected_mm: np.ndarray) -> tuple[dict, np.ndarray]:
    seed_metrics = [regression_metrics(expected_mm, predicted) for predicted in predicted_values]
    keys = seed_metrics[0]
    summary = {
        "model_seeds": len(seed_values),
        "metrics_mean": {key: float(np.mean([item[key] for item in seed_metrics])) for key in keys},
        "metrics_std": {key: float(np.std([item[key] for item in seed_metrics])) for key in keys},
        "per_seed": [{"seed": seed, **metrics} for seed, metrics in zip(seed_values, seed_metrics)],
    }
    return summary, np.mean(predicted_values, axis=0)


def evaluate_elm(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray,
                 expected_mm: np.ndarray) -> tuple[dict, np.ndarray]:
    predictions = []
    for seed in MODEL_SEEDS:
        predictions.append(predict_elm(train_x, train_y, test_x, seed))
    return summarize_predictions(MODEL_SEEDS, predictions, expected_mm)


def evaluate_mlp(train_x: np.ndarray, train_y: np.ndarray, test_x: np.ndarray,
                 expected_mm: np.ndarray) -> tuple[dict, np.ndarray]:
    scaler = StandardScaler().fit(train_x)
    x = scaler.transform(train_x)
    test = scaler.transform(test_x)
    predictions = []
    for seed in MLP_SEEDS:
        model = MLPRegressor(
            hidden_layer_sizes=(128, 64), activation="tanh", solver="adam",
            alpha=1e-3, batch_size=64, learning_rate_init=1e-3,
            max_iter=800, early_stopping=True, validation_fraction=0.15,
            n_iter_no_change=40, random_state=seed,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            model.fit(x, train_y)
        normalized = np.clip(model.predict(test), (0.0, 0.0), (1.0, 1.0))
        predictions.append(normalized * np.asarray((400.0, 200.0)))
    return summarize_predictions(MLP_SEEDS, predictions, expected_mm)


def draw_layout(path: Path, points: list[dict]) -> None:
    colors = {"center": "#f5b942", "corner": "#0d7f78", "probe": "#60a5fa"}
    markers = {"center": "o", "corner": ".", "probe": "s"}
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    ax.add_patch(plt.Rectangle((200, 0), 100, 20, color="#e2633b", alpha=0.4))
    for group, label in (("center", "area centres: train"),
                         ("corner", "50 mm corners: additional train"),
                         ("probe", "cell boundaries: held-out test")):
        selected = [point for point in points if point["group"] == group]
        ax.scatter([point["x_mm"] for point in selected], [point["y_mm"] for point in selected],
                   c=colors[group], marker=markers[group], s=55, edgecolor="#111827",
                   linewidth=0.6, label=label)
    ax.scatter([200], [100], marker="D", s=65, c="#111827", label="sensor")
    ax.set(xlim=(0, 400), ylim=(200, 0), aspect="equal", xlabel="x [mm]", ylabel="y [mm]",
           title="CalculiX XY-regression train/test positions")
    ax.grid(alpha=0.2); ax.legend(loc="lower right")
    fig.savefig(path, format="svg", metadata={"Date": None}); plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads((args.output / "xy-grid-metadata.json").read_text(encoding="utf-8"))
    sample_rate = float(metadata["sampling"]["hz"])
    expected_samples = int(metadata["sampling"]["samples"])
    sensor_node = int(metadata["sensor"]["node"])
    points = metadata["points"]
    frequencies = frequencies_from_dat(args.output / "acrylic_pan_xy.dat")

    raw = {}
    for point in points:
        times, displacement = dynamic_displacement(args.output / f"xy_{point['id']}.dat", sensor_node)
        if len(times) != expected_samples:
            raise RuntimeError(f"{point['id']} has {len(times)} samples; expected {expected_samples}")
        acceleration = np.gradient(np.gradient(displacement, times), times)
        sos = signal.butter(4, [100.0, 5000.0], btype="bandpass", fs=sample_rate, output="sos")
        raw[point["id"]] = signal.sosfiltfilt(sos, acceleration)

    centres = [point for point in points if point["group"] == "center"]
    corners = [point for point in points if point["group"] == "corner"]
    probes = [point for point in points if point["group"] == "probe"]
    test_x, test_y = augmented_dataset(probes, raw, 80, sample_rate, seed=90_001)
    expected_mm = test_y * np.asarray((400.0, 200.0))
    condition_specs = {
        "center_only_standard": (centres, 32),
        "center_only_count_matched": (centres, 160),
        "center_plus_corners": (centres + corners, 32),
    }
    summaries, average_predictions = {}, {}
    for index, (name, (training_points, variants)) in enumerate(condition_specs.items()):
        train_x, train_y = augmented_dataset(
            training_points, raw, variants, sample_rate, seed=10_000 + index
        )
        mlp_summary, mlp_predicted = evaluate_mlp(train_x, train_y, test_x, expected_mm)
        elm_summary, elm_predicted = evaluate_elm(train_x, train_y, test_x, expected_mm)
        summaries[name] = {
            "unique_training_positions": len(training_points),
            "variants_per_position": variants,
            "training_samples": len(train_x),
            "trainable_mlp": mlp_summary,
            "solist_style_elm": elm_summary,
        }
        average_predictions[name] = {"trainable_mlp": mlp_predicted,
                                     "solist_style_elm": elm_predicted}

    improvements = {}
    for model_name in ("trainable_mlp", "solist_style_elm"):
        baseline = summaries["center_only_count_matched"][model_name]["metrics_mean"]
        expanded = summaries["center_plus_corners"][model_name]["metrics_mean"]
        improvements[model_name] = {
            key: float((baseline[key] - expanded[key]) / baseline[key])
            for key in ("mae_x_mm", "mae_y_mm", "mean_distance_mm", "median_distance_mm",
                        "p90_distance_mm")
        }

    fig, axes = plt.subplots(1, 2, figsize=(11, 5), constrained_layout=True)
    names = ["center_only_standard", "center_only_count_matched", "center_plus_corners"]
    labels = ["centres\n256 samples", "centres\n1,280 samples", "centres + corners\n1,280 samples"]
    medians = [summaries[name]["trainable_mlp"]["metrics_mean"]["median_distance_mm"] for name in names]
    p90 = [summaries[name]["trainable_mlp"]["metrics_mean"]["p90_distance_mm"] for name in names]
    axes[0].bar(np.arange(3) - 0.18, medians, 0.36, label="median")
    axes[0].bar(np.arange(3) + 0.18, p90, 0.36, label="90th percentile")
    axes[0].set_xticks(range(3), labels); axes[0].set_ylabel("position error [mm]")
    axes[0].set_title("Held-out boundary probes, 10 trainable-MLP seeds"); axes[0].grid(axis="y", alpha=0.25); axes[0].legend()
    probe_xy = np.asarray([(point["x_mm"], point["y_mm"]) for point in probes])
    axes[1].scatter(probe_xy[:, 0], probe_xy[:, 1], marker="s", c="#111827", label="true probe")
    test_repetitions = 80
    for name, color, label in (("center_only_count_matched", "#f5b942", "centres only"),
                               ("center_plus_corners", "#0d7f78", "centres + corners")):
        predicted = average_predictions[name]["trainable_mlp"].reshape(
            len(probes), test_repetitions, 2
        ).mean(axis=1)
        axes[1].scatter(predicted[:, 0], predicted[:, 1], c=color, label=label)
        for true, estimate in zip(probe_xy, predicted):
            axes[1].plot([true[0], estimate[0]], [true[1], estimate[1]], color=color, alpha=0.45)
    axes[1].set(xlim=(0, 400), ylim=(200, 0), aspect="equal", xlabel="x [mm]", ylabel="y [mm]",
                title="Mean predicted coordinates"); axes[1].grid(alpha=0.2); axes[1].legend()
    fig.savefig(args.output / "xy-regression-comparison.svg", format="svg", metadata={"Date": None})
    plt.close(fig)
    draw_layout(args.output / "xy-training-layout.svg", points)

    ordered_ids = [point["id"] for point in points]
    np.savez_compressed(args.output / "xy-grid-responses.npz", point_ids=np.asarray(ordered_ids),
                        responses=np.stack([raw[point_id] for point_id in ordered_ids]),
                        sample_rate_hz=sample_rate)
    result = {
        **metadata,
        "computed_modes": len(frequencies),
        "maximum_computed_hz": frequencies[-1] if frequencies else None,
        "evaluation": {
            "models": ["trainable MLP 128-128-64-2", "Solist-style 128-128-2 ELM"],
            "features": "64 peak-normalized time samples + 64 normalized log FFT bands, 100-5000 Hz",
            "augmentation": "time shift +/-2 samples, gain, damping perturbation, 0.2-2% RMS noise",
            "test": "10 held-out cell-boundary probes x 80 variations",
            "conditions": summaries,
            "count_matched_center_plus_corners_improvement": improvements,
        },
    }
    (args.output / "xy-grid-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"modes": len(frequencies), "conditions": {
        name: summary["trainable_mlp"]["metrics_mean"] for name, summary in summaries.items()
    }, "improvement": improvements}))


if __name__ == "__main__":
    main()
