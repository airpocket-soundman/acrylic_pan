# 12クラスモデル・4セッション学習評価（2026-08-21）

> 2・3・4セッションモデルを同じリークなし480件で比較した結果は、
> [共通評価レポート](shared-holdout-evaluation-20260821.md)を参照する。

## 結論

追加収録した`20260821_221547_4c753402`は12クラス各50件、合計600件で整合していた。
このセッションを学習へ入れる前の完全未学習評価では、現行2セッションモデルの97.17%に対し、
前回の3セッション候補モデルは98.50%となり、**+1.33ポイント、正解数+8件**の改善を確認した。

その後、4セッション全2,345件で新しいモデルを学習した。4-fold LOSO平均は95.12%で、
3セッション候補の3-fold LOSO平均92.31%から+2.80ポイントとなった。このモデルは
2026-08-21に現行ファームウェアへ反映し、実機へ書き込み済みである。

## 完全未学習セッションでの比較

評価セッション`20260821_221547_4c753402`は、比較時点では両モデルの学習に未使用だった。

| モデル | 学習件数 | 正解 | 精度 | 現行との差 |
|---|---:|---:|---:|---:|
| 現行2セッションモデル | 1,145 | 583 / 600 | 97.17% | — |
| 3セッション候補モデル | 1,745 | 591 / 600 | 98.50% | +1.33 pt |

現行モデルで最も低かったクラス8のrecallは82%から96%へ改善した。3セッション候補では
クラス6が92%で最小となり、それ以外は96%以上だった。この結果から、前回追加した600件には
独立セッションへの汎化を改善する効果があったと判断する。

## 4セッションモデル

| セッション | 件数 |
|---|---:|
| `20260720_215533_aa9943ae` | 589 |
| `20260720_220935_bd12a70b` | 556 |
| `20260821_215225_8f38a3e4` | 600 |
| `20260821_221547_4c753402` | 600 |
| 合計 | 2,345 |

Input 128、Hidden 32、Output 12、Hard sigmoid、MSE、Seed 1、L2 0.001、
Bfloat16境界再現という条件は現行モデルから変更していない。

| LOSO評価セッション | 学習件数 | 評価件数 | 精度 |
|---|---:|---:|---:|
| `20260720_215533_aa9943ae` | 1,756 | 589 | 90.83% |
| `20260720_220935_bd12a70b` | 1,789 | 556 | 97.30% |
| `20260821_215225_8f38a3e4` | 1,745 | 600 | 93.83% |
| `20260821_221547_4c753402` | 1,745 | 600 | 98.50% |
| 4-fold平均 | — | 2,345 | **95.12%** |

前回の3セッション評価と共通する先頭3セッションだけで比較しても、LOSO平均は92.31%から
93.99%へ+1.68ポイント改善した。全2,345件で学習した最終候補の学習データ上精度は97.83%だが、
これは独立評価ではない。

古い`20260720_215533_aa9943ae`ではクラス0、8、9、新しい
`20260821_215225_8f38a3e4`ではクラス11のrecallが相対的に低く、セッション差はまだ残る。

## 成果物と運用

- `artifacts/real_model_300x400x5_12class_4sessions_20260821/training_report.json`
- `artifacts/real_model_300x400x5_12class_4sessions_20260821/model.npz`
- `artifacts/real_model_300x400x5_12class_4sessions_20260821/apan_12class_model_candidate.h`
- `artifacts/real_model_300x400x5_12class_4sessions_20260821/unseen_evaluation.json`

候補ヘッダーを`firmware/AcrylicPanCollector/generated/apan_12class_model.h`へ反映し、
実機用の現行モデルとした。学習成果物`model.npz`のSHA-256は
`c9ef1b8ebbebe08ace72a39275d1c45582ec051cbe8d8c43a594a28d0ac0496a`である。

## 実機デプロイ（2026-08-21）

- LEXIDEプロジェクト:
  `C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_xy_staged`
- 書き込みイメージ:
  `Debug\AIVibrationInference.hex`
- HEX SHA-256:
  `56b80c41c96db4a9e66f8cf0086b045e1e20fdd1dacfd7453800b3a58614973f`
- 書き込み器: MCU-Link CMSIS-DAP V3.172、シリアル`14OZPOHJLAY5E`
- OpenOCDによるerase、program、全バイトverify、resetが正常終了
- PC側Bfloat16参照実装でgolden case 12 / 12一致
- 書き込み後、FTDI COM3・115200 bpsで`AcrylicPanCollector`へ接続
- `instrument`モードへの切替ACKを受信し、推論開始状態を確認

実打撃による音・クラス出力の主観確認は、この自動デプロイ記録とは別に行う。

## 再学習コマンド

```powershell
C:\ProgramData\anaconda3\python.exe -m sim.real_model_pipeline `
  --sessions data/raw/sessions `
  --output-dir artifacts/real_model_300x400x5_12class_4sessions_20260821 `
  --header artifacts/real_model_300x400x5_12class_4sessions_20260821/apan_12class_model_candidate.h `
  --alpha D:\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy `
  --ridge 0.001 --class-count 12 `
  --session-id 20260720_215533_aa9943ae `
  --session-id 20260720_220935_bd12a70b `
  --session-id 20260821_215225_8f38a3e4 `
  --session-id 20260821_221547_4c753402
```
