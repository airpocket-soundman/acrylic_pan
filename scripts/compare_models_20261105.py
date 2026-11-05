"""Compare cumulative models using exact totals on independent evaluation data."""
import json
import sys
from pathlib import Path
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.evaluate_device_model import evaluate
from sim.pc_position_grid_runtime import discover_session_ids

SETS = ['evaluation_raspi_20261101', 'evaluation_raspi_20261102',
        'evaluation_raspi_20261102_set3', 'evaluation_raspi_20261103']
MODELS = {15000: 'raspi-20261103-train15000', 18000: 'raspi-20261104-train18000',
          21000: 'raspi-20261105-train21000'}

def exact(frame, event, arg):
    if event == 'return' and frame.f_code is evaluate.__code__:
        d = frame.f_locals
        counts.append(dict(count=len(d['truth']), correct=int(np.sum(d['predicted'] == d['truth'])),
                           area_correct=int(d['area_hit'].sum()),
                           within25=int(np.sum(d['distance'] <= 25)),
                           distance_sum=float(d['distance'].sum())))

if __name__ == '__main__':
    report = {}
    for size, folder in MODELS.items():
        model = ROOT / 'artifacts' / folder / 'device_position/model.npz'
        training = json.loads(model.with_name('training_report.json').read_text())
        counts = []
        sys.setprofile(exact)
        for name in SETS:
            source = ROOT / 'data/raw' / name
            ids = discover_session_ids(source)
            assert not set(ids).intersection(training['session_ids'])
            evaluate(model, source, ids)
        sys.setprofile(None)
        totals = {key: sum(row[key] for row in counts) for key in counts[0]}
        assert totals['count'] == 2400
        report[size] = dict(sets=dict(zip(SETS, counts)), totals=totals)
        print(size, totals, flush=True)
    (ROOT / 'docs/model-comparison-20261105.json').write_text(json.dumps(report, indent=2) + '\n')
