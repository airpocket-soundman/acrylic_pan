# 展示本番用PCの保守環境

確認日: 2026-10-28

展示本番では、このPC（`C:\Users\yamas\Documents\GitHub`）で保守を行う。Acrylic Pan と
IchiPing_solist_AI は同じ環境を使う。ここでは、このPCで使えるもの、足りないもの、
学習とビルドの手順をまとめる。

## 現況

| 項目 | 状態 |
|---|---|
| 学習用Python（venv） | 整備済み。`acrylic_pan\.venv`（Anaconda 3.11.5ベース） |
| 主なライブラリ | numpy 2.2.6、scipy 1.15.3、scikit-learn 1.5.1、pyserial 3.5、matplotlib 3.10.3、pytest |
| テスト | `python -m pytest tests` が157件成功、2件失敗、1件スキップ |
| Docker | 27.3.1。CalculiXによる解析に使用可能 |
| Visual Studio | 2022 Community。ファームのホストテストに使用可能 |
| LEXIDE-Ω、Build Tools | **未導入**。ファームのビルドができない |
| ML63Q25x7_DFP、公式サンプルソース | **未導入**。LEXIDEとDataTechnoの購入者ログインが必要 |
| 公式Solist-AI Simulator、MATLAB Runtime | **未導入**。Simulator上の確認ができない |
| 書き込み（MCU-Link、OpenOCD、pyocd） | **未導入**。LEXIDE同梱のOpenOCDまたは pyocd が必要 |

導入できていないものは、インストーラの入手に購入者ログインやライセンス同意が要る。
展示前に、本番PCで導入と動作確認を済ませる。

失敗している2件のテストは次の通り。

- ODL校正の事前分布のテストは、`artifacts/device_position_probability_400x300x5/model.npz`
  を必要とする。このファイルはリポジトリに無い。学習し直すか、元のPCから複製する。
- 位置推定Webのテストは、板の高さの上限を200 mmと見ている。400 × 300 mm板のままのため、
  テスト側の更新が要る。

## 学習環境（Python）

作成済みの venv を使う。作り直す場合は次を実行する。

```powershell
C:\ProgramData\anaconda3\python.exe -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt pytest
```

確認は次のとおり。

```powershell
.\.venv\Scripts\python.exe -m pytest tests
```

起動スクリプトは `-Python` で venv を指定する。

```powershell
.\scripts\run-monitor.ps1 -Python .\.venv\Scripts\python.exe -Page instrument.html
```

### alphaファイルのパス

`scripts/train-real-model.ps1` と一部の文書は、既定のalphaを `D:\GitHub\IchiPing_solist_AI\...`
としている。このPCにDドライブの配置は無い。次の場所を `-Alpha` で指定する。

```text
C:\Users\yamas\Documents\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy
```

```powershell
.\scripts\train-real-model.ps1 -Python .\.venv\Scripts\python.exe `
  -Alpha C:\Users\yamas\Documents\GitHub\IchiPing_solist_AI\sim_export\_alpha32_sim.npy
