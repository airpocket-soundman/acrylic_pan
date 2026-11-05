"""Plot independent evaluation accuracy and coordinate error against training size."""
import json
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager

ROOT = Path(__file__).resolve().parents[1]
report = json.loads((ROOT / 'docs/model-comparison-20261103.json').read_text(encoding='utf-8'))
font = next((f for f in ['Yu Gothic', 'Meiryo', 'Noto Sans CJK JP'] if f in {x.name for x in font_manager.fontManager.ttflist}), 'DejaVu Sans')
plt.rcParams.update({'font.family': font, 'font.size': 12, 'axes.spines.top': False, 'axes.spines.right': False})
models = report['models']
x = [int(k) for k in models]
fig, axes = plt.subplots(1, 2, figsize=(13, 5.4), layout='constrained')
fig.suptitle('学習データを増やしたときの精度の変化', fontsize=20, weight='bold')
for ax, key, title, factor, unit in [
    (axes[0], 'top1_position_accuracy', '60位置の正解率（高いほど良い）', 100, '%'),
    (axes[1], 'mean_distance_mm', '平均位置誤差（低いほど良い）', 1, ' mm'),
]:
    for field, label, color, style in [('aggregate', '評価4組・2,400打', '#1767b4', '-'), ('new', '今回追加・600打', '#e58b23', '--')]:
        y = [(v['aggregate'] if field == 'aggregate' else v['sets'][-1])[key] * factor for v in models.values()]
        ax.plot(x, y, style, marker='o', linewidth=2.5, color=color, label=label)
        if field == 'aggregate':
            for a, b in zip(x, y):
                ax.annotate(f'{b:.2f}{unit}', (a, b), xytext=(0, 12), textcoords='offset points', ha='center', fontsize=11, color=color)
    ax.set_title(title, pad=20)
    ax.set_xlabel('累積学習サンプル数')
    ax.set_xticks(x, [f'{v:,}' for v in x])
    ax.grid(alpha=.2)
    ax.legend(loc='lower right' if factor == 100 else 'upper right', fontsize=10)
axes[0].set_ylim(84, 100)
axes[0].set_ylabel('正解率（%）')
axes[1].set_ylim(0, 27)
axes[1].set_ylabel('平均位置誤差（mm）')
out = ROOT / 'docs/model-comparison-20261103.png'
fig.savefig(out, dpi=160, facecolor='white')
print(out)
