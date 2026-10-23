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

  /* One 128-32 network with five switchable 12-output heads.  Step 0 shows a single head in a straight line;
     step 1 slides it to the top and adds the other four, then the frame and the label of 60 outputs. */
  function headsSvg() {
    const rows = [];
    const x0 = 80, xB = 690, xR = 900, xF = 1250;
    rows.push('<svg viewBox="0 -14 1560 574" class="heads-svg" role="img" aria-label="12出力のβを5つ切り替えて、60出力相当にする図">');
    rows.push(`<g class="h-box"><rect x="${x0 - 60}" y="150" width="190" height="260" rx="18"/><text x="${x0 + 35}" y="262" class="h-t">入力</text><text x="${x0 + 35}" y="306" class="h-s">128点の波形</text></g>`);
    rows.push(`<line x1="${x0 + 130}" y1="280" x2="${x0 + 250}" y2="280" class="h-arrow"/>`);
    rows.push(`<g class="h-box fixed"><rect x="${x0 + 250}" y="130" width="290" height="300" rx="18"/><text x="${x0 + 395}" y="252" class="h-t">隠れ層 32</text><text x="${x0 + 395}" y="298" class="h-s">α は固定</text><text x="${x0 + 395}" y="336" class="h-s">(5回とも共通)</text></g>`);
    // the frame goes behind the five heads
    rows.push(`<rect x="${xR - 26}" y="-4" width="312" height="544" rx="26" class="h-group hd-frame"/>`);
    for (let i = 0; i < 5; i++) {
      const y = 20 + i * 104;
      const hidden = x0 + 540;
      const fan = i === 0
        ? `<path d="M${hidden} 280 L${xB} ${y + 40}" class="h-fan hd-fan1"/>`
        : `<line x1="${hidden}" y1="280" x2="${xB}" y2="${y + 40}" class="h-fan"/>`;
      if (i === 0) {
        // the first head starts as large as the input box and shrinks while it slides to the top (see deck.css)
        rows.push(`${fan}<g class="hd-r1">`
          + `<g class="h-head"><rect x="${xB}" y="${y}" width="170" height="80" rx="14" class="hd-b"/><text x="${xB + 85}" y="${y + 36}" class="h-ht hd-bt">β1</text><text x="${xB + 85}" y="${y + 66}" class="h-hs hd-bs">出力側の重み</text></g>`
          + `<path d="M${xB + 170} ${y + 40} L${xR} ${y + 40}" class="h-arrow short hd-arr1"/>`
          + `<g class="h-head res"><rect x="${xR}" y="${y}" width="260" height="80" rx="14" class="hd-o"/><text x="${xR + 130}" y="${y + 36}" class="h-ht hd-ot">12出力</text><text x="${xR + 130}" y="${y + 66}" class="h-hs hd-os">位置 1〜12</text></g>`
          + `</g>`);
        continue;
      }
      const body = `<g class="h-head"><rect x="${xB}" y="${y}" width="170" height="80" rx="14"/><text x="${xB + 85}" y="${y + 36}" class="h-ht">β${i + 1}</text><text x="${xB + 85}" y="${y + 66}" class="h-hs">出力側の重み</text></g>`
        + `<line x1="${xB + 170}" y1="${y + 40}" x2="${xR}" y2="${y + 40}" class="h-arrow short"/>`
        + `<g class="h-head res"><rect x="${xR}" y="${y}" width="260" height="80" rx="14"/><text x="${xR + 130}" y="${y + 36}" class="h-ht">12出力</text><text x="${xR + 130}" y="${y + 66}" class="h-hs">位置 ${i * 12 + 1}〜${i * 12 + 12}</text></g>`;
      rows.push(`<g class="hd-row hd-r${i + 1}">${fan}${body}</g>`);
    }
    rows.push(`<g class="h-box out hd-label"><text x="${xF + 70}" y="236" class="h-t">60出力相当</text><text x="${xF + 70}" y="290" class="h-s">12出力 × 5</text><text x="${xF + 70}" y="336" class="h-s">60位置の確率</text></g>`);
    rows.push('</svg>');
    return rows.join('');
  }

  /* Overlay chart of the ELM slide: three models, one after another (steps 1-3). Parameters and sizes are shown
     relative to the first model (= 100), the accuracy as it is, so the first two fall while the third stays up. */
  function elmChartSvg() {
    const models = [
      {name: '① CNN', params: 7116, paramText: '7,116', sizeTop: '約28', accuracy: 99.5},
      {name: '② 全結合NN', params: 6108, paramText: '6,108', sizeTop: '約24', accuracy: 99.2},
      {name: '③ Solist-AI ELM', params: 1920, paramText: '1,920', sizeTop: '約3.75', accuracy: 97.9},
    ];
    const sizes = [28464, 24432, 3840];
    const base = 690, unit = 4.75, barScale = 0.8, xs = [400, 800, 1200];   // bars use 80% of the height the accuracy axis has
    const y = value => base - value * unit;
    const out = [];
    out.push('<svg viewBox="0 0 1536 864" class="elm-chart" role="img" aria-label="3つのモデルのパラメータ数、データサイズ、正解率">');
    out.push('<g class="c-legend"><rect x="70" y="46" width="26" height="26" class="c-sw p"/><text x="106" y="68">パラメータ数(①を100とした割合)</text>'
      + '<rect x="610" y="46" width="26" height="26" class="c-sw d"/><text x="646" y="68">データサイズ(同じく)</text>'
      + '<circle cx="1010" cy="59" r="12" class="c-dot"/><text x="1034" y="68">正解率(%)</text></g>');
    for (const v of [0, 25, 50, 75, 100]) {
      out.push(`<line x1="150" y1="${y(v)}" x2="1450" y2="${y(v)}" class="c-grid"/><text x="1464" y="${y(v) + 9}" class="c-ytick">${v}</text>`);
    }
    models.forEach((m, i) => {
      const xc = xs[i], rel = m.params / models[0].params * 100, relSize = sizes[i] / sizes[0] * 100;
      const hp = rel * unit * barScale, hd = relSize * unit * barScale;
      out.push(`<g class="reveal r${i + 1}">`);
      out.push(`<rect x="${xc - 170}" y="${base - hp}" width="150" height="${hp}" rx="6" class="c-bar p"/>`);
      out.push(`<rect x="${xc + 20}" y="${base - hd}" width="150" height="${hd}" rx="6" class="c-bar d"/>`);
      out.push(`<g class="c-late"><text x="${xc - 95}" y="730" class="c-val">${m.paramText}</text><text x="${xc - 95}" y="764" class="c-unit">パラメータ</text>`
        + `<text x="${xc + 95}" y="730" class="c-val">${m.sizeTop}</text><text x="${xc + 95}" y="764" class="c-unit">KB</text>`
        + `<text x="${xc}" y="812" class="c-name${i === 2 ? ' elm' : ''}">${m.name}</text></g>`);
      out.push('</g>');
    });
    out.push(`<line x1="150" y1="${base}" x2="1450" y2="${base}" class="c-axis"/>`);
    // the accuracy line stays up: segments drawn when the next model appears
    out.push(`<path d="M${xs[0]} ${y(models[0].accuracy)} L${xs[1]} ${y(models[1].accuracy)}" pathLength="1" class="c-line seg2"/>`);
    out.push(`<path d="M${xs[1]} ${y(models[1].accuracy)} L${xs[2]} ${y(models[2].accuracy)}" pathLength="1" class="c-line seg3"/>`);
    models.forEach((m, i) => {
      out.push(`<g class="reveal r${i + 1} c-acc"><circle cx="${xs[i]}" cy="${y(m.accuracy)}" r="15" class="c-dot"/>`
        + `<text x="${xs[i]}" y="${y(m.accuracy) - 30}" class="c-accv">${m.accuracy}%</text></g>`);
    });
    out.push('<g class="reveal r3 c-note"><text x="1200" y="330" class="c-callout">サイズは右肩下がり。</text><text x="1200" y="378" class="c-callout">それでも正解率は</text><text x="1200" y="426" class="c-callout em">ほとんど変わらない</text></g>');
    out.push('<text x="70" y="846" class="c-foot">60か所の正解率(Top-1)。2点支持の5セッション・3,600打。同一セッション内の分割で評価(CNNと全部を学習する場合はPC上)</text>');
    out.push('</svg>');
    return out.join('');
  }

  /* ODL slide: accuracy before the ODL, and (after the right arrow) after it. */
  function odlChartSvg() {
    const base = 470, unit = 4.0;   // 100 % = 400 px
    const bars = [
      {x: 90, value: 60.0, label: '学習前', cls: 'before'},
      {x: 440, value: 95.0, label: 'ODL後', cls: 'after'},
    ];
    const out = [];
    out.push('<svg viewBox="0 0 720 560" class="odl-chart" role="img" aria-label="ODLの前後の正解率">');
    out.push(`<line x1="40" y1="${base - 100 * unit}" x2="700" y2="${base - 100 * unit}" class="o-grid"/><text x="40" y="${base - 100 * unit - 12}" class="o-tick">100%</text>`);
    bars.forEach(b => {
      const h = b.value * unit;
      out.push(`<g class="o-bar ${b.cls}"><rect x="${b.x}" y="${base - h}" width="220" height="${h}" rx="10" class="o-rect"/>`
        + `<text x="${b.x + 110}" y="${base - h - 24}" class="o-val">${b.value.toFixed(1)}%</text></g>`);
      out.push(`<text x="${b.x + 110}" y="${base + 56}" class="o-label ${b.cls}">${b.label}</text>`);
    });
    out.push(`<line x1="40" y1="${base}" x2="700" y2="${base}" class="o-axis"/>`);
    out.push(`<g class="o-arrow"><path d="M336 ${base - 150} L414 ${base - 150}" class="o-arr"/><path d="M400 ${base - 164} L416 ${base - 150} L400 ${base - 136}" class="o-arr"/>`
      + `<text x="375" y="${base - 178}" class="o-note">ODL</text></g>`);
    out.push('</svg>');
    return out.join('');
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
         action: '右矢印: 次のスライドへ',
         say: '以上が、ライブデモです。'},
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
      id: 'e2e', title: '情報処理的な理解', seconds: 40, scenes: 4, hitFrom: 1,
      notes: [
        '(右矢印で進む: 通常の表示 → 叩いた位置のカードが震える → アクリル板と一点の振動にフォーカス → 加速度センサとSolist-AIにフォーカス)',
      ],
      steps: [
        {title: '通常の表示', sec: 6,
         action: '右矢印: 叩いた位置のカードが、叩かれたように震える',
         say: 'これを情報処理的にとらえると、符号化と復号といいかえられます。'},
        {title: '叩く', sec: 5,
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
        ${head('END TO END', '情報処理的な理解')}
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
      id: 'aims', title: '開発のふたつのねらい', seconds: 28, scenes: 3,
      notes: [
        '(右矢印で、①→②の順にフォーカスが移る)',
      ],
      steps: [
        {title: '二つのテーマ', sec: 10,
         action: '右矢印: ①楽器兼ゲームにフォーカス',
         say: 'このプロジェクトは、二つのテーマを持って開発に当たりました。'},
        {title: '①楽器兼ゲーム', sec: 8, focus: 0,
         action: '右矢印: ②エッジAIの勉強にフォーカス',
         say: '一つ目は、デモでもお見せした、遊べる楽器兼ゲームを作ることです。'},
        {title: '②エッジAIの勉強', sec: 10, focus: 1,
         action: '右矢印: 次のスライドへ',
         say: 'もう一つは、面白い機能を持つSolistAIの勉強です。'},
      ],
      render: () => `
        ${head('OBJECTIVES', '開発のふたつのねらい')}
        <div class="two-col">
          <section class="card amber-card" data-focus="0">
            <span class="num">1</span>
            <h3>楽器兼ゲーム</h3>
            <p>叩くと音が鳴り、叩いた場所が見える。<br>曲に合わせて叩いて遊べる。</p>
          </section>
          <section class="card teal-card" data-focus="1">
            <span class="num">2</span>
            <h3>エッジAIの勉強</h3>
            <p>センサ1個の波形から、位置をボード上で推論する。<br>物理現象とエッジAIの役割分担を確かめる。</p>
          </section>
        </div>`,
    },
    {
      id: 'elm', title: 'ELMで学習するのはβだけ', seconds: 32, scenes: 4,
      notes: [
        '(右矢印で進む: モデルの紹介 → グラフを重ねて表示(①CNN) → ②同じ形で全部を学習 → ③ELM。②、③を出しながら説明する)',
      ],
      steps: [
        {title: 'ELMというモデル', sec: 8,
         action: '右矢印: 画面の8割の大きさのグラフを重ねて表示する(①のモデル)',
         say: 'ROHMさんも聞いておられるところで説明するのもあれなんですが、SolistAIでは、ELMというモデルを使っています。'},
        {title: 'グラフ表示(①)', sec: 12,
         action: '右矢印: ②のモデル(同じ形で全部を学習)を表示する',
         say: '同規模のモデルと比較すると、パラメータ数で3分の1から4分の1、データサイズで6分の1から7分の1にもかかわらず、かなりいい感じの精度が得られているのがわかります。'},
        {title: 'グラフ(②)', sec: 6,
         action: '右矢印: ③のモデル(ELM)を表示する',
         say: '(説明を続けながら、②のモデルを出す)'},
        {title: 'グラフ(③)', sec: 6,
         action: '右矢印: 次のスライドへ',
         say: '(説明を続けながら、③のモデルを出す。サイズは右肩下がりでも、正解率はほとんど変わらない)'},
      ],
      render: () => `
        ${head('WHY ELM', 'ELMで学習するのは、出力側のβだけ')}
        <div class="elm-grid">
          <section class="card elm-card">
            <p class="elm-name">① CNN<small>1次元の畳み込み</small></p>
            <p class="elm-struct">Conv − Conv − 全結合 60 <small>入力は波形448点</small></p>
            <div class="elm-size"><b>7,116</b><span>学習するパラメータ</span></div>
            <div class="elm-size"><b>約 28 KB</b><span>float32</span></div>
          </section>
          <section class="card elm-card">
            <p class="elm-name">② 全結合NN<small>3層のニューラルネット</small></p>
            <p class="elm-struct">128 − 32 − 60 <small>同じ形で、入力側も出力側も学習する</small></p>
            <div class="elm-size"><b>6,108</b><span>学習するパラメータ</span></div>
            <div class="elm-size"><b>約 24 KB</b><span>float32</span></div>
          </section>
          <section class="card teal-card elm-card">
            <p class="elm-name">③ Solist-AI ELM<small>入力側を固定した3層</small></p>
            <p class="elm-struct">128 − 32 − 60 <small>入力側は固定(seedから生成)</small></p>
            <div class="elm-size"><b>1,920</b><span>学習するパラメータ(β)</span></div>
            <div class="elm-size"><b>約 3.75 KB</b><span>bfloat16</span></div>
          </section>
        </div>
        <div class="elm-overlay"><div class="elm-panel">${elmChartSvg()}</div></div>`,
    },
    {
      id: 'odl', title: '12クラス版のODL', seconds: 27, scenes: 2,
      notes: [
        '(右矢印で、学習前の棒に続けて、ODL後の棒がアニメーションで表示される)',
      ],
      steps: [
        {title: 'ODLとは', sec: 22,
         action: '右矢印: ODL後の棒を表示する',
         say: 'このパラメータ数が少ないという利点は、SolistAIのもう一つの売りである、ODLにも効果的です。ODLは、簡単に言うと、デバイス上で実行できる追加学習です。SolistPanでも、この仕組みを使って、叩き方とか、マレットの変更による精度低下を、大幅に改善できます。例えば、マレットの代わりにサインペンでたたくと、認識精度が60%程度に落ちましたが、'},
        {title: 'ODL後', sec: 5,
         action: '右矢印: 次のスライドへ',
         say: 'ODLで95%まで回復しました。'},
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
            <p class="odl-title">サインペンで叩いた場合</p>
            ${odlChartSvg()}
          </div>
        </div>`,
    },
    {
      id: 'heads', title: '12ヘッド×5=60クラス', seconds: 55, scenes: 2,
      notes: [
        '(右矢印で、12出力のヘッド1つの図から、5つ束ねた図へ変わる)',
      ],
      steps: [
        {title: '12出力のヘッド', sec: 40,
         action: '右矢印: β1の12出力が上へ移り、β2〜β5が順に加わる',
         say: 'あと、精度を上げる工夫として、60クラス分類問題に解像度を上げて推論させています。用途としては、12クラス分類で十分なのですが、もう少し欲張って、確率分布表示したいなとか、座標回帰したいなというのが出てきまして、完全な座標回帰は、さすがに無理なので、60クラス分類を実装して、出力の表現を上げようというのをやりました。先ほど出てきたELMは、軽くて速いモデルですが、SolistAI上では、12クラスまでしか扱えないという制限がありました。'},
        {title: '5つ束ねる', sec: 15,
         action: '右矢印: 次のスライドへ',
         say: 'そこで、12ヘッドのモデルを5回回すことで、60クラス分類と同等の計算結果を得て、なんちゃって座標回帰とか、なんちゃって確率分布ヒートマップ表示を、実装できています。'},
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
      id: 'accuracy', title: '学習データと精度', seconds: 40, scenes: 3,
      notes: [
        '(右矢印で、アシンメトリー構造 → 制振ダンパー追加の順に、1項目ずつ表示される)',
      ],
      steps: [
        {title: '60クラスの実態', sec: 14,
         action: '右矢印: 「アシンメトリー構造」を表示する',
         say: 'これが、60クラスの実態です。12マスの、中央と四隅の、60点の打撃データを採取して、事前学習しています。デモでお見せしたのは、この60クラス分類モデルの推論です。'},
        {title: 'アシンメトリー構造', sec: 12,
         action: '右矢印: 「制振ダンパー追加」を表示する',
         say: '他にも、対称な振動による誤検出を避けるために、固定点をずらして、アシンメトリーな設計にしたり、'},
        {title: '制振ダンパー追加', sec: 14,
         action: '右矢印: 次のスライドへ',
         say: '連打時の精度を上げるために、ダンパーを追加したりという、くふうもあるのですが、時間ですので、ここまでとさせていただきます。'},
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
            <p class="sub tip acc-r1">精度を上げる工夫</p>
            <div class="stat txt acc-r1"><b>アシンメトリー構造</b><span>対称位置の信号分離性向上</span></div>
            <div class="stat txt acc-r2"><b>制振ダンパー追加</b><span>連打時の信号分離性向上</span></div>
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
