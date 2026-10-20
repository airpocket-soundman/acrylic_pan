/* Slide content, speaker notes and target times. Each slide renders into a fixed 1920 x 1080 stage.
   render(ctx): ctx.preview is true in the presenter thumbnails, where heavy media is replaced by a placeholder. */
(function () {
  const A = '../assets/';
  const esc = text => String(text).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  const head = (kicker, title) =>
    `<header class="s-head"><span class="kicker">${kicker}</span><h2>${title}</h2></header>`;

  /* 60 measurement points: 12 area centres and 48 points 25 mm from the centre (4 x 3 areas, 400 x 300 mm). */
  function gridSvg() {
    const W = 400, H = 300, pad = 14;
    const parts = [`<svg viewBox="${-pad} ${-pad} ${W + pad * 2} ${H + pad * 2}" class="grid-svg" role="img" aria-label="60測定点の配置">`];
    parts.push(`<rect x="0" y="0" width="${W}" height="${H}" rx="4" class="g-board"/>`);
    for (let c = 1; c < 4; c++) parts.push(`<line x1="${c * 100}" y1="0" x2="${c * 100}" y2="${H}" class="g-line"/>`);
    for (let r = 1; r < 3; r++) parts.push(`<line x1="0" y1="${r * 100}" x2="${W}" y2="${r * 100}" class="g-line"/>`);
    parts.push(`<rect x="200" y="0" width="100" height="20" class="g-clamp"/>`);
    for (let r = 0; r < 3; r++) for (let c = 0; c < 4; c++) {
      const cx = c * 100 + 50, cy = r * 100 + 50;
      for (const dx of [-25, 25]) for (const dy of [-25, 25])
        parts.push(`<circle cx="${cx + dx}" cy="${cy + dy}" r="5" class="g-corner"/>`);
      parts.push(`<circle cx="${cx}" cy="${cy}" r="8" class="g-center"/>`);
    }
    parts.push('</svg>');
    return parts.join('');
  }

  /* One 128-32 network with five switchable 12-output heads. */
  function headsSvg() {
    const rows = [];
    const x0 = 80, xH = 640, xO = 1040;
    rows.push('<svg viewBox="0 0 1500 560" class="heads-svg" role="img" aria-label="12出力のβを5つ切り替えて60出力にする図">');
    rows.push(`<g class="h-box"><rect x="${x0 - 60}" y="150" width="190" height="260" rx="18"/><text x="${x0 + 35}" y="262" class="h-t">入力</text><text x="${x0 + 35}" y="306" class="h-s">128点の波形</text></g>`);
    rows.push(`<line x1="${x0 + 130}" y1="280" x2="${x0 + 250}" y2="280" class="h-arrow"/>`);
    rows.push(`<g class="h-box fixed"><rect x="${x0 + 250}" y="130" width="290" height="300" rx="18"/><text x="${x0 + 395}" y="252" class="h-t">隠れ層 32</text><text x="${x0 + 395}" y="298" class="h-s">α は固定</text><text x="${x0 + 395}" y="336" class="h-s">(5回とも共通)</text></g>`);
    for (let i = 0; i < 5; i++) {
      const y = 20 + i * 104;
      rows.push(`<line x1="${x0 + 540}" y1="280" x2="${xH + 60}" y2="${y + 40}" class="h-fan"/>`);
      rows.push(`<g class="h-head"><rect x="${xH + 60}" y="${y}" width="330" height="80" rx="14"/><text x="${xH + 225}" y="${y + 36}" class="h-ht">β${i + 1}</text><text x="${xH + 225}" y="${y + 66}" class="h-hs">12出力(位置 ${i * 12 + 1}〜${i * 12 + 12})</text></g>`);
      rows.push(`<line x1="${xH + 390}" y1="${y + 40}" x2="${xO + 130}" y2="280" class="h-fan out"/>`);
    }
    rows.push(`<g class="h-box out"><rect x="${xO + 130}" y="150" width="270" height="260" rx="18"/><text x="${xO + 265}" y="262" class="h-t">60出力</text><text x="${xO + 265}" y="306" class="h-s">60位置の確率</text></g>`);
    rows.push('</svg>');
    return rows.join('');
  }

  const SLIDES = [
    {
      id: 'title', title: 'Solistpan', seconds: 15,
      notes: [
        'それではSolistpanについて説明させていただきます。airpocketと言います。よろしくお願いします。',
      ],
      render: () => `
        <div class="s-title">
          <div class="s-title-text">
            <span class="chip">Solist-AI × エッジAI</span>
            <h1>Solistpan</h1>
            <p class="lead">加速度センサ<strong>1個</strong>で、<br>叩いた場所を判断して音を鳴らす<br><span class="amber">Solist-AIの電子楽器</span></p>
            <p class="foot">ROHM EDGE HACK CHALLENGE 2026 決勝</p>
          </div>
          <figure class="s-title-fig"><img src="${A}solistpan-exterior-illustration.png" alt="Solistpanの外観"></figure>
        </div>`,
    },
    {
      id: 'demo', title: 'ライブデモ', seconds: 300, demo: true, scenes: 9, zoomSteps: 3,
      notes: [
        '(背景は、デモのカメラ映像の大写し。カメラの設定は、事前に済ませておく)',
        '(右矢印で進む: ①カメラ映像 → ②スチールパン → ③しくみ → ④画面が現れる → 各カードの説明 → 演奏 → 終了)',
      ],
      steps: [
        {title: '①導入(カメラ映像)', sec: 20,
         action: '右矢印で次の場面へ',
         say: 'Solistpanはひとことでいうと、アクリル板とSolistAIで作った、スチールパンを模した電子楽器です。'},
        {title: '②スチールパン', sec: 30,
         action: '右矢印: スチールパンの写真が重なる',
         say: 'スチールパンはこちらですね。カリブの島国、トリニダード・トバゴ発祥の、ドラム缶を切って叩いて作った楽器です。'},
        {title: '③しくみ', sec: 35,
         action: '右矢印: スチールパンが消える',
         say: 'このようにアクリル板を固定して、真ん中に設置した振動センサで、アクリル板を叩いた振動をピックアップして、SolistAIで、どこを叩いたかを推論しています。'},
        {title: '④デモ画面へ', sec: 15,
         action: '右矢印: カメラ映像が縮んで、デモの画面が現れる',
         say: 'ここから、実際の画面でご説明します。まず、画面の各カードを説明します。'},
        {title: 'カード1: 接続と設定', sec: 30, spot: 'controls',
         action: '右矢印: 上部のカード(板仕様・COMポートなど)が浮き上がる',
         say: '一番上のカードです。ここでは、SolistPanやカメラとの接続、設定などを操作します。'},
        {title: 'カード2: 12エリア確率', sec: 40, spot: 'areas',
         action: '右矢印: 12エリア確率のカードが浮き上がる',
         say: 'アクリル板は、3行4列のエリアに分かれていて、打撃位置がどのパネルに相当するのかを、SolistAIが推論します。推論結果は、合計100%に正規化されて、このエリアにグラフで表示されます。'},
        {title: 'カード3: 演奏の設定', sec: 45, spot: 'player',
         action: '右矢印: 最下段のカードが浮き上がる。「エリーゼのために」を選び、演奏を開始する',
         say: '最下段では、演奏モードや音色の設定を行います。では、実際に曲を選んで弾いてみます。',
         run: ['start', 'song:fur_elise']},
        {title: 'カード4: カメラ', sec: 70, spot: 'camera',
         action: '右矢印: カメラのカードが浮き上がる。演奏しながら説明する(エリーゼのために 2小節ほど)',
         say: 'ここには、カメラのライブ画像が映っていて、12エリアを枠線で表示できます。叩いた場所が、確率分布のヒートマップで表示されて、おおむね正解していることが分かるかと思います。ピンク色のマスが次に叩くエリア、薄いピンクが、その次のエリアです。'},
        {title: '終了', sec: 15,
         action: '右矢印: 演奏を終了する。次のスライドへ',
         say: '以上が、ライブデモです。',
         run: ['stop']},
      ],
      render: ctx => `
        <div class="s-demo">
          <div class="s-demo-frame" data-demo-frame>${ctx.preview ? '<div class="demo-ph">ライブデモ(確率演奏ページ)</div>' : ''}</div>
        </div>`,
    },
    {
      id: 'sim', title: 'なぜセンサ1個で位置がわかるか', seconds: 30,
      notes: [
        'なぜセンサ1個で位置がわかるのか。',
        'アクリル板には、たくさんの振動モードがあります。どのモードがどれだけ揺れるかは、叩いた位置で決まります。',
        'これは、3次元FEMで計算した12か所の打撃応答の動画です。叩く位置によって、板の揺れ方が違います。この違いが、中央のセンサ1個の波形にも「モードの混ざり方」として現れます。',
      ],
      render: ctx => `
        ${head('PRINCIPLE', 'なぜセンサ1個で位置がわかるのか')}
        <div class="sim-layout">
          <div class="sim-video">${ctx.preview
            ? '<div class="demo-ph">3次元FEM 12打点の動画</div>'
            : `<video src="${A}simulation/5mm-400x300/solid3d/solid3d-12-hits.mp4" controls muted loop playsinline preload="auto" data-autoplay></video>`}
            <p class="cap">3次元FEMによる12打点の過渡応答(400×300×5 mm、◆=センサ、★=打点)</p>
          </div>
          <ol class="steps">
            <li><b>板は多数の振動モードを持つ</b><span>5 mm板の解析値: 13, 21, 45, 73, 99, 119, 160 Hz …</span></li>
            <li><b>どのモードがどれだけ揺れるかは、叩いた位置で決まる</b><span class="formula">寄与 ∝ φ(打点) × φ(センサ位置)</span></li>
            <li><b>中央1点の波形にも、位置ごとに違う「モードの混ざり方」が現れる</b><span>この違いを Solist-AI に学習させる</span></li>
          </ol>
        </div>`,
    },
    {
      id: 'e2e', title: 'E2Eの符号化と復号', seconds: 25,
      notes: [
        '別の見方をすると、これはE2Eの符号化と復号です。',
        '叩いた位置という情報を、アクリル板が、振動モードの混ざり方に符号化します。これは物理現象です。そのまま、センサに受信されます。',
        'それを、Solist-AIが波形から60か所の確率へ復号します。モード解析はしていません。符号化は物理が、復号はエッジAIが担当する、という分担です。',
      ],
      render: () => `
        ${head('END TO END', 'アクリル板が「物理エンコーダー」になる')}
        <div class="flow">
          <div class="node"><small>情報源</small><b>叩いた位置</b><span>(x, y)</span></div>
          <div class="node amber-node"><small>エンコーダー</small><b>アクリル板</b><span>位置を「振動モードの混ざり方」に変換</span></div>
          <div class="node white-node"><small>符号</small><b>一点の振動</b><span>板のどの点にも現れる</span></div>
          <div class="node teal-node"><small>受信機</small><b>加速度センサ</b><span>KX134-1211 / 25.6 kHz</span></div>
          <div class="node teal-node"><small>デコーダー</small><b>Solist-AI</b><span>波形 → 60点の確率</span></div>
        </div>
        <div class="bracket-row"><span class="bracket amber">符号化 = 物理現象</span><span class="bracket teal">復号 = エッジAI</span></div>
        <div class="banner">物理現象そのものが計算機として働く — 物理並列計算機<br><small>モード解析はしない。板は位置を波形へ、AIは波形を位置へ、どちらもE2Eで変換する</small></div>`,
    },
    {
      id: 'aims', title: 'ふたつの狙い', seconds: 50, scenes: 3,
      notes: [
        '(右矢印で、①→②の順にフォーカスが移る)',
      ],
      steps: [
        {title: '二つのテーマ', sec: 10,
         action: '右矢印: ①楽器兼ゲームにフォーカス',
         say: 'このプロジェクトは、二つのテーマを持って開発に当たりました。'},
        {title: '①楽器兼ゲーム', sec: 30, focus: 0,
         action: '右矢印: ②エッジAI研究にフォーカス',
         say: '一つ目は、デモでもお見せした、遊べる楽器兼ゲームを作ることです。推論までのロジックはSolistAIで完結していますので、楽器としては、再生用の機材をつなげば、スタンドアロンでも成立します。今回は、デモでもお見せしたような、リッチなUIをつけて、推論結果だけでなく、教示機能なども含めて、実装しました。'},
        {title: '②エッジAI研究', sec: 10, focus: 1,
         action: '右矢印: 次のスライドへ',
         say: 'もう一つは、面白い機能を持つSolistAIの研究です。'},
      ],
      render: () => `
        ${head('OBJECTIVES', 'ふたつの狙いを、ひとつの作品で')}
        <div class="two-col">
          <section class="card amber-card" data-focus="0">
            <span class="num">1</span>
            <h3>楽器兼ゲーム</h3>
            <p>叩くと音が鳴り、叩いた場所が見える。<br>曲に合わせて叩いて遊べる。</p>
          </section>
          <section class="card teal-card" data-focus="1">
            <span class="num">2</span>
            <h3>エッジAI研究</h3>
            <p>センサ1個の波形から、位置をボード上で推論する。<br>物理現象とエッジAIの役割分担を確かめる。</p>
          </section>
        </div>`,
    },
    {
      id: 'elm', title: 'ELMだから小さい', seconds: 30,
      notes: [
        'ここで、SolistAIのELMに実装した意味をお話しします。PC版で使っている、通常のニューラルネットワークは、パラメータが、3つ合わせて約112万個。float32で、約4.3メガバイトあります。',
        'SolistAIのELMは、パラメータが約6千個、約12キロバイトです。個数で、約186分の1。1つ同士で比べても、約62分の1です。',
        'それなのに、60か所の正解率は、PC版が98.75パーセント、SolistAIが97.9パーセントで、ほとんど落ちていません。',
      ],
      render: () => `
        ${head('WHY ELM', 'ELMで実装すると、ここまで小さくなる')}
        <div class="elm-grid">
          <section class="card elm-card">
            <p class="sub">PC版: 通常のニューラルネット(全結合MLP)</p>
            <p class="elm-struct">714 − 384 − 192 − 96 − 60 <small>× 3つのアンサンブル</small></p>
            <div class="elm-size"><b>1,118,484</b><span>パラメータ</span></div>
            <div class="elm-size"><b>約 4.3 MB</b><span>float32</span></div>
          </section>
          <div class="elm-arrow"><b>約 1/186</b><span>1つ同士でも 1/62</span></div>
          <section class="card teal-card elm-card">
            <p class="sub">Solist-AI: ELM(ボード上で推論)</p>
            <p class="elm-struct">128 − 32 − 60 <small>固定の入力重み + 学習する出力重み</small></p>
            <div class="elm-size"><b>6,016</b><span>パラメータ(学習するのは出力側の1,920)</span></div>
            <div class="elm-size"><b>約 12 KB</b><span>bfloat16</span></div>
          </section>
        </div>
        <div class="elm-acc">
          <div class="elm-bar"><span>PC版</span><i style="width:98.75%"></i><b>98.75%</b></div>
          <div class="elm-bar teal"><span>Solist-AI</span><i style="width:97.9%"></i><b>97.9%</b></div>
          <p class="small">60か所の正解率(Top-1)。どちらも、2点支持の5セッション・3,600打で学習し、反復番号が5の倍数の打撃を除いた、同一セッション内の分割で評価(未知の条件での値ではない)</p>
        </div>`,
    },
    {
      id: 'heads', title: '12ヘッド×5=60クラス', seconds: 30,
      notes: [
        'Solist-AIの実機で動作を確認できているのは、12出力のネットワークです。そこで、12出力のヘッドを5つ束ねて、60クラス分類にしました。',
        '入力から隠れ層までは共通で固定です。出力側の重みβだけを、5回切り替えて実行し、60個のロジットを得ます。それをMCUでsoftmaxして、60か所の確率にします。',
        'つまり、実機で確認済みの12ヘッドの仕組みを、そのまま5回使って、60か所の位置まで細かくしています。',
      ],
      render: () => `
        ${head('ON-DEVICE MODEL', '12ヘッドを5つ束ねて、60クラスにする')}
        <div class="heads-layout">
          ${headsSvg()}
          <ul class="points">
            <li>実機で動作確認済みの<b>12出力</b>を、βだけ切り替えて<b>5回</b>実行</li>
            <li>入力128 → 隠れ32 → 出力60(<b>128-32-60</b>、bfloat16)</li>
            <li>MCUでsoftmax → <b>60位置の確率</b>をPCへ送信</li>
          </ul>
        </div>`,
    },
    {
      id: 'accuracy', title: '学習データと精度', seconds: 15,
      notes: [
        '60か所は、エリア中心の12点と、中心から25ミリ離れた四隅の48点です。5セッションで、合計3,600回叩いてデータを集めました。',
        '実機に載せたモデルの評価は、60か所の正解率が97.9パーセント、期待座標の平均誤差が5.5ミリです。ただし、同じ収録セッション内での分割評価なので、未知の条件での値ではありません。',
      ],
      render: () => `
        ${head('DATA &amp; RESULTS', '学習データと精度')}
        <div class="acc-layout">
          <div class="acc-map">${gridSvg()}
            <p class="legend"><i class="lg center"></i>エリア中心 12点 <i class="lg corner"></i>中心から±25 mmの4点 × 12 = 48点</p>
            <p class="strong">60点 × 5セッション = <b>3,600打点</b></p>
          </div>
          <div class="acc-cards">
            <p class="sub">実機搭載モデル(Solist-AI 128-32-60)</p>
            <div class="stat"><b>97.9%</b><span>60測定点の正解率(Top-1)</span></div>
            <div class="stat"><b>5.5 mm</b><span>期待座標の平均誤差(中央値 0.6 mm)</span></div>
            <div class="stat"><b>95.6%</b><span>誤差 25 mm 以内に収まる割合</span></div>
            <p class="small">※ 実機に書き込む重みと同じものをPC上で評価。評価の720打点は学習に未使用(同一セッション内の分割)</p>
          </div>
        </div>`,
    },
    {
      id: 'odl', title: '12ヘッド版のODL', seconds: 20,
      notes: [
        '12ヘッド版では、Solist-AIのオンデバイス学習、ODLが使えます。設置や固定、叩くマレットが変わっても、その場で補正できます。',
        'マレットを普段のものからサインペンに替えて、各エリアを5回ずつ、合わせて60回叩いて学習させました。学習の前と後に、120回ずつ叩いて確認したところ、正解率は60パーセントから95パーセントになりました。',
        '1回の学習は約2ミリ秒で、ボード上で完結します。精度が下がったときは保存せず、元に戻す安全策も入れています。60クラス側は対象外です。',
      ],
      render: () => `
        ${head('ON-DEVICE LEARNING', '12ヘッド版は、現場でODLにより補正できる')}
        <div class="odl-layout">
          <ol class="odl-steps">
            <li><b>確認</b><span>各エリア1打で、いまの精度を測る</span></li>
            <li><b>学習</b><span>各エリア5打 × 12 = 60打をボード上のOS-ELMで学習</span></li>
            <li><b>確認 → 保存 / 破棄</b><span>正解数が下がらなければFRAMへ保存。下がれば元へ戻す</span></li>
          </ol>
          <div class="odl-bars">
            <div class="bar-row"><span>サインペンで叩いた場合(実機)</span><div class="bar"><i style="width:60%" class="b-old"></i><i style="width:35%" class="b-gain"></i></div><em>60.0% → 95.0%</em></div>
            <p class="small">マレットを普段のものからサインペンに替え、各エリア5打(60打)のODL。ODLの前と後に、各エリア10打(120打)ずつ評価。実機、12クラスのモデル。</p>
          </div>
        </div>`,
    },
    {
      id: 'summary', title: 'まとめ', seconds: 15,
      notes: [
        'まとめです。センサ1個のアクリル板を、Solist-AIで楽器にしました。',
        '楽器兼ゲームとして遊べて、エッジAIの研究としても、物理が符号化し、エッジAIが復号するという分担を確かめられました。ありがとうございました。',
      ],
      render: () => `
        ${head('SUMMARY', 'センサ1個のアクリル板が、楽器になる')}
        <div class="two-col">
          <section class="card amber-card"><span class="num">1</span><h3>楽器兼ゲーム</h3>
            <p>ヒートマップ・疑似XY座標・音色変更・音楽教示で、叩いて遊べる</p></section>
          <section class="card teal-card"><span class="num">2</span><h3>エッジAI研究</h3>
            <p>物理が符号化し、Solist-AIが復号する。12ヘッド×5で60クラス、12ヘッド版はODLで現場補正</p></section>
        </div>
        <p class="thanks">ご清聴ありがとうございました</p>`,
    },
  ];

  const DEMO_INSTRUMENTS = [['steel_drum', 'スチールドラム'], ['piano', 'ピアノ'], ['harpsichord', 'チェンバロ'], ['guitar', 'ギター'], ['drums', 'ドラム']];
  const DEMO_SONGS = [['', '自由演奏'], ['ode_to_joy', '歓喜の歌'], ['fur_elise', 'エリーゼのために'], ['frere_jacques', 'フレール・ジャック'],
                      ['amazing_grace', 'アメイジング・グレイス'], ['auld_lang_syne', '蛍の光'], ['brahms_lullaby', 'ブラームスの子守歌'],
                      ['minuet_g', 'メヌエット ト長調'], ['korobeiniki', 'コロベイニキ']];

  window.DECK = {DEMO_INSTRUMENTS, DEMO_SONGS, SLIDES, esc, totalSeconds: SLIDES.reduce((sum, slide) => sum + slide.seconds, 0)};
})();
