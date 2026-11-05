"""Export validated 32-unit T8 model and matching ODL prior from cached features."""
import sys
import zlib
from pathlib import Path
import numpy as np
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from sim.device_position_probability import write_header, bfloat_logits, _engine_inputs
from sim.dummy_model_pipeline import quantize_bfloat16, from_bfloat16_bits
from sim.position_odl_calibration_prior import initial_p, PRIOR_RIDGE, CALIBRATION_WEIGHT, _c_float_array

folder=ROOT/'artifacts/raspi-20261105-train24000'
m=np.load(folder/'device_position/model.npz')
a,b=from_bfloat16_bits(m['alpha_bfloat16']),from_bfloat16_bits(m['beta_bfloat16'])
cache=ROOT/'artifacts/capacity_20261105'
edge=np.concatenate([np.load(cache/name)['edge'] for name in ('train_features.npz','t8_features.npz')])
scaler=StandardScaler().fit(edge[:,:127])
assert len(edge)==24000 and np.allclose(scaler.mean_,m['feature_mean'])
scaler.mean_,scaler.scale_=m['feature_mean'],m['feature_scale']
inputs=_engine_inputs(edge,scaler)
golden=quantize_bfloat16(inputs[np.linspace(0,len(inputs)-1,4,dtype=int)])
header=ROOT/'firmware/AcrylicPanCollector/generated/apan_position_probability_model.h'
write_header(header,scaler,a,b,m['support_xy_mm'],golden,bfloat_logits(golden,a,b))
hidden=np.clip(.2*(quantize_bfloat16(inputs).astype(np.float64)@a.astype(np.float64))+.5,0,1)
p=initial_p(hidden,PRIOR_RIDGE,CALIBRATION_WEIGHT).astype(np.float32)
crc=zlib.crc32(m['beta_bfloat16'].astype('<u2').tobytes())&0xffffffff
text=f'''/* Generated for T8: 24000 factory events. */
#ifndef APAN_CALIBRATION_PRIOR_H
#define APAN_CALIBRATION_PRIOR_H
#include <stdint.h>
#define APAN_CALIBRATION_HIDDEN_SIZE 32U
#define APAN_CALIBRATION_OUTPUT_SIZE 60U
#define APAN_CALIBRATION_MODEL_CRC32 (0x{crc:08X}UL)
'''+_c_float_array('apan_calibration_p0',p)+'#endif\n'
(ROOT/'firmware/AcrylicPanCollector/generated/apan_calibration_prior.h').write_text(text,encoding='ascii')
print('exported 128-32-60, 24000 events; CRC',hex(crc))
