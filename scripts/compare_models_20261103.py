"""Compare four saved final models on the same three independent evaluation sets."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.evaluate_device_model import evaluate
from sim.pc_position_grid_runtime import discover_session_ids

MODELS = {
    3000: 'raspi-20261101-train3000',
    6000: 'raspi-20261102-train6000',
    9000: 'raspi-20261102-train9000',
    12000: 'raspi-20261103-train12000',
    15000: 'raspi-20261103-train15000',
    18000: 'raspi-20261104-train18000',
}
EVAL = ['evaluation_raspi_20261101', 'evaluation_raspi_20261102', 'evaluation_raspi_20261102_set3', 'evaluation_raspi_20261103']

def main():
    total = 600 * len(EVAL)
    report = {'protocol': 'docs/dataset-evaluation-protocol-20261103.md', 'evaluation_count': total, 'models': {}}
    for count, folder in MODELS.items():
        model = ROOT / 'artifacts' / folder / 'device_position/model.npz'
        training = json.loads((model.parent / 'training_report.json').read_text(encoding='utf-8'))
        rows = []
        for name in EVAL:
            source = ROOT / 'data/raw' / name
            ids = discover_session_ids(source)
            assert not set(ids).intersection(training['session_ids'])
            result = evaluate(model, source, ids)
            assert result['all']['count'] == 600
            rows.append({'dataset': name, 'sessions': list(ids), **result['all']})
        aggregate = {key: sum(row[key] * row['count'] for row in rows) / total
                     for key in ('top1_position_accuracy', 'area_accuracy', 'mean_distance_mm', 'within_25mm')}
        report['models'][str(count)] = {'sets': rows, 'aggregate': aggregate}
        print(count, json.dumps(aggregate), flush=True)
    output = ROOT / 'docs/model-comparison-20261103.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

if __name__ == '__main__':
    main()
