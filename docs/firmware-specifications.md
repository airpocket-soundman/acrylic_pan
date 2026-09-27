# Acrylic Pan ファームウェア仕様

対象: DT-EBML63Q2557（ML63Q2557）/ KX134-1211

通信プロトコルの各メッセージ形式、ビルドと書き込みの手順は
[ファームウェア実装と通信プロトコル](../firmware/AcrylicPanCollector/README.md)に記載する。
本書はファームウェア全体の構成と、取得・推論・学習の仕様をまとめる。

## 1. 構成

ベンダーの `AIVibrationInference` サンプルを複製した private プロジェクトへ、
`firmware/AcrylicPanCollector` のオーバーレイ（`S_AcrylicPan`）と `main.c` を重ねて
1つのイメージを作る。収録・推論・学習は同じイメージの動作モードとして切り替える。

| mode | 用途 | 使うモデル |
|---:|---|---|
| 0 | 教師データの収録（2,048点波形を送信） | なし |
| 1 | 12エリア推論（結果と512点波形を送信） | 12クラス分類 |
| 2 | 楽器用の低遅延推論（結果のみ送信、再トリガ抑止付き） | 12クラス分類 |
| 3 | 60座標の確率分布推論 | 位置確率（5ヘッド） |
| 4 | 現場キャリブレーション | 12クラス分類を学習 |

| ソース | 役割 |
|---|---|
| `apan_capture.c` | 連続サンプルからの打撃検出と波形切り出し |
| `apan_protocol.c` | APANフレームの符号化・復号 |
| `apan_inference.c` | 12クラス分類の前処理と Solist-AI 推論 |
| `apan_position_inference.c` | 60座標位置確率の推論 |
| `apan_calibration.c` | OS-ELMによるβの逐次学習とFRAM保存 |
| `apan_ai_selftest.c` | 固定8クラスモデルによる Solist-AI の自己試験 |
| `integration/apan_collector_app.c` | モード管理、コマンド処理、送信、LCD・LED表示 |

## 2. ハードウェア

- MCU: ML63Q2557、Cortex-M0+ 48 MHz、Flash 256 KB、ワークRAM 16 KB、Solist-AI（AxlCORE-ODL）
- センサ: KX134-1211、Z軸、±32 g（1024 LSB/g）、ODR 25.6 kHz
- FRAM: MB85RS2MTA（256 KB、ソフトウェアSPI）。現場キャリブレーションのPとβを保持する
- 通信: UARTF1、115200 bps、8-N-1、フロー制御なし
- センサ位置: パネル中央
- 表示: 16文字×2行LCD、LED 3個

## 3. 通信フレーム

UARTはCOBSで符号化したバイナリフレームとし、`0x00` をフレーム境界に使う。
数値は little endian で、CRC32 は COBS 変換前の header + payload に適用する。

```text
magic[4] = "APAN"
protocol_version : uint8   (1)
message_type     : uint8
flags            : uint16
sequence         : uint32
timestamp_us     : uint32
payload_length   : uint16
payload[]
crc32            : uint32
```

応答は要求の sequence を保持し、受理した要求には `ACK`、拒否した要求には理由付きの
`NACK` を返す。診断用に `PING`、`STATUS`、`CAPTURE` のASCIIコマンドも受け付ける。

## 4. 打撃検出と切り出し

- 512点のダブルバッファで連続取得し、ブロック境界をまたいで判定する。
- 候補: 隣接サンプル差 ≥ 700 LSB かつ振幅 ≥ 200 LSB。
- 確定: 候補から16サンプル以内に、直前64サンプル平均からの偏差が 3,000 LSB 以上になること。
- 切り出し: プリトリガ64点（2.5 ms）を含み、収録モードは2,048点（80 ms）、
  推論モードは512点（20 ms）。トリガ位置は常にインデックス64。
- イベント全体がそろってからセンサを停止し、送信・推論を行う。
- mode 2 では、指定間隔（0〜500 ms、既定80 ms）内の再打撃を結果として送らない。

## 5. 推論

### 5.1 前処理

1. プリトリガ64点の平均をベースラインとして差し引く。
2. トリガ後448点の絶対値最大で割り、振幅を正規化する。
3. トリガ後448点から等間隔に128点を取り出す。
4. 学習時の平均・標準偏差で標準化し、bfloat16 へ丸めて Solist-AI へ入力する。

前処理の係数は PC の学習パイプラインが `generated/*.h` へ書き出し、PCとMCUで同じ入力になる。

### 5.2 モデル

| モデル | 構成 | 出力 | ヘッダー |
|---|---|---|---|
| 12クラス分類 | 入力128・隠れ32・出力12、hard sigmoid | 12エリアのスコア、argmax をエリアとする | `apan_12class_model.h` |
| 位置確率 | 入力128・隠れ32・出力12 × 5ヘッド | 60座標のロジット、温度0.05のsoftmax | `apan_position_probability_model.h` |
| 自己試験 | 入力128・隠れ32・出力8 | 固定入力8件の出力 | `apan_dummy_model.h` |

α は公式 Simulator の seed-1 射影で、学習するのは β だけである。α・β・入力・出力は
bfloat16 で扱う。PC 側の参照計算（`sim.dummy_model_pipeline.mcu_reference`）は
同じ bfloat16 の丸め位置を再現する。

## 6. 現場キャリブレーション

12クラス分類の β を、設置後の数十打で現場の固定状態に合わせる。

- 更新: 忘却係数1の OS-ELM を CPU の float32 で計算し、更新後の β を bfloat16 へ丸めて
  AxlCORE へ書き込む。AxlCORE の `ODL_StartTrain` は P と β を bfloat16 で保持するため使わない。
- 初期値: 工場 β と `P0 = 30 (G + I)⁻¹`（`generated/apan_calibration_prior.h`）。
  校正1打は工場データ30打分の重みを持つ。
- 記憶域: FRAM のアドレス 100000 以降に、作業用 P（32 × 32）・作業用 β（32 × 12）・
  保存済み β・ヘッダーを float32 で置き、1行ずつ読み書きする。
- 保存: 確定時に保存済み β を書き、CRC32 付きヘッダーを最後に書く。起動時は、ヘッダーの
  工場モデル CRC32 と β の CRC32 が一致した場合だけ保存済み β を使う。
- 適用範囲: mode 1・2・4。位置確率（mode 3）は対象外。

評価と設計判断は[現場キャリブレーションのPC検証](odl-calibration-experiment-20260927.md)を参照する。

## 7. 資源

| 項目 | 値 |
|---|---|
| Flash（text） | 約64.7 KB / 256 KB |
| RAM（data + bss） | 12,116 B、スタック 1,280 B、空き約2.9 KB / 16 KB |
| 現場キャリブレーションの追加RAM | 316 B |
| FRAM使用量 | 約7.2 KB（アドレス 100000〜） |

## 8. 検証

- `firmware/AcrylicPanCollector/tools/test-host.ps1`: 打撃検出、APANフレーム、
  現場キャリブレーションの数値計算を Visual C++ でホスト実行して検証する。
- `AI_SELFTEST`: 固定入力に対する Solist-AI の出力を PC の参照値と比較する
  （[ダミーモデルによる検証](ai-dummy-validation.md)）。
- `python -m pytest tests`: PC 側のプロトコル、学習パイプライン、ヘッダーの整合性を検証する。
