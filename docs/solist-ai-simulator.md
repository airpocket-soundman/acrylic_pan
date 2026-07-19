# Solist-AI Simulator 環境と8クラスモデル作成手順

実測2セッションによる現在の学習結果と再現コマンドは
[`real-model-training.md`](real-model-training.md)を参照する。

この文書は、Acrylic Panの振動波形から4 × 2領域（8クラス）を識別するモデルを、ROHM公式 **Solist-AI Sim 教師あり学習対応版 SLV1.00.04** で作成・検証するための再現手順である。

`D:\GitHub\IchiPing_solist_AI` で得られた知見を参照しているが、IchiPing固有の167入力・14/32出力モデルや学習データは流用しない。Acrylic Panでは実測KX134振動データ、128入力特徴、8出力one-hot教師を使う。

## 1. このPCで確認済みの環境

2026-07-16時点で、次のインストールを実ファイルで確認した。

| 項目 | このPCのパス・状態 |
|---|---|
| 公式Simulator | `C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp` |
| 実行ファイル | `C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp\application\SolistAI_Sim_SLV10004.exe` |
| 実行ファイル情報 | File/Product version `1.4.0.0`、ファイル更新日 2025-07-07 |
| MATLAB Runtime | `C:\Program Files\MATLAB\MATLAB Runtime\R2024a` |
| Runtime DLLパス | `C:\Program Files\MATLAB\MATLAB Runtime\R2024a\bin\win64` |
| 公式サンプル | Simulatorの `application\Sample_data` と `application\Sample_AImodel` |

通常はスタートメニュー、または上記の `SolistAI_Sim_SLV10004.exe` から起動する。MATLAB Runtimeの初期化により、ロゴが消えてから画面が出るまで数十秒、環境によっては数分かかる。

このリポジトリからは、次のスクリプトでインストール状態の検査と起動ができる。

```powershell
.\scripts\launch-solist-ai-sim.ps1 -CheckOnly
.\scripts\launch-solist-ai-sim.ps1
```

### 新しいPCへのインストール

1. Windows 11 x64、管理者権限、インターネット接続、Cドライブの空きを用意する。
2. ROHM配布ZIPを展開する。
3. `SolistAI_Sim_******_Installer_web.exe` を右クリックし、管理者として実行する。
4. インストーラーの指示に従う。MATLAB Runtime R2024a（約800 MB）も必要に応じて取得・導入される。
5. `C:\Program Files\ROHM\...` と `C:\Program Files\MATLAB\MATLAB Runtime\R2024a` が生成されたことを確認する。

10分以上待っても起動しない場合は、まずタスクマネージャーでプロセスを確認する。Runtime DLL探索に失敗している場合は、公式ガイドに従って `C:\Program Files\MATLAB\MATLAB Runtime\R2024a\bin\win64` をWindowsの`PATH`へ追加する。実行ファイルをRuntimeの`win64`直下へコピーして管理者実行する方法も公式ガイドに記載されているが、通常運用では元のインストール先から起動する。

## 2. Acrylic Panのデータ形式

現行の標準形式は、1行を1打撃イベントとするCSVである。

```text
input_0, ... , input_127,target_0, ... ,target_7
<128個の標準化済みFFT特徴>,<8クラスのone-hot教師>
```

- 入力: 512点波形をDC除去してHann窓を掛け、FFTのDCを除く先頭128 binをlog振幅化し、学習データの平均・標準偏差で標準化した128列。
- 教師: 8列。正解領域だけ`1`、残りを`0`とするone-hot。
- 1行 = 1 chunk、chunkの形は入力・教師とも`1 row`。
- CSVの1行目はヘッダー。Simulatorではデータとして扱わない。
- 学習時と実機推論時で、FFT、ビン範囲、log変換、標準化係数、列順を完全に一致させる。

データ生成はリポジトリルートで次を実行する。

```powershell
python -m sim.solist_dataset --npz data\captures --output-dir data\solist_sim
```

実測データがない段階の配線確認だけなら、`--npz`を省略した合成データも使用できる。ただし、合成データの精度は実機性能の根拠にしない。

主な生成物は次のとおり。

| ファイル | 用途 |
|---|---|
| `data/solist_sim/train_8class.csv` | SimulatorのTraining Data |
| `data/solist_sim/test_8class.csv` | SimulatorのTest Data |
| `data/solist_sim/feature_scaler.npz` | 実機とPCで再利用する標準化係数 |

### 100万セルの目安

公式ガイドは、ファイル内の全データ数を **1,000,000以下** にすることを推奨している。超過は必ずしもエラーではないが、読み込みが著しく遅くなる。

