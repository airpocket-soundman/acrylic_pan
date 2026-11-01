# モデルのスナップショット: pre-retrain-20261029

- 保存日時: 2026-10-29 19:17
- 説明: 2026-10-29: PCで現行60位置モデルを再学習・再書込みする直前の配備ヘッダー退避
- Git: `fbaae826dea50b96d704106f4a125e4d25ce471b`(未コミットの変更を含むことがある)

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
| `firmware-generated/apan_12class_model.h` | 56,203 | `018c84a59613fa86…` |
| `firmware-generated/apan_calibration_golden.h` | 59,021 | `5169a95944df1242…` |
| `firmware-generated/apan_calibration_prior.h` | 16,914 | `ff3bb6c783ad146d…` |
| `firmware-generated/apan_position_probability_model.h` | 63,913 | `64c71a606e6e8de1…` |

## 戻し方

```
python scripts/model_snapshot.py restore pre-retrain-20261029
```

ファームのモデルヘッダーとPC側のモデルを元の場所へ戻す。ボードのファームは、戻したあとで、ビルドして書き込む(`firmware/`に当時のイメージがあれば、それを書き込める)。
