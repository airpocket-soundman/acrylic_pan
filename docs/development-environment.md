# DT-EBML63Q2557 開発環境

確認日: 2026-07-16

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
| デバッガ | SEGGER J-Link PLUS、J-Link Software 7.62以降 | SWDデバッグと内蔵Flash書込み |
| USB通信 | FTDI FT2232H VCP/D2XXドライバ | UARTチャンネルB、SPIチャンネルA |
| PC収録 | Python 3 + pyserial + numpy | 打撃波形の受信、ラベル、品質管理、保存 |

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
| J-Link Software | 本体未導入。古いWindowsドライバ登録だけ存在 |
| FT2232H | 現在ボード未接続のためVCP/D2XX認識は未確認 |
| Docker | Desktop Linux Engine 28.0.4、解析コンテナ実行済み |
| Python | numpy/scipy/matplotlibによる解析実行済み |

公式Simulatorは
`C:\Program Files\ROHM\SolistAI_Sim_SLV10004sp\application\SolistAI_Sim_SLV10004.exe`
にあります。`scripts/launch-solist-ai-sim.ps1 -CheckOnly`でSimulatorとRuntimeを検査できます。
IchiPing側から移植した8クラス用の設定とCSV生成方法は
[`solist-ai-simulator.md`](solist-ai-simulator.md)を参照してください。

## 導入順序

1. 導入済みのLEXIDE-Ω、Build Tools、DFPで公式サンプルをCLIビルドする。
2. DAPLinkまたはJ-Linkの実機接続方式を確定し、対応する書込みCLIを導入する。
3. DT-EBML63Q2557をUSB Type-Cで接続し、FT2232HのCOM番号を確認する。
4. 公式サンプルを書込み、KX134とUARTの動作を無改造で確認する。
5. 動作確認済みプロジェクトを複製し、Acrylic Pan収録ファームウェアを実装する。
6. 収録データから8クラスCSVを生成し、公式Simulatorでモデルを学習・保存する。

## Acrylic Panで追加するファームウェア

- KX134-1211をZ軸、25.6 kHzで取得
- 512点イベント用リングバッファ
- jerkによる打撃トリガ
- 前128点（5 ms）+ 後384点（15 ms）のイベント波形
- UARTF1によるコマンドとイベントパケット転送
- sequence、timestamp、flags、CRC32
- 後段でSolist-AI 8出力モデルを組込み

UARTFの115,200 bpsでは25.6 kHz・16 bitの連続Z波形を常時転送できません。
初期実装はボード側のリングバッファで512点を切り出し、1打ごとにUART送信します。
約1.1 kBのイベント送信には約95 msかかるため、連打対応ではイベント領域の2面化、
ボーレート向上、またはSPI転送を検討します。

## 公式資料

- [ROHM Solist-AI開発支援システム](https://www.rohm.com/lapis-tech/product/micon/solistai-software)
- [ML63Q2500 LEXIDE-Ωチュートリアル](https://fscdn.rohm.com/lapis/en/products/databook/applinote/ic/micon/FEXT63Q2500_LEXIDE_TUTORIAL.pdf)
- [DT-EBML63Q2557ダウンロード](https://www.datatecno.co.jp/prod_info/solistai_board_download/)
- [DT-EBML63Q2557ハードウェアマニュアル](https://www.datatecno.co.jp/datatecno_core/content/uploads/2025/06/DT-EBML63Q2557_hardware_users_manual_Rev.20250527.pdf)
