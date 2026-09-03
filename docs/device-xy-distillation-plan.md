# Solist-AI向け座標推論モデルの実施計画

最終更新: 2026-08-27

> **実装状況（2026-09-03）**
> 本書で第三候補としていた60座標確率の蒸留モデルを実装した。現行方式と評価結果は
> [PC側・確率分布型位置推定](position-inference.md#デバイス確率推論モード)を参照する。
> 本書は、そこへ至る比較方針、データリーク防止策、実機整合条件を残す実施計画である。

## 1. 目的

開発用PCに保存されている実測波形とPC側座標推論モデルを使い、ML63Q25x7の
Solist-AIで実行可能な座標推論モデルを作る。単純な直接XY回帰だけでなく、PCモデルからの
知識蒸留、正式な複数AIモデル機構による固定ランダム特徴の拡張、分類＋エリア内残差を
同じ評価条件で比較する。

この計画の第一目標は、測定済み60座標およびその近傍で、現行の単段Solist型XY回帰より
明確に小さい誤差を実機上で得ることである。測定点間の任意座標に対する連続補間性能は、
未学習座標を含む独立データを用意するまで別の課題として扱う。

## 2. 現在地と判断

| 構成 | 評価結果 | 判断 |
|---|---:|---|
| Solist型直接XY `128-32-2` | LOSO平均47.15 mm | 単一の固定射影では容量不足 |
| PC直接XY MLP | 8中心LOSO平均5.68 mm | 波形には位置情報が含まれる |
| Solist向け多段MLP `128-64×7-2` | Bfloat16 LOSO平均7.10 mm | 数値精度は十分だが実機の段切替が成立しない |
| 同多段MLPのMCU逐次積和 | 約0.93秒/推論 | 製品ファームには遅すぎる |
| 最新PC XY回帰 | 7セッション共通holdout平均7.48 mm | 蒸留元候補。ただし同一セッション内holdout |
| 最新PC 60座標確率モデル | 共通holdout期待座標平均1.81 mm | 蒸留元候補。未知セッション評価は未実施 |

多段化を再試行するのではなく、Solist-AIが正式に持つ複数モデル機構を利用して単段ELMを
横方向へ拡張する。ROHM資料の「ML63Q2500グループに実装可能なAIモデル数」では、
最大入力データ数128の場合に4モデルを搭載可能と示されている。実際のHidden、Output、
使用ライブラリによる制限は、採用前に`ODL_Initialize`の戻り値と実機推論で確認する。

関連資料:

- [PC側・中心点XY座標回帰モデルの比較](pc-xy-regression.md)
- [PC側・確率分布型位置推定](position-inference.md)
- [Solist-AI 多段XY座標回帰](solist-xy-staged.md)
- [実測振動によるモデル学習](real-model-training.md)
- `doc/solist-ai_algorithm_axlcore-odl_an-j.pdf` P.39

## 3. 採用候補の構成

### 3.1 第一候補: 4インスタンス広幅ELM

同じ128特徴を、異なるseedの4つの単段Solistモデルへ入力する。

```text
時間波形128特徴
  ├─ instance 0 / seed A / 128-H-2 ┐
  ├─ instance 1 / seed B / 128-H-2 ├─ CPUで4出力を加算
  ├─ instance 2 / seed C / 128-H-2 ┤        ↓
  └─ instance 3 / seed D / 128-H-2 ┘      X, Y
```

Hiddenは最初に32を使用し、4モデル初期化が成功する場合だけ64を試す。4×32なら実質128、
4×64なら実質256個の固定ランダム特徴を持つ単層モデルになる。

各モデルを別々に学習して平均するのではなく、4モデルの隠れ出力を結合してbetaを一括学習する。

```text
Hk = BF16(activation(BF16(X) × BF16(alpha_k)))
H  = [H0, H1, H2, H3]
B  = solve(H.T × H + ridge × I, H.T × target)
```

学習後に`B`をHidden行ずつ`B0`〜`B3`へ分割する。実機では各インスタンスが
`yk = Hk × Bk`を計算し、CPUが`y = y0 + y1 + y2 + y3`を求める。この分割は、結合した
広幅単層ELMの出力と数学的に同じである。必要なら最後にCPU上の小さな2×2アフィン校正と
パネル範囲へのclipを適用する。

### 3.2 第二候補: 12エリア分類＋エリア内残差

現行12クラス分類は座標の粗い推定として強いため、座標全体を一度に回帰せず、次の二段階へ
問題を分解する。

1. 12出力からエリアまたはエリア確率を求める。
2. エリア中心からの`dx, dy`を回帰する。
3. `xy = area_center + (dx, dy)`として復元する。

比較する出力形式は次の2つとする。

- 14出力: 12分類スコア＋全エリア共通の`dx, dy`
- 36出力: 12分類スコア＋12エリア別の`dx, dy`、推論時は選択エリアの2値だけを使う

36出力の場合、各エリアの残差betaはそのエリアの学習行だけで解く。対象外エリアへゼロ教師を
与える方式は、不要なゼロ出力を学習して容量を消費するため基準方式にはしない。Output 36が
ライブラリ上限、RAM、推論時間を満たすかは先に最小モデルで確認する。

### 3.3 第三候補: 60座標確率の蒸留

PC教師の60座標確率をSolistのスコアへ蒸留し、CPUでsoftmaxと確率加重平均を計算する。
固定測定点に対しては直接XYより安定する可能性がある。ただしOutput 60の実機可否、beta容量、
UART出力時間を確認してから実施する。これは測定していない座標の補間精度を保証しない。

## 4. 知識蒸留

### 4.1 生徒が使える入力

教師モデルが714特徴を使っていても、生徒モデルは実機で再現済みの時間波形128特徴だけを使う。

- 512点イベント、trigger index 64
- プリトリガ64点の平均をbaselineとして減算
- ポストトリガ448点の最大絶対値で振幅正規化
- 等間隔に128点を抽出
- 学習セッションだけで求めた平均・標準偏差で標準化
- 入力、alpha、隠れ出力、beta出力を実機と同じ順序でBfloat16化

FFTを生徒へ追加する場合は、同一波形に対するPCとAxlCORE-ODLのFFT各binを照合した後、
別実験として扱う。

### 4.2 教師値

直接XY回帰では、正解座標と教師予測を混ぜた値を使う。

```text
target = (1 - teacher_weight) × true_xy + teacher_weight × teacher_xy
```

`teacher_weight`は`0.0, 0.25, 0.5, 0.75, 1.0`を比較する。正解座標を完全に捨てない
`0.25`または`0.5`を開始候補とする。最終選択は教師との一致度ではなく、holdoutの正解座標に
対する距離誤差で行う。

確率蒸留では、教師の温度付き60座標確率またはlogitを使用する。温度は`1, 2, 4`を比較し、
生徒出力からCPU softmaxを計算する。数値安定化のためsoftmax前に最大スコアを引く。

### 4.3 データリークを防ぐ手順

LOSOの各foldで、評価セッションを教師モデルの学習にも生徒モデルの学習にも使用してはならない。

1. 1セッションを最終評価用として分離する。
2. 残りの学習セッションだけで特徴scalerとfold用教師を学習する。
3. 教師は学習セッションのイベントだけに擬似教師を付ける。
4. 生徒を同じ学習セッションで学習する。
5. 評価セッションは正解座標との比較にだけ使用する。

全データ学習済みの現行PC bundleでLOSO評価データへ擬似教師を付けると、教師経由のリークに
なる。比較レポートには必ず、教師がfoldごとに再学習されたかを記録する。

ラベルなし実測波形がある場合は、教師による擬似ラベル付与へ利用できる。ただし信頼度が低い、
飽和している、収録条件が不明な波形は除外し、擬似ラベル波形をテスト件数へ含めない。

## 5. alphaと複数インスタンスの実装条件

広幅化には異なるalphaが必要である。同じalphaへ異なるbetaを4組載せても隠れ特徴空間は
増えないため、容量改善はほとんど期待できない。

開発用PCで次を準備する。

1. 公式Simulatorから、採用する4 seedのalphaを同じInput、Hidden、activation、scaleAlphaで出力する。
2. alphaのshape、dtype、SHA-256、seed、Simulator版をmanifestへ保存する。
3. PythonのBfloat16参照計算とSimulator出力をgolden入力で照合する。
4. 実機がseedから内部生成するalphaを使う場合、PCで取得したalphaと完全一致することを確認する。

現行コードの`ODL_SetWeightAlpha(alpha, offset, size)`にはinstance引数がなく、alphaを
グローバルに置き換える挙動が確認されている。そのため、現在の外部alpha APIを4モデルへ
順番に呼ぶ方式は採用しない。使用中のライブラリ版にinstance別alpha設定APIが追加されている
場合だけ、そのAPIを小さなgolden testで検証して使用する。

複数モデル初期化では次を守る。

- 対応する複数モデル用AIライブラリを選ぶ。
- `ODL_SetModelCount(4)`の呼出時期は使用ライブラリのサンプルとヘッダで確認する。
- `ODL_Initialize`はinstance 0、1、2、3の昇順で呼ぶ。資料上、逆順では正常動作しない。
- 各`ODL_Initialize`とモデル数設定の戻り値を検査し、失敗時は推論を開始しない。
- 推論ごとに同じ128入力を各instanceへ渡し、結果を取得してCPUで加算する。

現在はdummy self-testがinstance 0、分類推論がinstance 1を独立に初期化している。4モデル版では
アクセラレータの所有権と初期化を1モジュールへ集約する。dummy self-test用インスタンスを別に
予約せず、配置済み4モデル自身のgolden caseを起動時またはコマンド受信時に検査する。

## 6. 実験マトリクス

すべて同じセッション分割、同じ128入力、同じ座標系で比較する。

| ID | 構成 | 教師 | 目的 |
|---|---|---|---|
| E0 | 1×32、直接XY | 正解座標 | 現行47.15 mmの再現 |
| E1 | 4×32、直接XY | 正解座標 | 広幅化単独の効果 |
| E2 | 4×32、直接XY | 正解＋PC XY | 蒸留効果 |
| E3 | 4×64、直接XY | 正解＋PC XY | 実機初期化可能な場合の上限 |
| E4 | 4×32、12分類＋共通残差 | 正解＋PC XY | 粗密分解の効果 |
| E5 | 4×32、12分類＋エリア別残差 | 正解＋PC XY | エリア条件付き回帰 |
| E6 | 4×32、60座標スコア | PC確率 | Output 60が実機対応する場合 |

各構成でridge、activation、scaleAlpha、seedの組を探索する。ただし評価セッションの結果を
見てseedを選ぶと評価リークになるため、ハイパーパラメータ選択は学習セッション内のnested
validationで行う。最終外部テストは1回だけ実行する。

## 7. 評価指標と採用条件

### 7.1 精度

- 距離誤差の平均、中央値、90・95パーセンタイル
- 25 mm以内、50 mm以内の割合
- X、YそれぞれのbiasとMAE
- 測定点別、エリア別、セッション別、飽和有無別の誤差
- エリア正解率、60座標top-1正解率
- 教師あり／なしの同一イベント対応差

必須条件は、同じLOSO予測に対してE0より平均距離を30%以上改善し、かつ90%点を悪化させない
こととする。開発目標は平均15 mm以下、90%点30 mm以下、25 mm以内90%以上とする。
最終的な採用判断は、完全未使用セッションでも改善が再現することを条件とする。

### 7.2 PC・実機一致

- 各instanceの生Bfloat16出力を保存する。
- golden入力では、可能なら各出力をbit単位で一致させる。
- bit一致しない場合は、最初に差が発生した入力、隠れ出力、beta出力を特定する。
- CPU加算、非正規化、clip後の座標差は0.5 mm以内を暫定許容値とする。

### 7.3 資源とリアルタイム性

- 4モデル合計推論時間、前処理時間、後処理時間
- 推論中の25.6 kHzサンプリング欠落数
- Flash、static RAM、stack peak
- 連続1,000打撃でのtimeout、watchdog reset、結果欠落

4モデル合計推論は20 ms未満を目標とし、サンプリングを並行する場合は欠落0を必須とする。
ROHM資料の参考グラフから128入力の単モデル推論は約2 msだが、4モデル合計時間は必ず実測する。

## 8. 開発用PCでの実装順序

### Phase A: データと教師の固定

1. `data/raw/sessions`の全イベントを監査する。
2. 次回収録または完全未使用セッションを外部テストとして凍結する。
3. 現行714特徴のPC XY教師と60座標確率教師を、学習データだけで再学習可能にする。
4. データセットmanifestと外部テストのSHA-256を保存する。

### Phase B: 4インスタンスPCエミュレーション

1. seed別alphaローダーを追加する。
2. 4つの隠れ出力を結合してbetaを一括ridge学習する。
3. Bfloat16境界を含む推論関数を実装する。
4. E0〜E6を同じLOSO foldで比較する。
5. 蒸留weight、ridge、activation、Hiddenをnested validationで選ぶ。

### Phase C: モデル出力

次の成果物を生成する。

```text
artifacts/device_xy_distillation/
  experiment_report.json
  loso_predictions.npz
  external_test_predictions.npz
  alpha_manifest.json
  model.npz
  golden_cases.npz
  resource_estimate.json
firmware/AcrylicPanCollector/generated/
  apan_xy_wide_model.h
```

`experiment_report.json`には、データSHA-256、全セッションID、fold、教師bundle SHA-256、
alpha SHA-256、seed、scaleAlpha、ridge、蒸留weight、量子化位置、全指標を保存する。

### Phase D: ファームウェア

1. Solistアクセラレータを管理する単一モジュールを作る。
2. 4インスタンスを昇順に初期化し、分割betaを設定する。
3. 同じ128特徴を4回推論し、CPUで部分出力を加算する。
4. APANプロトコルへ`x_mm, y_mm`、信頼度、推論時間を追加する。
5. golden case、実測保存波形、実打撃の順に検証する。
6. 連続推論とサンプリング欠落を測る。

### Phase E: 外部テストと採否決定

モデル構成とハイパーパラメータを固定した後、凍結した外部テストを1回だけ評価する。
不合格でも同じ外部テストへ合わせて再調整せず、次版用の分析データとして保持する。

## 9. 追加予定のコード

実装時の推奨配置は次のとおりとする。

- `sim/device_xy_distillation.py`: fold教師、蒸留、広幅ELM学習、Bfloat16評価
- `scripts/run-device-xy-distillation.ps1`: 開発用PC向け再実行入口
- `tests/test_device_xy_distillation.py`: beta分割、部分和、リーク防止、再現性
- `firmware/AcrylicPanCollector/include/apan_xy_wide_inference.h`
- `firmware/AcrylicPanCollector/src/apan_xy_wide_inference.c`
- `docs/device-xy-distillation-results.md`: 実験完了後の数値と採否

想定コマンド例:

```powershell
.\scripts\run-device-xy-distillation.ps1 `
  -Python C:\ProgramData\anaconda3\python.exe `
  -Sessions data\raw\sessions `
  -AlphaDirectory D:\GitHub\IchiPing_solist_AI\sim_export\xy_wide `
  -TeacherBundle artifacts\pc_position_runtime_400x300x5\position_ensemble.joblib `
  -OutputDirectory artifacts\device_xy_distillation
```

引数名は実装時に確定するが、データ、alpha、教師bundle、出力先を暗黙の固定パスだけに依存させない。

## 10. 中止・切替条件

- 4×32が`ODL_Initialize`で成立しない: 3×32、2×64、2×32を同じ方法で比較する。
- seed別alphaをPCと実機で一致させられない: 複数モデル蒸留を止め、分類＋残差の単一モデルを優先する。
- PC Bfloat16評価でもE0比30%改善しない: ファーム実装へ進まず、入力特徴または問題分解を見直す。
- PCでは良いが実機だけ不一致: 学習を変えず、前処理、alpha、Bfloat16、beta配置、出力加算を順に調べる。
- 外部セッションで大幅に悪化する: モデル容量ではなくセッション差を優先課題とし、固定し直し・日・打撃者を増やす。
- 4モデルが20 msを超える: 2モデル版との精度・速度Pareto比較、または12分類＋残差へ切り替える。

## 11. 完了条件

- 同じ固定テストセットでE0〜採用候補の比較表が作成されている。
- fold別教師学習により蒸留時のデータリークがない。
- seed別alphaとPC・実機のBfloat16出力がgolden caseで一致する。
- 実機で座標、信頼度、推論時間を出力できる。
- 連続動作でサンプリング欠落とwatchdog resetがない。
- 採用条件を満たすか、不採用理由と次の切替案が記録されている。
