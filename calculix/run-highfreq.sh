#!/bin/bash
set -euo pipefail
OUT=${1:-/work}
mkdir -p "$OUT"
python3 /solver/calculix/generate_highfreq_model.py --output "$OUT"
cd "$OUT"
ccx acrylic_pan_hf 2>&1 | tee highfreq-run.log
for note in c4 d4 e4 g4 a4 c5 d5 e5; do
  cp acrylic_pan_hf.eig "hf_${note}.eig"
  ccx "hf_${note}" 2>&1 | tee "hf_${note}-run.log"
  rm -f "hf_${note}.eig"
done
python3 /solver/calculix/postprocess_highfreq.py --output "$OUT"
echo "CalculiX high-frequency analyses completed"
