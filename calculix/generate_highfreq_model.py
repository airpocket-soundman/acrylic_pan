"""Generate the separate high-frequency CalculiX model for a 50 ms window."""

import argparse,json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from generate_model import build_mesh,write_set,HITS,SENSOR

X=list(range(0,401,10)); Y=list(range(0,201,10)); Z=[-1,0,1]
MODES=280; FMAX=6000.; FS=25600.; DT=1/FS; DURATION=.05

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,required=True); a=p.parse_args(); a.output.mkdir(parents=True,exist_ok=True)
    nodes,coords,elements=build_mesh(X,Y,Z)
    fixed=[n for n,(x,y,z) in coords.items() if 200<=x<=300 and 0<=y<=20]
    sensor=nodes[SENSOR]; hit_nodes={note:nodes[(x,y,1)] for note,x,y in HITS}; observation=[sensor,*hit_nodes.values()]
    inp=a.output/"acrylic_pan_hf.inp"
    with inp.open("w",encoding="ascii",newline="\n") as f:
        f.write("*HEADING\nAcrylic Pan high-frequency C3D20R model\n*NODE\n")
        for n,(x,y,z) in coords.items(): f.write(f"{n},{x:.8f},{y:.8f},{z:.8f}\n")
        f.write("*ELEMENT,TYPE=C3D20R,ELSET=SOLID\n")
        for n,c in enumerate(elements,1):
            f.write(f"{n},"+",".join(map(str,c[:15]))+"\n"+",".join(map(str,c[15:]))+"\n")
        write_set(f,"FIXED",fixed); write_set(f,"OBSERVATION",observation); write_set(f,"SENSOR",[sensor])
        for note,node in hit_nodes.items(): write_set(f,"HIT_"+note,[node])
        f.write("*MATERIAL,NAME=PMMA\n*ELASTIC\n3200.,0.35\n*DENSITY\n1.18E-9\n*SOLID SECTION,ELSET=SOLID,MATERIAL=PMMA\n*BOUNDARY\nFIXED,1,3\n")
        f.write(f"*STEP\n*FREQUENCY,STORAGE=YES,SOLVER=SPOOLES\n{MODES},0.,{FMAX:.1f}\n")
        f.write("*NODE FILE,NSET=OBSERVATION,GLOBAL=YES\nU\n*NODE PRINT,NSET=OBSERVATION,GLOBAL=YES\nU\n*END STEP\n")
    base=inp.read_text(encoding="ascii").split("*STEP",1)[0]
    for note,_,_ in HITS:
        dynamic=base+(f"*AMPLITUDE,NAME=IMPULSE,TIME=TOTAL TIME\n0.,0.,{DT:.10f},{FS:.1f},{2*DT:.10f},0.,{DURATION:.8f},0.\n"
            f"*STEP,INC=5000\n*MODAL DYNAMIC,DIRECT\n{DT:.10f},{DURATION:.8f}\n*MODAL DAMPING,MODAL=DIRECT\n1,{MODES},0.012\n"
            f"*CLOAD,AMPLITUDE=IMPULSE\nHIT_{note},3,1.0\n*NODE PRINT,NSET=SENSOR,GLOBAL=YES,FREQUENCY=1\nU\n*END STEP\n")
        (a.output/f"hf_{note.lower()}.inp").write_text(dynamic,encoding="ascii",newline="\n")
    meta={"solver":"CalculiX 2.20","profile":"high-frequency-50ms","element":"C3D20R","mesh":{"elements":len(elements),"nodes":len(coords),"xy_mm":10,"z_mm":1},"frequency":{"requested_modes":MODES,"max_hz":FMAX},"sampling":{"hz":FS,"period_us":DT*1e6,"duration_ms":DURATION*1000,"samples":round(FS*DURATION)},"sensor":{"node":sensor,"xyz_mm":SENSOR},"hits":[{"note":n,"node":hit_nodes[n],"xyz_mm":[x,y,1]} for n,x,y in HITS]}
    (a.output/"highfreq-metadata.json").write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding="utf-8")
    fig,ax=plt.subplots(figsize=(10,5),constrained_layout=True)
    for x in X: ax.plot([x,x],[0,200],color="#90a4ae",lw=.25)
    for y in Y: ax.plot([0,400],[y,y],color="#90a4ae",lw=.25)
    ax.fill_between([200,300],0,20,color="#e2633b",alpha=.55); ax.scatter([200],[100],marker="D",color="#34d399",edgecolor="#111827"); ax.scatter([h[1] for h in HITS],[h[2] for h in HITS],marker="*",color="#f5b942",edgecolor="#111827")
    ax.set(xlim=(0,400),ylim=(200,0),aspect="equal",xlabel="x [mm]",ylabel="y [mm]",title=f"High-frequency C3D20R mesh - {len(elements)} elements, {len(coords)} nodes, 10 mm pitch")
    fig.savefig(a.output/"highfreq-mesh.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    print(json.dumps({"nodes":len(coords),"elements":len(elements),"sensor":sensor}))
if __name__=="__main__": main()
