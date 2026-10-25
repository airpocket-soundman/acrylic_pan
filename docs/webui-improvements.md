# Web UIの機能(音色、8点フィット、ODL、接続模式図)

派生プロジェクト [acrylic_pan_uno_q](https://github.com/airpocket-soundman/acrylic_pan_uno_q) のWebUIの改良を取り込み、
60位置単一モデルの現場キャリブレーション(ODL)の画面を確率演奏ページへ統合した。AIの出力元はSolist-AIである。
UNO Q固有の項目(内部SPIの接続固定、UNO Qカメラの配信、Linux推論モデルの選択)は取り込んでいない。
使い方の全体は[収録システム・実機Web UIクイックスタート](collector-quickstart.md)を参照する。

| 機能 | 内容 | 場所 |
|---|---|---|
| 音色 | FluidSynth と SoundFont(GeneralUser GS)で、1音ずつWAVを作り、`/api/audio/note.wav` で配信する。FluidSynth がなければ、手続き型の合成の音を返す。音の長さ(最大5倍)と音色(スチールドラム、ピアノ、チェンバロ、ギター、ドラム)を選べる。ライセンスは `THIRD_PARTY_NOTICES.md` | `pc/acrylic_pan_web/audio_synth.py`、`static/server-audio.js` |
| 音量・音の割り当て | 音量スライダーと、エリアごとの音の割り当て。曲を選ぶと、その曲に必要な音がエリアに割り当てられる | `static/instrument.js`、`static/song.js` |
| 演奏補助 | 曲(エリーゼのために、歓喜の歌、マリオなど)を選ぶと、次に叩くエリア(濃いピンク)とその次のエリア(薄いピンク)が光る。手本演奏もできる | `static/instrument-probability.js`、`static/song.js` |
| 8点フィット | 確率楽器ページで、カメラ映像の上に、板の四隅と行の境界(4点)を置いて、エリアの枠を合わせる | `static/instrument-probability.js` |
| ODLキャリブレーション | 確率演奏ページで高速（12中心点・ソフトターゲット）または精密（60地点・one-hot）を選択。地点表示、保存状態、工場復帰。操作は `/api/calibration/*` | `static/position-calibration.js`、`server.py` |
| 接続模式図 | SolistAI の CN3 と KX134-1211-EVK-001 の J1 のピン配置と配線の図 | `web/pages/hardware.html` |
| プレゼン用の埋め込み | `?embed=1` を付けた確率楽器ページを、プレゼンのメイン画面が埋め込み、操作を受け付ける | [決勝審査会プレゼン資料](final-presentation-plan.md) |

## 仕様上の注意

- ODL、位置ヒートマップ、12エリア演奏は同じ60出力モデルを使う。保存済みβは1件で、次の校正コミットにより置き換わる。
- ODLの更新は、AxlCOREの`ODL_StartTrain`で行う([ファームウェア仕様](firmware-specifications.md#6-現場キャリブレーション))。
- 「工場に戻す」は、保存済みのキャリブレーションを無効にする。無効にしたあとで、保存済みを再び有効にする操作はなく、再度使うには、キャリブレーションをやり直す。
