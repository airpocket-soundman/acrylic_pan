# モデルのスナップショット: support-2point-5sessions

- 保存日時: 2026-10-19 22:16
- 説明: ボードを2点で支持したときのモデル一式。2点支持の5セッション(センター2、四隅3、3,600打)で学習した60位置・12クラスのデバイスモデルと、ODLの事前分布。ボードで動いているファームのイメージつき
- Git: `d31664b0e584f526fb24e2e83e8175b101ebd82f`(未コミットの変更を含むことがある)

## 学習に使ったセッション(`data/raw/sessions_2point`)

- `20261019_205614_7b39e09d`
- `20261019_210631_9e9fd28a`
- `20261019_211259_81afc894`
- `20261019_211815_650952aa`
- `20261019_213127_bc1895b9`

## ファイル

| ファイル | バイト | SHA-256 |
|---|---:|---|
| `artifacts/device_position_probability_400x300x5/model.npz` | 12,759 | `65d030f91924c3ae…` |
| `artifacts/device_position_probability_400x300x5/training_report.json` | 2,461 | `ce5578073c08e6c1…` |
| `artifacts/support-2point-5sessions/.finished` | 5 | `d117fa006ba92085…` |
| `artifacts/support-2point-5sessions/apan_12class_model.h` | 56,208 | `937fa38ca520ad86…` |
| `artifacts/support-2point-5sessions/apan_calibration_golden.h` | 58,999 | `e45253840f89e0e6…` |
| `artifacts/support-2point-5sessions/apan_calibration_prior.h` | 16,850 | `4cf32192637269f2…` |
| `artifacts/support-2point-5sessions/apan_position_probability_model.h` | 29,395 | `c21146ae8ee20681…` |
| `artifacts/support-2point-5sessions/class12/model.npz` | 29,424 | `d62be1542620a53e…` |
| `artifacts/support-2point-5sessions/class12/training_report.json` | 29,386 | `de42ad8ead726fd8…` |
| `artifacts/support-2point-5sessions/device_position/model.npz` | 12,759 | `65d030f91924c3ae…` |
| `artifacts/support-2point-5sessions/device_position/training_report.json` | 2,461 | `ce5578073c08e6c1…` |
| `artifacts/support-2point-5sessions/features_cache.npz` | 1,717,303 | `d647d065134fd1ad…` |
| `artifacts/support-2point-5sessions/train_class12.log` | 246 | `d14c68ef419f1ca8…` |
| `artifacts/support-2point-5sessions/train_device.log` | 755 | `4fdac833f033bf8b…` |
| `firmware/AIVibrationInference.elf` | 654,644 | `7cf7c8a599f49410…` |
| `firmware/AIVibrationInference.hex` | 193,108 | `44076bad174a2fb7…` |
| `firmware-generated/apan_12class_model.h` | 56,208 | `937fa38ca520ad86…` |
| `firmware-generated/apan_calibration_golden.h` | 58,999 | `e45253840f89e0e6…` |
| `firmware-generated/apan_calibration_prior.h` | 16,850 | `4cf32192637269f2…` |
| `firmware-generated/apan_position_probability_model.h` | 29,395 | `c21146ae8ee20681…` |

## 戻し方

```
python scripts/model_snapshot.py restore support-2point-5sessions
```

ファームのモデルヘッダーとPC側のモデルを元の場所へ戻す。ボードのファームは、戻したあとで、ビルドして書き込む(`firmware/`に当時のイメージがあれば、それを書き込める)。