```

なお、`train-real-model.ps1` は `-Python` を持たず PATH上の `python` を呼ぶ。venv を使うには、
venv を有効化してから実行する（`.\.venv\Scripts\Activate.ps1`）。

収録データは `data/raw/` に置く。リポジトリ管理外のため、本番前にコピーしておく。

## ファームウェアのビルドと書き込み

LEXIDE-Ωなどの導入後、次の順に行う。詳細は [開発環境](development-environment.md) と
[収録システム・実機Web UIクイックスタート](collector-quickstart.md) を参照する。

```powershell
.\scripts\check-collector-env.ps1
.\scripts\build-firmware.ps1
.\scripts\flash-firmware.ps1
```

`check-collector-env.ps1` の既定の参照先は次のとおり。このPCでは存在しないため、
導入先が違う場合は `-FirmwareProject` で指定する。

- `C:\LAPIS\LEXIDE\Utilities\Bin\make.exe`
- `C:\Users\yamas\lexide\workspace_omega_v2\AIVibrationInference`

## IchiPingとの共有

IchiPing_solist_AI も、公式Simulator、LEXIDE、ML63Q2557の書き込み環境を同じ形で使う。
導入物は一度入れれば両方から使える。Python環境は、学習の依存が異なるため別にする。
IchiPing側の記述は、同リポジトリの `docs/DEVELOPMENT.md` の「開発環境の共有」を参照する。

## 導入ファイルの置き場（2026-10-28）

`C:\Users\yamas\Downloads` にそろえた配布物のうち、インストール不要のものは
`C:\Users\yamas\solist-tools\` に展開した。

| 配布物 | 状態 | 次にすること |
|---|---|---|
| `LAPIS_LEXIDE_V2_3_0.zip.part00`〜`07` | 結合・展開済み（MD5一致） | `solist-tools\LEXIDE_V2_3_0\LexideInstaller_20260904.exe` を実行する。標準の導入先は `C:\LAPIS\LEXIDE` |
| `ML63Q2500_DFP_r.1.1.0` | 展開済み | LEXIDE導入後、`ROHM.ML63Q25x7_DFP.1.1.0.pack` をPack Managerで追加 |
| `ML63Q2500_ReferenceSoftware_v130` | 展開済み | LEXIDEで開く |
| `IODriverSource` | 展開済み | 中の `IODriverV1.0.25.0602.zip` をサンプルへ重ねる |
| `Solist-AI_Scope_1.2.12.3` | 展開済み | `SolistAiScopeSetup-ML63Q25xx-1.2.12.2.exe` で実機確認用ツールを導入 |
| `AIVibrationInferenceRev.20260417` | 展開済み | ビルド済みhexと書き込み手順書。ソースは含まない |
| `Solist-AI_Sim_SupervisedLearning` | 展開済み | `SolistAI_Sim_SLV10004_Installer_web.exe` を実行（使用許諾に同意が要る） |
| `Solist-AI_Sim_AnomalyDetection` | 未展開 | 異常検知版。今回の用途では不要 |
| `AISignalInferenceHost`、`AIVibrationInferenceHost` | **未取得** | データテクノ側のダウンロードが中断していた |

LEXIDE-Ωの使用にはキーコードが要る。取得方法は ProtoPedia の
[作品8201](https://protopedia.net/prototype/8201) に案内がある。
ソフトのインストーラは使用許諾への同意が要るため、PCの前でユーザーが実行する。

## 導入の進み具合（2026-10-28 夜）

- LEXIDE-Ω V2.3.0 を `C:\LAPIS\LEXIDE` へ導入済み。
- ML63Q25x7_DFP は `C:\Users\yamas\AppData\Local\Arm\Packs\ROHM\` に展開し、Pack Managerで「Up to date」を確認した。
  取り込みの画面操作が見つからなかったため、`.pack`（zip）を標準の置き場に展開して反応させた。
- `IODriverV1.0.25.0602.zip` を `C:\Users\yamas\solist-tools\IODriver_extracted\` に展開した。基板のサンプル9本
  （`MemsAccelerometer` など）とドライバ共通部が入っている。
- **ファームウェアの overlay には、`AIVibrationInference` のサンプルソース一式が別に要る。**
  手元の配布物は、ビルド済みhexと手順書だけで、ソースを含まない。データテクノ側の
  AIVibrationInferenceHost（未取得）に含まれる可能性が高い。`install-overlay.ps1` の既定値は
  `C:\Users\yamas\lexide\workspace_omega_v2\AIVibrationInference` を参照する。

## ビルド確認（2026-10-29）

基板サンプル `MemsAccelerometer` を LEXIDE-Ω V2.3.0 でビルドし、`0 errors, 3 warnings`（約35秒）で通った。
ツール、デバイスパック、コンパイラ、リンカの経路は動作している。

つまずいた点と対処は次のとおり。

- **`core_cm0plus.h` が見つからない。** ROHM のデバイスパックは、ARM の `ARM.CMSIS` パック（5.9.0）を前提にする。
  LEXIDE の索引に `ARM.CMSIS` が出ないため、公式の `ARM.CMSIS.5.9.0.pack`（約34 MB、
  `ARM-software/CMSIS_5` のリリース5.9.0）を取得し、`Packs\ARM\CMSIS\5.9.0` へ展開した。
- **コンソールの `DOSKEY` の文字化けと警告。** ビルド結果には影響しない。
- 画面なしの起動（`eclipsec.exe -application ...headlessbuild`）は、作業フォルダの初期化で止まった。ビルドは画面から行う。

## 実機用ファームの再ビルド（2026-10-29）

土台の `AIVibrationInference`（LEXIDEプロジェクト一式、約1.5 MBのzip）を
`C:\Users\yamas\lexide\workspace_omega_v2\` に展開し、`install-overlay.ps1` で
`AcrylicPanCollector_private` を作った。次のコマンドで、実機用ファームをビルドできる。

```powershell
.\scripts\build-firmware.ps1 -SourceProject C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_private
```

成果物は `.local\firmware-build\<日時>\AcrylicPanCollector_private\Debug\AIVibrationInference.hex`（約157 KB）に出る。
元のプロジェクトは変更しない。

土台のプロジェクトは Build Tools Ver.20260317 の場所を参照しており、このPCにあるのは Ver.20260904 だけだった。
`build-firmware.ps1` は、ビルド用のコピーの参照先を、導入済みの最新版へ書き換えてからビルドするよう直した。
また、`DOSKEY` の警告で失敗扱いにならないよう、終了コードで成否を判断するようにした。

書き込みは、実機をつないだ状態で `scripts\flash-firmware.ps1` を使う。MCU-LinkとOpenOCDの動作は、このPCでは未確認である。
