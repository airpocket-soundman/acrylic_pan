#!/bin/bash
set -euo pipefail
OUT=${1:-/work}
THICKNESS_MM=${2:-3}
mkdir -p "$OUT"
python3 /solver/calculix/generate_xy_grid_model.py --output "$OUT" --thickness-mm "$THICKNESS_MM"
cd "$OUT"
ccx acrylic_pan_xy 2>&1 | tee xy-grid-run.log
for input in xy_p*.inp; do
  job=${input%.inp}
  cp acrylic_pan_xy.eig "${job}.eig"
  ccx "$job" 2>&1 | tee "${job}-run.log"
  rm -f "${job}.eig"
done
python3 /solver/calculix/postprocess_xy_grid.py --output "$OUT"
echo "CalculiX XY-grid analyses completed"
