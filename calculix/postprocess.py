"""Convert CalculiX modal/dynamic outputs into web visualizations."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.animation as animation
import matplotlib.tri as mtri
from matplotlib import cm, colors
from mpl_toolkits.mplot3d.art3d import Poly3DCollection
import numpy as np

NOTES = ("C4","D4","E4","G4","A4","C5","D5","E5")


def save_video_preview(video_path: Path, image_path: Path, seconds: float = 2.0):
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(seconds),
         "-i", str(video_path), "-frames:v", "1", str(image_path)],
        check=True,
    )


def gradient_rolloff_compensation(fft_f, fs, passes=2, floor=0.05):
    """Undo the sin(x)/x amplitude roll-off of repeated np.gradient differentiation.

    Each central-difference pass scales a component at frequency f by
    sinc(2 f / fs); the floor keeps near-Nyquist bins from exploding.
    """
    response = np.sinc(2.0 * np.asarray(fft_f) / fs) ** passes
    return np.maximum(response, floor)


def frequencies_from_dat(path):
    values=[]; active=False
    for line in path.read_text(errors="replace").splitlines():
        if "E I G E N V A L U E" in line: active=True; continue
        if active:
            match=re.match(r"\s*(\d+)\s+[\d.E+-]+\s+[\d.E+-]+\s+([\d.E+-]+)\s+",line)
            if match: values.append(float(match.group(2)))
            elif values and "P A R T I C I P A T I O N" in line: break
    return values


def modes_from_frd(path):
    modes=[]; frequency=None; reading=False; field={}
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("  100CL") and "MODAL" in line:
            parts=line.split(); frequency=float(parts[2])
        elif line.startswith(" -4  DISP"):
            reading=True; field={}
        elif reading and line.startswith(" -1"):
            node=int(line[3:13]); nums=re.findall(r"[-+]?\d\.\d+E[-+]\d+",line[13:])
            if len(nums)>=3: field[node]=tuple(map(float,nums[:3]))
        elif reading and line.startswith(" -3"):
            modes.append((frequency,field)); reading=False
    return modes


def dynamic_displacement(path, sensor_node):
    times=[]; values=[]; current=None; waiting=False
    for line in path.read_text(errors="replace").splitlines():
        match=re.search(r"displacements .* time\s+([\d.E+-]+)",line)
        if match: current=float(match.group(1)); waiting=True; continue
        if waiting:
            parts=line.split()
            if len(parts)>=4 and parts[0].isdigit() and int(parts[0])==sensor_node:
                times.append(current); values.append(float(parts[3])); waiting=False
    return np.array(times),np.array(values)


def save_mode(path,nodes,mode,number,frequency,top_z):
    ids=[n for n,xyz in nodes.items() if abs(xyz[2]-top_z)<1e-8 and n in mode]
    x=np.array([nodes[n][0] for n in ids]); y=np.array([nodes[n][1] for n in ids]); z=np.array([mode[n][2] for n in ids])
    z/=max(np.max(np.abs(z)),1e-15); tri=mtri.Triangulation(x,y)
    fig,ax=plt.subplots(figsize=(7.2,3.8),constrained_layout=True)
    contour=ax.tricontourf(tri,z,levels=np.linspace(-1,1,17),cmap="RdBu_r",extend="both")
    ax.tricontour(tri,z,levels=[0],colors="#263238",linewidths=.55)
    ax.fill_between([200,300],0,20,color="#333",alpha=.3,hatch="////")
    ax.scatter([200],[100],marker="D",s=35,color="#34d399",edgecolor="#111827")
    ax.set(xlim=(0,400),ylim=(200,0),aspect="equal",xlabel="x [mm]",ylabel="y [mm]",title=f"CalculiX C3D20R — Mode {number}: {frequency:.2f} Hz")
    fig.colorbar(contour,ax=ax,shrink=.78,label="normalized z displacement")
    fig.savefig(path,format="svg",metadata={"Date":None}); plt.close(fig)


def save_comparison(path,calc,old2d,old3d):
    count=12; y=np.arange(1,count+1); fig,ax=plt.subplots(figsize=(8,5),constrained_layout=True)
    ax.plot(old2d[:count],y,"o-",label="2D plate FDM",lw=1.5)
    ax.plot(old3d[:count],y,"s-",label="custom HEX8",lw=1.5)
    ax.plot(calc[:count],y,"D-",label="CalculiX C3D20R",lw=1.8)
    ax.set(xlabel="frequency [Hz]",ylabel="mode order",yticks=y,title="Eigenfrequency comparison (mode order only; MAC not yet applied)")
    ax.grid(True,alpha=.25); ax.legend(); fig.savefig(path,format="svg",metadata={"Date":None}); plt.close(fig)


def save_hit_animation(path,nodes,modes,metadata,duration=.06,frames=120):
    """Animate all eight point impacts from CalculiX mass-normalized modes."""
    top_z=metadata["sensor"]["xyz_mm"][2]
    top_ids=[n for n,xyz in nodes.items() if abs(xyz[2]-top_z)<1e-8 and all(n in mode for _,mode in modes)]
    x=np.array([nodes[n][0] for n in top_ids]); y=np.array([nodes[n][1] for n in top_ids]); tri=mtri.Triangulation(x,y)
    frequencies=np.array([f for f,_ in modes]); omega=2*np.pi*frequencies; damping=.012
    wd=omega*np.sqrt(1-damping**2); times=np.linspace(0,duration,frames)
    top_modes=np.array([[mode[n][2] for _,mode in modes] for n in top_ids])
    fields=[]
    for hit in metadata["hits"]:
        hit_modes=np.array([mode[hit["node"]][2] for _,mode in modes])
        modal=hit_modes[:,None]/wd[:,None]*np.exp(-damping*omega[:,None]*times)*np.sin(wd[:,None]*times)
        fields.append((top_modes@modal).T)
    common=max(np.max(np.abs(field)) for field in fields); fields=[field/max(common,1e-15) for field in fields]
    fig,axes=plt.subplots(2,4,figsize=(12.8,6.5),constrained_layout=True); artists=[]
    for ax,hit,field in zip(axes.ravel(),metadata["hits"],fields):
        artist=ax.tripcolor(tri,field[0],shading="gouraud",cmap="RdBu_r",vmin=-1,vmax=1)
        ax.fill_between([200,300],0,20,color="#333",alpha=.28,hatch="////")
        ax.scatter([hit["xyz_mm"][0]],[hit["xyz_mm"][1]],marker="*",s=65,color="#f5b942",edgecolor="#111827")
        ax.scatter([200],[100],marker="D",s=25,color="#34d399",edgecolor="#111827")
        ax.set(xlim=(0,400),ylim=(200,0),aspect="equal",xticks=[0,200,400],yticks=[0,100,200],title=f'{hit["note"]} ({hit["xyz_mm"][0]}, {hit["xyz_mm"][1]}) mm')
        artists.append(artist)
    title=fig.suptitle("CalculiX C3D20R impulse response — t = 0.00 ms (slow motion)")
    fig.colorbar(artists[0],ax=axes,shrink=.72,label="normalized z displacement (common scale)")
    def update(frame):
        for artist,field in zip(artists,fields): artist.set_array(field[frame])
        title.set_text(f"CalculiX C3D20R impulse response — t = {times[frame]*1000:.2f} ms (slow motion)")
        return [*artists,title]
    movie=animation.FuncAnimation(fig,update,frames=frames,interval=1000/24,blit=False)
    movie.save(path,writer=animation.FFMpegWriter(fps=24,codec="libx264",bitrate=2400,extra_args=["-pix_fmt","yuv420p"]))
    plt.close(fig)


def save_hit_perspective_animation(path,nodes,modes,metadata,duration=.06,frames=120,visual_z_mm=18.0):
    """Animate visibly exaggerated out-of-plane motion in an oblique view."""
    top_z=metadata["sensor"]["xyz_mm"][2]
    top_ids=[n for n,xyz in nodes.items() if abs(xyz[2]-top_z)<1e-8 and all(n in mode for _,mode in modes)]
    x=np.array([nodes[n][0] for n in top_ids]); y=np.array([nodes[n][1] for n in top_ids]); tri=mtri.Triangulation(x,y)
    frequencies=np.array([f for f,_ in modes]); omega=2*np.pi*frequencies; damping=.012
    wd=omega*np.sqrt(1-damping**2); times=np.linspace(0,duration,frames)
    top_modes=np.array([[mode[n][2] for _,mode in modes] for n in top_ids])
    fields=[]
    for hit in metadata["hits"]:
        hit_modes=np.array([mode[hit["node"]][2] for _,mode in modes])
        modal=hit_modes[:,None]/wd[:,None]*np.exp(-damping*omega[:,None]*times)*np.sin(wd[:,None]*times)
        fields.append((top_modes@modal).T)
    common=max(np.max(np.abs(field)) for field in fields); fields=[field/max(common,1e-15) for field in fields]
    triangles=tri.triangles; cmap=matplotlib.colormaps["RdBu_r"]; norm=colors.Normalize(-1,1)
    fig=plt.figure(figsize=(12.8,6.5),constrained_layout=True); axes=[]; surfaces=[]
    for index,(hit,field) in enumerate(zip(metadata["hits"],fields),1):
        ax=fig.add_subplot(2,4,index,projection="3d"); axes.append(ax)
        z=field[0]*visual_z_mm
        vertices=np.stack((x[triangles],y[triangles],z[triangles]),axis=2)
        surface=Poly3DCollection(vertices,linewidths=.08,edgecolors=(.1,.12,.15,.18))
        surface.set_facecolor(cmap(norm(field[0][triangles].mean(axis=1)))); ax.add_collection3d(surface); surfaces.append(surface)
        ax.plot([200,300,300,200,200],[0,0,20,20,0],[visual_z_mm*1.08]*5,color="#222",lw=2.2)
        ax.scatter([hit["xyz_mm"][0]],[hit["xyz_mm"][1]],[visual_z_mm*1.22],marker="*",s=42,color="#f5b942",edgecolor="#111827")
        ax.scatter([200],[100],[visual_z_mm*1.22],marker="D",s=20,color="#34d399",edgecolor="#111827")
        ax.set(xlim=(0,400),ylim=(200,0),zlim=(-visual_z_mm*1.35,visual_z_mm*1.35),xticks=[0,200,400],yticks=[0,100,200],zticks=[-visual_z_mm,0,visual_z_mm],title=f'{hit["note"]} ({hit["xyz_mm"][0]}, {hit["xyz_mm"][1]}) mm')
        ax.set_zticklabels(["−1","0","+1"]); ax.set_box_aspect((2,1,.34)); ax.view_init(elev=27,azim=-62)
    scalar=cm.ScalarMappable(norm=norm,cmap=cmap); scalar.set_array([])
    fig.colorbar(scalar,ax=axes,shrink=.60,pad=.02,label="amplified normalized z displacement (shared scale)")
    title=fig.suptitle(f"CalculiX C3D20R perspective deflection - t = 0.00 ms (visual amplitude +/-{visual_z_mm:g} mm)")
    def update(frame):
        for surface,field in zip(surfaces,fields):
            z=field[frame]*visual_z_mm
            surface.set_verts(np.stack((x[triangles],y[triangles],z[triangles]),axis=2))
            surface.set_facecolor(cmap(norm(field[frame][triangles].mean(axis=1))))
        title.set_text(f"CalculiX C3D20R perspective deflection - t = {times[frame]*1000:.2f} ms (visual amplitude +/-{visual_z_mm:g} mm)")
        return [*surfaces,title]
    movie=animation.FuncAnimation(fig,update,frames=frames,interval=1000/24,blit=False)
    movie.save(path,writer=animation.FFMpegWriter(fps=24,codec="libx264",bitrate=3000,extra_args=["-pix_fmt","yuv420p"]))
    plt.close(fig)


def main():
    parser=argparse.ArgumentParser(); parser.add_argument("--output",type=Path,required=True); parser.add_argument("--reference",type=Path,required=True); parser.add_argument("--long-videos-only",action="store_true")
    args=parser.parse_args(); meta=json.loads((args.output/"model-metadata.json").read_text()); nodes={int(k):v for k,v in json.loads((args.output/"mesh-nodes.json").read_text()).items()}
    frequencies=frequencies_from_dat(args.output/"acrylic_pan.dat"); modes=modes_from_frd(args.output/"acrylic_pan.frd")
    if args.long_videos_only:
        save_hit_animation(args.output/"calculix-eight-hits-long.mp4",nodes,modes,meta,duration=.32,frames=240)
        save_hit_perspective_animation(args.output/"calculix-eight-hits-perspective-long.mp4",nodes,modes,meta,duration=.32,frames=240)
        save_video_preview(args.output/"calculix-eight-hits-long.mp4",args.output/"calculix-eight-hits-preview.png")
        save_video_preview(args.output/"calculix-eight-hits-perspective-long.mp4",args.output/"calculix-eight-hits-perspective-preview.png")
        print(json.dumps({"duration_ms":320,"frames":240,"frequencies_hz":frequencies[:3]}))
        return
    top_z=meta["sensor"]["xyz_mm"][2]
    for index,(frequency,mode) in enumerate(modes[:8],1): save_mode(args.output/f"calculix-mode-{index}.svg",nodes,mode,index,frequency,top_z)
    signals=[]; time_ref=None
    for note in NOTES:
        t,u=dynamic_displacement(args.output/f"hit_{note.lower()}.dat",meta["sensor"]["node"])
        if len(t)<100: raise RuntimeError(f"Insufficient dynamic samples for {note}: {len(t)}")
        time_ref=t; acceleration=np.gradient(np.gradient(u,t),t); signals.append(acceleration)
    common=max(np.max(np.abs(s)) for s in signals); window=np.hanning(len(time_ref)); fft_f=np.fft.rfftfreq(len(time_ref),np.median(np.diff(time_ref)))
    compensation=gradient_rolloff_compensation(fft_f,1/np.median(np.diff(time_ref)))
    spectra=[np.abs(np.fft.rfft(s*window))*2/max(window.sum(),1e-15)/compensation for s in signals]; fft_common=max(np.max(s) for s in spectra)
    hits=[]
    for item,signal,spectrum in zip(meta["hits"],signals,spectra):
        hits.append({"note":item["note"],"x_mm":item["xyz_mm"][0],"y_mm":item["xyz_mm"][1],
                     "waveform":np.round(signal/common,6).tolist(),"fft":np.round(spectrum/fft_common,6).tolist(),
                     "peak_relative":round(float(np.max(np.abs(signal))/common),6)})
    sensor={"model":"CalculiX 2.20 C3D20R modal dynamic","sample_rate_hz":round(float(1/np.median(np.diff(time_ref))),6),
            "sample_count":len(time_ref),"duration_ms":round(float(time_ref[-1]*1000),4),"damping_ratio":.012,
            "quantity":"centre sensor Z acceleration from CalculiX displacement","units":"normalized common scale across 8 hits",
            "time_ms":np.round(time_ref*1000,5).tolist(),"frequency_hz":np.round(fft_f,5).tolist(),"hits":hits}
    (args.output/"sensor-response-calculix.json").write_text(json.dumps(sensor,ensure_ascii=False,indent=2),encoding="utf-8")
    (args.output/"sensor-response-calculix.js").write_text("window.ACRYLIC_SENSOR_DATA = "+json.dumps(sensor,ensure_ascii=False,separators=(",",":"))+";\n",encoding="utf-8")
    old2d=json.loads((args.reference/"results.json").read_text())["frequencies_hz"]
    old3d=json.loads((args.reference/"solid3d/solid3d-results.json").read_text())["frequencies_hz"]
    save_comparison(args.output/"frequency-comparison.svg",frequencies,old2d,old3d)
    save_hit_animation(args.output/"calculix-eight-hits.mp4",nodes,modes,meta)
    save_hit_perspective_animation(args.output/"calculix-eight-hits-perspective.mp4",nodes,modes,meta)
    save_hit_animation(args.output/"calculix-eight-hits-long.mp4",nodes,modes,meta,duration=.32,frames=240)
    save_hit_perspective_animation(args.output/"calculix-eight-hits-perspective-long.mp4",nodes,modes,meta,duration=.32,frames=240)
    save_video_preview(args.output/"calculix-eight-hits-long.mp4",args.output/"calculix-eight-hits-preview.png")
    save_video_preview(args.output/"calculix-eight-hits-perspective-long.mp4",args.output/"calculix-eight-hits-perspective-preview.png")
    result={**meta,"frequencies_hz":frequencies,"outputs":{"modes":min(8,len(modes)),"dynamic_samples":len(time_ref),"frequency_resolution_hz":round(float(fft_f[1]-fft_f[0]),6)}}
    (args.output/"calculix-results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"frequencies_hz":frequencies[:12],"samples":len(time_ref)}))


if __name__=="__main__": main()
