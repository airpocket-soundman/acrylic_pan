"""Postprocess the 25.6 kHz / 50 ms CalculiX sensor responses."""

import argparse,json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy import signal
from postprocess import frequencies_from_dat,dynamic_displacement,NOTES

def cosine_distances(features):
    f=features/np.maximum(np.linalg.norm(features,axis=1,keepdims=True),1e-15); d=1-f@f.T
    values=d[np.triu_indices(len(f),1)]; return float(values.min()),float(values.mean())

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    meta=json.loads((a.output/"highfreq-metadata.json").read_text()); sensor_node=meta["sensor"]["node"]; fs=meta["sampling"]["hz"]
    freqs=frequencies_from_dat(a.output/"acrylic_pan_hf.dat"); times=[]; raw=[]
    for note in NOTES:
        t,u=dynamic_displacement(a.output/f"hf_{note.lower()}.dat",sensor_node); times=t; raw.append(np.gradient(np.gradient(u,t),t))
    raw=np.array(raw); cutoffs=[0,100,250,500,750,1000]; scores=[]; spectra_by_cutoff={}; fft_f=np.fft.rfftfreq(raw.shape[1],1/fs); window=np.hanning(raw.shape[1])
    for hp in cutoffs:
        sos=signal.butter(4,[max(hp,20),5000],btype="bandpass",fs=fs,output="sos")
        filtered=signal.sosfiltfilt(sos,raw,axis=1)
        spectra=np.abs(np.fft.rfft(filtered*window,axis=1)); band=(fft_f>=max(hp,100))&(fft_f<=5000)
        lo,mean=cosine_distances(spectra[:,band]); scores.append({"highpass_hz":hp,"minimum_cosine_distance":lo,"mean_cosine_distance":mean}); spectra_by_cutoff[hp]=spectra
    spectra=spectra_by_cutoff[100]; common=max(float(spectra.max()),1e-15)
    fig,ax=plt.subplots(figsize=(9,5),constrained_layout=True)
    for note,s in zip(NOTES,spectra): ax.plot(fft_f,s/common,lw=1,label=note)
    ax.set(xlim=(0,5600),ylim=(0,1.03),xlabel="frequency [Hz]",ylabel="normalized acceleration spectrum",title="CalculiX high-frequency response - HPF 100 Hz / LPF 5 kHz / 50 ms"); ax.grid(alpha=.22); ax.legend(ncol=4)
    fig.savefig(a.output/"highfreq-sensor-fft.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4.5),constrained_layout=True); x=[s["highpass_hz"] for s in scores]
    ax.plot(x,[s["minimum_cosine_distance"] for s in scores],"o-",label="worst pair"); ax.plot(x,[s["mean_cosine_distance"] for s in scores],"s-",label="mean pair")
    ax.set(xlabel="high-pass cutoff [Hz]",ylabel="cosine distance of 8 hit spectra",title="Preliminary hit separability versus HPF cutoff"); ax.grid(alpha=.25); ax.legend()
    fig.savefig(a.output/"highpass-separability.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    result={**meta,"computed_modes":len(freqs),"frequencies_hz":freqs,"maximum_computed_hz":freqs[-1] if freqs else None,"highpass_scores":scores,"true_fft_resolution_hz":fs/raw.shape[1]}
    (a.output/"highfreq-results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"modes":len(freqs),"max_hz":freqs[-1] if freqs else None,"samples":raw.shape[1],"scores":scores}))
if __name__=="__main__": main()