Acrylic Panの標準CSVは`128入力 + 8教師 = 136列`なので、ヘッダーを含めて安全側に倒すと最大データ行数は次の目安になる。

```text
floor(1,000,000 / 136) - 1 = 7,351 data rows
```

現行エクスポーターは既定で上限超過をエラーにし、明示的に`--limit-rows`を指定した場合だけ列数から安全な上限を計算して行単位で切り詰める。学習データを増やす場合は、単純に巨大な1ファイルへ連結せず、クラスごとの件数を均衡させた代表セットを作る。学習・テスト分割は、同じ打撃の近接窓が両側へ混ざらないよう、収録セッション単位で分けるのが望ましい。

## 3. SLV1.00.04の画面設定

### タブ 1: Training Data

`train_8class.csv`を選び、次を設定する。

| 設定 | 値 |
|---|---:|
| Input data / First column | 1 |
| Input data / Rows at one chunk | 1 |
| Input data / Columns at one chunk | 128 |
| Expected data / First column | 129 |
| Expected data / Rows at one chunk | 1 |
| Expected data / Columns at one chunk | 8 |
| Number of chunks | Automatic calculation |

`Check`を押し、入力層ノード数`128`、出力層ノード数`8`、chunk数、および先頭chunkの値を確認する。

特徴数を変更した場合は、特徴数を`F`としてInput columnsを`F`、Expected first columnを`F + 1`に変更する。例えば18帯域特徴なら、Inputは1列目から18列、Expectedは19列目から8列である。

### タブ 2: Test Data

`test_8class.csv`を選ぶ。入力・教師の開始列はTraining Dataと同じく`1`と`129`。chunkの行数・列数は学習データと一致している必要がある。Expected dataを有効にし、テストでも8列one-hot教師とActual dataを比較できるようにする。

### タブ 3: AI settings and Sim

最初の基準設定を次に示す。これは探索の開始点であり、実測データで比較して決定する。

| 設定 | 初期値 | 備考 |
|---|---:|---|
| Input layer | 128 | Dataタブから反映 |
| Hidden layer | 64 | 正式な初期値。必要なら32/64/128を同じ分割で比較 |
| Output layer | 8 | Dataタブから反映 |
| Activation function | Hard sigmoid | 保存xlsx内部では`sigmoid`表記になる場合があるため設定画面と保存値を記録する |
| Loss function | MSE / `mean_squared_error` | 8個のone-hot教師と8個の出力スコアを学習 |
| Forgetting rate | 1.0 | 通常学習の基準値 |
| Seed | 1 | 比較実験では固定し、必要時にSeed +1も比較 |
| Number of training repetitions | 0から開始 | 不足時のみ増加。過学習に注意 |
| Calculated with MCU precision (Bfloat16) | 最初OFF | doubleで候補決定後、ONで実機相当を再確認 |
| scaleAlpha | 0.2を開始候補 | 飽和率と精度を見て調整 |
| scaleGamma / leakRate | 0 | 本モデルは時系列ESN接続を使わず、1打撃を1chunkで処理 |
| l2Param | 0.01～0.1を比較 | 出力重みの過学習抑制 |

ノード合計は`128 + 64 + 8 = 200`で、公式ガイドの目安`570以下`に十分収まる。ただし最終判断は画面に表示されるAI RAM使用量と1 chunk当たり処理時間で行う。

`Start Sim`を実行したら、少なくとも次を記録する。

- Training/Testのlossと8出力Actual data
- 各行の`argmax(Actual)`と正解クラスの一致率
- 混同行列、クラス別再現率、境界付近打撃の誤分類
- doubleとBfloat16の差
- Hidden、Seed、scaleAlpha、l2Param、データセット版

画面のGraphは一度に5列までの表示に限られる。8出力すべての判定は、Saveで出力されるテスト結果xlsxのActual dataを使って計算する。実機ではSimulator内の最大値選択をモデル出力に含めず、MCU側Cコードが8スコアのargmaxを取る。

## 4. `model1.xlsx`と実機モデルの扱い

学習後はタブ`5. Save`から、実験ごとに新しいフォルダーへ保存する。推奨例は次のとおり。

```text
data/solist_sim/models/
  20260716_128f_h64_seed1_bf16off/
    model1.xlsx
    model1.h
    model1.mat
    ...training/test results...
```

`model1.xlsx`には少なくとも次のシートがある。

