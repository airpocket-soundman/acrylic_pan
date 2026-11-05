"""Cumulative-data capacity experiment; PC simulation, not official Simulator."""
import json
import sys
from pathlib import Path
import numpy as np
from scipy.special import softmax
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sim.pc_position_grid_runtime import load_position_dataset, discover_session_ids, extract_grid_features
from sim.real_model_pipeline import extract_hybrid_features
from sim.device_position_probability import _teacher_probabilities, _engine_inputs, bfloat_logits
from sim.position_model_size_experiment import _expanded_alpha, _fit
from sim.dummy_model_pipeline import from_bfloat16_bits

OUT = ROOT / 'artifacts/capacity_20261105'
FOLDERS = ['raspi-20261101-train3000', 'raspi-20261102-train6000',
           'raspi-20261102-train9000', 'raspi-20261103-train12000',
           'raspi-20261103-train15000', 'raspi-20261104-train18000',
           'raspi-20261105-train21000']
EVAL = ['evaluation_raspi_20261101', 'evaluation_raspi_20261102',
        'evaluation_raspi_20261102_set3', 'evaluation_raspi_20261103']

def features(root, ids, target):
    if target.exists():
        return dict(np.load(target, allow_pickle=False))
    d = load_position_dataset(root, tuple(ids))
    result = dict(edge=np.stack([extract_hybrid_features(x) for x in d.samples]),
                  rich=np.stack([extract_grid_features(x) for x in d.samples]),
                  xy=d.xy_mm, sessions=d.session_ids, sha=np.array(d.dataset_sha256))
    np.savez_compressed(target, **result)
    return result

def metrics(xy, inputs, alpha, beta, support):
    p = softmax(bfloat_logits(inputs, alpha, beta) / .05, axis=1)
    truth = np.argmin(np.linalg.norm(xy[:, None] - support[None], axis=2), axis=1)
    area = lambda x: (x[:, 1] // 100 * 4 + x[:, 0] // 100).astype(int)
    ap = np.stack([p[:, area(support) == i].sum(axis=1) for i in range(12)], axis=1)
    distance = np.linalg.norm(p @ support - xy, axis=1)
    return dict(count=len(xy), correct=int(np.sum(p.argmax(axis=1) == truth)),
                area_correct=int(np.sum(ap.argmax(axis=1) == area(xy))),
                top1=float(np.mean(p.argmax(axis=1) == truth)),
                area=float(np.mean(ap.argmax(axis=1) == area(xy))),
                mean_mm=float(distance.mean()), within25=float(np.mean(distance <= 25)))

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    latest = json.loads((ROOT/'artifacts'/FOLDERS[-1]/'device_position/training_report.json').read_text())
    train = features(ROOT/'data/raw/sessions', latest['session_ids'], OUT/'train_features.npz')
    evaluations = [features(ROOT/'data/raw'/name, discover_session_ids(ROOT/'data/raw'/name), OUT/(name+'.npz')) for name in EVAL]
    exy = np.concatenate([d['xy'] for d in evaluations])
    ee = np.concatenate([d['edge'] for d in evaluations])
    eval_ids = set(np.concatenate([d['sessions'] for d in evaluations]))
    results = []
    for folder in FOLDERS:
        source = ROOT/'artifacts'/folder/'device_position'
        report = json.loads((source/'training_report.json').read_text())
        assert not eval_ids.intersection(report['session_ids'])
        mask = np.isin(train['sessions'], report['session_ids'])
        edge, rich, xy = train['edge'][mask], train['rich'][mask], train['xy'][mask]
        assert len(xy) == report['sample_count']
        support, labels = np.unique(xy, axis=0, return_inverse=True)
        base = np.load(source/'model.npz')
        scaler = StandardScaler().fit(edge[:, :127])
        assert np.allclose(scaler.mean_, base['feature_mean'])
        inputs, einputs = _engine_inputs(edge, scaler), _engine_inputs(ee, scaler)
        baseline = metrics(exy, _engine_inputs(ee, type('Scaler', (), {'transform': lambda self,x:(x-base['feature_mean'])/base['feature_scale']})()),
                           from_bfloat16_bits(base['alpha_bfloat16']), from_bfloat16_bits(base['beta_bfloat16']), support)
        results.append(dict(samples=len(xy), hidden=32, seed=1, **baseline))
        teacher_path = OUT/(str(len(xy))+'_teacher.npy')
        if teacher_path.exists():
            teacher = np.load(teacher_path)
        else:
            print('teacher', len(xy), flush=True)
            allrows = np.ones(len(xy), dtype=bool)
            teacher = _teacher_probabilities(rich, labels, allrows, allrows)
            np.save(teacher_path, teacher)
        targets = .75*np.eye(60, dtype=np.float32)[labels] + .25*teacher.astype(np.float32)
        for hidden in (48, 64):
            for seed in (7, 21, 42):
                target = OUT/f'{len(xy)}_h{hidden}_s{seed}.npz'
                alpha = _expanded_alpha(hidden, seed)
                if target.exists():
                    beta = np.load(target)['beta']
                else:
                    beta = _fit(inputs, targets, alpha, 160)
                    np.savez_compressed(target, alpha=alpha, beta=beta, mean=scaler.mean_, scale=scaler.scale_, support=support)
                row = dict(samples=len(xy), hidden=hidden, seed=seed, **metrics(exy, einputs, alpha, beta, support))
                results.append(row)
                print(row, flush=True)
                (OUT/'report.json').write_text(json.dumps(dict(method='PC simulation; appended random BF16 alpha; seeds 7,21,42; 160 epochs; 25% distilled targets; independent E1-E4', rows=results), indent=2))
    lines = ['# 累積データ数と隠れ層容量の比較', '', 'PCシミュレーション。48/64は公式αの32列を保持してランダム列を追加。3系列平均。評価は独立E1〜E4の2,400打。入力128・出力60は固定。', '', '| 学習打数 | 隠れ | 位置精度（平均・範囲） | エリア精度 | 平均距離誤差 | 25 mm以内 |', '|---:|---:|---:|---:|---:|---:|']
    for size in range(3000, 21001, 3000):
        for hidden in (32,48,64):
            rows = [r for r in results if r['samples']==size and r['hidden']==hidden]
            avg = lambda k: np.mean([r[k] for r in rows])
            lo,hi=min(r['top1'] for r in rows),max(r['top1'] for r in rows)
            lines.append(f'| {size:,} | {hidden} | {avg("top1")*100:.3f}%（{lo*100:.3f}–{hi*100:.3f}） | {avg("area")*100:.3f}% | {avg("mean_mm"):.3f} mm | {avg("within25")*100:.3f}% |')
    (ROOT/'docs/capacity-comparison-20261105.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')

if __name__ == '__main__':
    with threadpool_limits(limits=1):
        main()
