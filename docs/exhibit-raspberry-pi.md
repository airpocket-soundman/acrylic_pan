# 展示会用ラズパイのセットアップ手順

展示用の4ページ（[exhibit-kiosk.md](exhibit-kiosk.md)）をラズパイで動かし、電源を入れたら演奏ページが全画面で自動的に開くようにする。

## 展示機の役割分担

展示時のAI処理はラズパイではなく、センサ基板側で完結する。

```text
KX134加速度センサ
  └─ SPI → ML63Q2557 / Solist-AI
             打撃検出・特徴抽出・60位置分類・softmax・ODL
             └─ USBシリアル → Raspberry Pi
                                  Python Webサーバ・Web UI・発音
                                  USBカメラ映像・デモ動画
```

- MCUは単一の60出力モデルを推論し、12エリア表示・発音は60位置確率を合計して求める。
- 高速／精密ODLもMCU上の同じ60出力モデルを更新し、確定したβは基板のFRAMへ1件だけ保存する。
- Raspberry PiはUSBシリアルで結果を受け取る表示・音・映像側であり、通常運転ではモデル推論やODL計算を行わない。
- USBケーブルは給電専用ではなく、データ通信対応品を使う。通常接続では起動後に960 kbpsへ切り替わる。

## 用意するもの

- Raspberry Pi（Raspberry Pi OS 64bit、デスクトップ付き）と、HDMIまたはタッチディスプレイ
- センサ基板（USBシリアル）、USBカメラ（使う場合）、スピーカー（HDMIまたは3.5mm）
- ネットワーク（セットアップ時に必要。Tailscaleで遠隔保守する場合は展示運用中も必要）

## 展示キット持ち物チェックリスト

- [ ] アクリル板
- [ ] フレーム
- [ ] Solist-AI
- [ ] センサー
- [ ] センサーケーブル
- [ ] USB A to C（Solist-AIとRaspberry Piの接続用）
- [ ] Raspberry Pi 4
- [ ] Raspberry Pi用電源
- [ ] HDMI micro to miniケーブル
- [ ] マウス
- [ ] モバイルモニター
- [ ] USB A to C（モバイルモニター電源用）
- [ ] モバイルモニター用AC電源
- [ ] モバイルスピーカー
- [ ] モバイルスピーカー用電源
- [ ] ミニオーディオケーブル

## 1. リポジトリを置く

```bash
git clone <リポジトリURL> ~/acrylic_pan
cd ~/acrylic_pan
git checkout solistpan-final
```

動画は `web/assets/pv/` のmp4が対象。別の場所に置く場合は、サービスの起動引数に `--video-dir` を足す。

## 2. インストール

```bash
bash scripts/raspberrypi/install-exhibit.sh
```

スクリプトが行うこと:

1. `python3-venv`、`libfluidsynth3`、`libportaudio2`、`unclutter`、Chromiumを入れる
2. リポジトリ内に `.venv` を作り、Raspberry Piでwheelを利用できる版のscikit-learnを含むPython依存を入れる
3. ユーザーを `dialout` グループに追加する（シリアルポート用）
4. 管理ページ用の3個の固定ヘルパーをroot所有で`/usr/local/sbin/`へ置き、このユーザーだけにパスワードなし実行を許可する
5. systemdサービス `acrylic-pan-exhibit` を作って有効化する（`--host 127.0.0.1 --port 8765 --no-browser --auto-connect`）
6. ログイン時に `start-exhibit-kiosk.sh` が走るよう、labwc（Bookworm）とXDG autostartの両方へ登録する
7. 画面の自動消灯を止める

`dialout` の反映にはログインし直しか再起動が必要。

Chromiumの自動起動にはデスクトップセッションへのログインが必要である。展示機は`raspi-config`の「Boot / Auto Login」でDesktop Autologinを有効にする。systemdのWebサーバはログイン前から起動するが、Chromiumは自動ログイン後に起動する。

## 3. 再起動して確認する

```bash
sudo reboot
```

起動後、次の順に確認する。

1. 演奏ページが全画面で開く（上部に「演奏」「追加学習」「デモ動画」「管理」のバー）
2. 右上の丸が緑で「センサ接続中」になる
3. 基板を叩くと音が鳴り、エリアが光る
4. 「追加学習」で高速を選び、基板を叩いて1周進む
5. 「デモ動画」で動画が再生され、シークバーとリピートが動く
6. 「管理」を開くと始まる自動検査で、展示に必要な項目に赤がないことを確認する（黄色は表示された対処内容を確認し、未使用の任意機能は黄色でもよい）

管理ページは開いた時点で自動検査を始める。演奏・追加学習・収録中はUARTへ検査コマンドを割り込ませず、直近の受信状態だけを表示するため、演奏は停止しない。完全なHELLO/STATUS往復検査は演奏停止中に再検査する。追加学習または収録中は、管理ページのシステム操作が無効になる。

コマンドでも自動起動を確認する。

```bash
systemctl is-enabled acrylic-pan-exhibit
systemctl is-active acrylic-pan-exhibit
curl -fsS http://127.0.0.1:8765/api/status
journalctl -u acrylic-pan-exhibit -b --no-pager | tail -n 50
```

最後に、ネットワークを切った状態で再起動し、演奏・発音・カメラが動くことを確認する。Tailscaleを使わない限り、展示の通常動作はインターネット接続を必要としない。

## 4. ファームウェア

