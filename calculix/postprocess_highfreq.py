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

def rms_pair_distances(features):
    """Return RMS feature distance and the closest pair for eight templates."""
    pairs=np.triu_indices(len(features),1)
    values=np.sqrt(np.mean((features[pairs[0]]-features[pairs[1]])**2,axis=1))
    closest=int(np.argmin(values))
    return float(values[closest]),float(values.mean()),[NOTES[pairs[0][closest]],NOTES[pairs[1][closest]]]

def e4_relative_band_features(spectra,fft_f,low_hz,bands=18,shape_only=False):
    """Compress spectra to equal-width log-energy bands and reference them to E4."""
    edges=np.linspace(low_hz,5000,bands+1)
    compressed=[]
    for spectrum in spectra:
        values=[]
        for left,right in zip(edges[:-1],edges[1:]):
            use=(fft_f>=left)&(fft_f<(right if right<5000 else right+1e-9))
            values.append(np.sqrt(np.mean(spectrum[use]**2)) if np.any(use) else 0.0)
        values=np.asarray(values)
        if shape_only:
            values=values/max(float(values.sum()),1e-30)
        compressed.append(np.log10(np.maximum(values,1e-30)))
    compressed=np.asarray(compressed)
    relative=compressed-compressed[NOTES.index("E4")]
    return relative,edges,compressed

def main():
    p=argparse.ArgumentParser(); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    meta=json.loads((a.output/"highfreq-metadata.json").read_text()); sensor_node=meta["sensor"]["node"]; fs=meta["sampling"]["hz"]
    freqs=frequencies_from_dat(a.output/"acrylic_pan_hf.dat"); times=[]; raw=[]
    for note in NOTES:
        t,u=dynamic_displacement(a.output/f"hf_{note.lower()}.dat",sensor_node); times=t; raw.append(np.gradient(np.gradient(u,t),t))
    raw=np.array(raw); cutoffs=[0,100,250,500,750,1000]; scores=[]; e4_scores=[]; spectra_by_cutoff={}; fft_f=np.fft.rfftfreq(raw.shape[1],1/fs); window=np.hanning(raw.shape[1])
    for hp in cutoffs:
        sos=signal.butter(4,[max(hp,20),5000],btype="bandpass",fs=fs,output="sos")
        filtered=signal.sosfiltfilt(sos,raw,axis=1)
        spectra=np.abs(np.fft.rfft(filtered*window,axis=1)); band=(fft_f>=max(hp,100))&(fft_f<=5000)
        lo,mean=cosine_distances(spectra[:,band]); scores.append({"highpass_hz":hp,"minimum_cosine_distance":lo,"mean_cosine_distance":mean}); spectra_by_cutoff[hp]=spectra
        low=max(hp,100)
        level_features,edges,level_unreferenced=e4_relative_band_features(spectra,fft_f,low,shape_only=False)
        shape_features,_,_=e4_relative_band_features(spectra,fft_f,low,shape_only=True)
        level_min,level_mean,level_pair=rms_pair_distances(level_features)
        shape_min,shape_mean,shape_pair=rms_pair_distances(shape_features)
        translation_error=float(np.max(np.abs(
            np.sqrt(np.mean((level_features[:,None]-level_features[None,:])**2,axis=2))-
            np.sqrt(np.mean((level_unreferenced[:,None]-level_unreferenced[None,:])**2,axis=2))
        )))
        e4_scores.append({
            "highpass_hz":hp,"features":18,"band_range_hz":[float(edges[0]),float(edges[-1])],
            "level_and_shape":{"minimum_rms_log10_distance":level_min,"mean_rms_log10_distance":level_mean,"closest_pair":level_pair},
            "shape_only":{"minimum_rms_log10_distance":shape_min,"mean_rms_log10_distance":shape_mean,"closest_pair":shape_pair},
            "baseline_translation_distance_error":translation_error
        })
    spectra=spectra_by_cutoff[100]; common=max(float(spectra.max()),1e-15)
    fig,ax=plt.subplots(figsize=(9,5),constrained_layout=True)
    for note,s in zip(NOTES,spectra): ax.plot(fft_f,s/common,lw=1,label=note)
    ax.set(xlim=(0,5600),ylim=(0,1.03),xlabel="frequency [Hz]",ylabel="normalized acceleration spectrum",title="CalculiX high-frequency response - HPF 100 Hz / LPF 5 kHz / 50 ms"); ax.grid(alpha=.22); ax.legend(ncol=4)
    fig.savefig(a.output/"highfreq-sensor-fft.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    fig,ax=plt.subplots(figsize=(8,4.5),constrained_layout=True); x=[s["highpass_hz"] for s in scores]
    ax.plot(x,[s["minimum_cosine_distance"] for s in scores],"o-",label="worst pair"); ax.plot(x,[s["mean_cosine_distance"] for s in scores],"s-",label="mean pair")
    ax.set(xlabel="high-pass cutoff [Hz]",ylabel="cosine distance of 8 hit spectra",title="Preliminary hit separability versus HPF cutoff"); ax.grid(alpha=.25); ax.legend()
    fig.savefig(a.output/"highpass-separability.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    fig,axes=plt.subplots(2,1,figsize=(9,7),sharex=True,constrained_layout=True)
    for ax,key,title in zip(axes,["level_and_shape","shape_only"],["level + spectral shape","shape only (per-hit energy normalized)"]):
        ax.plot(x,[s[key]["minimum_rms_log10_distance"] for s in e4_scores],"o-",label="worst pair")
        ax.plot(x,[s[key]["mean_rms_log10_distance"] for s in e4_scores],"s-",label="mean pair")
        ax.set(ylabel="RMS log10 band-ratio distance",title=title); ax.grid(alpha=.25); ax.legend()
    axes[-1].set_xlabel("high-pass cutoff [Hz]")
    fig.suptitle("E4-referenced 18-band feature separability")
    fig.savefig(a.output/"e4-baseline-feature-separability.svg",format="svg",metadata={"Date":None}); plt.close(fig)
    result={**meta,"computed_modes":len(freqs),"frequencies_hz":freqs,"maximum_computed_hz":freqs[-1] if freqs else None,"highpass_scores":scores,"e4_baseline_18_band_scores":e4_scores,"true_fft_resolution_hz":fs/raw.shape[1]}
    (a.output/"highfreq-results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"modes":len(freqs),"max_hz":freqs[-1] if freqs else None,"samples":raw.shape[1],"scores":scores,"e4_scores":e4_scores}))
if __name__=="__main__": main()
