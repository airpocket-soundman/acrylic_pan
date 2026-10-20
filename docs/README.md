# 開発ドキュメント目次

Acrylic Pan の開発に必要なドキュメントを目的別にまとめる。まず「環境と手順」で開発環境を整え、
「仕様」で現在の構成を把握し、判断の根拠が必要になったら「実験記録」を参照する。

## 環境と手順

| ドキュメント | 内容 |
|---|---|
| [開発環境](development-environment.md) | DT-EBML63Q2557、LEXIDE、書き込み環境の構成 |
| [収録システム・実機Web UIクイックスタート](collector-quickstart.md) | Python環境の確認、ファームのビルド・書き込み、Web UIでの収録と演奏 |
| [Solist-AI Simulator](solist-ai-simulator.md) | 公式Simulatorの導入、CSV形式、8クラスモデルの作成手順 |
| [シミュレーション環境](simulation-environment.md) | Docker・CalculiX など振動解析で使うソフトと実行条件 |
| [KX134-1211-EVK-001 変換配線](kx134-evk-adapter.md) | 交換用センサ評価基板の配線(実機確認済み) |
| [Web UIの機能](webui-improvements.md) | 音色、8点フィット、ODL、接続模式図、プレゼン用の埋め込み |
| [決勝審査会プレゼン資料](final-presentation-plan.md) | 決勝審査会用のスライド型Webアプリ(メイン画面・プレゼンター画面)の構成と使い方 |
| [レイテンシ](latency.md) | 打撃から発音までの経路、遅延の内訳、短縮の対策、FTDIのLatency Timer設定 |

## 仕様

| ドキュメント | 内容 |
|---|---|
| [設計メモ](design.md) | パネル、固定方法、打点配置、収録仕様などの全体設計 |
| [ファームウェア仕様](firmware-specifications.md) | 取得・打撃検出・推論・表示の仕様 |
| [ファームウェア実装と通信プロトコル](../firmware/AcrylicPanCollector/README.md) | 動作モード、APANプロトコル、現場キャリブレーション、ビルド手順 |
| [中央センサ時間波形・FFT仕様](sensor-response.md) | 波形の切り出しとFFTの定義 |
| [PC側・確率分布型位置推定](position-inference.md) | 60座標の確率分布による位置推定と評価 |
| [データ配置](../data/README.md) | 収録データとダミーモデルの置き場所 |

## 振動シミュレーション

| ドキュメント | 内容 |
|---|---|
| [振動シミュレーション手法](simulation-method.md) | 薄板モデルによる固有モードと応答の解析手法 |
| [CalculiX基準解析](calculix-analysis.md) | 3 mm・5 mm板の固有モード解析と板厚比較 |
| [CalculiX高周波・50 ms窓解析](calculix-highfrequency.md) | 25.6 kHz取得を想定した高周波帯の識別性 |
| [CalculiX 50 mm格子・XY回帰比較](calculix-xy-grid.md) | 格子打点を加えたときの座標回帰の改善 |
| [3次元ソリッドFEM](solid-fem.md) | 板厚方向を含む固有モードと過渡応答 |

## 実験記録

モデルと処理方式を決めた評価の記録。日付順に並べ、結論を1行で示す。

| 日付 | ドキュメント | 結論 |
|---|---|---|
| 2026-07-16 | [ダミーモデルによるSolist-AI検証](ai-dummy-validation.md) | PCの参照計算と実機Solist-AIの出力が一致することを確認 |
| 2026-07 | [実測振動による8クラスモデル学習](real-model-training.md) | 3 mm板・512点入力の初期8クラスモデル |
| 2026-07-18 | [サンプリング周波数比較](sampling-experiment-20260718.md) | 8クラス分類は6.4 kHz・FFT 128入力が最良（98.63%） |
| 2026-07-18 | [打撃トリガー閾値の再評価](trigger-threshold-analysis-20260718.md) | 一次差分1,000 LSBに振幅確認を加えて誤トリガを抑制 |
| 2026-08-21 | [12クラス・3セッション追加学習](model-training-3sessions-20260821.md) | 独立セッションの精度が97.17%から98.50%へ改善 |
| 2026-08-21 | [12クラス・4セッション学習](model-training-4sessions-20260821.md) | 4セッション・2,345件で再学習、LOSO平均92.31% → 95.12% |
| 2026-08-21 | [2・3・4セッション共通評価](shared-holdout-evaluation-20260821.md) | 同じ480件で96.25% → 98.13% → 98.33% |
| 2026-08-23 | [直接分類と座標由来分類の比較](area-classification-comparison-20260823.md) | 12クラス直接分類と座標由来分類の差は+0.07ポイント |
| 2026-08-23 | [KX134センサ診断](sensor-diagnostic-20260823.md) | センサ無応答の原因を電源・配線・基板の順に切り分け |
| 2026-08 | [PC側・中心点XY座標回帰](pc-xy-regression.md) | 自由なMLPで中心座標の平均距離誤差が47.15 mm → 5.68 mm |
| 2026-08-28 | [12クラス最大データ再学習](model-training-max-data-20260828.md) | 全7,132件で配備モデルを再学習、四隅精度52.4% → 85.3% |
| 2026-08-28 | [疑似XY回帰と直接XY回帰](pc-pseudo-vs-direct-xy-20260828.md) | 既知座標では60クラス確率方式が有利 |
| 2026-08-29 | [MPU9250代替評価](mpu9250-substitution-experiment-20260829.md) | 4 kHz・20 ms相当でも平均距離誤差6.63 mmでKX134と同等 |
| 2026-09-27 | [オンデバイス学習による現場キャリブレーション](odl-calibration-experiment-20260927.md) | 60打の校正で未知セッション精度が87.9% → 90.2%、ファームウェアに実装 |
| 2026-10-19 | [2点支持の学習](support-2point-training-20261019.md) | 2点支持の5セッション(3,600打)で、デバイス・PCのモデルを学習し直した。60位置の正解率97.9%(同一セッション内) |
| 2026-10-20 | [CNN・MLP・ELMの比較](cnn-comparison-20261020.md) | 同じ分割で、ELM 97.9%、PC版MLP 99.2%、CNN 99.5%。ELMは学習するパラメータが約1/580 |
| 2026-10-20 | [AxlCORE ODLの安定性の実機実験](odl-engine-experiment-20261020.md) | ODLのエンジン方式が960回の更新で発散せず、現場キャリブレーションの既定にした |
| 2026-10-20 | [ODLでマレットの違いを吸収できるか](odl-mallet-experiment-20261020.md) | サインペンに替えた実機で、60打のODLにより正解率 60.0% → 95.0% |

## 外部資料

ROHM・LAPIS の提供資料は [`doc/`](../doc) に PDF と検索用テキストで保存している。
Solist-AI のアルゴリズムと AxlCORE-ODL は `solist-ai_algorithm_axlcore-odl_an-j`、
AIライブラリの API は `Solist-AI_AnomalyDetection_an-j`、MCU の仕様は `FJDL63Q2500` を参照する。
