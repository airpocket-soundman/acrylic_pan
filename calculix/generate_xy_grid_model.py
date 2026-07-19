"""Generate CalculiX decks for centre/corner XY-regression comparison."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from generate_model import HITS, build_mesh, thickness_z_grid, write_set


MODES = 280
FMAX = 6000.0
FS = 25_600.0
DT = 1.0 / FS
DURATION = 0.05


def point_definitions() -> list[dict]:
    points: list[dict] = []
    centres = [(note, float(x), float(y)) for note, x, y in HITS]
    for area, (note, x, y) in enumerate(centres):
        points.append({"group": "center", "area": area, "name": note, "x_mm": x, "y_mm": y})

    moved_near_clamp = {(225.0, 25.0): (225.0, 35.0), (275.0, 25.0): (275.0, 35.0)}
    corner_names = ((-25.0, -25.0, "up_left"), (25.0, -25.0, "up_right"),
                    (-25.0, 25.0, "down_left"), (25.0, 25.0, "down_right"))
    for area, (note, cx, cy) in enumerate(centres):
        for dx, dy, corner_name in corner_names:
            x, y = moved_near_clamp.get((cx + dx, cy + dy), (cx + dx, cy + dy))
            points.append({
                "group": "corner", "area": area, "name": f"{note}_{corner_name}",
                "x_mm": x, "y_mm": y, "nominal_x_mm": cx + dx,
                "nominal_y_mm": cy + dy,
            })

    probes = [
        *( (x, y) for y in (50.0, 150.0) for x in (100.0, 200.0, 300.0) ),
        *( (x, 100.0) for x in (50.0, 150.0, 250.0, 350.0) ),
    ]
    for index, (x, y) in enumerate(probes):
        points.append({"group": "probe", "area": None, "name": f"probe_{index + 1:02d}",
                       "x_mm": x, "y_mm": y})
    for index, point in enumerate(points):
        point["id"] = f"p{index:03d}"
    return points


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thickness-mm", type=float, default=3.0)
    args = parser.parse_args()
    if args.thickness_mm <= 0:
        parser.error("--thickness-mm must be positive")
    args.output.mkdir(parents=True, exist_ok=True)

    points = point_definitions()
    x_grid = sorted(set(range(0, 401, 10)) | {int(p["x_mm"]) for p in points})
    y_grid = sorted(set(range(0, 201, 10)) | {int(p["y_mm"]) for p in points})
    z_grid = thickness_z_grid(args.thickness_mm)
    nodes, coordinates, elements = build_mesh(x_grid, y_grid, z_grid)
    top_z = z_grid[-1]
    fixed = [n for n, (x, y, _z) in coordinates.items() if 200 <= x <= 300 and 0 <= y <= 20]
    sensor = nodes[(200, 100, top_z)]
    for point in points:
        point["node"] = nodes[(point["x_mm"], point["y_mm"], top_z)]

    model = args.output / "acrylic_pan_xy.inp"
    with model.open("w", encoding="ascii", newline="\n") as handle:
        handle.write("*HEADING\nAcrylic Pan XY grid C3D20R model\n*NODE\n")
        for number, (x, y, z) in coordinates.items():
            handle.write(f"{number},{x:.8f},{y:.8f},{z:.8f}\n")
        handle.write("*ELEMENT,TYPE=C3D20R,ELSET=SOLID\n")
        for number, connectivity in enumerate(elements, 1):
            handle.write(f"{number}," + ",".join(map(str, connectivity[:15])) + "\n")
            handle.write(",".join(map(str, connectivity[15:])) + "\n")
        write_set(handle, "FIXED", fixed)
        write_set(handle, "SENSOR", [sensor])
        for point in points:
            write_set(handle, "HIT_" + point["id"].upper(), [point["node"]])
        handle.write("*MATERIAL,NAME=PMMA\n*ELASTIC\n3200.,0.35\n*DENSITY\n1.18E-9\n")
        handle.write("*SOLID SECTION,ELSET=SOLID,MATERIAL=PMMA\n*BOUNDARY\nFIXED,1,3\n")
        handle.write(f"*STEP\n*FREQUENCY,STORAGE=YES,SOLVER=SPOOLES\n{MODES},0.,{FMAX:.1f}\n")
        handle.write("*NODE PRINT,NSET=SENSOR,GLOBAL=YES\nU\n*END STEP\n")

    base = model.read_text(encoding="ascii").split("*STEP", 1)[0]
    for point in points:
        dynamic = base + (
            f"*AMPLITUDE,NAME=IMPULSE,TIME=TOTAL TIME\n0.,0.,{DT:.10f},{FS:.1f},"
            f"{2 * DT:.10f},0.,{DURATION:.8f},0.\n"
            "*STEP,INC=5000\n*MODAL DYNAMIC,DIRECT\n"
            f"{DT:.10f},{DURATION:.8f}\n*MODAL DAMPING,MODAL=DIRECT\n"
            f"1,{MODES},0.012\n*CLOAD,AMPLITUDE=IMPULSE\n"
            f"HIT_{point['id'].upper()},3,1.0\n"
            "*NODE PRINT,NSET=SENSOR,GLOBAL=YES,FREQUENCY=1\nU\n*END STEP\n"
        )
        (args.output / f"xy_{point['id']}.inp").write_text(dynamic, encoding="ascii", newline="\n")

    metadata = {
        "solver": "CalculiX 2.20", "profile": "xy-grid-50ms",
        "element": "C3D20R", "thickness_mm": args.thickness_mm,
        "mesh": {"elements": len(elements), "nodes": len(coordinates),
                 "x_grid_mm": x_grid, "y_grid_mm": y_grid,
                 "z_grid_mm": z_grid, "through_thickness_elements": 2},
        "frequency": {"requested_modes": MODES, "max_hz": FMAX},
        "sampling": {"hz": FS, "duration_ms": DURATION * 1000,
                     "samples": round(FS * DURATION)},
        "damping_ratio": 0.012,
        "sensor": {"node": sensor, "xyz_mm": [200, 100, top_z]},
        "training_groups": {"center": 8, "corner": 32},
        "test_group": {"probe": 10},
        "points": points,
    }
    (args.output / "xy-grid-metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"nodes": len(coordinates), "elements": len(elements),
                      "points": len(points), "sensor": sensor}))


if __name__ == "__main__":
    main()
