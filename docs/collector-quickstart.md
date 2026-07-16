# Acrylic Pan 収録システム クイックスタート

## 接続

- `COM3`: 開発ボードのUART、115200 bps、8-N-1
- `COM5`: MCU-Link VCom（ファーム書き込みには使用しない）
- 書き込み: MCU-LinkのCMSIS-DAPインターフェースをOpenOCDから使用

## ファームのビルドと書き込み

LEXIDE GUIやJavaは不要です。privateプロジェクトをCLIでビルドします。

```powershell
.\firmware\AcrylicPanCollector\tools\build-private-project.ps1 `
  -Project C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_cli_verified2
```

書き込み、Flash全バイト検証、リセット実行を行います。

```powershell
.\scripts\flash-firmware.ps1 `
  -FirmwareHex C:\Users\yamas\lexide\workspace_omega_v2\AcrylicPanCollector_cli_verified2\Debug\AIVibrationInference.hex `
  -Execute
```

スクリプトはLEXIDE同梱のROHM版 `openocd_arm.exe` とML63Q25x7 DFPを使用します。生成HEXにはRAM用レコードも含まれるため、ELFからFlash領域専用バイナリを作って検証します。

## PC Webモニタ

```powershell
.\scripts\run-monitor.ps1
```

ブラウザで `http://127.0.0.1:8765/` を開き、COM3へ接続します。

- 「ボード確認」: APAN `HELLO` を送り、`AcrylicPanCollector` 応答を確認
- 「静止波形を取得」: `CAPTURE` でZ軸512点をワンショット取得
- 波形とDC除去・Hann窓FFTを表示
- NPZ、`manifest.csv`、`manifest.jsonl`へ自動保存

主なHTTP API:

- `GET /api/status`
- `GET /api/ports`
- `POST /api/connect` — `{"port":"COM3","baudrate":115200}`
- `POST /api/command` — `{"command":"ping"}` または `{"command":"capture"}`
- `GET /api/events/latest`
- `POST /api/session`

## ファームUART API

APAN v1、COBS、CRC32、末尾 `0x00` のバイナリフレームです。

- `HELLO (0x01)`
- `STATUS (0x02)`
- `START (0x10)` — 次の衝撃を待つ
- `STOP (0x11)`
- `CAPTURE (0x13)` — 次のZ軸512点を直ちに取得
- `EVENT_DATA (0x20)`
- `ACK (0x70)` / `NACK (0x71)`

`STATUS.flags` のbit 0はLCDへの `test` 描画成功、bit 1～3はLED1～3の実出力状態です。起動自己診断がすべて成功した場合は `0x0f` になります。

端末診断用にASCIIの `PING`、`STATUS`、`CAPTURE`、`GET_STATIC` も使用できます。

起動時は停止状態です。`CAPTURE` は1ブロックだけセンサを動かして送信後に停止へ戻るため、繰り返し安全に呼べます。通常の衝撃収録は `START` で待機し、128点（5 ms）のプリトリガを含む512点を返します。

## 実機確認値

静止状態で確認したZ軸512点は平均約4,031 LSB、標準偏差約231 LSBでした。KX134の±8 g設定（4096 LSB/g）では平均約0.984 gで、Z軸方向の重力を正しく検出しています。
