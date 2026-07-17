# Acrylic Pan ファームウェア仕様

文書版: Draft 0.1  
対象: DT-EBML63Q2557 / ML63Q2557 / KX134-1211  
作成日: 2026-07-13

## 1. 方針

用途の異なる2つの独立したファームウェアを作る。

| バイナリ | 目的 | AIモデル |
|---|---|---|
| `acrylic_pan_collector` | 教師データの高品質な収録 | 搭載しない |
| `acrylic_pan_inference` | 打撃位置推論と低遅延イベント出力 | 8出力モデルを搭載 |

センサ、時刻、CRC、USB-UART、共通パケット、エラー処理は共通モジュールにする。
初期段階ではJ-Linkで書き分ける。自己書換えブートローダや1バイナリ内のモード切替は
実装範囲に含めない。

## 2. 共通仕様

### 2.1 ハードウェア

- MCU: ML63Q2557、48 MHz、Flash 256 KB、RAM 16 KB
- センサ: KX134-1211、Z軸、符号付き16 bit
- 初期設定: ±8 g、ODR 25.6 kHz（最大）、High Performance、LPF ODR/2
- USB: FT2232H Channel B / UARTF1、115200 bps、8-N-1、フロー制御なし
- センサ位置: `(200,100) mm`

### 2.2 バージョン情報

両ファームは次を応答する。

- firmware kind: `COLLECTOR` / `INFERENCE`
- semantic version
- protocol version
- Git commit
- build timestamp
- sensor configuration
- model version/hash（推論用のみ）

### 2.3 通信フレーム

UARTはバイナリフレームとする。COBSで符号化し、`0x00`をフレーム境界に使用する。

```text
magic[4] = "APAN"
protocol_version : uint8
message_type     : uint8
flags            : uint16
sequence         : uint32
timestamp_us     : uint32
payload_length   : uint16
payload[]
crc32            : uint32
```

数値はlittle endian。CRCはCOBS変換前のheader + payloadへ適用する。sequence欠落、
CRC不一致、長さ不一致をPCで検出する。

### 2.4 共通コマンド

| コマンド | 内容 |
|---|---|
| `HELLO` | 識別・バージョン取得 |
| `GET_STATUS` | 状態、エラー、統計取得 |
| `GET_CONFIG` | センサ・通信設定取得 |
| `SET_CONFIG` | 許可された設定変更 |
| `START` | 動作開始 |
| `STOP` | 安全に停止してIDLEへ |
| `CLEAR_STATS` | 統計カウンタ初期化 |
| `PING` | 通信確認 |
| `RESET` | MCUソフトリセット |

全コマンドにACK/NACKを返し、NACKは理由コードを含む。実行中に変更できない設定は
`ERR_BUSY`を返す。

### 2.5 共通品質フラグ

- `CLIPPED`: ±8 g付近で飽和
- `TOO_WEAK`: トリガ後ピークが採用下限未満
- `MULTI_PEAK`: 同一窓に複数の立上り
- `BUFFER_OVERRUN`: サンプル欠落
- `TX_BACKLOG`: 未送信イベント滞留
- `SENSOR_ERROR`: KX134通信／設定異常
- `CRC_ERROR`: PCコマンド破損
- `TIMING_ERROR`: サンプル周期逸脱

## 3. データ採取用ファームウェア

### 3.1 目的

PCが教師ラベルとセッションを管理し、ボードは連続サンプリング、リングバッファ、
打撃検出、波形切出し、欠損のない送信を担当する。AIライブラリはリンクしない。

### 3.2 状態

```text
BOOT -> SELF_TEST -> IDLE -> ARMED -> CAPTURING -> QUEUED -> TRANSMITTING
                      ^                                      |
                      +--------------------------------------+
任意状態 -> ERROR -> IDLE（CLEAR/RESET後）
```

### 3.3 通常EVENTモード

- リングバッファ: Z軸2048点、80 ms、int16
- トリガ: `abs(z[n]-z[n-1])` を基本とするjerkしきい値
- 保存: 1,280点、前128点（5 ms）+ 後1,152点（45 ms）
- trigger index: 128
- 再トリガ抑止: 初期150 ms、PCから変更可能
- 1 Hz程度の単打収録に使用
- 1イベント約2.5 KB、UART送信は1 Hz程度の教師収録に限定

トリガ時刻は仮位置である。PCは保存波形内の最大jerkを再探索し、整列済み位置を
manifestへ別フィールドで保存する。

### 3.4 BURSTモード

同時打撃と100 ms連打を収録する。

- 明示的なPCコマンドでARM
- 25.6 kHzで取得しながらアンチエイリアスLPFと4分周を実行
- 保存はZ軸6.4 kHz・2048点、320 msの1ブロック
- 先頭に40 ms以上の静止区間を含める
- ブロック内の全ピークを保存し、ボード側では1打へ分割しない
- 取得完了後にUART送信
- 100/150/200 ms間隔、同一点連打、異なる点への遷移をPC側でラベル化

