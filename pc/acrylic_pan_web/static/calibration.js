/* ODL on-site calibration of the 12-area model (firmware mode 4).
   Absorbs differences of mallets and mounting: check, learn N rounds, check, keep if not worse. */
const calibrationUi = (() => {
  const AREAS = 12, COLUMNS = 4;
  const el = id => document.getElementById(id);
  let running = false, cancelRequested = false;

  const areaLabel = area => `エリア${area + 1}(${Math.floor(area / COLUMNS) + 1}行${area % COLUMNS + 1}列)`;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const sourceLabel = {factory: '工場モデル', saved: '校正済みモデル(保存済み)', working: '校正中モデル'};

  function setMessage(text) { el('calibrationMessage').textContent = text; }
  function setProgress(text) { el('calibrationProgress').textContent = text; }
  function markTarget(area) {
    document.querySelectorAll('#hitGrid [data-class]').forEach(cell =>
      cell.classList.toggle('calibration-target', Number(cell.dataset.class) === area));
  }
  function showModel(status) {
    if (!status) { el('calibrationModel').textContent = '使用中のモデル: 未確認'; return; }
    const saved = status.saved_valid ? `、保存済み ${status.saved_count}打` : '';
    el('calibrationModel').textContent = `使用中のモデル: ${sourceLabel[status.source] || status.source}${saved}`;
  }
  function setBusy(busy) {
    running = busy;
    for (const id of ['calibrationStart', 'calibrationFactory', 'calibrationStatusButton', 'calibrationTest', 'calibrationSong'])
      el(id).disabled = busy;
    el('calibrationCancel').disabled = !busy;
  }

  async function waitResult(previousCount) {
    for (let waited = 0; waited < 120000; waited += 150) {
      if (cancelRequested) throw new Error('中止しました');
      const state = await api('/api/calibration/state');
      if (state.error) throw new Error(state.error);
      if (state.result_count > previousCount) return state.result;
      await sleep(150);
    }
    throw new Error('打撃が検出されませんでした(タイムアウト)');
  }

  async function hit(target) {
    const before = (await api('/api/calibration/state')).result_count;
    await api('/api/calibration/arm', {target});
    const result = await waitResult(before);
    // Let the player hear what was recognised.
    try { playArea(result.predicted_class, Math.max(...result.outputs.map(Number)), true); } catch (_) { /* sound is optional */ }
    return result;
  }

  async function checkPass(phase, tally = null) {
    let correct = 0;
    for (let area = 0; area < AREAS; area++) {
      markTarget(area);
      setProgress(`[${phase}] ${areaLabel(area)} を1回叩いてください`);
      const result = await hit(null);
      const ok = result.predicted_class === area;
      correct += ok ? 1 : 0;
      if (tally) { tally.total[area]++; tally.hit[area] += ok ? 1 : 0; if (!ok) tally.confused.push(`${area + 1}→${result.predicted_class + 1}`); }
      setMessage(`${areaLabel(area)}: ${ok ? '○ 正解' : `× → ${areaLabel(result.predicted_class)} と推定`}`);
    }
    return correct;
  }

  async function prepare() {
    const current = await api('/api/status');
    if (!current.connected) throw new Error('先にCOMポートへ接続してください。');
    if ((current.panel?.class_count ?? 0) !== AREAS)
      throw new Error('ODLキャリブレーションは12エリアの板仕様専用です。板仕様を400 × 300 mmにしてください。');
    if (current.inference_active) await stopPerformance();
    return api('/api/calibration/enter', {});
  }

  async function leave() {
    markTarget(-1);
    try { await api('/api/device/mode', {mode: 'instrument'}); } catch (_) { /* leave the board as is */ }
    if (typeof refreshStatus === 'function') await refreshStatus();
  }

  async function run() {
    if (running) return;
    const rounds = Math.max(1, Math.min(10, Number(el('calibrationRounds').value) || 5));
    cancelRequested = false; setBusy(true); el('error').textContent = '';
    let began = false;
    try {
      showModel(await prepare());
      try { await ensureAudio(); } catch (_) { /* no sound */ }
      const before = await checkPass('校正前の確認');
      await api('/api/calibration/begin', {}); began = true;
      let trained = 0;
      for (let round = 0; round < rounds; round++) {
        for (let area = 0; area < AREAS; area++) {
          markTarget(area);
          setProgress(`[学習 ${round + 1}/${rounds}] ${areaLabel(area)} を叩いてください`);
          const result = await hit(area);
          if (!result.trained) throw new Error('ファームが打撃を学習できませんでした。');
          trained++;
          setMessage(`学習済み ${result.work_count}打(更新 ${result.train_ms.toFixed(0)} ms)`);
        }
      }
      const after = await checkPass('校正後の確認');
      markTarget(-1);
      const summary = `確認打の正解: 校正前 ${before}/${AREAS}、校正後 ${after}/${AREAS}`;
      if (after >= before) {
        const status = await api('/api/calibration/commit', {});
        began = false;
        showModel(status);
        setProgress(`${summary}。校正を保存しました(${status.saved_count}打分、再起動後も有効)。`);
      } else {
        const status = await api('/api/calibration/discard', {});
        began = false;
        showModel(status);
        setProgress(`${summary}。精度が下がったため破棄し、元のモデルのままです。`);
      }
      setMessage(`学習 ${trained}打`);
    } catch (error) {
      el('error').textContent = error.message;
      if (began) { try { showModel(await api('/api/calibration/discard', {})); } catch (_) { /* ignore */ } }
      setProgress('キャリブレーションを終了しました(変更は保存されていません)。');
    } finally {
      setBusy(false);
      await leave();
    }
  }

  /* Recognition test: a few passes over all areas with the model that is in use. Nothing is learned or saved. */
  async function recognitionTest() {
    if (running) return;
    const rounds = Math.max(1, Math.min(10, Number(el('calibrationRounds').value) || 3));
    cancelRequested = false; setBusy(true); el('error').textContent = '';
    const tally = {total: Array(AREAS).fill(0), hit: Array(AREAS).fill(0), confused: []};
    let correct = 0;
    try {
      showModel(await prepare());
      try { await ensureAudio(); } catch (_) { /* no sound */ }
      for (let round = 0; round < rounds; round++) {
        correct += await checkPass(`認識テスト ${round + 1}/${rounds}`, tally);
      }
      markTarget(-1);
      const total = rounds * AREAS;
      const perArea = tally.hit.map((h, i) => `${i + 1}:${h}/${tally.total[i]}`).join('  ');
      const counts = {};
      tally.confused.forEach(key => { counts[key] = (counts[key] || 0) + 1; });
      const worst = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 5).map(([k, v]) => `${k}(${v}回)`).join('、');
      setProgress(`認識テスト: 正解 ${correct}/${total}(${(100 * correct / total).toFixed(1)}%)`);
      setMessage(`エリア別の正解数 ${perArea}${worst ? ` / 間違いの多い組(叩いた→認識) ${worst}` : ''}`);
    } catch (error) {
      el('error').textContent = error.message;
      setProgress('認識テストを終了しました。');
    } finally {
      setBusy(false);
      await leave();
    }
  }

  /* Guided song test: the next panel lights up, every strike is recognised by the model in use (nothing is
     learned) and the recognised area sounds.  The song moves on after each strike, right or wrong. */
  function lightUp(first, second) {
    document.querySelectorAll('#hitGrid [data-class]').forEach(cell => {
      const area = Number(cell.dataset.class);
      cell.classList.toggle('next-note', area === first);
      cell.classList.toggle('second-next-note', area === second && area !== first);
    });
  }

  async function songTest() {
    if (running) return;
    const song = window.classSong ? window.classSong.prepare('mario') : null;
    if (!song) { el('error').textContent = '教示演奏の曲を読み込めませんでした。'; return; }
    cancelRequested = false; setBusy(true); el('error').textContent = '';
    const area = note => settings.notes.indexOf(note);
    const wrong = [];
    let correct = 0;
    try {
      showModel(await prepare());
      try { await ensureAudio(); } catch (_) { /* no sound */ }
      for (let i = 0; i < song.notes.length; i++) {
        const target = area(song.notes[i]);
        lightUp(target, i + 1 < song.notes.length ? area(song.notes[i + 1]) : -1);
        setProgress(`[教示演奏 ${i + 1}/${song.notes.length}] ${song.notes[i]} → エリア${target + 1} を叩いてください`);
        const result = await hit(null);
        const ok = result.predicted_class === target;
        if (ok) correct++; else wrong.push(`${song.notes[i]}(エリア${target + 1}→${result.predicted_class + 1})`);
        setMessage(`${song.notes[i]}: ${ok ? '○ 正解' : `× エリア${result.predicted_class + 1} と認識`}  (${correct}/${i + 1})`);
      }
      setProgress(`教示演奏テスト(${song.name}): 正解 ${correct}/${song.notes.length}(${(100 * correct / song.notes.length).toFixed(1)}%)`);
      setMessage(wrong.length ? `認識を間違えた音: ${wrong.join('、')}` : 'すべての音を正しく認識しました。');
    } catch (error) {
      el('error').textContent = error.message;
      setProgress('教示演奏テストを終了しました。');
    } finally {
      lightUp(-1, -1);
      setBusy(false);
      await leave();
    }
  }

  async function factoryReset() {
    if (running) return;
    if (!window.confirm('保存済みの校正を無効にして工場モデルへ戻します。再び使うには、もう一度キャリブレーションが必要です。よろしいですか？')) return;
    setBusy(true); el('error').textContent = '';
    try {
      await prepare();
      showModel(await api('/api/calibration/factory_reset', {}));
      setProgress('工場モデルへ戻しました。');
    } catch (error) { el('error').textContent = error.message; }
    finally { setBusy(false); await leave(); }
  }

  async function status() {
    if (running) return;
    setBusy(true); el('error').textContent = '';
    try { showModel(await prepare()); setProgress(''); }
    catch (error) { el('error').textContent = error.message; }
    finally { setBusy(false); await leave(); }
  }

  el('calibrationStart').onclick = run;
  el('calibrationFactory').onclick = factoryReset;
  el('calibrationTest').onclick = recognitionTest;
  el('calibrationSong').onclick = songTest;
  el('calibrationStatusButton').onclick = status;
  el('calibrationCancel').onclick = () => { cancelRequested = true; };
  return {run, status};
})();
