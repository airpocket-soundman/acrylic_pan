#!/bin/bash
set -euo pipefail
OUT=${1:-/work}
mkdir -p "$OUT"
python3 /solver/calculix/generate_model.py --output "$OUT"
cd "$OUT"
ccx acrylic_pan 2>&1 | tee calculix-run.log
for note in c4 d4 e4 g4 a4 c5 d5 e5; do
  cp acrylic_pan.eig "hit_${note}.eig"
  ccx "hit_${note}" 2>&1 | tee "hit_${note}-run.log"
done
python3 /solver/calculix/postprocess.py --output "$OUT" --reference /reference
echo "CalculiX modal and dynamic analyses completed"
