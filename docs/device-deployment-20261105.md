# 展示基板の更新（2026-11-05）

配備構成は128入力・32隠れ・60出力。学習はT1〜T8、計24,000打。48/64ユニット版は仮生成αによるPC比較のみで、配備していない。

`scripts/export_t8_device.py` でモデルと対応するODL初期行列を生成した。工場βのCRC32は `0xEF3DC389`。異なるモデルに紐づく保存済み校正はファームのCRC検査で使用されない。

クリーンビルド後、リンクマップの `ApanCollectorAppInitialize` と `apan_position_beta` を確認し、実行バイナリ内のβが24,000打モデルと全一致することを確認。MCU-Linkで書き込み、別のOpenOCD照合で57,424バイトの一致を確認しリセット実行した。通信用USBがPC側に接続されていなかったため、書き込み直後のシリアル動作確認は未実施。

以前の増分ビルドではベンダー標準mainの古いオブジェクトが残り、展示用コードがリンクから外れた。再書き込み時は `scripts/build-firmware.ps1 -Clean` を使用し、リンクマップと実行バイナリを確認する。

学習用アーカイブは `data/archives/20261103/T1_train.zip`〜`T8_train.zip`（合計24,000打）、独立評価用は `E1_evaluation.zip`〜`E4_evaluation.zip`（計2,400打）。ディレクトリ名は当初の保管日を維持し、追加セットも同じ場所へ集約。各ZIPは元のセッション構造を保持し、`manifest.json` にSHA-256・件数・用途を記録する。

比較表は `capacity-comparison-t8-20261105.md`、個別系列の数値は `capacity-comparison-t8-20261105.json`。