| シート | 内容 |
|---|---|
| `Sd_ni_m_no` | 入力形状、入力/隠れ/出力ノード数、活性化、損失、精度種別、Seedなど |
| `beta` | 隠れ層から出力層への重み。Acrylic Pan H=64なら64 × 8 |
| `p` | オンライン学習内部パラメータ。H=64なら64 × 64 |

タブ`4. Graph`の`Load AI model`から`model1.xlsx`を指定すると、AI settingsと内部パラメータを再読込できる。ただし、公式ガイド上、xlsxには`Number of training repetitions`が保存されない。再現実験ではこの値を別途メモする。

実機へ組み込む基本成果物はSimulatorが生成した`model1.h`である。公式ガイドでは`.h`に`Calculated with MCU precision (Bfloat16)`、`Number of training repetitions`、`Forgetting rate`が保存されないとされているため、これらも実験メタデータとして残す。

IchiPingでは、公式Simulatorで保存した`model1.xlsx`をテンプレートにしてPCで学習したbetaを書き込む手法を使用した。この方法自体は構造確認に有用だが、IchiPingのファイルは`167入力 × 32隠れ × 14/32出力`であり、Acrylic Panの`128 × 64 × 8`とは互換性がない。IchiPingの`model1.xlsx`、beta、p、alpha相当値をそのままコピーしてはならない。Acrylic Panでは、まず公式Simulator自身が生成した8出力モデルを正本とする。

Excelを手編集するとシート寸法や内部状態の不整合を作りやすい。将来PC側学習betaを注入する場合も、次を自動検査できる専用エクスポーターを用意してから行う。

- `Sd_ni_m_no`の入力・隠れ・出力ノードが`128/64/8`
- `beta`が`64 × 8`
- `p`が`64 × 64`
- 活性化、損失、Seed、Bfloat16条件が実験記録と一致
- SimulatorへLoad後、同一テストCSVでスコアが再現

## 5. IchiPingから流用する点・変更する点

| 項目 | 流用する知見 | Acrylic Panでの変更 |
|---|---|---|
| 学習器 | 固定ランダム射影 + beta学習の軽量ELM、MSE、出力スコアをCPU側argmax | 14/32状態ではなく8打点領域 |
| データ配置 | 1行1chunk、入力列の直後にone-hot教師列 | 167入力+14/32出力から128入力+8出力へ変更 |
| 前処理 | FFT、log振幅、標準化、学習と推論の完全一致 | 音声-baseline差分ではなくKX134の衝撃振動窓 |
| データ上限 | 1ファイル100万セル以下 | 136列なので7,351データ行以下を安全目安にする |
| 初期設定 | Seed=1、MSE、scaleAlpha探索、double後にBfloat16確認 | Hidden=64を初期値とし、実測で32/64/128を比較 |
| モデル保存 | `model1.xlsx`で再読込、`model1.h`をMCUへ引継ぎ | IchiPingのモデル実体はコピーせず8出力で新規生成 |
| 評価 | Actual出力を保存し、外部でargmax精度を算出 | 8クラス混同行列に加え、隣接領域誤りと期待座標も評価 |
| 現地適応 | センサ/環境変更時は再学習・追加学習 | 板材、固定方法、センサ接着、打撃具、打撃強度を収録条件として記録 |

## 6. 完了条件

Simulator工程は、次を満たしたとき完了とする。

1. 実測の学習・テストCSVがセッション分離され、各ファイル100万セル以下である。
2. SimulatorのCheck表示が入力128、出力8、意図したchunk数になっている。
3. Hidden/Seed/scaleAlpha/l2Paramを記録し、doubleとBfloat16の両方で評価済みである。
4. 未学習セッションで8クラス精度、混同行列、クラス別再現率を保存している。
5. 保存した`model1.xlsx`をLoadし直して同じテスト結果を再現できる。
6. `model1.h`と標準化係数をファームウェアへ組み込み、PC Simulatorと実機の8出力スコアを同じ入力で照合できる。

## 7. 参照元

- ローカル公式ガイド: `D:\GitHub\IchiPing_solist_AI\doc\Solist-AI_Sim_SupervisedLearning_qs-j.pdf`
- IchiPing検証記録: `D:\GitHub\IchiPing_solist_AI\README.md`
- IchiPing Simulatorメモ: `D:\GitHub\IchiPing_solist_AI\sim_export\README.md`
- このリポジトリのデータ生成: `sim/solist_dataset.py`
- このリポジトリの参照ELM: `sim/solist_elm.py`

この文書だけで運用できるよう必要事項を転記しているため、通常作業でIchiPingリポジトリを変更・参照する必要はない。
