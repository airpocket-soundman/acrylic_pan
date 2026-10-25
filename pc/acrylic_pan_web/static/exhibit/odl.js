/* Exhibit ODL page: pick quick (12 centres) or precise (60 points), then add 5 more rounds, redo 5 rounds, or go back to the
   factory model.  Uses the same /api/calibration/* sequence as the instrument page. */
(() => {
  const ROUNDS = 5;
  const $ = id => document.getElementById(id);
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const SVG = 'http://www.w3.org/2000/svg';
  // The 60 support points of the 400 x 300 mm panel (same layout as the firmware) and the 12 area centres.
  const edge = [];
  for (const x of [25, 75, 125, 175, 225, 275, 325, 375]) {
    const ys = (x === 225 || x === 275) ? [35, 75, 125, 175, 225, 275] : [25, 75, 125, 175, 225, 275];
    for (const y of ys) edge.push([x, y]);
  }
  const support = edge.concat([50, 150, 250, 350].flatMap(x => [50, 150, 250].map(y => [x, y])))
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const centres = [6, 21, 36, 51, 7, 22, 37, 52, 8, 23, 38, 53];
  const areaOf = position => { const [x, y] = support[position]; return Math.floor(y / 100) * 4 + Math.floor(x / 100); };
  const KIND = { quick: '高速', precise: '精密' };

  let mode = 'quick', running = false, cancelled = false, saved = null;
  let pointNodes = [], cellNodes = [], resultNodes = [], pulseNode = null;

  function node(name, attrs, parent) {
    const element = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, value);
    parent.appendChild(element);
    return element;
  }

  function drawBoard() {
    const svg = $('board');
    svg.replaceChildren();
    cellNodes = [];
    for (let area = 0; area < 12; area++) {
      const col = area % 4, row = Math.floor(area / 4);
      cellNodes.push(node('rect', { x: col * 100, y: row * 100, width: 100, height: 100,
        class: 'cell' + ((col + row) % 2 ? ' alt' : '') }, svg));
      const label = node('text', { x: col * 100 + 8, y: row * 100 + 22, class: 'cell-label' }, svg);
      label.textContent = String(area + 1);
    }
    node('rect', { x: 0, y: 0, width: 400, height: 300, class: 'board-frame' }, svg);
    const left = node('text', { x: 0, y: -3, class: 'axis' }, svg); left.textContent = '0 mm';
    const right = node('text', { x: 400, y: -3, class: 'axis', 'text-anchor': 'end' }, svg); right.textContent = '400 mm';
    resultNodes = [];
    pointNodes = [];
    const positions = mode === 'quick' ? centres : support.map((_, index) => index);
    positions.forEach((position, target) => {
      const [x, y] = support[position];
      pointNodes[target] = node('circle', { cx: x, cy: y, r: mode === 'quick' ? 11 : 6, class: 'pt' }, svg);
    });
    pulseNode = null;
  }

  function clearResult() {
    resultNodes.forEach(item => item.remove());
    resultNodes = [];
    if (pulseNode) { pulseNode.remove(); pulseNode = null; }
  }

  function resetPoints() {
    pointNodes.forEach(point => point.setAttribute('class', 'pt'));
    cellNodes.forEach(cell => cell.classList.remove('target'));
    clearResult();
  }

  function setCurrent(target, position) {
    clearResult();
    pointNodes[target].setAttribute('class', 'pt current');
    cellNodes.forEach((cell, area) => cell.classList.toggle('target', area === areaOf(position)));
    const [x, y] = support[position];
    pulseNode = node('circle', { cx: x, cy: y, r: mode === 'quick' ? 14 : 9, class: 'pulse' }, $('board'));
  }

  function showResult(position, predicted) {
    if (pulseNode) { pulseNode.remove(); pulseNode = null; }
    const [x, y] = support[position], [px, py] = support[predicted];
    if (predicted !== position) {
      resultNodes.push(node('line', { x1: x, y1: y, x2: px, y2: py, class: 'result-line' }, $('board')));
    }
    resultNodes.push(node('circle', { cx: px, cy: py, r: 13, class: 'result-ring' }, $('board')));
  }

  let roundChips = [];
  const roundStats = [];
  function drawRounds() {
    const box = $('rounds');
    box.replaceChildren();
    roundStats.length = 0;
    roundChips = Array.from({ length: ROUNDS }, (_, round) => {
      const chip = document.createElement('div');
      chip.className = 'round-chip';
      chip.innerHTML = `<i></i><b>${round + 1}周目</b><strong>—</strong><small>&nbsp;</small>`;
      box.appendChild(chip);
      roundStats.push({ hits: 0, total: 0 });
      return chip;
    });
  }
  function showRound(round, finished) {
    const { hits, total } = roundStats[round], chip = roundChips[round];
    const percent = total ? Math.round(100 * hits / total) : 0;
    chip.querySelector('strong').textContent = total ? `${percent}%` : '—';
    chip.querySelector('small').textContent = total ? `${hits} / ${total} 正解` : ' ';
    chip.querySelector('i').style.width = `${percent}%`;
    chip.classList.toggle('live', !finished);
    chip.classList.toggle('done', finished);
    chip.classList.toggle('low', percent < 70);
  }

  function setMessage(text, kind) {
    const element = $('instruction');
    element.textContent = text;
    element.className = 'odl-instruction' + (kind ? ' ' + kind : '');
  }

  function showSaved(status) {
    if (!status) return;
    saved = status;
    $('modelState').textContent = status.source === 'working' ? '学習中…'
      : status.saved_valid ? `${KIND[status.saved_kind] || '—'}学習済み（${status.saved_count}回）` : '初期モデル';
    updateButtons();
  }

  function updateButtons() {
    const can = Boolean(saved && saved.saved_valid && saved.saved_kind === mode);
    document.querySelectorAll('.mode-card').forEach(card => {
      card.setAttribute('aria-checked', String(card.dataset.mode === mode));
      card.disabled = running;
    });
    $('more').disabled = running || !can;
    $('redo').disabled = running;
    $('reset').disabled = running || !(saved && saved.saved_valid);
    $('cancel').hidden = !running;
    $('hint').textContent = running || !saved ? ''
      : can ? `今の${KIND[mode]}学習の続きから、さらに${ROUNDS}周を学習します。`
      : saved.saved_valid
        ? `追加学習は同じ方式（${KIND[saved.saved_kind]}）のときだけできます。${KIND[mode]}を学習するときは「取り直し」を選んでください。`
        : `まだ学習していません。「新たに${ROUNDS}周を取り直す」で始めます。`;
    window.exhibit.lockNav(running);
  }

  async function call(path, body) {
    const response = await fetch(path, body === undefined ? { cache: 'no-store' } : {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const text = await response.text(), data = text ? JSON.parse(text) : {};
    if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
    return data;
  }

  let audio = null;
  function chime(good) {
    try {
      audio = audio || new AudioContext();
      const now = audio.currentTime, osc = audio.createOscillator(), gain = audio.createGain();
      osc.frequency.value = good ? 880 : 330;
      gain.gain.setValueAtTime(.18, now);
      gain.gain.exponentialRampToValueAtTime(.001, now + .25);
      osc.connect(gain).connect(audio.destination);
      osc.start(now); osc.stop(now + .26);
    } catch (_) { /* sound is optional */ }
  }

  async function waitResult(before) {
    for (let waited = 0; waited < 180000; waited += 150) {
      if (cancelled) throw new Error('中止しました');
      const state = await call('/api/calibration/state');
      if (state.error) throw new Error(state.error);
      if (state.result_count > before) return state.result;
      await sleep(150);
    }
    throw new Error('打撃が検出されませんでした（タイムアウト）');
  }

  async function enter() {
    const current = await call('/api/status');
    if (!current.connected) throw new Error('センサ基板に接続できていません。USBケーブルを確認してください。');
    if (current.inference_active) await call('/api/inference/stop', {});
    return call('/api/calibration/enter', {});
  }

  async function leave() {
    try { await call('/api/device/mode', { mode: 'device_position' }); } catch (_) { /* the play page sets it again */ }
  }

  async function train(keepSaved) {
    if (running) return;
    cancelled = false; running = true; updateButtons();
    $('error').textContent = '';
    drawBoard();
    drawRounds();
    const targets = [...pointNodes.keys()];
    const total = targets.length * ROUNDS;
    let began = false, done = 0, matched = 0;
    try {
      showSaved(await enter());
      showSaved(await call('/api/calibration/begin', { kind: mode, continue: keepSaved }));
      began = true; running = true; updateButtons();
      for (let round = 0; round < ROUNDS; round++) {
        resetPoints();
        for (const target of targets) {
          const position = mode === 'quick' ? centres[target] : target;
          setCurrent(target, position);
          setMessage(`ここを叩いてください  X ${support[position][0]} / Y ${support[position][1]} mm`);
          $('counter').textContent = `${round + 1} / ${ROUNDS} 周目　${target + 1} / ${targets.length} 回目`;
          const state = await call('/api/calibration/state');
          await call('/api/calibration/arm', { target, kind: mode });
          const result = await waitResult(state.result_count);
          if (!result.trained) throw new Error('ファームウェアが打撃を学習できませんでした。');
          done++;
          const predicted = result.predicted_class;
          const hit = mode === 'quick' ? areaOf(predicted) === areaOf(position) : predicted === position;
          if (hit) matched++;
          roundStats[round].total++;
          if (hit) roundStats[round].hits++;
          showRound(round, false);
          pointNodes[target].setAttribute('class', 'pt done');
          showResult(position, predicted);
          chime(hit);
          setMessage(hit ? '学習しました ✓' : `学習しました（判定: エリア${areaOf(predicted) + 1}）`, hit ? 'hit-ok' : 'hit-miss');
          $('bar').style.width = `${100 * done / total}%`;
          await sleep(450);
        }
        showRound(round, true);
      }
      const status = await call('/api/calibration/commit', {});
      began = false; showSaved(status);
      setMessage(`学習完了 — 保存しました（合計${status.saved_count}回）`, 'hit-ok');
      $('counter').textContent = `全${ROUNDS}周の正解率 ${Math.round(100 * matched / total)}%（叩いた時点の判定）`;
    } catch (error) {
      $('error').textContent = error.message;
      if (began) { try { showSaved(await call('/api/calibration/discard', {})); } catch (_) { /* ignore */ } }
      setMessage('学習を終了しました（保存していません）');
    } finally {
      running = false; updateButtons();
      await leave();
    }
  }

  let resetTimer = null;
  function disarm() {
    clearTimeout(resetTimer); resetTimer = null;
    $('reset').textContent = '初期モデルに戻す';
    $('reset').classList.remove('armed');
  }
  async function factoryReset() {
    if (running) return;
    if (!resetTimer) {
      $('reset').textContent = 'もう一度押すと初期モデルに戻ります';
      $('reset').classList.add('armed');
      resetTimer = setTimeout(disarm, 4000);
      return;
    }
    disarm();
    running = true; updateButtons();
    try {
      await enter();
      showSaved(await call('/api/calibration/factory_reset', {}));
      setMessage('初期モデルに戻しました');
      drawBoard();
    } catch (error) { $('error').textContent = error.message; }
    finally { running = false; updateButtons(); await leave(); }
  }

  async function refresh() {
    try {
      const status = await call('/api/status');
      if (!status.connected) { $('modelState').textContent = 'センサ未接続'; return false; }
      if (status.inference_active) await call('/api/inference/stop', {});
      let state = await call('/api/calibration/enter', {});
      if (state.source === 'working') state = await call('/api/calibration/discard', {});   // left over from an interrupted run
      showSaved(state);
      await leave();
      return true;
    } catch (error) { $('error').textContent = error.message; return false; }
  }

  document.querySelectorAll('.mode-card').forEach(card => {
    card.onclick = () => {
      if (running) return;
      mode = card.dataset.mode; drawBoard(); drawRounds(); updateButtons();
      setMessage(`${KIND[mode]}学習：${mode === 'quick' ? '12エリアの中心' : '60地点'}を叩きます`);
      $('counter').textContent = ''; $('bar').style.width = '0';
    };
  });
  $('more').onclick = () => train(true);
  $('redo').onclick = () => train(false);
  $('cancel').onclick = () => { cancelled = true; };
  $('reset').onclick = factoryReset;

  drawBoard(); drawRounds(); updateButtons();
  setMessage('学習方式を選んで、学習を始めてください');
  (async function waitBoard() { while (!(await refresh())) await sleep(2000); })();
})();
