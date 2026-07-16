# Acrylic Pan 収録システム クイックスタート

## 接続

- `COM3`: 開発ボードのUART、115200 bps、8-N-1
- `COM5`: MCU-Link VCom（ファーム書き込みには使用しない）
- 書き込み: MCU-LinkのCMSIS-DAPインターフェースをOpenOCDから使用

## ファームのビルドと書き込み

LEXIDE GUIやJavaは不要です。privateプロジェクトをCLIでビルドします。

```powershell
.\firmware\AcrylicPanCollector\tools\build-private-project.ps1 `
  -Project C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_cli_verified2
```

書き込み、Flash全バイト検証、リセット実行を行います。

```powershell
.\scripts\flash-firmware.ps1 `
  -FirmwareHex C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_cli_verified2\Debug\AIVibrationInference.hex `
  -Execute
```

スクリプトはLEXIDE同梱のROHM版 `openocd_arm.exe` とML63Q25x7 DFPを使用します。生成HEXにはRAM用レコードも含まれるため、ELFからFlash領域専用バイナリを作って検証します。

## PC Webモニタ

```powershell
.\scripts\run-monitor.ps1
```

ブラウザで `http://127.0.0.1:8765/` を開き、COM3へ接続します。

- 「ボード確認」: APAN `HELLO` を送り、`AcrylicPanCollector` 応答を確認
- 「静止波形を取得」: `CAPTURE` でZ軸512点をワンショット取得
- 波形とDC除去・Hann窓FFTを表示
- NPZ、`manifest.csv`、`manifest.jsonl`へ自動保存

主なHTTP API:

- `GET /api/status`
- `GET /api/ports`
- `POST /api/connect` — `{"port":"COM3","baudrate":115200}`
- `POST /api/command` — `{"command":"ping"}` または `{"command":"capture"}`
- `GET /api/events/latest`
- `POST /api/session`
- `GET /api/collection`
- `POST /api/collection/start` — `{"repetitions":10,"position_pattern":"five","output_root":"data/raw/sessions"}`
- `POST /api/collection/stop`

ガイド画面のエリア番号はパネルを正面から見て、上段左から1～4、下段左から5～8です。
位置パターンは中心1点、中心＋上下左右5点、3×3の9点から選べます。GUIの既定は5点です。
400×200 mmのパネル図に次の打点をマーカー表示し、8エリア×各打点の保存件数、エリア合計、
全体進捗を500 msごとに更新します。採取開始後は、表示位置を叩く→512点を保存→次の位置を
自動待受、を繰り返します。
保存が成功するまでクラス番号は進まないため、UART遅延で誤ラベルになることはありません。

## 学習用保存形式

収録1回を1セッションとし、`data/raw/sessions/<session_id>/`へ次の構成で保存します。

```text
session.json             セッションID、作成・終了日時、イベント数、収録条件
manifest.jsonl           1行1イベントの正本インデックス、class_idとannotationsを含む
manifest.csv             人がExcel等で確認するための同内容の主要列
events/*.npz             int16波形と数値メタデータ
```

NPZには `samples`、`sample_rate_hz`、`trigger_index`、`peak_abs`、`flags`、
`sequence`、`timestamp_us`、`class_id`、`received_at`を保存します。8クラス学習用の
`class_id`は0～7です。未ラベル収録はNPZで-1、manifestでnull／空欄になり、学習ローダーは
誤混入を防ぐため拒否します。

1セッションを1回の採取runとし、ガイド画面の位置指示に従って0～7の全クラスを同じrun内へ
収録します。ガイド採取APIは次イベントの指示クラスを自動的にmanifestとNPZへ記録します。
学習評価には完全な8クラスrunが最低2回必要です。

`sim.solist_dataset.load_recorded_sessions`はsession、JSONL、NPZの件数・ラベル・サンプリング
周波数を相互検証します。`split_dataset_by_session`はrun単位で分割し、同じrunのイベントが
trainとtestへ跨がないこと、および両側に全8クラスが存在することを検証します。

エリア内5点または9点のガイド採取では、各manifest行の`annotations`へ次を保存します。

- `target_class_id`、`target_point_id`（どちらも0始まり）
- `target_point_name`
- `target_x_mm`、`target_y_mm`（パネル上の絶対位置）
- `offset_x_mm`、`offset_y_mm`（エリア中心からの相対位置）
- `repetition`（1始まり）

`validate_guided_collection`は8クラス×5点または9点×指定反復の全組合せ、点定義、絶対位置と
オフセットから求めたエリア中心の整合性をrunごとに検査します。

### 実機フロー検証（2026-07-16）

COM3の実機へAIデモ込みファームを書き込み、各エリア1回のガイド採取を強制取得で確認しました。
8件すべてが`class_id` 0～7の順に保存され、欠落・CRCエラー・重複・順序逆転・保存失敗は
すべて0でした。静止Z軸の8波形平均は全体で約4038 LSB（約0.986 g）でした。
この検証はセンサ静止時の通信・保存確認であり、学習データには使用しません。
強制取得の`trigger_index`は0、実際の衝撃待受`START`ではプリトリガー後の128になります。

## ファームUART API

APAN v1、COBS、CRC32、末尾 `0x00` のバイナリフレームです。

- `HELLO (0x01)`
- `STATUS (0x02)`
- `START (0x10)` — 次の衝撃を待つ
- `STOP (0x11)`
- `CAPTURE (0x13)` — 次のZ軸512点を直ちに取得
- `EVENT_DATA (0x20)`
- `ACK (0x70)` / `NACK (0x71)`

`STATUS.flags` のbit 0はLCDへの `test` 描画成功、bit 1～3はLED1～3の実出力状態です。起動自己診断がすべて成功した場合は `0x0f` になります。

端末診断用にASCIIの `PING`、`STATUS`、`CAPTURE`、`GET_STATIC` も使用できます。

起動時は停止状態です。`CAPTURE` は1ブロックだけセンサを動かして送信後に停止へ戻るため、繰り返し安全に呼べます。通常の衝撃収録は `START` で待機し、128点（5 ms）のプリトリガを含む512点を返します。静止ノイズ調査の最大隣接差1,351 LSBに対し、誤検知を防ぐjerkしきい値は2,000 LSBです。

## 実機確認値

静止状態で確認したZ軸512点は平均約4,031 LSB、標準偏差約231 LSBでした。KX134の±8 g設定（4096 LSB/g）では平均約0.984 gで、Z軸方向の重力を正しく検出しています。

2026-07-17のCOM3再検証では、jerkしきい値2,000のファームで静止待機6秒間に自然発火0件、
強制測定1回の後にさらに6秒待機しても合計1件のままであることを確認しました。実際の打撃に
対する感度は、アクリル板を取り付けた状態で打撃強度を変えて最終調整します。
