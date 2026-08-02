"""Postprocess a configurable Acrylic Pan CalculiX modal profile."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np

from postprocess import frequencies_from_dat, modes_from_frd


def save_mode(path: Path, nodes, mode, number, frequency, metadata):
    width, height, _thickness = metadata["geometry_mm"]
    top_z = metadata["sensor"]["xyz_mm"][2]
    ids = [node for node, xyz in nodes.items() if abs(xyz[2] - top_z) < 1e-8 and node in mode]
    x = np.array([nodes[node][0] for node in ids])
    y = np.array([nodes[node][1] for node in ids])
    z = np.array([mode[node][2] for node in ids])
    z /= max(np.max(np.abs(z)), 1e-15)
    tri = mtri.Triangulation(x, y)
    fig, ax = plt.subplots(figsize=(7.2, 4.8), constrained_layout=True)
    contour = ax.tricontourf(tri, z, levels=np.linspace(-1, 1, 17), cmap="RdBu_r", extend="both")
    ax.tricontour(tri, z, levels=[0], colors="#263238", linewidths=.55)
    fixed = metadata["fixed"]
    ax.fill_between(fixed["x_mm"], fixed["y_mm"][0], fixed["y_mm"][1], color="#333", alpha=.3, hatch="////")
    sensor = metadata["sensor"]["xyz_mm"]
    ax.scatter([sensor[0]], [sensor[1]], marker="D", s=35, color="#34d399", edgecolor="#111827")
    ax.set(xlim=(0, width), ylim=(height, 0), aspect="equal", xlabel="x [mm]", ylabel="y [mm]",
           title=f"CalculiX C3D20R — Mode {number}: {frequency:.2f} Hz")
    fig.colorbar(contour, ax=ax, shrink=.78, label="normalized z displacement")
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)


def save_comparison(path: Path, calc, plate, solid):
    count = min(12, len(calc), len(plate), len(solid))
    order = np.arange(1, count + 1)
    fig, ax = plt.subplots(figsize=(8, 5), constrained_layout=True)
    ax.plot(plate[:count], order, "o-", label="2D plate FDM", lw=1.5)
    ax.plot(solid[:count], order, "s-", label="custom HEX8", lw=1.5)
    ax.plot(calc[:count], order, "D-", label="CalculiX C3D20R", lw=1.8)
    ax.set(xlabel="frequency [Hz]", ylabel="mode order", yticks=order,
           title="400 x 300 x 5 mm eigenfrequency comparison")
    ax.grid(True, alpha=.25)
    ax.legend()
    fig.savefig(path, format="svg", metadata={"Date": None})
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()
    metadata = json.loads((args.output / "model-metadata.json").read_text(encoding="utf-8"))
    nodes = {int(key): value for key, value in json.loads((args.output / "mesh-nodes.json").read_text()).items()}
    frequencies = frequencies_from_dat(args.output / "acrylic_pan.dat")
    modes = modes_from_frd(args.output / "acrylic_pan.frd")
    for index, (frequency, mode) in enumerate(modes[:8], 1):
        save_mode(args.output / f"calculix-mode-{index}.svg", nodes, mode, index, frequency, metadata)
    plate = json.loads((args.reference / "plate/results.json").read_text(encoding="utf-8"))["frequencies_hz"]
    solid = json.loads((args.reference / "solid3d/solid3d-results.json").read_text(encoding="utf-8"))["frequencies_hz"]
    save_comparison(args.output / "frequency-comparison.svg", frequencies, plate, solid)
    result = {**metadata, "frequencies_hz": frequencies, "outputs": {"modes": min(8, len(modes))}}
    (args.output / "calculix-results.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"frequencies_hz": frequencies[:12], "modes": len(modes)}))


if __name__ == "__main__":
    main()
