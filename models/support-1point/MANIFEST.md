# モデルのスナップショット: support-1point

- 保存日時: 2026-10-19 20:54
- 説明: ボードを1点で固定していたときのモデル一式(12クラス、60位置のデバイスモデル、PC側モデル)。2点支持への変更前
- Git: `d31664b0e584f526fb24e2e83e8175b101ebd82f`(未コミットの変更を含むことがある)

## 学習に使ったセッション(`data/raw/sessions`)

- `20260720_215533_aa9943ae`
- `20260720_220935_bd12a70b`
- `20260821_215225_8f38a3e4`
- `20260821_221547_4c753402`
- `20260823_081435_223491a5`
- `20260823_082825_9374f169`
- `20260823_083716_e0336d95`
- `20260823_084554_6d26bd22`
- `20260823_102330_ae4338bd`
- `20260823_103846_18384ce0`
- `20260823_104754_2553f5d8`

## ファイル

| ファイル | バイト | SHA-256 |
|---|---:|---|
| `artifacts/device_position_probability_400x300x5/model.npz` | 12,739 | `b0c8a8a1fc730656…` |
| `artifacts/device_position_probability_400x300x5/training_report.json` | 2,662 | `4c9fe557ec55f08c…` |
| `artifacts/pc_area_classifier_400x300x5/area_classifier.joblib` | 12,178,751 | `b1f312078408eb6d…` |
| `artifacts/pc_area_classifier_400x300x5/area_classifier_40reps_before_100reps_20260823.joblib` | 12,192,745 | `c7ab1dd7bf6a63da…` |
| `artifacts/pc_position_runtime/position_ensemble.joblib` | 4,330,741 | `4f49cf2d52f0522c…` |
| `artifacts/pc_position_runtime/training_report.json` | 3,761 | `401777cac540b68e…` |
| `artifacts/pc_position_runtime_400x300x5/external_unused_sessions_evaluation_3grids.json` | 2,192 | `e3b4aba92684f951…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble.joblib` | 33,383,267 | `3f3a4923949ba481…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_1grid_20260823.joblib` | 21,027,903 | `53167139ec3a455f…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_2grids_20260823.joblib` | 21,040,168 | `ef9e223f28a4d650…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_3grids_20260823.joblib` | 21,038,854 | `def9da60cc3c5acc…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_48cell_20260823.joblib` | 33,311,192 | `e7f594d583906c4a…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_4grid_probability_before_100reps_20260823.joblib` | 33,337,508 | `e46261523e92238f…` |
| `artifacts/pc_position_runtime_400x300x5/position_ensemble_4grids_xy_20260823.joblib` | 21,029,610 | `cf3721e3a534b719…` |
| `artifacts/pc_position_runtime_400x300x5/training_report.json` | 7,762 | `e870d9f7f7a84178…` |
| `artifacts/pc_position_runtime_400x300x5/validation_predictions.npz` | 20,637 | `e84c52ed86b8f482…` |
| `firmware/AIVibrationInference.elf` | 654,644 | `c8b5ec4cfc49eae1…` |
| `firmware/AIVibrationInference.hex` | 193,108 | `90b5d9c55daf841f…` |
| `firmware-generated/apan_12class_model.h` | 56,183 | `e81cde77692cdad3…` |
| `firmware-generated/apan_calibration_golden.h` | 59,147 | `245eee1d26492b43…` |
| `firmware-generated/apan_calibration_prior.h` | 17,181 | `15f6cf61163cd7a4…` |
| `firmware-generated/apan_position_probability_model.h` | 29,358 | `6824d4b8274c80cf…` |

## 戻し方

```
python scripts/model_snapshot.py restore support-1point
```

ファームのモデルヘッダーとPC側のモデルを元の場所へ戻す。ボードのファームは、戻したあとで、ビルドして書き込む(`firmware/`に当時のイメージがあれば、それを書き込める)。
