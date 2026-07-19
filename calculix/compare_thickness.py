"""Compare 3 mm and 5 mm high-frequency CalculiX hit separability."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


def load(path: Path) -> dict:
    return json.loads((path / "highfreq-results.json").read_text(encoding="utf-8"))


def by_cutoff(result: dict, key: str) -> dict[int, dict]:
    return {int(item["highpass_hz"]): item for item in result[key]}


def best_score(result: dict) -> dict:
    return max(result["highpass_scores"], key=lambda item: item["minimum_cosine_distance"])


def ratio(after: float, before: float) -> float:
    return float(after / before) if before else float("nan")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--three-mm", type=Path, required=True)
    parser.add_argument("--five-mm", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    results = {3: load(args.three_mm), 5: load(args.five_mm)}
    for thickness, result in results.items():
        actual = float(result.get("thickness_mm", -1))
        if abs(actual - thickness) > 1e-9:
            raise RuntimeError(f"Expected {thickness} mm input, got {actual} mm")

    common_cutoffs = sorted(
        set(by_cutoff(results[3], "highpass_scores"))
        & set(by_cutoff(results[5], "highpass_scores"))
    )
    colors = {3: "#2563eb", 5: "#e05a33"}

    fig, axes = plt.subplots(2, 2, figsize=(11, 8), constrained_layout=True)
    for thickness, result in results.items():
        scores = by_cutoff(result, "highpass_scores")
        band_scores = by_cutoff(result, "e4_baseline_18_band_scores")
        x = common_cutoffs
        axes[0, 0].plot(x, [scores[hp]["minimum_cosine_distance"] for hp in x], "o-", color=colors[thickness], label=f"{thickness} mm")
        axes[0, 1].plot(x, [scores[hp]["mean_cosine_distance"] for hp in x], "o-", color=colors[thickness], label=f"{thickness} mm")
        axes[1, 0].plot(x, [band_scores[hp]["level_and_shape"]["minimum_rms_log10_distance"] for hp in x], "s-", color=colors[thickness], label=f"{thickness} mm")
        axes[1, 1].plot(x, [band_scores[hp]["shape_only"]["minimum_rms_log10_distance"] for hp in x], "s-", color=colors[thickness], label=f"{thickness} mm")
    labels = [
        (axes[0, 0], "Worst-pair FFT-profile separation", "minimum cosine distance"),
        (axes[0, 1], "Mean FFT-profile separation", "mean cosine distance"),
        (axes[1, 0], "Worst-pair 18-band separation: level + shape", "RMS log10 distance"),
        (axes[1, 1], "Worst-pair 18-band separation: shape only", "RMS log10 distance"),
    ]
    for ax, title, ylabel in labels:
        ax.set(title=title, xlabel="high-pass cutoff [Hz]", ylabel=ylabel)
        ax.grid(alpha=0.25)
        ax.legend()
    fig.suptitle("CalculiX 3 mm vs 5 mm - eight-hit separability at 25.6 kHz / 50 ms")
    fig.savefig(args.output / "thickness-separability-comparison.svg", format="svg", metadata={"Date": None})
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 5), constrained_layout=True)
    for thickness, result in results.items():
        frequencies = np.asarray(result["frequencies_hz"], dtype=float)
        count = min(80, len(frequencies))
        ax.plot(np.arange(1, count + 1), frequencies[:count], "o-", ms=3, lw=1.2, color=colors[thickness], label=f"{thickness} mm")
    ax.set(title="Calculated eigenfrequency shift with plate thickness", xlabel="mode order", ylabel="frequency [Hz]")
    ax.grid(alpha=0.25)
    ax.legend()
    fig.savefig(args.output / "thickness-eigenfrequency-comparison.svg", format="svg", metadata={"Date": None})
    plt.close(fig)

    fixed_hp = 100
    fixed = {}
    for thickness, result in results.items():
        score = by_cutoff(result, "highpass_scores")[fixed_hp]
        band = by_cutoff(result, "e4_baseline_18_band_scores")[fixed_hp]
        fixed[str(thickness)] = {
            "minimum_cosine_distance": score["minimum_cosine_distance"],
            "mean_cosine_distance": score["mean_cosine_distance"],
            "minimum_18band_level_and_shape_distance": band["level_and_shape"]["minimum_rms_log10_distance"],
            "minimum_18band_shape_only_distance": band["shape_only"]["minimum_rms_log10_distance"],
            "closest_18band_level_and_shape_pair": band["level_and_shape"]["closest_pair"],
            "closest_18band_shape_only_pair": band["shape_only"]["closest_pair"],
        }
    best = {str(thickness): best_score(result) for thickness, result in results.items()}
    first_modes = min(20, len(results[3]["frequencies_hz"]), len(results[5]["frequencies_hz"]))
    frequency_ratios = np.asarray(results[5]["frequencies_hz"][:first_modes]) / np.asarray(results[3]["frequencies_hz"][:first_modes])
    summary = {
        "solver": "CalculiX CrunchiX 2.20",
        "comparison": "3 mm production plate versus 5 mm candidate",
        "controlled_conditions": ["400 x 200 mm PMMA", "C3D20R", "same in-plane mesh", "same fixed volume in x-y", "modal damping ratio 1.2%", "25.6 kHz", "50 ms", "eight unit point impulses"],
        "fixed_highpass_hz": fixed_hp,
        "fixed_highpass_metrics": fixed,
        "five_over_three_ratios_at_100hz": {
            key: ratio(fixed["5"][key], fixed["3"][key])
            for key in ("minimum_cosine_distance", "mean_cosine_distance", "minimum_18band_level_and_shape_distance", "minimum_18band_shape_only_distance")
        },
        "best_fft_profile_cutoff": best,
        "computed_modes": {str(t): results[t]["computed_modes"] for t in (3, 5)},
        "maximum_computed_hz": {str(t): results[t]["maximum_computed_hz"] for t in (3, 5)},
        "first_mode_hz": {str(t): results[t]["frequencies_hz"][0] for t in (3, 5)},
        "five_over_three_eigenfrequency_ratio_first_20": {
            "mean": float(frequency_ratios.mean()),
            "minimum": float(frequency_ratios.min()),
            "maximum": float(frequency_ratios.max()),
        },
        "source_results": {
            "3_mm": "../calculix-highfreq/highfreq-results.json",
            "5_mm": "5mm-highfreq-results.json",
        },
    }
    shutil.copy2(
        args.five_mm / "highfreq-results.json",
        args.output / "5mm-highfreq-results.json",
    )
    shutil.copy2(
        args.five_mm / "highfreq-metadata.json",
        args.output / "5mm-highfreq-metadata.json",
    )
    (args.output / "thickness-comparison.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
