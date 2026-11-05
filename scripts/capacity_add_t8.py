"""Add T8 to the validated capacity comparison, preserving cached T1-T7."""
import json
import hashlib
import numpy as np
from sklearn.preprocessing import StandardScaler
from threadpoolctl import threadpool_limits
from capacity_comparison import ROOT, OUT, EVAL, features, metrics, _engine_inputs, _teacher_probabilities, _expanded_alpha, _fit
from sim.pc_position_grid_runtime import discover_session_ids
from sim.dummy_model_pipeline import to_bfloat16_bits

IDS = ('20261105_194325_3c60eb92', '20261105_194942_0523c534')

def main():
    new = features(ROOT/'data/raw/sessions', IDS, OUT/'t8_features.npz')
    assert len(new['xy']) == 3000
    previous = dict(np.load(OUT/'train_features.npz', allow_pickle=False))
    train = {k: np.concatenate([previous[k], new[k]]) for k in ('edge','rich','xy','sessions')}
    assert len(train['xy']) == 24000
    evaluations = [features(ROOT/'data/raw'/n, discover_session_ids(ROOT/'data/raw'/n), OUT/(n+'.npz')) for n in EVAL]
    assert not set(train['sessions']).intersection(np.concatenate([d['sessions'] for d in evaluations]))
    exy, ee = (np.concatenate([d[k] for d in evaluations]) for k in ('xy','edge'))
    assert len(exy) == 2400
    support, labels = np.unique(train['xy'], axis=0, return_inverse=True)
    scaler = StandardScaler().fit(train['edge'][:, :127])
    inputs, einputs = _engine_inputs(train['edge'], scaler), _engine_inputs(ee, scaler)
    teacher_path = OUT/'24000_teacher.npy'
    if teacher_path.exists():
        teacher = np.load(teacher_path)
    else:
        print('validated T8=3000, cumulative=24000; teacher training', flush=True)
        mask = np.ones(24000, dtype=bool)
        teacher = _teacher_probabilities(train['rich'], labels, mask, mask)
        np.save(teacher_path, teacher)
    targets = .75*np.eye(60, dtype=np.float32)[labels] + .25*teacher.astype(np.float32)
    report = json.loads((OUT/'report.json').read_text())
    report['rows'] = [r for r in report['rows'] if r['samples'] != 24000]
    for hidden in (32,48,64):
        for seed in ((1,) if hidden==32 else (7,21,42)):
            alpha = _expanded_alpha(hidden, seed)
            path = OUT/f'24000_h{hidden}_s{seed}.npz'
            if path.exists():
                beta = np.load(path)['beta']
            else:
                beta = _fit(inputs, targets, alpha, 160)
                np.savez_compressed(path, alpha=alpha, beta=beta, mean=scaler.mean_, scale=scaler.scale_, support=support)
            row = dict(samples=24000, hidden=hidden, seed=seed, **metrics(exy,einputs,alpha,beta,support))
            report['rows'].append(row)
            print(row, flush=True)
            (OUT/'report_t8.json').write_text(json.dumps(report,indent=2))
            if hidden == 32:
                folder=ROOT/'artifacts/raspi-20261105-train24000/device_position'
                folder.mkdir(parents=True,exist_ok=True)
                np.savez_compressed(folder/'model.npz', alpha_bfloat16=to_bfloat16_bits(alpha), beta_bfloat16=to_bfloat16_bits(beta),feature_mean=scaler.mean_.astype(np.float32),feature_scale=scaler.scale_.astype(np.float32),support_xy_mm=support.astype(np.float32))
                (folder/'training_report.json').write_text(json.dumps(dict(sample_count=24000,session_ids=list(dict.fromkeys(train['sessions'])),epochs=160,distillation_weight=.25,model_sha256=hashlib.sha256((folder/'model.npz').read_bytes()).hexdigest()),indent=2))
    lines=['# T8追加後のユニットサイズ比較', '', 'PCシミュレーション、入力128・出力60固定。48/64は仮生成αを使う3系列平均。評価は独立E1〜E4の2,400打。', '', '| 学習打数 | 隠れ | 位置精度（平均・範囲） | エリア精度 | 平均距離誤差 | 25 mm以内 |','|---:|---:|---:|---:|---:|---:|']
    for size in range(3000,24001,3000):
        for hidden in (32,48,64):
            rows=[r for r in report['rows'] if r['samples']==size and r['hidden']==hidden]
            avg=lambda k:np.mean([r[k] for r in rows])
            lo,hi=min(r['top1'] for r in rows),max(r['top1'] for r in rows)
            lines.append(f'| {size:,} | {hidden} | {avg("top1")*100:.3f}%（{lo*100:.3f}–{hi*100:.3f}） | {avg("area")*100:.3f}% | {avg("mean_mm"):.3f} mm | {avg("within25")*100:.3f}% |')
    (ROOT/'docs/capacity-comparison-t8-20261105.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':
    with threadpool_limits(limits=1): main()
