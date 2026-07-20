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

画面上部の「学習データ採取」「推論結果」「位置推定」「楽器」タブで用途を切り替えます。タブを
切り替えると、停止中のファームへ対応する動作モードを送ってから遷移します。
実際のモデル学習はPC/Solist-AIで行うため、ファーム側の対応モード名は
「データ採取モード」と「推論モード」です。

- 「ボード確認」: APAN `HELLO` を送り、`AcrylicPanCollector` 応答を確認
- 「静止波形を取得」: `CAPTURE` でZ軸25.6 kHz・2,048点（80 ms）を取得
- 波形とDC除去・Hann窓FFTを表示
- NPZ、`manifest.csv`、`manifest.jsonl`へ自動保存

主なHTTP API:

- `GET /api/status`
- `GET /api/ports`
- `POST /api/connect` — `{"port":"COM3","baudrate":115200}`
- `POST /api/command` — `{"command":"ping"}` または `{"command":"capture"}`
- `POST /api/device/mode` — `{"mode":"collection"}` または `{"mode":"inference"}`
- `POST /api/inference/start`
- `POST /api/inference/stop`

推論モードの実打撃では、ファームが判定クラスと推論に使った512点波形を
`INFERENCE_EVENT`として1フレームで返します。推論結果画面は該当エリアの色を
変え、その下に受信波形と、同じ波形からPC側で計算したFFTを表示します。

「位置推定」タブでは、この512点波形をPC側の全層学習MLP 3モデルへ入力し、
推定XYの平均、モデル間ばらつき、ファームの8エリアスコアを統合した確率分布を
400 × 200 mmの板上へヒートマップ表示します。白い十字が推定座標です。

```powershell
.\scripts\run-position-monitor.ps1
```

ライブ用モデルを再学習する場合:

```powershell
.\scripts\train-pc-position-runtime.ps1
```

現在の教師座標は8エリア中心だけです。中心間の表示はNNによる補間であり、任意位置の
実測精度を保証するものではありません。四隅・格子点データ取得後に分布幅を再校正します。
- `GET /api/events/latest`
- `POST /api/session`
- `GET /api/collection`
- `POST /api/collection/start` — `{"repetitions":10,"position_pattern":"corners","output_root":"data/raw/sessions"}`
- `POST /api/collection/stop`
- `POST /api/collection/undo` — `{"expected_completed_samples":12}`（直前の1件を削除し、同じ位置へ戻る）

## 収録済みデータの閲覧と削除

「収録済みデータ」パネルで保存先のセッションを選ぶと、イベント一覧（No.、ラベル、打点、
peak、受信時刻）を表示します。行を選ぶと、その波形とFFTを同じページのグラフへ描画します。
表示中のデータは「表示中のデータを削除」で、セッション全体は「セッション削除」で消せます。
どちらも確認ダイアログを挟み、取り消しはできません。

削除は`events/*.npz`、`manifest.jsonl`、`manifest.csv`、`session.json`の`event_count`を
まとめて更新します。`manifest.jsonl`が正本インデックスであり、`session.json`の`event_count`と
行数が食い違うと`sim.solist_dataset.load_recorded_sessions`がセッション全体を拒否するためです。
manifestを先に書き換えてからNPZを削除するので、途中で異常終了した場合に残るのは
参照されない孤児ファイルだけで、セッションは読める状態を保ちます。

保存済みイベントの閲覧は`/api/events/latest`を書き換えません。過去データを見ている間も、
最後に受信したライブ波形は保持されます。

事故防止のため次の操作は拒否します。

- ガイド採取の実行中の削除（採取件数カウンタと保存内容がずれるため）
- 記録中セッションの「セッション削除」

イベント番号（No.）は削除後も振り直しません。同一セッション内で一意であり続けます。

閲覧・削除のHTTP API:

- `GET /api/library/sessions` — `?root=` 省略時は現在の保存先
- `GET /api/library/events` — `?session=<session_id>`
- `GET /api/library/event` — `?session=<session_id>&index=<No.>`。ライブ波形と同形式のJSON
- `POST /api/library/delete` — `{"session":"<session_id>","index":2}`
- `POST /api/library/delete_session` — `{"session":"<session_id>"}`

ガイド画面のエリア番号はパネルを正面から見て、上段左から1～4、下段左から5～8です。
位置パターンは[設計メモ](design.md)3節の収録系統に対応し、次の2つです。

| パターン | 系統 | 打点 | 座標 |
| --- | --- | --- | --- |
| A: エリア中心 1点 | A（8クラス分類の主力） | 8点 | X = 50, 150, 250, 350、Y = 50, 150 |
| B: 50 mm格子 四隅 4点 | B（座標回帰・細分化用） | 32点 | X = 25～375、Y = 25～175（50 mm間隔） |

B系統はエリア中心から見て四隅（±25 mm）の左上・右上・左下・右下にあたり、8エリア×4点で
仕様の50 mm格子32点を再現します。ただし固定具（`x=200～300 mm、y=0～20 mm`）の直下にある
2点だけは例外で、エリア3の左上・右上を (225, 35)、(275, 35) へずらします。この2点のみ
オフセットが (±25, -15) になります。系統ごとに打数が異なる（A: 90打、B: 20打）ため、
AとBは別々の採取runとして収録します。

