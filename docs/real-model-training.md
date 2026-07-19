# 実測振動による8クラスモデル学習

> このページは512点・旧2セッションによるv1モデルの記録です。2,048点・新4セッションの
> サンプリング周波数比較、8クラス分類、XY直接回帰の結果は
> [4セッション・サンプリング周波数比較](sampling-experiment-20260718.md)を参照してください。

## 現在のモデル

2026-07-17に収録・選別した中心打点2セッション、合計781件を使用した。
削除済みのNGイベントは含めず、欠測として扱う。

| 学習セッション | 評価セッション | 学習件数 | 評価件数 | Bfloat16精度 |
|---|---|---:|---:|---:|
| `20260717_224111_5989080d` | `20260717_230800_09e74326` | 390 | 391 | 96.93% |
| `20260717_230800_09e74326` | `20260717_224111_5989080d` | 391 | 390 | 96.41% |

セッション分離2-foldの平均は **96.67%**。イベント単位のランダム分割は行っていない。
最終モデルは全781件で再学習し、学習データ上のBfloat16精度は98.21%だった。
この値は独立評価ではないため、実機統合後に別セッションで再評価する。

## 入力特徴

モデル名は `acrylic_pan_time128_h32_8class_v1`。入力は128値の時間波形だけを使う。

- 512点波形の先頭64点をプリトリガ区間とし、その平均を全点から引く
- trigger index 64以後の448点から、等間隔に128点を選ぶ
- ポストトリガ区間の最大絶対値で正規化する
- 学習セッションから求めた列ごとの平均・標準偏差で標準化する
- 標準化係数とサンプルindexは生成ヘッダに固定してPCと実機で共用する

以前検討したFFT併用特徴は、PCのNumPy FFTとML63Q25x7向けFFTライブラリの
スケーリングが未校正だった。そのためv1では、実機で同じ計算を再現しやすい
時間波形128値を採用した。FFTを再導入する場合は、同一波形に対するPC・実機の
各binを照合してからモデルを更新する。

## Solist-AI設定

| 項目 | 値 |
|---|---:|
| Input | 128 |
| Hidden | 32 |
| Output | 8 |
| Activation | Hard sigmoid |
| Loss | MSE |
| Seed | 1 |
| L2 | 0.1 |
| 判定 | 8出力をCPU側でargmax |

固定alphaはROHM公式SimulatorのSeed 1から取得した
`D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy`を使用する。
評価では入力、alpha、beta、隠れ層、出力のBfloat16境界を再現する。

## 再学習

```powershell
.\scripts\train-real-model.ps1
```

主な成果物:

- `artifacts/real_model/training_report.json`: 件数、セッション分離精度、混同行列、モデル条件
- `artifacts/real_model/fold1_*_8class.csv`: セッション1学習・セッション2評価
- `artifacts/real_model/fold2_*_8class.csv`: 逆方向の評価
- `artifacts/real_model/final_train_8class.csv`: 全781件の最終学習CSV
- `artifacts/real_model/model.npz`: alpha、beta、標準化係数、時間サンプルindex
- `firmware/AcrylicPanCollector/generated/apan_8class_model.h`: 実機統合用モデル

公式SimulatorではInput 128、Hidden 32、Output 8、Hard sigmoid、MSE、
Seed 1、L2 0.1を設定する。ファームへ組み込む前に、同じ入力に対する
Simulatorの8出力と`training_report.json`のgolden caseを比較する。
