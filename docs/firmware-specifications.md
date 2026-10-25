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
| 1 | 12エリア推論（結果と512点波形を送信） | 60位置確率を12エリアへ集約 |
| 2 | 楽器用の低遅延推論（結果のみ送信、再トリガ抑止付き） | 60位置確率を12エリアへ集約 |
| 3 | 60座標の確率分布推論 | 位置確率（1ヘッド） |
| 4 | 現場キャリブレーション | 60位置モデルをODLで学習 |

| ソース | 役割 |
|---|---|
| `apan_capture.c` | 連続サンプルからの打撃検出と波形切り出し |
| `apan_protocol.c` | APANフレームの符号化・復号 |
| `apan_inference.c` | 旧12クラスモデルの履歴（通常ビルド対象外） |
| `apan_position_inference.c` | 60座標位置確率の単一推論と2方式のODL学習(AxlCORE) |
| `apan_calibration.c` | 確定したβのFRAM保存(CRC32付き)と、工場βとの照合 |
| `apan_odl_experiment.c` | AxlCORE ODLとCPUのOS-ELMの比較実験(通常のビルドでは空。`APAN_ODL_EXPERIMENT` = 1 のとき有効) |
| `apan_ai_selftest.c` | 固定8クラスモデルによる Solist-AI の自己試験 |
| `integration/apan_collector_app.c` | モード管理、コマンド処理、送信、LCD・LED表示 |

## 2. ハードウェア

- MCU: ML63Q2557、Cortex-M0+ 48 MHz、Flash 256 KB、ワークRAM 16 KB、Solist-AI（AxlCORE-ODL）
- センサ: KX134-1211、Z軸、±32 g（1024 LSB/g）、ODR 25.6 kHz
- FRAM: MB85RS2MTA（256 KB、ソフトウェアSPI）。確定した現場キャリブレーションのβを保持する
- 通信: UARTF1、8-N-1、フロー制御なし。起動時は 115200 bps で、`SET_CONFIG` の要求で 960 kbps へ切り替える([レイテンシ](latency.md))
- センサ位置: パネル中央(交換用の評価基板 KX134-1211-EVK-001 の配線は[変換配線](kx134-evk-adapter.md))
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
| 位置確率（現行） | 入力128・隠れ32・出力60、hard sigmoid | 60座標のロジット、温度0.05のsoftmax。12エリア用途では確率をエリアごとに合計 | `apan_position_probability_model.h` |
| 12クラス分類（履歴） | 入力128・隠れ32・出力12 | 過去の比較・ODL実験用。通常ビルド対象外 | `apan_12class_model.h` |
| 自己試験 | 入力128・隠れ32・出力8 | 固定入力8件の出力 | `apan_dummy_model.h` |

α は公式 Simulator の seed-1 射影で、学習するのは β だけである。α は AxlCORE が `ODL_Initialize` の seed と範囲から
生成するので、通常のビルドは α の配列を持たない(位置確率のヘッダーも β だけを持つ)。α・β・入力・出力は
bfloat16 で扱う。PC 側の参照計算（`sim.dummy_model_pipeline.mcu_reference`）は
同じ bfloat16 の丸め位置を再現する。

位置確率は60出力を1回実行し、温度0.05のsoftmaxで確率にする。旧5ヘッドとのシミュレーター比較では全4,680件の全logitがbfloat16ビット単位で一致した。
softmax はボード上の整数演算(Q27のロジット、3つの指数テーブル、64ビット整数の乗算)で、float32の `expf` との最大絶対誤差は約3e-8 である。

## 6. 現場キャリブレーション

60位置モデルの β を、設置後の打撃で現場の固定状態に合わせる。高速方式は12中心点を同一エリア5点のソフトターゲット（0.6、4 × 0.1）で学習し、精密方式は60地点をone-hotで学習する。保存済みβは方式にかかわらず1件で、新しいコミットが以前の結果を置き換える。

- 更新: AxlCORE の `ODL_StartTrain`（忘却係数1）で、1打ごとに学習する。P と β は AxlCORE が bfloat16 で保持する。
  開始時に、工場 β と P0（bfloat16へ丸めたもの）を AxlCORE へ書く。旧12出力モデルでの更新時間は約2 msだったが、60出力版の実機時間は別途計測する。
  CPU の float32 で OS-ELM を計算する方式は、`APAN_CALIBRATION_ENGINE` を 0 にしたビルドで使える
  （[AxlCORE ODL の実機実験](odl-engine-experiment-20261020.md)）。
- 初期値: 工場 β と `P0 = 30 (G + I)⁻¹`（`generated/apan_calibration_prior.h`）。
  校正1打は工場データ30打分の重みを持つ。
- 記憶域: 作業中の P と β は AxlCORE にある。FRAM のアドレス 100000 以降には、確定した β（32 × 60、float32）と
  ヘッダーだけを置く。
- 保存: 確定時に、AxlCORE の β を1行ずつ読み出して保存済み β を書き、CRC32 付きヘッダーを最後に書く。起動時は、ヘッダーの
  工場モデル CRC32 と β の CRC32 が一致した場合だけ保存済み β を使う。
- 適用範囲: mode 1・2・3・4。同じ60位置βを位置表示と12エリア集約の両方へ使う。

現行仕様は[60クラスODL仕様変更](position-odl-60class-design-20261024.md)、旧12クラスの評価は[現場キャリブレーションのPC検証](odl-calibration-experiment-20260927.md)を参照する。

## 7. 資源

| 項目 | 値 |
|---|---|
| Flash（text） | 56,244 B / 256 KB（60出力版の実ビルド） |
| RAM（data + bss） | 13,088 B（data 1,308 B + bss 11,780 B）/ 16 KB（60出力版の実ビルド） |
| 現場キャリブレーションの静的作業領域 | 行バッファ等736 B（32要素の`p_h`・`p_row`と60要素の`error`・`beta_row`） |
| FRAM使用量 | 約19.5 KBを予約（CPU代替方式のP・作業β、確定β 32 × 60 のfloat32、ヘッダー。通常のエンジン方式で永続化する本体は約7.5 KB） |

## 8. 検証

- `firmware/AcrylicPanCollector/tools/test-host.ps1`: 打撃検出、APANフレーム、
  現場キャリブレーションの数値計算を Visual C++ でホスト実行して検証する。
- `AI_SELFTEST`: 固定入力に対する Solist-AI の出力を PC の参照値と比較する。ケース 0〜7 は固定8クラスモデル
  （[ダミーモデルによる検証](ai-dummy-validation.md)）、0x80〜0x87 は位置確率モデルとそのsoftmaxである。旧12クラス固定入力は通常実装から外した。
- `python -m pytest tests`: PC 側のプロトコル、学習パイプライン、ヘッダーの整合性を検証する。
