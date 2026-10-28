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
