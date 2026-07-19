"""Generate a graded C3D20R solid mesh and CalculiX input decks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


X_GRID = [0, 25, 50, 75, 100, 125, 150, 175, 190, 200, 210, 225, 250, 275, 290, 300, 310, 325, 350, 375, 400]
Y_GRID = [0, 5, 10, 15, 20, 30, 50, 75, 90, 100, 110, 125, 150, 175, 200]
DEFAULT_THICKNESS_MM = 3.0
HITS = [("C4", 50, 50), ("D4", 150, 50), ("E4", 250, 50), ("G4", 350, 50),
        ("A4", 50, 150), ("C5", 150, 150), ("D5", 250, 150), ("E5", 350, 150)]


def thickness_z_grid(thickness_mm):
    half = thickness_mm / 2.0
    return [-half, 0.0, half]


def chunks(values, count=16):
    for start in range(0, len(values), count):
        yield values[start:start + count]


def build_mesh(x_grid=None,y_grid=None,z_grid=None):
    x_grid=X_GRID if x_grid is None else x_grid; y_grid=Y_GRID if y_grid is None else y_grid; z_grid=Z_GRID if z_grid is None else z_grid
    nodes = {}
    coordinates = {}
    elements = []

    def node(x, y, z):
        key = (round(x, 8), round(y, 8), round(z, 8))
        if key not in nodes:
            number = len(nodes) + 1
            nodes[key] = number
            coordinates[number] = key
        return nodes[key]

    for z0, z1 in zip(z_grid[:-1], z_grid[1:]):
        for y0, y1 in zip(y_grid[:-1], y_grid[1:]):
            for x0, x1 in zip(x_grid[:-1], x_grid[1:]):
                xm, ym, zm = (x0+x1)/2, (y0+y1)/2, (z0+z1)/2
                connectivity = [
                    node(x0,y0,z0), node(x1,y0,z0), node(x1,y1,z0), node(x0,y1,z0),
                    node(x0,y0,z1), node(x1,y0,z1), node(x1,y1,z1), node(x0,y1,z1),
                    node(xm,y0,z0), node(x1,ym,z0), node(xm,y1,z0), node(x0,ym,z0),
                    node(xm,y0,z1), node(x1,ym,z1), node(xm,y1,z1), node(x0,ym,z1),
                    node(x0,y0,zm), node(x1,y0,zm), node(x1,y1,zm), node(x0,y1,zm),
                ]
                elements.append(connectivity)
    return nodes, coordinates, elements


def write_set(handle, name, node_numbers):
    handle.write(f"*NSET,NSET={name}\n")
    for group in chunks(sorted(node_numbers)):
        handle.write(",".join(map(str, group)) + "\n")


def save_mesh_figure(path, coordinates, elements, fixed, sensor):
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    for x in X_GRID: ax.plot([x,x], [0,200], color="#78909c", lw=0.45)
    for y in Y_GRID: ax.plot([0,400], [y,y], color="#78909c", lw=0.45)
    ax.fill_between([200,300], 0, 20, color="#e2633b", alpha=0.55, label="fixed volume")
    ax.scatter([sensor[0]], [sensor[1]], marker="D", s=55, color="#34d399", edgecolor="#111827", label="sensor")
    ax.scatter([h[1] for h in HITS], [h[2] for h in HITS], marker="*", s=75, color="#f5b942", edgecolor="#111827", label="hit points")
    ax.set(xlim=(0,400), ylim=(200,0), xlabel="x [mm]", ylabel="y [mm]",
           title=f"CalculiX graded C3D20R mesh — {len(elements)} elements, {len(coordinates)} nodes")
    ax.set_aspect("equal"); ax.legend(loc="lower right")
    fig.savefig(path, format="svg", metadata={"Date": None}); plt.close(fig)


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--thickness-mm", type=float, default=DEFAULT_THICKNESS_MM)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True)
    if args.thickness_mm <= 0:
        parser.error("--thickness-mm must be positive")
    z_grid = thickness_z_grid(args.thickness_mm)
    sensor_xyz = (200, 100, z_grid[-1])
    nodes, coordinates, elements = build_mesh(z_grid=z_grid)
    fixed = [n for n,(x,y,z) in coordinates.items() if 200-1e-8 <= x <= 300+1e-8 and 0-1e-8 <= y <= 20+1e-8]
    top = [n for n,(x,y,z) in coordinates.items() if abs(z-z_grid[-1]) < 1e-8]
    sensor = nodes[sensor_xyz]
    hit_nodes = {note:nodes[(x,y,z_grid[-1])] for note,x,y in HITS}
    observation = [sensor, *hit_nodes.values()]

    inp = args.output / "acrylic_pan.inp"
    with inp.open("w", encoding="ascii", newline="\n") as f:
        f.write("*HEADING\nAcrylic Pan graded C3D20R modal model\n*NODE\n")
        for number,(x,y,z) in coordinates.items(): f.write(f"{number},{x:.8f},{y:.8f},{z:.8f}\n")
        f.write("*ELEMENT,TYPE=C3D20R,ELSET=SOLID\n")
        for number,conn in enumerate(elements,1):
            f.write(f"{number}," + ",".join(map(str,conn[:15])) + "\n")
            f.write(",".join(map(str,conn[15:])) + "\n")
        write_set(f,"FIXED",fixed); write_set(f,"TOPSURFACE",top); write_set(f,"OBSERVATION",observation)
        write_set(f,"SENSOR",[sensor])
        for note,node_number in hit_nodes.items(): write_set(f,"HIT_"+note,[node_number])
        f.write("*MATERIAL,NAME=PMMA\n*ELASTIC\n3200.,0.35\n*DENSITY\n1.18E-9\n")
        f.write("*SOLID SECTION,ELSET=SOLID,MATERIAL=PMMA\n*BOUNDARY\nFIXED,1,3\n")
        f.write("*STEP\n*FREQUENCY,STORAGE=YES,SOLVER=SPOOLES\n30,0.,1200.\n*CLOAD\n")
        for node_number in hit_nodes.values(): f.write(f"{node_number},3,0.0\n")
        f.write("*NODE FILE,NSET=TOPSURFACE,GLOBAL=YES\nU\n*NODE PRINT,NSET=OBSERVATION,GLOBAL=YES\nU\n*END STEP\n")

    metadata = {"solver":"CalculiX CrunchiX 2.20","element":"C3D20R","integration":"reduced",
                "thickness_mm":args.thickness_mm,
                "mesh":{"elements":len(elements),"nodes":len(coordinates),"x_grid_mm":X_GRID,"y_grid_mm":Y_GRID,"z_grid_mm":z_grid},
                "material":{"E_MPa":3200,"poisson":0.35,"density_tonne_mm3":1.18e-9},
                "fixed":{"x_mm":[200,300],"y_mm":[0,20],"z_mm":[z_grid[0],z_grid[-1]],"nodes":len(fixed)},
                "sensor":{"node":sensor,"xyz_mm":sensor_xyz},
                "hits":[{"note":note,"node":hit_nodes[note],"xyz_mm":[x,y,z_grid[-1]]} for note,x,y in HITS],
                "frequency_step":{"requested_modes":30,"range_hz":[0,1200],"storage":True}}
    (args.output/"model-metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.output/"mesh-nodes.json").write_text(json.dumps({str(k):v for k,v in coordinates.items()}),encoding="utf-8")
    save_mesh_figure(args.output/"calculix-graded-mesh.svg",coordinates,elements,fixed,sensor_xyz)
    base = inp.read_text(encoding="ascii").split("*STEP", 1)[0]
    for note, _x, _y in HITS:
        job = f"hit_{note.lower()}"
        dynamic = base + (
            "*AMPLITUDE,NAME=IMPULSE,TIME=TOTAL TIME\n"
            "0.,0.,0.00015625,6400.,0.0003125,0.,0.320000,0.\n"
            "*STEP,INC=10000\n*MODAL DYNAMIC,DIRECT\n0.00015625,0.320000\n"
            "*MODAL DAMPING,MODAL=DIRECT\n1,30,0.012\n"
            f"*CLOAD,AMPLITUDE=IMPULSE\nHIT_{note},3,1.0\n"
            "*NODE PRINT,NSET=SENSOR,GLOBAL=YES,FREQUENCY=1\nU\n*END STEP\n"
        )
        (args.output/f"{job}.inp").write_text(dynamic,encoding="ascii",newline="\n")
    print(json.dumps({"input":str(inp),"nodes":len(coordinates),"elements":len(elements),"fixed_nodes":len(fixed),"sensor":sensor}))


if __name__ == "__main__": main()
