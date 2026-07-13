"""Three-dimensional solid finite-element model of the acrylic panel.

The model uses structured eight-node hexahedral elements with three
translational degrees of freedom per node and two elements through thickness.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.animation as animation
import matplotlib.pyplot as plt
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import eigsh
from .sensor_signal import serializable_sensor_data

WIDTH, HEIGHT, THICKNESS = 0.400, 0.200, 0.002
E, NU, RHO = 3.2e9, 0.35, 1180.0
FIXED_X, FIXED_Y = (0.200, 0.300), (0.000, 0.020)
SENSOR = (0.200, 0.100)
HITS = [(x, y) for y in (0.050, 0.150) for x in (0.050, 0.150, 0.250, 0.350)]
NOTES = ("C4", "D4", "E4", "G4", "A4", "C5", "D5", "E5")


def elasticity_matrix() -> np.ndarray:
    lam = E * NU / ((1 + NU) * (1 - 2 * NU))
    mu = E / (2 * (1 + NU))
    return np.array([
        [lam + 2 * mu, lam, lam, 0, 0, 0],
        [lam, lam + 2 * mu, lam, 0, 0, 0],
        [lam, lam, lam + 2 * mu, 0, 0, 0],
        [0, 0, 0, mu, 0, 0], [0, 0, 0, 0, mu, 0], [0, 0, 0, 0, 0, mu],
    ])


def hex8_stiffness(hx: float, hy: float, hz: float) -> np.ndarray:
    """Return the 24 x 24 full-integration stiffness of one brick."""
    signs = np.array([
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ], dtype=float)
    constitutive = elasticity_matrix()
    jac_inv = np.diag([2 / hx, 2 / hy, 2 / hz])
    det_j = hx * hy * hz / 8
    ke = np.zeros((24, 24))
    g = 1 / np.sqrt(3)
    for xi in (-g, g):
        for eta in (-g, g):
            for zeta in (-g, g):
                natural = np.empty((8, 3))
                for a, (sx, sy, sz) in enumerate(signs):
                    natural[a] = (
                        sx * (1 + sy * eta) * (1 + sz * zeta) / 8,
                        sy * (1 + sx * xi) * (1 + sz * zeta) / 8,
                        sz * (1 + sx * xi) * (1 + sy * eta) / 8,
                    )
                deriv = natural @ jac_inv
                b = np.zeros((6, 24))
                for a, (dx, dy, dz) in enumerate(deriv):
                    c = 3 * a
                    b[0, c] = dx; b[1, c + 1] = dy; b[2, c + 2] = dz
                    b[3, c:c + 2] = (dy, dx)
                    b[4, c + 1:c + 3] = (dz, dy)
                    b[5, (c, c + 2)] = (dz, dx)
                ke += b.T @ constitutive @ b * det_j
    return ke


def node_id(i: int, j: int, k: int, nx: int, ny: int) -> int:
    return (k * ny + j) * nx + i


def build_model(ex: int = 32, ey: int = 16, ez: int = 2):
    nx, ny, nz = ex + 1, ey + 1, ez + 1
    xs = np.linspace(0, WIDTH, nx); ys = np.linspace(0, HEIGHT, ny)
    zs = np.linspace(-THICKNESS / 2, THICKNESS / 2, nz)
    ke = hex8_stiffness(WIDTH / ex, HEIGHT / ey, THICKNESS / ez)
    rows, cols, values = [], [], []
    nodal_mass = np.zeros(nx * ny * nz)
    element_mass = RHO * WIDTH / ex * HEIGHT / ey * THICKNESS / ez
    for k in range(ez):
        for j in range(ey):
            for i in range(ex):
                nodes = [node_id(i, j, k, nx, ny), node_id(i + 1, j, k, nx, ny),
                         node_id(i + 1, j + 1, k, nx, ny), node_id(i, j + 1, k, nx, ny),
                         node_id(i, j, k + 1, nx, ny), node_id(i + 1, j, k + 1, nx, ny),
                         node_id(i + 1, j + 1, k + 1, nx, ny), node_id(i, j + 1, k + 1, nx, ny)]
                dofs = np.array([[3 * n, 3 * n + 1, 3 * n + 2] for n in nodes]).ravel()
                rows.extend(np.repeat(dofs, 24)); cols.extend(np.tile(dofs, 24)); values.extend(ke.ravel())
                nodal_mass[nodes] += element_mass / 8
    ndof = 3 * nx * ny * nz
    stiffness = sparse.coo_matrix((values, (rows, cols)), shape=(ndof, ndof)).tocsr()
    mass = sparse.diags(np.repeat(nodal_mass, 3), format="csr")
    fixed_nodes = [node_id(i, j, k, nx, ny) for k, _z in enumerate(zs) for j, y in enumerate(ys)
                   for i, x in enumerate(xs) if FIXED_X[0] - 1e-12 <= x <= FIXED_X[1] + 1e-12
                   and FIXED_Y[0] - 1e-12 <= y <= FIXED_Y[1] + 1e-12]
    fixed_dofs = {3 * n + d for n in fixed_nodes for d in range(3)}
    free = np.array([d for d in range(ndof) if d not in fixed_dofs])
    return xs, ys, zs, stiffness, mass, free, fixed_nodes


def solve_modes(ex: int, ey: int, ez: int, count: int):
    xs, ys, zs, stiffness, mass, free, fixed_nodes = build_model(ex, ey, ez)
    values, vectors = eigsh(stiffness[free][:, free], k=count, M=mass[free][:, free], sigma=0.0, which="LM")
    order = np.argsort(values); values, vectors = values[order], vectors[:, order]
    frequencies = np.sqrt(np.maximum(values, 0)) / (2 * np.pi)
    modes = np.zeros((stiffness.shape[0], count)); modes[free] = vectors
    return xs, ys, zs, frequencies, modes, fixed_nodes


def nearest_top_node(x: float, y: float, xs, ys, zs) -> int:
    return node_id(int(np.argmin(abs(xs - x))), int(np.argmin(abs(ys - y))), len(zs) - 1, len(xs), len(ys))


def save_mode(path: Path, xs, ys, zs, mode: np.ndarray, frequency: float, number: int):
    nx, ny = len(xs), len(ys)
    top = np.array([node_id(i, j, len(zs) - 1, nx, ny) for j in range(ny) for i in range(nx)])
    w = mode[3 * top + 2].reshape(ny, nx); w /= max(np.max(np.abs(w)), 1e-15)
    xx, yy = np.meshgrid(xs * 1000, ys * 1000)
    fig = plt.figure(figsize=(7.2, 4.5), constrained_layout=True); ax = fig.add_subplot(projection="3d")
    surf = ax.plot_surface(xx, yy, 1 + 14 * w, cmap="RdBu_r", vmin=-1, vmax=1,
                           linewidth=0.15, edgecolor=(0, 0, 0, 0.22), antialiased=True)
    for y in (0, 200): ax.plot([0, 400], [y, y], [-1, -1], color="#374151", linewidth=1)
    ax.set(xlabel="x [mm]", ylabel="y [mm]", zlabel="z (exaggerated) [mm]",
           title=f"3-D solid FEM — Mode {number}: {frequency:.1f} Hz")
    ax.set_box_aspect((2, 1, 0.35)); ax.view_init(28, -62)
    fig.colorbar(surf, ax=ax, shrink=0.62, pad=0.08, label="normalized z displacement")
    fig.savefig(path, format="svg", metadata={"Date": None}); plt.close(fig)


def save_mesh(path: Path, xs, ys, zs):
    """Draw the actual structured solid mesh and constrained volume."""
    xx, yy = np.meshgrid(xs * 1000, ys * 1000)
    fig = plt.figure(figsize=(8.2, 4.8), constrained_layout=True); ax = fig.add_subplot(projection="3d")
    for z in zs * 1000:
        zz = np.full_like(xx, z)
        ax.plot_wireframe(xx, yy, zz, rstride=1, cstride=1, color="#52717c", linewidth=0.25, alpha=0.72)
    for x in xs * 1000:
        for y in (0, 200): ax.plot([x, x], [y, y], [-1, 1], color="#52717c", linewidth=0.35)
    for y in ys * 1000:
        for x in (0, 400): ax.plot([x, x], [y, y], [-1, 1], color="#52717c", linewidth=0.35)
    fx = np.linspace(200, 300, 9); fy = np.linspace(0, 20, 3); fxx, fyy = np.meshgrid(fx, fy)
    for z in (-1, 1): ax.plot_surface(fxx, fyy, np.full_like(fxx, z), color="#e2633b", alpha=0.68, shade=False)
    ax.scatter([200], [100], [1], marker="D", s=45, c="#34d399", edgecolors="#111827", label="sensor")
    ax.set(xlabel="x [mm]", ylabel="y [mm]", zlabel="z [mm]",
           title=f"3-D solid mesh — {len(xs)-1} × {len(ys)-1} × {len(zs)-1} HEX8 elements")
    ax.set_box_aspect((2, 1, 0.18)); ax.set_zlim(-12, 12); ax.view_init(25, -62); ax.legend(loc="upper right")
    fig.savefig(path, format="svg", metadata={"Date": None}); plt.close(fig)


def response_fields(xs, ys, zs, frequencies, modes, duration=0.06, frames=120):
    nx, ny = len(xs), len(ys)
    top = np.array([node_id(i, j, len(zs) - 1, nx, ny) for j in range(ny) for i in range(nx)])
    top_modes = modes[3 * top + 2]; omega = 2 * np.pi * frequencies
    times = np.linspace(0, duration, frames); all_fields = []
    for hit in HITS:
        hit_node = nearest_top_node(*hit, xs, ys, zs)
        participation = modes[3 * hit_node + 2] / np.maximum(omega, 1e-9)
        q = participation[:, None] * np.sin(omega[:, None] * times) * np.exp(-0.012 * omega[:, None] * times)
        all_fields.append((top_modes @ q).T.reshape(frames, ny, nx))
    scale = max(np.max(np.abs(field)) for field in all_fields)
    return times, [field / max(scale, 1e-15) for field in all_fields]


def save_animation(path: Path, times, fields):
    fig, axes = plt.subplots(2, 4, figsize=(12.8, 6.5), constrained_layout=True); images = []
    for ax, note, hit, field in zip(axes.ravel(), NOTES, HITS, fields):
        image = ax.imshow(field[0], extent=(0, 400, 200, 0), cmap="RdBu_r", vmin=-1, vmax=1,
                          interpolation="bilinear", aspect="equal")
        ax.scatter([hit[0] * 1000], [hit[1] * 1000], marker="*", s=70, c="#f5b942", edgecolors="#111827")
        ax.scatter([200], [100], marker="D", s=28, c="#34d399", edgecolors="#111827")
        ax.set_title(f"{note}  ({hit[0]*1000:.0f}, {hit[1]*1000:.0f}) mm")
        ax.set_xticks([0, 200, 400]); ax.set_yticks([0, 100, 200]); images.append(image)
    stamp = fig.suptitle("3-D solid FEM impulse response — t = 0.00 ms (slow motion)")
    fig.colorbar(images[0], ax=axes, shrink=0.72, label="normalized z displacement (common scale)")
    def update(frame):
        for image, field in zip(images, fields): image.set_data(field[frame])
        stamp.set_text(f"3-D solid FEM impulse response — t = {times[frame]*1000:.2f} ms (slow motion)")
        return [*images, stamp]
    movie = animation.FuncAnimation(fig, update, frames=len(times), interval=1000 / 24, blit=False)
    movie.save(path, writer=animation.FFMpegWriter(fps=24, codec="libx264", bitrate=2200,
                                                   extra_args=["-pix_fmt", "yuv420p"]))
    plt.close(fig)


def save_hit_stills(path: Path, times, fields):
    """Save one representative maximum-response frame for every hit point."""
    fig, axes = plt.subplots(2, 4, figsize=(12.8, 6.5), constrained_layout=True); images = []
    for ax, note, hit, field in zip(axes.ravel(), NOTES, HITS, fields):
        frame = int(np.argmax(np.sqrt(np.mean(field**2, axis=(1, 2)))))
        image = ax.imshow(field[frame], extent=(0, 400, 200, 0), cmap="RdBu_r", vmin=-1, vmax=1,
                          interpolation="bilinear", aspect="equal")
        ax.scatter([hit[0] * 1000], [hit[1] * 1000], marker="*", s=75, c="#f5b942", edgecolors="#111827")
        ax.scatter([200], [100], marker="D", s=28, c="#34d399", edgecolors="#111827")
        ax.set_title(f"{note} — t={times[frame]*1000:.2f} ms")
        ax.set_xticks([0, 200, 400]); ax.set_yticks([0, 100, 200]); images.append(image)
    fig.suptitle("3-D solid FEM — representative deformation after each point impulse")
    fig.colorbar(images[0], ax=axes, shrink=0.72, label="normalized z displacement (common scale)")
    fig.savefig(path, format="svg", metadata={"Date": None}); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, default=Path("web/assets/simulation/solid3d"))
    parser.add_argument("--elements", default="32,16,2"); parser.add_argument("--modes", type=int, default=16)
    parser.add_argument("--skip-video", action="store_true"); args = parser.parse_args()
    ex, ey, ez = map(int, args.elements.split(",")); args.output.mkdir(parents=True, exist_ok=True)
    xs, ys, zs, frequencies, modes, fixed_nodes = solve_modes(ex, ey, ez, args.modes)
    sensor_node = nearest_top_node(*SENSOR, xs, ys, zs); omega = 2 * np.pi * frequencies; hits = []
    for note, hit in zip(NOTES, HITS):
        hit_node = nearest_top_node(*hit, xs, ys, zs)
        coupling = np.abs(modes[3 * hit_node + 2] * modes[3 * sensor_node + 2] * omega)
        coupling /= max(coupling.max(), 1e-15)
        hits.append({"note": note, "x_mm": int(hit[0] * 1000), "y_mm": int(hit[1] * 1000),
                     "modal_coupling": np.round(coupling, 5).tolist()})
    result = {"method": "3-D linear elasticity; structured HEX8 solid elements; 2x2x2 Gauss integration; lumped mass",
              "mesh": {"elements": [ex, ey, ez], "nodes": len(xs)*len(ys)*len(zs),
                       "degrees_of_freedom": 3*len(xs)*len(ys)*len(zs), "fixed_nodes": len(fixed_nodes)},
              "material": {"E_GPa": E/1e9, "poisson": NU, "density_kg_m3": RHO},
              "geometry_mm": [400, 200, 2], "fixed_volume_mm": {"x": [200, 300], "y": [0, 20], "z": [-1, 1]},
              "sensor_mm": [200, 100, 1], "frequencies_hz": np.round(frequencies, 3).tolist(), "hits": hits,
              "limitations": ["linear isotropic PMMA", "perfectly fixed clamp volume", "no sensor/adhesive mass", "small deformation"]}
    (args.output/"solid3d-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    hit_nodes = [nearest_top_node(*hit, xs, ys, zs) for hit in HITS]
    signed_participation = np.array([
        modes[3*node+2, :] * modes[3*sensor_node+2, :] for node in hit_nodes
    ])
    sensor_data = serializable_sensor_data(
        NOTES, HITS, frequencies, signed_participation, "3-D HEX8 solid FEM"
    )
    (args.output/"sensor-response-3d.json").write_text(
        json.dumps(sensor_data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output/"sensor-response-3d.js").write_text(
        "window.ACRYLIC_SENSOR_DATA = " + json.dumps(sensor_data, ensure_ascii=False, separators=(",", ":")) + ";\n",
        encoding="utf-8",
    )
    save_mesh(args.output/"solid3d-mesh.svg", xs, ys, zs)
    for i in range(min(6, args.modes)): save_mode(args.output/f"solid3d-mode-{i+1}.svg", xs, ys, zs, modes[:, i], frequencies[i], i+1)
    times, fields = response_fields(xs, ys, zs, frequencies, modes)
    save_hit_stills(args.output/"solid3d-eight-hit-stills.svg", times, fields)
    if not args.skip_video:
        save_animation(args.output/"solid3d-eight-hits.mp4", times, fields)
    print(json.dumps({"mesh": result["mesh"], "frequencies_hz": result["frequencies_hz"][:8], "video": not args.skip_video}))


if __name__ == "__main__": main()
