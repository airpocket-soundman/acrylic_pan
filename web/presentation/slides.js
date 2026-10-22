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
    const x0 = 80, xB = 690, xR = 900, xF = 1250;
    rows.push('<svg viewBox="0 -14 1560 574" class="heads-svg" role="img" aria-label="12出力のβを5つ切り替えて、合計60出力にする図">');
    rows.push(`<g class="h-box"><rect x="${x0 - 60}" y="150" width="190" height="260" rx="18"/><text x="${x0 + 35}" y="262" class="h-t">入力</text><text x="${x0 + 35}" y="306" class="h-s">128点の波形</text></g>`);
    rows.push(`<line x1="${x0 + 130}" y1="280" x2="${x0 + 250}" y2="280" class="h-arrow"/>`);
    rows.push(`<g class="h-box fixed"><rect x="${x0 + 250}" y="130" width="290" height="300" rx="18"/><text x="${x0 + 395}" y="252" class="h-t">隠れ層 32</text><text x="${x0 + 395}" y="298" class="h-s">α は固定</text><text x="${x0 + 395}" y="336" class="h-s">(5回とも共通)</text></g>`);
    for (let i = 0; i < 5; i++) {
      const y = 20 + i * 104;
      rows.push(`<line x1="${x0 + 540}" y1="280" x2="${xB}" y2="${y + 40}" class="h-fan"/>`);
      rows.push(`<g class="h-head"><rect x="${xB}" y="${y}" width="170" height="80" rx="14"/><text x="${xB + 85}" y="${y + 36}" class="h-ht">β${i + 1}</text><text x="${xB + 85}" y="${y + 66}" class="h-hs">出力側の重み</text></g>`);
      rows.push(`<line x1="${xB + 170}" y1="${y + 40}" x2="${xR}" y2="${y + 40}" class="h-arrow short"/>`);
      rows.push(`<g class="h-head res"><rect x="${xR}" y="${y}" width="260" height="80" rx="14"/><text x="${xR + 130}" y="${y + 36}" class="h-ht">12出力</text><text x="${xR + 130}" y="${y + 66}" class="h-hs">位置 ${i * 12 + 1}〜${i * 12 + 12}</text></g>`);
    }
    // One frame around the five 12-output heads: together they work as a 60-output network.
    rows.splice(rows.findIndex(row => row.includes('class="h-head res"')), 0,
      `<rect x="${xR - 26}" y="-4" width="312" height="544" rx="26" class="h-group"/>`);
    rows.push(`<g class="h-box out"><text x="${xF + 70}" y="236" class="h-t">60出力相当</text><text x="${xF + 70}" y="290" class="h-s">12出力 × 5</text><text x="${xF + 70}" y="336" class="h-s">60位置の確率</text></g>`);
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
      id: 'demo', title: 'ライブデモ', seconds: 183, demo: true, scenes: 9, zoomSteps: 3,
      notes: [
        '(背景は、デモのカメラ映像の大写し。カメラの設定は、事前に済ませておく)',
        '(右矢印で進む: ①カメラ映像 → ②スチールパン → ③しくみ → ④画面が現れる → 各カードの説明 → 演奏 → 終了)',
      ],
      steps: [
        {title: '①導入(カメラ映像)', sec: 20,
         action: '右矢印で次の場面へ',
         say: 'Solistpanはひとことでいうと、アクリル板とSolistAIで作った、スチールパンを模した電子楽器です。'},
        {title: '②スチールパン', sec: 10,
         action: '右矢印: スチールパンの写真が重なる',
         say: 'スチールパンは、この写真の楽器ですね。'},
        {title: '③しくみ', sec: 35,
         action: '右矢印: スチールパンが消える',
         say: 'このようにアクリル板を固定して、真ん中に設置した振動センサで、アクリル板を叩いた振動をピックアップして、SolistAIで、どこを叩いたかを推論しています。'},
        {title: '④デモ画面へ', sec: 15,
         action: '右矢印: カメラ映像が縮んで、デモの画面が現れる',
         say: 'ここから、実際の画面でご説明します。まず、画面の各カードを説明します。'},
        {title: 'カード1: 接続と設定', sec: 8, spot: 'controls',
         action: '右矢印: 上部のカード(板仕様・COMポートなど)が浮き上がる',
         say: 'こんな感じで、いろいろ設定できるようになってます。(と言いながら、カード3まで進む)'},
        {title: 'カード2: 12エリア確率', sec: 8, spot: 'areas',
         action: '右矢印: 12エリア確率のカードが浮き上がる',
         say: '(同じ説明を続けながら進む)'},
        {title: 'カード3: 演奏の設定', sec: 12, spot: 'player',
         action: '右矢印: 最下段のカードが浮き上がる。「エリーゼのために」を選び、演奏を開始する',
         say: '(同じ説明を続けながら進む。曲は自動で選ばれ、演奏が始まる)',
         run: ['start', 'song:fur_elise']},
        {title: 'カード4: カメラ', sec: 60, spot: 'camera',
         action: '右矢印: カメラのカードが浮き上がる。演奏しながら説明する(エリーゼのために 2小節ほど)',
         say: 'ここにライブ画像を出して、AR的に情報を重畳しています。'},
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
        'ではセンサ一個で打撃位置がわかるしくみについてですが、このアクリル板をたたくと、板はいろんな揺れ方をします。振動モードっていうんですが、縦とか横とか圧縮伸長とかねじれとか、いろんな揺れ方です。',
        'いろんな揺れ方が混ざって、複雑な揺れになるんですが、叩いた位置によって、振動の混ざり方が変わります。なので、その振動を測定してあげると、どこをたたいたかの情報を持っている、ということになります。',
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
      id: 'e2e', title: 'E2Eの符号化と復号', seconds: 40, scenes: 4,
      notes: [
        '(右矢印で進む: 通常の表示 → 叩いた位置のカードが震える → アクリル板と一点の振動にフォーカス → 加速度センサとSolist-AIにフォーカス)',
      ],
      steps: [
        {title: '通常の表示', sec: 6,
         action: '右矢印: 叩いた位置のカードが、叩かれたように震える',
         say: 'これを情報処理的にとらえると、符号化と復号といいかえられます。'},
        {title: '叩く', sec: 5, hit: true,
         action: '右矢印: アクリル板と一点の振動にフォーカス',
         say: 'まず、アクリルをたたくと振動が発生します。'},
        {title: '板が符号化', sec: 9, focus: 1,
         action: '右矢印: 加速度センサとSolist-AIにフォーカス',
         say: 'この振動は、アクリル板を伝達するうえで、振動モードの混ざり具合という形で、位置情報を符号化します。'},
        {title: '受信と復号', sec: 20, focus: 2,
         action: '右矢印: 次のスライドへ',
         say: 'それを加速度センサで読み取って、SolistAIが復号するという流れになります。つまり、アクリル板がE2Eで符号化し、その情報をSolistAIが、同じくE2Eで復号しているという構造で、アクリル板自体が、物理エンコーダーとして働いているのが、面白いなーなんて思うところです。'},
      ],
      render: () => `
        ${head('END TO END', 'アクリル板が「物理エンコーダー」になる')}
        <div class="flow">
          <div class="node" data-focus="0" data-hit="1"><small>情報源</small><b>叩いた位置</b><span>(x, y)</span></div>
          <div class="node amber-node" data-focus="1"><small>エンコーダー</small><b>アクリル板</b><span>位置を「振動モードの混ざり方」に変換</span></div>
          <div class="node white-node" data-focus="1"><small>符号</small><b>一点の振動</b><span>板のどの点にも現れる</span></div>
          <div class="node teal-node" data-focus="2"><small>受信機</small><b>加速度センサ</b><span>KX134-1211 / 25.6 kHz</span></div>
          <div class="node teal-node" data-focus="2"><small>デコーダー</small><b>Solist-AI</b><span>波形 → 60点の確率</span></div>
        </div>
        <div class="bracket-row"><span class="bracket amber">符号化 = 物理現象</span><span class="bracket teal">復号 = エッジAI</span></div>
        <div class="banner">物理現象そのものが計算機として働く — 物理並列計算機<br><small>モード解析はしない。板は位置を波形へ、AIは波形を位置へ、どちらもE2Eで変換する</small></div>`,
    },
    {
      id: 'aims', title: 'ふたつの狙い', seconds: 28, scenes: 3,
      notes: [
        '(右矢印で、①→②の順にフォーカスが移る)',
      ],
      steps: [
        {title: '二つのテーマ', sec: 10,
         action: '右矢印: ①楽器兼ゲームにフォーカス',
         say: 'このプロジェクトは、二つのテーマを持って開発に当たりました。'},
        {title: '①楽器兼ゲーム', sec: 8, focus: 0,
         action: '右矢印: ②エッジAI研究にフォーカス',
         say: '一つ目は、デモでもお見せした、遊べる楽器兼ゲームを作ることです。'},
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
      id: 'elm', title: 'ELMで学習するのはβだけ', seconds: 25,
      notes: [
        'ROHMさんも聞いておられるところで説明するのもあれなんですが、SolistAIでは、ELMというモデルを使っています。',
        '同規模のモデルと比較すると、パラメータ数で3分の1から4分の1、データサイズで6分の1から7分の1にもかかわらず、かなりいい感じの精度が得られているのがわかります。',
      ],
      render: () => `
        ${head('WHY ELM', 'ELMで学習するのは、出力側のβだけ')}
        <div class="elm-grid">
          <section class="card elm-card">
            <p class="sub">同じ形で、全部を学習</p>
            <p class="elm-struct">128 − 32 − 60 <small>入力側も出力側も学習する</small></p>
            <div class="elm-size"><b>6,108</b><span>学習するパラメータ</span></div>
            <div class="elm-size"><b>約 24 KB</b><span>float32</span></div>
          </section>
          <section class="card elm-card">
            <p class="sub">CNN(小): 1次元の畳み込み</p>
            <p class="elm-struct">Conv − Conv − 全結合 60 <small>入力は波形448点</small></p>
            <div class="elm-size"><b>7,116</b><span>学習するパラメータ</span></div>
            <div class="elm-size"><b>約 28 KB</b><span>float32</span></div>
          </section>
          <section class="card teal-card elm-card">
            <p class="sub">Solist-AI: ELM(ボード上で推論)</p>
            <p class="elm-struct">128 − 32 − 60 <small>入力側は固定(seedから生成)</small></p>
            <div class="elm-size"><b>1,920</b><span>学習するパラメータ(β)</span></div>
            <div class="elm-size"><b>約 3.75 KB</b><span>bfloat16</span></div>
          </section>
        </div>
        <p class="elm-ratio">パラメータ数は約 <b>1/3〜1/4</b>、データサイズは約 <b>1/6〜1/7</b></p>
        <div class="elm-acc">
          <div class="elm-bar"><span>全部を学習</span><i style="width:99.2%"></i><b>99.2%</b></div>
          <div class="elm-bar"><span>CNN(小)</span><i style="width:99.5%"></i><b>99.5%</b></div>
          <div class="elm-bar teal"><span>Solist-AI</span><i style="width:97.9%"></i><b>97.9%</b></div>
          <p class="small">60か所の正解率(Top-1)。2点支持の5セッション・3,600打。同一セッション内の分割で評価(全部を学習する場合とCNNはPC上)</p>
        </div>`,
    },
    {
      id: 'odl', title: '12クラス版のODL', seconds: 20,
      notes: [
        'このパラメータ数が少ないという利点は、SolistAIのもう一つの売りである、ODLにも効果的です。ODLは、簡単に言うと、デバイス上で実行できる追加学習です。',
        'SolistPanでも、この仕組みを使って、叩き方とか、マレットの変更による精度低下を、大幅に改善できます。',
      ],
      render: () => `
        ${head('ON-DEVICE LEARNING', '12クラス版は、現場でODLにより補正できる')}
        <div class="odl-layout">
          <ol class="odl-steps">
            <li><b>確認</b><span>各エリア1打で、いまの精度を測る</span></li>
            <li><b>学習</b><span>各エリア5打 × 12 = 60打をボード上のOS-ELMで学習</span></li>
            <li><b>確認 → 保存 / 破棄</b><span>正解数が下がらなければFRAMへ保存。下がれば元へ戻す</span></li>
          </ol>
          <div class="odl-bars">
            <div class="bar-row"><span>サインペンで叩いた場合(実機)</span><div class="bar"><i style="width:60%" class="b-old"></i><i style="width:35%" class="b-gain"></i></div><em>60.0% → 95.0%</em></div>
            <p class="small">手作りのマレットから固いサインペンに替え、各エリア5打(60打)のODL。ODLの前と後に、各エリア10打(120打)ずつ評価。実機、12クラスのモデル。</p>
          </div>
        </div>`,
    },
    {
      id: 'heads', title: '12ヘッド×5=60クラス', seconds: 50,
      notes: [
        'あと、精度を上げる工夫として、60クラス分類問題に解像度を上げて推論させています。',
        '用途としては、12クラス分類で十分なのですが、もう少し欲張って、確率分布表示したいなとか、座標回帰したいなというのが出てきまして、完全な座標回帰は、さすがに無理なので、60クラス分類を実装して、出力の表現を上げようというのをやりました。',
        '先ほど出てきたELMは、軽くて速いモデルですが、SolistAI上では、12クラスまでしか扱えないという制限がありました。',
        'そこで、12ヘッドのモデルを5回回すことで、60クラス分類と同等の計算結果を得て、なんちゃって座標回帰とか、なんちゃって確率分布ヒートマップ表示を、実装できています。',
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
      id: 'accuracy', title: '学習データと精度', seconds: 20,
      notes: [
        'これが、60クラスの実態です。12マスの、中央と四隅の、60点の打撃データを採取して、事前学習しています。デモでお見せしたのは、この60クラス分類モデルの推論です。',
      ],
      render: () => `
        ${head('DATA &amp; RESULTS', '学習データと精度')}
        <div class="acc-layout">
          <div class="acc-map">${gridSvg()}
            <p class="legend"><i class="lg center"></i>エリア中心 12点 <i class="lg corner"></i>中心から±25 mmの4点 × 12 = 48点</p>
            <p class="strong">60点(12中心+48四隅)、5セッション、計 <b>3,600打</b></p>
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
      id: 'end', title: 'Solistpan', seconds: 5,
      notes: [
        '以上で発表を終わります。ありがとうございました。',
      ],
      render: () => `
        <div class="s-title">
          <div class="s-title-text">
            <h1>Solistpan</h1>
          </div>
          <figure class="s-title-fig"><img src="${A}solistpan-exterior-illustration.png" alt="Solistpanの外観"></figure>
        </div>`,
    },
  ];

  const DEMO_INSTRUMENTS = [['steel_drum', 'スチールドラム'], ['piano', 'ピアノ'], ['harpsichord', 'チェンバロ'], ['guitar', 'ギター'], ['drums', 'ドラム']];
  const DEMO_SONGS = [['', '自由演奏'], ['ode_to_joy', '歓喜の歌'], ['fur_elise', 'エリーゼのために'], ['frere_jacques', 'フレール・ジャック'],
                      ['amazing_grace', 'アメイジング・グレイス'], ['auld_lang_syne', '蛍の光'], ['brahms_lullaby', 'ブラームスの子守歌'],
                      ['minuet_g', 'メヌエット ト長調'], ['korobeiniki', 'コロベイニキ']];

  window.DECK = {DEMO_INSTRUMENTS, DEMO_SONGS, SLIDES, esc, totalSeconds: SLIDES.reduce((sum, slide) => sum + slide.seconds, 0)};
})();
