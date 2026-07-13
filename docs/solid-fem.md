# 3次元ソリッドFEM解析仕様

## 目的

400 × 200 × 2 mmのPMMA板を3次元弾性体として離散化し、100 × 20 mmの挟み込み固定領域を板厚全体で拘束したときの固有モードと、8打点に対する過渡応答を比較する。中央の加速度センサは1個だけとし、上面 `(200, 100, 1) mm` でZ方向応答を観測する。

## 解析モデル

| 項目 | 条件 |
|---|---|
| 支配方程式 | 3次元微小変形・線形弾性 |
| 要素 | 8節点六面体ソリッド（HEX8） |
| 積分 | 2 × 2 × 2 Gauss完全積分 |
| メッシュ | 32 × 16 × 2要素、1,683節点、5,049自由度 |
| 要素寸法 | 12.5 × 12.5 × 1.0 mm |
| 材料 | 等方PMMA、E=3.2 GPa、ν=0.35、ρ=1,180 kg/m³ |
| 質量 | 節点集中質量 |
| 拘束 | x=200–300、y=0–20、z=-1–1 mmに含まれる54節点のXYZ変位を固定 |
| 固有値解法 | SciPy `eigsh`、shift-invert、σ=0 |
| 過渡応答 | 16モードのモード重ね合わせ、各打点へZ方向単位力積、モード減衰比1.2% |

出力した先頭8固有振動数は `18.362, 40.644, 53.146, 96.750, 166.043, 236.237, 272.552, 307.045 Hz`。板理論モデルとの差は、3次元応力状態、メッシュ、質量近似、固定節点の表現に加えて、薄肉ソリッドのせん断ロッキングの影響を含む。このため絶対値の確定値ではなく、実測FRFとの照合前の比較モデルとして扱う。

## 出力

- `solid3d-mesh.svg`: ワークの3次元メッシュ、固定領域、中央センサ
- `solid3d-mode-1.svg`～`solid3d-mode-6.svg`: 変形を強調した固有モード静止画
- `solid3d-eight-hit-stills.svg`: 8打点ごとの代表変形静止画
- `solid3d-eight-hits.mp4`: 8打点を同一時間軸・共通振幅スケールで比較するスローモーション動画
- `solid3d-results.json`: メッシュ、材料、境界条件、周波数、センサ結合係数

動画の色はZ変位の正負、星印は打点、菱形は中央センサを示す。変位は比較用に正規化しており、実変位量ではない。

## Docker再現条件

本プロジェクトはCVATとは独立した専用イメージと一時コンテナを使用する。

| 項目 | 固定値 |
|---|---|
| イメージ名 | `acrylic-pan-solid-fem:local` |
| 実行コンテナ名 | `acrylic-pan-solid-fem-run` |
| ベース | `python:3.12-slim`、digest `sha256:423ed6…9fbf` |
| Python依存 | NumPy 2.2.6、SciPy 1.15.3、Matplotlib 3.10.3 |
| 動画エンコーダ | ffmpeg 7:7.1.5-0+deb13u1、H.264/yuv420p |
| ネットワーク | 実行時 `--network none` |
| ファイルシステム | コンテナをread-only、`/tmp`だけtmpfs |
| 書込み許可 | このリポジトリの `web/assets/simulation` のみbind mount |
| 後処理 | `--rm`で解析終了時に専用コンテナを削除 |

再現コマンドはPowerShellで次の1行。

```powershell
.\run-solid-fem.ps1
```

スクリプトは同名コンテナが既に存在すると停止し、勝手に削除しない。CVATのコンテナ、イメージ、ネットワーク、ボリュームを列挙・停止・削除・再構築する処理は含まない。

手動実行する場合も、専用名と出力先を維持する。

```powershell
docker build -t acrylic-pan-solid-fem:local .
$out = (Resolve-Path "web\assets\simulation").Path
docker run --rm --name acrylic-pan-solid-fem-run --network none --read-only `
  --tmpfs /tmp:rw,nosuid,nodev,size=256m `
  -e PYTHONDONTWRITEBYTECODE=1 -e MPLCONFIGDIR=/tmp/matplotlib `
  -v "${out}:/workspace/web/assets/simulation" acrylic-pan-solid-fem:local
```

## 精度上の注意と次の改善

現モデルは3次元ソリッドFEMだが、HEX8完全積分は板厚に対して面内要素が大きく、曲げでせん断ロッキングが生じ得る。競技提出用の確度を上げるには、次の順で比較する。

1. 面内メッシュを2倍にして固有振動数とMACの収束を確認する。
2. 二次六面体要素または選択低減積分要素と比較する。
3. センサ基板・接着層の質量と剛性を追加する。
4. クランプを完全固定から接触・摩擦・締付け圧へ発展させる。
5. 実測FRFで材料減衰、ヤング率、固定剛性を同定する。
