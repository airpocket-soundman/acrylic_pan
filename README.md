# Acrylic Pan

ROHM Edge Hack Challenge 向けに、アクリルパネルを打楽器兼タッチインターフェースにするプロジェクトです。

パネル中央に KX134-1211 加速度センサを1個取り付け、DT-EBML63Q2557（ML63Q2557、Solist-AI）で
打撃波形を取得します。打撃位置によって振動モードの出方が変わることを利用し、Solist-AI が
どのエリアを叩いたかを推論して、音階・確率ヒートマップ・推定座標へ変換します。
板は2点で支持します(以前の1点支持では、連打で振動が残って誤検知したため)。

Solist-AI では、次の3つを使っています。

- **単一の60クラス分類**: 入力128・隠れ層32・出力60のELMを1回実行し、60座標の確率を出します。12エリア分類は、この確率をエリアごとに合計して求めます。
- **60クラスODL**: AxlCOREのオンデバイス学習で、設置やマレットの違いを補正します。高速は12中心点のソフトターゲット、精密は60地点のone-hotターゲットを使います。
- **小さなモデル**: 学習するのは出力側のβ(60位置で1,920個、約3.75 KiB)だけで、入力側のαはAxlCOREがseedから生成します。

## 対応パネル

| パネル | エリア分割 | 対応範囲 |
|---|---|---|
| 400 × 300 × 5 mm | 4 × 3（12エリア） | 現行ファームウェアの推論対象。単一60クラスモデルによる位置確率、12エリア集約、現場キャリブレーション(ODL) |
| 400 × 200 × 3 mm | 4 × 2（8エリア） | 初期の試作パネル。収録とWeb UIの板仕様、8クラスモデルの学習に対応 |

座標はパネルを正面から見た左上を原点とし、エリア番号は左上から右へ、上の行から順に振ります。

```text
(0, 0) ┌──────── 400 mm ────────┐
       │ 1  │ 2  │ 3  │ 4  │
       ├────┼────┼────┼────┤
       │ 5  │ 6  │ 7  │ 8  │ 300 mm
       ├────┼────┼────┼────┤
       │ 9  │ 10 │ 11 │ 12 │
       └─────────────────────┘ (400, 300)
```

## 処理の流れ

1. KX134-1211 のZ軸加速度を 25.6 kHz・±32 g で連続取得する。
2. 加速度差分と振幅確認で打撃を検出し、プリトリガ64点を含む512点（20 ms）を切り出す。
3. 打撃後448点(17.5 ms)から128点を取り出し、振幅で正規化・標準化して Solist-AI へ入力する。
4. 入力128・隠れ層32・出力60の Solist-AI モデルが60座標の確率を出力する。
5. PCまたはRaspberry PiのWeb UIが、60確率を12エリアへ集約し、音階・ヒートマップ・座標として表示・発音する。MCUとのUSBシリアルは起動後に960 kbpsへ切り替える。

設置後に画面で指定された地点を叩くだけで、モデルをその場の固定状態やマレットに合わせる**現場キャリブレーション**に対応しています。
サインペンに替えた実機の評価では、60打のODLで、12エリアの正解率が60.0%から95.0%になりました
([記録](docs/odl-mallet-experiment-20261020.md))。この数値は旧12クラスモデルでの実験記録で、現行実装では60位置モデルそのものをODLします。

## すぐに使う

実機のUARTから推論結果を受け取ってブラウザで発音する画面は、PC上のPythonサーバーで動作します。

```powershell
.\scripts\run-monitor.ps1 `
  -Python C:\ProgramData\anaconda3\python.exe `
  -Page instrument-probability.html
```

起動後に [http://127.0.0.1:8765/instrument-probability.html](http://127.0.0.1:8765/instrument-probability.html) を開き、
板仕様とCOMポートを選んで「接続」→「演奏開始」の順に操作します。

| 用途 | ローカルURL |
|---|---|
| 推論結果 | `http://127.0.0.1:8765/` |
| 学習データ採取 | `http://127.0.0.1:8765/collector.html` |
| 位置推定 | `http://127.0.0.1:8765/position.html` |
| 旧クラス演奏UI（60位置確率を12エリアへ集約） | `http://127.0.0.1:8765/instrument.html` |
| 60座標確率から発音する楽器UI（ODL画面を含む） | `http://127.0.0.1:8765/instrument-probability.html` |

発表用のスライド型の画面は、次の手順で開きます([決勝審査会プレゼン資料](docs/final-presentation-plan.md))。

```powershell
.\scripts\run-presentation.ps1 -Python C:\ProgramData\anaconda3\python.exe
```

メイン画面は `http://127.0.0.1:8780/presentation/index.html`、プレゼンター画面は `http://127.0.0.1:8780/presentation/presenter.html` です。

現場キャリブレーションは次のコマンドで行います。画面の案内に従って各地点を叩きます。

```powershell
C:\ProgramData\anaconda3\python.exe -m pc.acrylic_pan_monitor.calibrate --port COM3
```

`run-monitor.ps1` の既定値はPATH上の `python` です。必要なライブラリが入った Python を
`-Python` で指定してください。環境の確認方法やエラー対処は
[収録システム・実機Web UIクイックスタート](docs/collector-quickstart.md)を参照してください。

## リポジトリ構成

| パス | 内容 |
|---|---|
| `firmware/AcrylicPanCollector/` | ML63Q2557 用ファームウェア（ベンダーサンプルへ重ねるオーバーレイ）。推論・ODL・整数softmax |
| `pc/acrylic_pan_monitor/` | 通信プロトコル、収録、キャリブレーションツール |
| `pc/acrylic_pan_web/` | 実機Web UI（収録・推論表示・楽器・位置推定） |
| `sim/` | モデル学習、評価実験(CNN・ELM・MLPの比較、ODLの実機実験を含む)、振動シミュレーション |
| `web/presentation/` | 決勝審査会用のスライド型Webアプリ(メイン画面・プレゼンター画面) |
| `models/` | 支持方法ごとに保存した学習済みモデル(`scripts/model_snapshot.py`) |
| `calculix/`、`run-*.ps1` | CalculiX・ソリッドFEMによる振動解析 |
| `scripts/` | ビルド・書き込み・学習・起動用スクリプト |
| `tests/` | Python のテスト（`python -m pytest tests`） |
| `docs/` | 開発ドキュメント |
| `doc/` | ROHM・LAPIS 提供資料（PDFと検索用テキスト） |
| `web/`、`index.html` | 設計内容をまとめたHTMLドキュメント |
| `data/` | 収録データとダミーモデル（収録データ本体は git 管理外） |

## ドキュメント

開発に必要なドキュメントは [docs/README.md](docs/README.md) に目的別にまとめています。