「追加学習（さらに5周）」は、保存済みβから続ける開始フラグ（`0x10`）に対応したファームウェアが必要。ビルドして基板に書き込む（[firmware/AcrylicPanCollector/README.md](../firmware/AcrylicPanCollector/README.md)）。古いファームウェアのままだと、追加学習の開始が拒否される。

## 5. マレット交換後

マレットを変えると振動特性が変わるため、モデルを学習し直す。学習データの採取と再学習は [collector-quickstart.md](collector-quickstart.md) と [model-training-max-data-20260828.md](model-training-max-data-20260828.md) の手順で行い、生成されたモデルをファームウェアに取り込んで書き込む。

## 運用

| 操作 | コマンド |
| --- | --- |
| サーバの状態 | `systemctl status acrylic-pan-exhibit` |
| サーバのログ | `journalctl -u acrylic-pan-exhibit -f` |
| サーバの再起動 | `sudo systemctl restart acrylic-pan-exhibit` |
| 画面からの点検・再起動・電源断 | 上部の「管理」（システム操作は3秒間長押し） |
| ブラウザだけ開き直す | `bash scripts/raspberrypi/start-exhibit-kiosk.sh` |
| コードの更新 | `git pull` のあと、サーバを再起動 |
| 画面の抜け出し | キーボードで `Alt+F4`（キオスク表示を閉じる） |

### Tailscaleによる遠隔保守（任意）

Tailscaleは展示の実行要件ではない。利用する場合は公式のRaspberry Pi OS向け手順で導入し、Tailscale SSHを有効にする。Webサーバは`127.0.0.1`待受のままにし、ポート8765をLANやTailnetへ直接公開しない。

```bash
tailscale status
tailscale ip -4
ssh <ラズパイのユーザー>@<Tailnet上のホスト名>
```

遠隔PCのブラウザで画面を確認するときは、SSHポート転送を使う。

```bash
ssh -L 8765:127.0.0.1:8765 <ラズパイのユーザー>@<Tailnet上のホスト名>
```

その接続中だけ、遠隔PCで`http://127.0.0.1:8765/exhibit/play.html`を開ける。展示前には`tailscale status`とSSH接続を確認し、会場ネットワークが使えない場合に備えてローカル操作手段も残す。

### Codexを使う場合

Codexは実行時依存ではなく、遠隔でログ確認や修正を行うための開発ツールである。Codexが未起動でも展示は動く。展示機へ導入する場合もsystemdの展示サービスには組み込まず、次の順で状態を採取してから限定的に使用する。

```bash
cd ~/acrylic_pan
git status --short
systemctl status acrylic-pan-exhibit --no-pager
journalctl -u acrylic-pan-exhibit -b --no-pager | tail -n 100
```

本番直前にCodexから自動更新、依存パッケージ更新、ファーム書き込みを行わない。変更した場合は、下記チェックリストを最初から再実施する。認証情報はリポジトリへ保存しない。

## 展示前の最終チェック

1. 電源投入だけでデスクトップへ自動ログインし、Chromiumがキオスク表示になる。
2. `acrylic-pan-exhibit`が`enabled`かつ`active`で、`/api/status`が応答する。
3. `/dev/serial/by-id/`または`/dev/ttyACM*`に基板が現れ、画面が「センサ接続中」になる。
4. 60位置推論から12エリアが光り、スピーカーから遅延なく発音する。
5. USBカメラ開始、8点フィット、停止後の定型グリッド復帰を確認する。
6. 高速ODLを開始・中止できることを確認する。保存試験を行った場合は、本番用の校正状態へ戻す。
7. デモ動画の再生・シーク・リピートと、演奏ページへ戻れることを確認する。
8. 管理ページの自動検査を実行し、必須項目に赤がないことを確認する。任意のTailscale、Codex、カメラは使わなければ黄色でもよい。
9. 自動検査後に演奏ページへ戻り、推論と発音を再開できることを確認する。
10. USBシリアルを抜き差しし、自動再接続することを確認する。
11. ネットワークなしで再起動し、ローカル展示機能が復帰することを確認する。
12. Tailscaleを使う場合だけ、別端末からSSH接続とログ取得を確認する。

## うまく動かないとき

- **「センサ未接続」のまま**: `ls /dev/ttyACM* /dev/ttyUSB*` で基板が見えるか確認する。見えるのに接続しない場合は `groups` に `dialout` があるか確認する。
- **音が出ない**: ラズパイの音声出力先（HDMI／3.5mm）を、デスクトップの音量メニューで選ぶ。サーバのログに `procedural-fallback` と出る場合は、`libfluidsynth3` が入っていない。
- **カメラの許可を聞かれる**: キオスク起動は `--use-fake-ui-for-media-stream` 付きなので聞かれない。手で開いた場合は許可する。
- **ポート8765が使用中**: `EXHIBIT_PORT` を変えてインストールし直す（サービスとキオスクの両方に反映される）。
- **サーバは動くが画面が自動で開かない**: Desktop Autologinが有効か、`~/.config/labwc/autostart`と`~/.config/autostart/acrylic-pan-exhibit.desktop`を確認する。
- **違うUSBシリアルへ接続する**: `ls -l /dev/serial/by-id/`で基板の固定名を確認する。展示機にUSBシリアル機器が複数ある場合は、サービスの`--auto-connect`へこの固定パスを明示する。
- **管理ページの再起動・電源断が失敗する**: `bash scripts/raspberrypi/install-admin-actions.sh`を再実行し、`sudo -n /usr/local/sbin/acrylic-pan-service-restart`がパスワード入力なしで動くか確認する。再起動・電源断ヘルパーは確認だけで実行しない。
