#!/bin/sh
set -eu
python -m sim.modal_plate --output /workspace/web/assets/simulation
python -m sim.solid_fem --output /workspace/web/assets/simulation/solid3d
python -m sim.solist_feasibility \
  --modal-results /workspace/web/assets/simulation/results.json \
  --output /workspace/web/assets/simulation/solist-feasibility.json