### 3.5 メッセージ

- `ARM_EVENT`: 通常イベント収録開始
- `ARM_BURST`: バースト収録開始
- `DISARM`: トリガ待ち解除
- `SET_TRIGGER`: しきい値、抑止時間、弱打下限
- `EVENT_DATA`: 1,280点波形とメタデータ
- `BURST_DATA`: 2048点波形とメタデータ
- `EVENT_REJECTED`: 品質フラグと統計のみ
- `STATS`: sample/event/reject/overrun/CRCカウンタ

PCのarea ID、座標、note、session IDはボード処理に使用しない。送信波形との対応確認用に
32 bitの`capture_token`だけボードへ渡し、PC manifestと照合する。

### 3.6 受入条件

- 25.6 kHzのサンプル数誤差0、連続10分でoverrun 0
- 1 Hz × 100イベントでsequence欠落0、CRCエラー0
- 前128点のプリトリガが全イベントに存在
- ±8 g飽和を確実にflag
- 100 ms間隔の2打がBURST波形内に両方存在
- STOP後100 ms以内にIDLE
- センサ切断時にERRORへ遷移

## 4. 推論用ファームウェア

### 4.1 目的

ボード上で打撃検出、前処理、Solist-AI推論を実行し、PCへ位置スコアとイベント情報を
低遅延で送る。通常は生波形を送らない。

### 4.2 初期モデル

- 教師あり、3層FFNN、隠れ層1層
- 取得: Z軸 25.6 kHz、50 ms、1,280点
- 連続フィルタ: HPF 100 Hzを初期候補とし、約5 kHz LPF後に2分周
- 入力32～64: 12.8 kHz・640点のFFT帯域強度（Hann窓、1,024点へゼロパディング）
- 隠れ64: Hard Sigmoid
- 出力8: AREA_0..AREA_7の独立スコア
- 入力64時は合計136ノード、最終AI RAMはSolist-AI Sim表示で確認
- 演算精度: bfloat16
- 単打教師: one-hot
- 同時2点教師: multi-hot

モデル、入力正規化、しきい値、エリア／音階対応にはversionとCRCを付ける。

### 4.3 イベント処理

```text
Z 25.6 kHz連続取得 -> 連続HPF -> jerkトリガ -> 前128点（5 ms）を含む1,280点
-> 打撃開始再整列 -> LPF -> 2分周 -> 640点 -> Hann窓 -> 1024点FFT -> 帯域圧縮 -> 正規化 -> Solist-AI
-> 8スコア -> 単打/同時2点判定 -> RESULT送信
```

- 入力窓は前5 ms＋後45 msの合50 ms
- 打撃開始から60 ms以内に再アーム
- 100 ms以上離れた打撃を別イベントとして処理
- 前打の残響を含む学習データで2打目を評価
- 同時打撃は最大2エリアを返す
- 3エリア以上がしきい値を超えた場合は`AMBIGUOUS`を付け、上位2件を参考値として返す

### 4.4 判定後処理

- 各出力をPC設定の校正値で補正
- 有効下限しきい値と上位差を確認
- 単打: 1つのarea ID
- 同時2点: 2つのarea ID
- 不確実: `UNKNOWN`または`AMBIGUOUS`
- 単打時のみ確率重み付き期待座標を計算
- 同時2点時は中間座標へ潰さず、2点を別々に報告

### 4.5 メッセージ

- `START_INFERENCE` / `STOP_INFERENCE`
- `GET_MODEL_INFO`
- `SET_THRESHOLDS`
- `INFERENCE_RESULT`
- `DEBUG_SNAPSHOT`: 明示要求時だけ1,280点生波形、640点分周後波形、圧縮特徴を付加
- `INFERENCE_STATS`: hit/unknown/ambiguous/latency/overrun統計

`INFERENCE_RESULT`にはsequence、timestamp、8 raw scores、検出数、area IDs、
期待座標、peak、品質flag、推論時間を含める。

### 4.6 受入条件

- Solist-AI SimでAI RAMがML63Q2557の制約内
- 単打の別セッション8エリア精度90%以上、デモ目標95%以上
- 同時2点は完全一致率とlabel F1を報告
- 100 ms連打の2打目欠落率1%未満を目標
- 打撃開始からRESULT送信開始まで50 ms未満
- 再アーム60 ms以内
- 1時間連続動作でoverrun、hard fault、watchdog reset 0
- model CRC不一致時は推論を開始しない

## 5. 共通ソース構成案

```text
firmware/
  common/
    kx134.c
    sample_clock.c
    ring_buffer.c
    trigger.c
    protocol.c
    crc32.c
    uart_transport.c
    diagnostics.c
  collector/
    main.c
    collector_state.c
    burst_capture.c
  inference/
    main.c
    inference_state.c
    preprocessing.c
    model_config.h
    postprocess.c
pc/
  collector/
models/
```

2つのファームで共通モジュールの同じcommitを使用し、センサ波形の差がファーム差に
起因しないようにする。
