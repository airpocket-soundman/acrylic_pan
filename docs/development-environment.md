# DT-EBML63Q2557 開発環境

確認日: 2026-09-27

## 推奨構成

| 分類 | 必要なもの | 用途 |
|---|---|---|
| OS | Windows 10/11 x64、RAM 8 GB以上、Cドライブ空き4 GB以上 | LEXIDE-Ωと公式Windowsツール |
| IDE | LAPIS Development Tools LEXIDE-Ω V2.2.0 | ML63Q2557の編集、ビルド、デバッグ |
| ビルドツール | LEXIDE-Ω Build Tools Ver.20260317 | Arm Cコンパイラ、リンカ、GDB |
| デバイス情報 | ROHM.ML63Q25x7_DFP + CMSIS-Core(M) | レジスタ、起動コード、リンカ、FLM、SVD |
| 基盤ソース | ML63Q2500 Reference Software | IOドライバとサンプルプロジェクト |
| ボードソフト | AISignalInferenceと対応するソース／IOドライバ | DT-EBML63Q2557固有のセンサ・通信実装 |
| AI | Solist-AI Sim 教師あり版 SLV1.00.04 | 学習、bfloat16確認、モデルの.h出力 |
| 書込み | MCU-Link（CMSIS-DAP）+ LEXIDE同梱のROHM版OpenOCD | 内蔵Flash書込みと全バイト検証 |
| USB通信 | FTDI FT2232H VCPドライバ | UARTF1（COM3、115200 bps） |
| PC収録・実機Web UI | Anaconda Python 3.12.7 + pyserial + numpy + scikit-learn | 打撃波形の受信、推論表示、ブラウザ発音、ラベル、品質管理、保存 |

LEXIDE-Ω V2.2.0のインストーラは `LexideInstaller_20260317.exe`、標準インストール先は
`C:\LAPIS\LEXIDE`。ML63Q2500グループ用ArmデバイスパックはLEXIDE-Ωの
CMSIS-Pack Managerから追加する。LEXIDE本体だけではML63Q2557の機種情報は入らない。

## このPCの確認結果

| 項目 | 状態 |
|---|---|
| Solist-AI Sim 教師あり版 | 導入済み: SLV1.00.04（実行ファイルのFileVersion 1.4.0.0） |
| MATLAB Runtime | R2024a導入済み: `C:\Program Files\MATLAB\MATLAB Runtime\R2024a` |
| LEXIDE-Ω | V2.2.0導入済み: `C:\LAPIS\LEXIDE` |
| Build Tools | Ver.20260317導入済み。付属makeによるCLIビルドを確認 |
| ML63Q25x7_DFP / CMSIS-Core(M) | Pack Managerへ導入済み。ROHM ML63Q25x7を認識 |
| サンプルファーム | `AIVibrationInference`をLEXIDEへ取込み、0 errorsでビルド確認 |
| 書込み | MCU-LinkとOpenOCDで書込み・検証（`scripts/flash-firmware.ps1`） |
| UART | COM3（115200 bps、8-N-1）。COM5はMCU-Link VCom |
| Visual C++ | Visual Studio 2022 Community。ファームウェアのホストテストに使用 |
| Docker | Desktop Linux Engine 28.0.4、解析コンテナ実行済み |
| Python | `C:\ProgramData\anaconda3\python.exe`（3.12.7）でWeb UIを動作確認。scikit-learn 1.5.1、joblib 1.4.2、pyserial 3.5導入済み |

公式Simulatorは
`C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp\application\SolistAI_Sim_SLV10004.exe`
にあります。`scripts/launch-solist-ai-sim.ps1 -CheckOnly`でSimulatorとRuntimeを検査できます。
IchiPing側から移植した8クラス用の設定とCSV生成方法は
[`solist-ai-simulator.md`](solist-ai-simulator.md)を参照してください。

実機Web UIの起動ではPATH上の `python` に依存せず、次のように動作確認済み環境を明示します。
現在PATHで先に選ばれる `C:\Python313\python.exe` は別環境であり、必要ライブラリが揃って
いません。詳細は [`collector-quickstart.md`](collector-quickstart.md)を参照してください。

```powershell
.\scripts\run-monitor.ps1 `
  -Python C:\ProgramData\anaconda3\python.exe `
  -Page instrument.html
```

## 導入順序

1. LEXIDE-Ω、Build Tools、ML63Q25x7_DFPを導入し、公式サンプル `AIVibrationInference` をLEXIDEで一度ビルドする。
2. `firmware/AcrylicPanCollector/tools/install-overlay.ps1` で、サンプルを複製したprivateプロジェクトを作る。
3. `scripts/build-firmware.ps1` または `build-private-project.ps1` でビルドし、`scripts/flash-firmware.ps1` で書き込む。
4. Anaconda Pythonで `python -m pytest tests` と `firmware/AcrylicPanCollector/tools/test-host.ps1` を実行する。
5. `scripts/run-monitor.ps1` でWeb UIを起動し、収録と推論を確認する。

手順の詳細は [収録システム・実機Web UIクイックスタート](collector-quickstart.md)、
ファームウェアの構成は [ファームウェア仕様](firmware-specifications.md) を参照する。

## 公式資料

- [ROHM Solist-AI開発支援システム](https://www.rohm.com/lapis-tech/product/micon/solistai-software)
- [ML63Q2500 LEXIDE-Ωチュートリアル](https://fscdn.rohm.com/lapis/en/products/databook/applinote/ic/micon/FEXT63Q2500_LEXIDE_TUTORIAL.pdf)
- [DT-EBML63Q2557ダウンロード](https://www.datatecno.co.jp/prod_info/solistai_board_download/)
- [DT-EBML63Q2557ハードウェアマニュアル](https://www.datatecno.co.jp/datatecno_core/content/uploads/2025/06/DT-EBML63Q2557_hardware_users_manual_Rev.20250527.pdf)
