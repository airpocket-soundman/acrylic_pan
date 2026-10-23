# モデルのスナップショット: support-2point-20261022

- 保存日時: 2026-10-22 21:38
- 説明: 2点支持を2026-10-22に採り直して学習したモデル一式(センター3、四隅3、4,680打)。ボードに書き込み済み
- Git: `b794a3e5d0c270ea27a46b97da9114e9f1693662`(未コミットの変更を含むことがある)

## 学習に使ったセッション(`data/raw/sessions_2point`)

- `20261022_204459_287ce95e`
- `20261022_205036_f13f36fe`
- `20261022_205502_037d2052`
- `20261022_210832_3c60e03b`
- `20261022_211749_19c638c0`
- `20261022_212448_161ea00e`

## ファイル

| ファイル | バイト | SHA-256 |
|---|---:|---|
| `artifacts/device_position_probability_400x300x5/model.npz` | 12,768 | `9d9eb98cda941df3…` |
| `artifacts/device_position_probability_400x300x5/training_report.json` | 2,496 | `99867822b906d429…` |
| `artifacts/support-2point-20261022/apan_12class_model.h` | 56,203 | `018c84a59613fa86…` |
| `artifacts/support-2point-20261022/apan_calibration_golden.h` | 59,021 | `5169a95944df1242…` |
| `artifacts/support-2point-20261022/apan_calibration_prior.h` | 16,987 | `18312ab8f7746cc0…` |
| `artifacts/support-2point-20261022/apan_position_probability_model.h` | 29,376 | `030dc95b7183205c…` |
| `artifacts/support-2point-20261022/class12/model.npz` | 29,424 | `b17560e1fefe6cae…` |
| `artifacts/support-2point-20261022/class12/training_report.json` | 30,066 | `3c2d79a1ecd6a158…` |
| `artifacts/support-2point-20261022/device_position/model.npz` | 12,768 | `9d9eb98cda941df3…` |
| `artifacts/support-2point-20261022/device_position/training_report.json` | 2,496 | `99867822b906d429…` |
| `artifacts/support-2point-20261022/features_cache.npz` | 2,232,932 | `c4658c71696dbe9e…` |
| `artifacts/support-2point-20261022/session_ids.txt` | 150 | `1b028b422281a33b…` |
| `artifacts/support-2point-20261022/status.txt` | 29 | `702705742700689a…` |
| `artifacts/support-2point-20261022/train_class12.log` | 242 | `71629ecc34bb3199…` |
| `artifacts/support-2point-20261022/train_device.log` | 757 | `3eb0cbf9f9d6b887…` |
| `firmware/AIVibrationInference.elf` | 593,896 | `c559a45637a17aee…` |
| `firmware/AIVibrationInference.hex` | 178,077 | `7322864c2a9cc368…` |
| `firmware-generated/apan_12class_model.h` | 56,203 | `018c84a59613fa86…` |
| `firmware-generated/apan_calibration_golden.h` | 59,021 | `5169a95944df1242…` |
| `firmware-generated/apan_calibration_prior.h` | 16,987 | `18312ab8f7746cc0…` |
| `firmware-generated/apan_position_probability_model.h` | 29,376 | `030dc95b7183205c…` |

## 戻し方

```
python scripts/model_snapshot.py restore support-2point-20261022
```

ファームのモデルヘッダーとPC側のモデルを元の場所へ戻す。ボードのファームは、戻したあとで、ビルドして書き込む(`firmware/`に当時のイメージがあれば、それを書き込める)。