パターンを選ぶと400×200 mmのパネル図に、そのパターンの全打点（8点／32点）をドットで
描画します。パネル固定具（`x=200～300 mm、y=0～20 mm`）も斜線のブロックで図示するので、
叩いてはいけない範囲がひと目で分かります。採取開始前はプレビュー表示で、開始後は各ドットに
その位置の採取済み回数を表示し、完了した打点は緑になります。次に叩く打点は十字マーカーとオレンジのドットで
強調し、8エリア×各打点の保存件数、エリア合計、全体進捗を500 msごとに更新します。
採取開始後は、表示位置を叩く→2,048点を保存→次の位置を自動待受、を繰り返します。
デバイスはRAM節約のため512点ずつ4フレームに分け、PCは4つ揃った場合だけ
1件として保存します。欠落や内容衝突がある部分データは保存せず、同じ打点を再測定します。
保存が成功するまでクラス番号は進まないため、UART遅延で誤ラベルになることはありません。

### 打点の任意選択

採取中は、パネル図の任意のドットをクリックすると順序に関係なくその打点へ切り替わります。
以降の打撃はクリックした打点のラベル・座標で保存されます。指定回数に達した打点は選択が
自動的に解除され、ガイドは残っている最初の打点へ戻ります。すでに完了した打点や範囲外の
指定は拒否します。打点の既定の巡回順は「未完了の最初の打点」であり、途中で飛ばしても
最終的に全打点が指定回数に達すれば採取完了になります。

- `GET /api/collection/targets?pattern=corners` — 採取開始前の打点プレビュー
- `POST /api/collection/select` — `{"target_index":17}`

`/api/collection` と `/api/collection/targets` の `panel` にはパネル寸法と固定具の範囲
（`clamp`）が入ります。GUIはこの値だけを見て図を描くため、寸法をJavaScript側へ複製して
いません。固定具の位置を変えるときは `server.py` の `CLAMP_FOOTPRINT_MM` と
`CLAMP_POINT_MOVES` を直せば、打点・図・検証がまとめて追従します。

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

ガイド採取では、各manifest行の`annotations`へ次を保存します。

- `target_class_id`、`target_point_id`（どちらも0始まり）
- `target_point_name`
- `target_x_mm`、`target_y_mm`（パネル上の絶対位置）
- `offset_x_mm`、`offset_y_mm`（エリア中心からの相対位置）
- `repetition`（1始まり）

`validate_guided_collection`は8クラス×1点（A系統）または4点（B系統）×指定反復の全組合せ、
点定義、絶対位置とオフセットから求めたエリア中心の整合性をrunごとに検査します。固定具直下の
2点はエリアごとにオフセットが異なるため、点定義の一致はエリア単位で検査し、点名の一致のみ
全エリア共通で検査します。

### 実機フロー検証（2026-07-16）

COM3の実機へAIデモ込みファームを書き込み、各エリア1回のガイド採取を強制取得で確認しました。
8件すべてが`class_id` 0～7の順に保存され、欠落・CRCエラー・重複・順序逆転・保存失敗は
すべて0でした。静止Z軸の8波形平均は全体で約4038 LSB（約0.986 g）でした。
この検証はセンサ静止時の通信・保存確認であり、学習データには使用しません。
強制取得の`trigger_index`は0、実際の衝撃待受`START`では64になります。±32 g設定で、前64点（2.5 ms）とトリガを含む後448点（17.5 ms）を保存します。

## ファームUART API

APAN v1、COBS、CRC32、末尾 `0x00` のバイナリフレームです。

- `HELLO (0x01)`
- `STATUS (0x02)`
- `START (0x10)` — 次の衝撃を待つ
- `STOP (0x11)`
- `CAPTURE (0x13)` — 次のZ軸2,048点を直ちに取得（採取モード）
- `EVENT_CHUNK (0x23)` — 2,048点イベントを512点ずつ4分割して送信
- `EVENT_DATA (0x20)`
- `ACK (0x70)` / `NACK (0x71)`

`STATUS.flags` のbit 0はLCDへの `test` 描画成功、bit 1～3はLED1～3の実出力状態です。起動自己診断がすべて成功した場合は `0x0f` になります。

端末診断用にASCIIの `PING`、`STATUS`、`CAPTURE`、`GET_STATIC` も使用できます。

起動時は停止状態です。`CAPTURE` は2,048点を取得して送信後に停止へ戻るため、繰り返し安全に呼べます。通常の衝撃収録は `START` で待機し、64点（2.5 ms）のプリトリガを含む2,048点（80 ms）を返します。推論モードだけは現行モデル互換の512点を使用します。5 mm板向けの±32 g設定ではjerkしきい値700 LSB、levelしきい値200 LSB、16サンプル以内の確認振幅3,000 LSBです。

## 実機確認値

±32 g設定（1024 LSB/g）の実機強制測定では、静止Z軸平均−953 LSB（約−0.93 g、取付方向による符号）、標準偏差218 LSB、クリッピング0点でした。STATUS応答は512点、trigger index 64、25.6 kHzを返しました。

2026-07-17のCOM3再検証結果は旧±8 g・jerk 2,000の条件です。±32 gで物理換算したjerk 500は静止8秒以内に自然発火したため、実測最大隣接差760 LSBに余裕を持たせて1,000へ変更しました。jerk 1,000では静止30秒間の自然発火は0件でした。旧条件では
強制測定1回の後にさらに6秒待機しても合計1件のままであることを確認しました。実際の打撃に
対する感度は、アクリル板を取り付けた状態で打撃強度を変えて最終調整します。
