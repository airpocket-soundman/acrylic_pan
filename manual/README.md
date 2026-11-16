# Solistpan HTML操作マニュアル

公開URL: https://airpocket-soundman.github.io/acrylic_pan/manual/

展示・演奏シンプル版: https://airpocket-soundman.github.io/acrylic_pan/manual/simple.html

`simple.html` と `simple.css` は初期設定済みの展示機向けの5項目のガイド。`solistpan-simple.pdf` はA4縦・5ページ。設置・配線・起動と演奏・曲とカメラ・終了に限定し、調整や開発作業はフル版へ案内する。写真枠P02/P08/P05/P09はフル版と同じ対象を示す。

GitHub Pagesの既存設定（main / リポジトリ直下）で配信する静的ページ。外部フォント、CDN、ビルドは不要。HTML内のローカルAPIへのリクエストはなく、説明書から実機操作は行わない。

- `index.html`: 本文。最新配備モデルのみを記載。
- `style.css`: レスポンシブ表示とA4縦の印刷指定。
- `manual.js`: 目次、画像拡大、印刷。
- `solistpan-manual.pdf`: A4縦・33ページのPDF版。HTMLヘッダーの「PDF版」からダウンロード可能。
- `pdf.css`: ダウンロード用PDFの追加印刷レイアウト。
- `assets/*.png`: 実装を実際に起動して撮影したUIと、後付けの番号・枠。
- `assets/device-wiring.svg`、`assets/debug-network.svg`: 機器配線とデバッグ時のネットワークを示すベクター模式図。
- `assets/capture-notes.json`: 撮影元コミット、環境、ボタン位置の記録。

## 写真の差し替え

本文末尾にP01〜P07の撮影リストがある。各 `div.photo#photo-pXX` を実写のfigureに置き換える。写真は `assets/` に置き、内容が分かるaltと図の説明を付ける。撮影依頼の矩形は写真提供までは維持する。

## 確認方法

リポジトリ直下で `python -m http.server 8878 --bind 127.0.0.1` を起動し、`http://127.0.0.1:8878/manual/` を開く。PC、幅390pxのスマートフォン、画像拡大、目次、内部リンク、印刷時のA4縦レイアウトを確認する。

画面写真はWindows / Chromeで撮影。演奏と追加学習は `scripts/exhibit_fake_server.py`、それ以外は基板接続なしの `python -m pc.acrylic_pan_web --no-browser` を使用した。カメラ・実機通信・ラズパイのOS操作は画像撮影の対象外。管理画面の端末固有情報は伏せている。疑似基板の正解率は性能値として使わない。

モデル情報の更新時は配備記録、アーカイブmanifest、独立評価記録、生成ヘッダーCRCを照合する。「セット」とファイル上の「セッション」を区別する。

## PDFの再生成

Pythonに `playwright` と `PyMuPDF` を導入し、リポジトリ直下で次を実行する。

```powershell
python scripts/export-manual-pdf.py --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

`output/pdf/solistpan-manual.pdf` を作成し、公開用の `manual/solistpan-manual.pdf` にもコピーする。リンク付きの目次とページ番号を生成する。画像表示、全ページの改ページ、A4縦の用紙サイズを確認し、本文とPDFを一緒に更新する。写真の追加などでページ数が変わった場合は本READMEのページ数も更新する。

シンプル版は同じ環境で次を実行する。

```powershell
python scripts/export-simple-manual-pdf.py --browser-executable "C:/Program Files/Google/Chrome/Application/chrome.exe"
```

`output/pdf/solistpan-simple.pdf` と公開用の `manual/solistpan-simple.pdf` を更新する。
