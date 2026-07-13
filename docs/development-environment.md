# DT-EBML63Q2557 開発環境

確認日: 2026-07-13

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
| Solist-AI Sim 教師あり版 | 導入済み: `SolistAI_Sim_SLV10004` 1.0004 |
| LEXIDE-Ω | 未導入（`C:\LAPIS`なし） |
| ML63Q25x7_DFP / CMSIS-Core(M) | 未導入 |
| ML63Q2500 Reference Software | 未確認／プロジェクト内になし |
| J-Link Software | 本体未導入。古いWindowsドライバ登録だけ存在 |
| FT2232H | 現在ボード未接続のためVCP/D2XX認識は未確認 |
| Docker | Desktop Linux Engine 28.0.4、解析コンテナ実行済み |
| Python | numpy/scipy/matplotlibによる解析実行済み |

`D:\GitHub\IchiPing_solist_AI` にはSolist-AI互換ELM参照実装、Sim入力データ、
モデル出力があるが、LEXIDE-Ω用 `.project` / `.cproject` やML63Q2557ファームウェア
ソースは含まれていない。ボードの収録ファームウェアは公式リファレンス／サンプルソースを
取得して別途プロジェクト化する必要がある。

## 導入順序

1. ROHMからLEXIDE-Ω V2.2.0を入手し、短いローカルパスへインストールする。
2. LEXIDE-Ω CMSIS-Pack Managerへ最新の `ROHM.ML63Q25x7_DFP` と
   `CMSIS-Core(M)` をインポートする。
3. ML63Q2500 Reference Softwareを取得する。
4. Data Technoの購入者向けページへログインし、ボード上のバージョンに対応した
   AISignalInference、ホスト、IOドライバ、移行ガイドを取得する。
5. J-Link Software 7.62以降を導入し、ML63Q25x7用FLM/XMLを登録する。
6. DT-EBML63Q2557をUSB Type-Cで接続し、FT2232Hの2チャンネルとCOM番号を確認する。
7. 公式サンプルを無改造でビルド、Flash書込み、センサ値確認まで行う。
8. その動作確認済みプロジェクトを複製し、Acrylic Pan収録ファームウェアを実装する。

## Acrylic Panで追加するファームウェア

- KX134-1211をZ軸、±8 g、6.4 kHzで取得
- 1024点程度のリングバッファ
- jerkによる打撃トリガ
- 前32点 + 後480点のイベント波形
- UARTF1によるコマンドとイベントパケット転送
- sequence、timestamp、flags、CRC32
- 後段でSolist-AI 8出力モデルを組込み

UARTFの最大115,200 bpsでは6.4 kHz・16 bitの連続Z波形を常時転送できない。
初期実装はボード側で512点を切り出し、1打ごとにUART送信する。連続転送が必要になった
場合は、同じFT2232HのSPIチャンネルAをバルクデータ、UARTチャンネルBを制御に使う。

## 公式資料

- [ROHM Solist-AI開発支援システム](https://www.rohm.com/lapis-tech/product/micon/solistai-software)
- [ML63Q2500 LEXIDE-Ωチュートリアル](https://fscdn.rohm.com/lapis/en/products/databook/applinote/ic/micon/FEXT63Q2500_LEXIDE_TUTORIAL.pdf)
- [DT-EBML63Q2557ダウンロード](https://www.datatecno.co.jp/prod_info/solistai_board_download/)
- [DT-EBML63Q2557ハードウェアマニュアル](https://www.datatecno.co.jp/datatecno_core/content/uploads/2025/06/DT-EBML63Q2557_hardware_users_manual_Rev.20250527.pdf)

