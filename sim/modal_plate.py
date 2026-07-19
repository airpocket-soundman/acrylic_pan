"""Preliminary modal analysis for the 400 x 200 x 3 mm acrylic panel.

The plate is represented by a finite-difference Kirchhoff plate model.  The
asymmetric clamp sandwiches a 100 x 20 mm area of the acrylic, represented by
constraining every deflection node in x=200..300 mm, y=0..20 mm.  This is a
design-screening model, not a replacement for a converged 3-D contact analysis.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import eigsh
from .sensor_signal import serializable_sensor_data


WIDTH = 0.400
HEIGHT = 0.200
THICKNESS = 0.003
YOUNGS_MODULUS = 3.2e9
POISSON = 0.35
DENSITY = 1180.0
FIXED_X = (0.200, 0.300)
FIXED_Y = (0.000, 0.020)
SENSOR = (0.200, 0.100)
HITS = [(x, y) for y in (0.050, 0.150) for x in (0.050, 0.150, 0.250, 0.350)]
NOTES = ("C4", "D4", "E4", "G4", "A4", "C5", "D5", "E5")


def build_laplacian(nx: int, ny: int, dx: float, dy: float) -> sparse.csr_matrix:
    """Return a 5-point Laplacian with natural/free outside treatment."""
    n = nx * ny
    rows: list[int] = []
    cols: list[int] = []
    vals: list[float] = []
    for j in range(ny):
        for i in range(nx):
            p = j * nx + i
            diagonal = 0.0
            for di, dj, scale in ((-1, 0, dx), (1, 0, dx), (0, -1, dy), (0, 1, dy)):
                ii, jj = i + di, j + dj
                weight = 1.0 / scale**2
                if 0 <= ii < nx and 0 <= jj < ny:
                    rows.append(p)
                    cols.append(jj * nx + ii)
                    vals.append(weight)
                    diagonal -= weight
            rows.append(p)
            cols.append(p)
            vals.append(diagonal)
    return sparse.csr_matrix((vals, (rows, cols)), shape=(n, n))


def nearest_node(x: float, y: float, xs: np.ndarray, ys: np.ndarray) -> int:
    i = int(np.argmin(np.abs(xs - x)))
    j = int(np.argmin(np.abs(ys - y)))
    return j * len(xs) + i


def solve_modes(nx: int = 81, ny: int = 41, count: int = 18):
    xs = np.linspace(0.0, WIDTH, nx)
    ys = np.linspace(0.0, HEIGHT, ny)
    dx, dy = xs[1] - xs[0], ys[1] - ys[0]
    lap = build_laplacian(nx, ny, dx, dy)
    area = dx * dy
    rigidity = YOUNGS_MODULUS * THICKNESS**3 / (12.0 * (1.0 - POISSON**2))
    stiffness = rigidity * area * (lap.T @ lap)
    mass_value = DENSITY * THICKNESS * area

    fixed: set[int] = set()
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            if (
                FIXED_X[0] - dx / 2 <= x <= FIXED_X[1] + dx / 2
                and FIXED_Y[0] - dy / 2 <= y <= FIXED_Y[1] + dy / 2
            ):
                fixed.add(j * nx + i)
    free = np.array([p for p in range(nx * ny) if p not in fixed], dtype=int)
    kff = stiffness[free][:, free]
    mff = sparse.eye(len(free), format="csr") * mass_value
    values, vectors = eigsh(kff, k=count + 4, M=mff, sigma=0.0, which="LM")
    order = np.argsort(values)
    values, vectors = values[order], vectors[:, order]
    keep = values > 1e-4
    values, vectors = values[keep][:count], vectors[:, keep][:, :count]
    frequencies = np.sqrt(values) / (2.0 * np.pi)

    mass_modes = np.zeros((nx * ny, len(frequencies)))
    mass_modes[free, :] = vectors
    display_modes = mass_modes / np.max(np.abs(mass_modes), axis=0, keepdims=True)
    return xs, ys, frequencies, display_modes, mass_modes


def modal_signatures(xs, ys, frequencies, mass_modes):
    sensor_idx = nearest_node(*SENSOR, xs, ys)
    omega = 2.0 * np.pi * frequencies
    signatures = []
    for note, hit in zip(NOTES, HITS):
        hit_idx = nearest_node(*hit, xs, ys)
        # For a point impulse, modal acceleration amplitude at the sensor is
        # proportional to phi(hit) * phi(sensor) * angular frequency.
        coupling = np.abs(mass_modes[hit_idx, :] * mass_modes[sensor_idx, :] * omega)
        coupling /= max(float(coupling.max()), 1e-12)
        signatures.append(
            {
                "note": note,
                "x_mm": int(hit[0] * 1000),
                "y_mm": int(hit[1] * 1000),
                "modal_coupling": np.round(coupling, 4).tolist(),
            }
        )
    return signatures


def save_mode_svg(path: Path, xs, ys, mode: np.ndarray, frequency: float, number: int):
    field = mode.reshape(len(ys), len(xs))
    fig, ax = plt.subplots(figsize=(6.4, 3.25), constrained_layout=True)
    levels = np.linspace(-1.0, 1.0, 17)
    contour = ax.contourf(xs * 1000, ys * 1000, field, levels=levels, cmap="RdBu_r", extend="both")
    ax.contour(xs * 1000, ys * 1000, field, levels=[0], colors="#18202b", linewidths=0.75)
    ax.add_patch(Rectangle((200, 0), 100, 20, facecolor="#333333", alpha=0.35,
                           edgecolor="#111827", linewidth=1.2, hatch="////"))
    ax.scatter([SENSOR[0] * 1000], [SENSOR[1] * 1000], marker="D", s=42, color="#f5b942", edgecolor="#111827", linewidth=0.7)
    ax.set(xlim=(0, 400), ylim=(200, 0), xlabel="x [mm]", ylabel="y [mm]", title=f"Mode {number}   {frequency:.1f} Hz")
    ax.set_aspect("equal")
    ax.set_xticks([0, 100, 200, 300, 400])
    ax.set_yticks([0, 100, 200])
    bar = fig.colorbar(contour, ax=ax, shrink=0.78, pad=0.03)
    bar.set_label("normalized deflection")
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)


def hit_intensity(xs, ys, frequencies, mass_modes, hit):
    """Return spatial RMS acceleration from an impulsive point hit."""
    hit_idx = nearest_node(*hit, xs, ys)
    omega = 2.0 * np.pi * frequencies
    modal_acceleration = mass_modes * (mass_modes[hit_idx, :] * omega)[None, :]
    # Time-average over incoherent modal phases: RMS of modal amplitudes.
    return np.sqrt(0.5 * np.sum(modal_acceleration**2, axis=1))


def save_hit_intensity_svg(path: Path, xs, ys, intensity, hit, note: str, peak_relative: float):
    """Save spatial RMS acceleration using a common scale across all hits."""
    field = intensity.reshape(len(ys), len(xs))

    fig, ax = plt.subplots(figsize=(6.4, 3.25), constrained_layout=True)
    levels = np.linspace(0.0, 1.0, 17)
    contour = ax.contourf(xs * 1000, ys * 1000, field, levels=levels, cmap="magma", extend="max")
    ax.add_patch(Rectangle((200, 0), 100, 20, facecolor="#eeeeee", alpha=0.65,
                           edgecolor="#111827", linewidth=1.2, hatch="////"))
    ax.scatter([SENSOR[0] * 1000], [SENSOR[1] * 1000], marker="D", s=42,
               color="#34d399", edgecolor="#111827", linewidth=0.7, label="sensor")
    ax.scatter([hit[0] * 1000], [hit[1] * 1000], marker="*", s=115,
               color="#60a5fa", edgecolor="#ffffff", linewidth=0.8, label="hit")
    ax.set(xlim=(0, 400), ylim=(200, 0), xlabel="x [mm]", ylabel="y [mm]",
           title=f"{note} hit ({hit[0] * 1000:.0f}, {hit[1] * 1000:.0f}) mm   peak={peak_relative:.2f}")
    ax.set_aspect("equal")
    ax.set_xticks([0, 100, 200, 300, 400])
    ax.set_yticks([0, 100, 200])
    ax.legend(loc="lower right", fontsize=8, framealpha=0.9)
    bar = fig.colorbar(contour, ax=ax, shrink=0.78, pad=0.03)
    bar.set_label("normalized RMS acceleration")
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("web/assets/simulation"))
    parser.add_argument("--modes", type=int, default=18)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    xs, ys, frequencies, display_modes, mass_modes = solve_modes(count=args.modes)
    signatures = modal_signatures(xs, ys, frequencies, mass_modes)
    raw_intensity_maps = [hit_intensity(xs, ys, frequencies, mass_modes, hit) for hit in HITS]
    common_peak = max(float(values.max()) for values in raw_intensity_maps)
    intensity_maps = [values / max(common_peak, 1e-12) for values in raw_intensity_maps]
    for signature, values in zip(signatures, intensity_maps):
        signature["spatial_rms_peak_relative"] = round(float(values.max()), 4)
    result = {
        "model": {
            "width_mm": 400,
            "height_mm": 200,
            "thickness_mm": int(round(THICKNESS * 1000)),
            "youngs_modulus_gpa": 3.2,
            "poisson_ratio": POISSON,
            "density_kg_m3": DENSITY,
            "fixture_x_mm": [200, 300],
            "fixture_y_mm": [0, 20],
            "fixture_area_mm": [100, 20],
            "sensor_mm": [int(SENSOR[0] * 1000), int(SENSOR[1] * 1000)],
            "grid": [len(xs), len(ys)],
            "method": "finite-difference Kirchhoff plate; all nodes in 100 x 20 mm clamp area constrained",
        },
        "frequencies_hz": np.round(frequencies, 2).tolist(),
        "response_map": {
            "quantity": "normalized spatial RMS acceleration",
            "modes_used": int(len(frequencies)),
            "excitation": "unit point impulse",
            "combination": "root-sum-square over modal acceleration amplitudes",
            "normalization": "common maximum across all 8 hit maps",
        },
        "hits": signatures,
    }
    (args.output / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "simulation-data.js").write_text(
        "window.ACRYLIC_SIM = " + json.dumps(result, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    sensor_idx = nearest_node(*SENSOR, xs, ys)
    hit_indices = [nearest_node(*hit, xs, ys) for hit in HITS]
    signed_participation = np.array([
        mass_modes[index, :] * mass_modes[sensor_idx, :] for index in hit_indices
    ])
    sensor_data = serializable_sensor_data(
        NOTES, HITS, frequencies, signed_participation, "Kirchhoff plate finite-difference"
    )
    (args.output / "sensor-response-2d.json").write_text(
        json.dumps(sensor_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "sensor-response-2d.js").write_text(
        "window.ACRYLIC_SENSOR_DATA = " + json.dumps(sensor_data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    for index in range(min(8, len(frequencies))):
        save_mode_svg(args.output / f"mode-{index + 1}.svg", xs, ys, display_modes[:, index], frequencies[index], index + 1)
    for note, hit, values in zip(NOTES, HITS, intensity_maps):
        save_hit_intensity_svg(args.output / f"hit-{note.lower()}.svg", xs, ys, values,
                               hit, note, float(values.max()))
    print(json.dumps({"output": str(args.output), "frequencies_hz": result["frequencies_hz"][:8]}))


if __name__ == "__main__":
    main()
