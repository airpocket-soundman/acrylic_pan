# データ配置

収録した生データは `data/raw/`、前処理後のデータは `data/processed/` に置く。
`data/raw/` は容量が大きいため、`data/*.log` とともにGit管理外である（`.gitignore`）。

## ディレクトリ構成

| パス | 内容 |
|---|---|
| `data/raw/sessions/` | 打撃位置の学習・評価に使う収録セッション。1セッション1ディレクトリ |
| `data/raw/verification/` | 収録系の動作確認用セッション |
| `data/raw/verification-guided/` | ガイド付き収録の動作確認用セッション |
| `data/raw/verification-threshold2000/` | トリガーしきい値2000での動作確認用セッション |
| `data/processed/` | 前処理後データの置き場所（Git管理対象、`.gitkeep` のみ） |
| `data/dummy_model/` | ダミーSolist-AIモデルの検証データ（[ダミーモデル検証](../docs/ai-dummy-validation.md)） |

`data/dummy_model/` には `model.npz`、`dummy_dataset.npz`、`golden_cases.npz`、
`golden_outputs.json`、`board_comparison.json` を置いている。

## セッションディレクトリ

セッションディレクトリ名は `YYYYMMDD_HHMMSS_<8桁の16進ID>` 形式で、`session_id` と一致する
（例: `20260823_104754_2553f5d8`）。各セッションは次のファイルで構成する。

- `session.json`: セッションのメタデータ。`format` は `acrylic-pan-session-v1`。`session_id`、
  `created_at`、`closed_at`、`event_count`、`user_metadata`（シリアルポート、ボーレート、
  `panel_profile_id` とパネル寸法・分割数など）を持つ
- `manifest.csv`: イベント一覧。列は `index,sequence,received_at,file,class_id,sample_rate_hz,sample_count,trigger_index,peak_abs,flags,timestamp_us`
- `manifest.jsonl`: `manifest.csv` と同じ項目に、`annotations` を加えたイベント一覧（1行1イベント）
- `events/event_<index 6桁>_seq_<sequence 10桁>.npz`: 1打撃分の波形

`annotations` には、収録時の目標打点として `target_class_id`、`target_area`、`target_point_id`、
`target_point_name`、`target_x_mm`、`target_y_mm`、`offset_x_mm`、`offset_y_mm`、
`position_pattern`、`repetition` などを記録する。

イベントの `.npz` は、1軸の波形 `samples`（例: 2,048サンプル）と、`sample_rate_hz`、`trigger_index`、
`peak_abs`、`flags`、`sequence`、`timestamp_us`、`class_id`、`received_at` を持つ。

`manifest.jsonl.bak-*` は、打点ラベルを修正する前の `manifest.jsonl` の控えである。

## 分割の単位

各打撃は、`session_id`、イベント番号、打点のクラスと座標（mm）、加速度波形を対応付けて扱う。
学習・検証・テストの分割や、未知セッション評価（LOSO）は `session_id` 単位で行う。
