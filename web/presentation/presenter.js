/* Presenter window: current / next slide, speaker notes, timer and slide list. Drives the main window. */
(function () {
  const {SLIDES} = window.DECK;
  const $ = id => document.getElementById(id);
  const sync = window.DeckSync(render);
  let shown = -1;

  // Planned start time (seconds) of every slide, from the per-slide targets.
  const plannedStart = [];
  SLIDES.reduce((sum, slide, i) => { plannedStart[i] = sum; return sum + slide.seconds; }, 0);
  const plannedTotal = window.DECK.totalSeconds;

  function mountThumb(el, slide) {
    window.Deck.mount(el, slide, {preview: true});
  }

  function renderList(index) {
    const list = $('list');
    if (!list.children.length) {
      SLIDES.forEach((slide, i) => {
        const li = document.createElement('li');
        li.innerHTML = `<span>${i + 1}</span><span>${window.DECK.esc(slide.title)}</span><span>${window.Deck.format(slide.seconds)}</span>`;
        li.onclick = () => sync.goTo(i);
        list.append(li);
      });
    }
    [...list.children].forEach((li, i) => {
      li.classList.toggle('cur', i === index);
      li.classList.toggle('done', i < index);
    });
    list.children[index]?.scrollIntoView({block: 'nearest'});
  }

  function renderScript(slide, state) {
    const esc = window.DECK.esc;
    const lead = slide.notes
      .map(text => `<p class="${text.startsWith('(') ? 'direction' : 'lead'}">${esc(text)}</p>`).join('');
    if (!slide.steps) {
      $('notes').innerHTML = slide.notes
        .map(text => `<p class="${text.startsWith('(') ? 'direction' : ''}">${esc(text)}</p>`).join('');
      return;
    }
    $('notes').innerHTML = lead + '<div class="steps-list">' + slide.steps.map((step, i) =>
      `<div class="step" data-step="${i}"><div class="step-h"><span>${i + 1}. ${esc(step.title)}</span><em>${window.Deck.format(step.sec)}</em></div>` +
      `<div class="act">${esc(step.action)}</div><div class="say">${esc(step.say)}</div></div>`).join('') + '</div>';
    $('notes').querySelectorAll('.step').forEach(el => { el.onclick = () => sync.step(Number(el.dataset.step)); });
  }

  function markStep(slide, state) {
    const steps = slide.steps;
    $('stepControls').hidden = !steps;
    $('stepClock').hidden = !steps;
    if (!steps) return;
    const current = Math.max(0, Math.min(steps.length - 1, state.stepIndex));
    document.querySelectorAll('#notes .step').forEach(el => {
      const i = Number(el.dataset.step);
      el.classList.toggle('cur', i === current);
      el.classList.toggle('done', i < current);
    });
    document.querySelector('#notes .step.cur')?.scrollIntoView({block: 'nearest'});
    $('stepName').textContent = steps[current].title;
  }

  function fillRemote() {
    $('rInstrument').replaceChildren(...window.DECK.DEMO_INSTRUMENTS.map(([id, name]) => new Option(name, id)));
    $('rSong').replaceChildren(...window.DECK.DEMO_SONGS.map(([id, name]) => new Option(name, id)));
  }

  function renderRemote(state) {
    const demo = state.demoState || {};
    if (document.activeElement !== $('rInstrument') && demo.instrument) $('rInstrument').value = demo.instrument;
    if (document.activeElement !== $('rSong') && demo.song !== undefined) $('rSong').value = demo.song || '';
    $('rAuto').checked = state.autoDemo;
    const status = $('rStatus');
    if (state.demoMode === 'video') { status.textContent = 'デモはPV動画です。'; status.className = 'remote-status'; return; }
    if (demo.error) { status.textContent = `エラー: ${demo.error}`; status.className = 'remote-status err'; return; }
    if (demo.connected === undefined) { status.textContent = 'デモ画面の応答を待っています(メイン画面でデモのスライドを開く)'; status.className = 'remote-status'; return; }
    status.textContent = `${demo.connected ? '接続中' : '未接続'} / ${demo.performing ? '演奏中' : '停止中'}${demo.guide ? ' / 手本演奏中' : ''}`;
    status.className = `remote-status ${demo.performing ? 'ok' : ''}`;
  }

  // The step that is entered runs its own action (e.g. choosing a song) when automatic mode is on.
  let ranStep = '';
  function runStepActions(state, slide) {
    if (!slide.steps || !state.autoDemo) { ranStep = ''; return; }
    const index = Math.max(0, Math.min(slide.steps.length - 1, state.stepIndex));
    const key = `${state.index}:${index}:${state.stepStart}`;
    if (key === ranStep) return;
    ranStep = key;
    (slide.steps[index].run || []).filter(action => action !== 'start').forEach(action => sync.demoCommand(action));
  }

  function render(state) {
    const index = Math.max(0, Math.min(SLIDES.length - 1, state.index));
    const slide = SLIDES[index];
    if (index !== shown) {
      shown = index;
      mountThumb($('current'), slide);
      const next = SLIDES[index + 1];
      if (next) mountThumb($('next-thumb'), next);
      else $('next-thumb').innerHTML = '<div class="demo-ph" style="color:#9bb0c0;font-size:20px">最後のスライドです</div>';
      $('curTitle').textContent = `${index + 1} / ${SLIDES.length}  ${slide.title}`;
      $('nextTitle').textContent = next ? `${index + 2}  ${next.title}` : '—';
      $('slideTime').textContent = `目安 ${window.Deck.format(slide.seconds)}(開始 ${window.Deck.format(plannedStart[index])})`;
      renderScript(slide, state);
      renderList(index);
    }
    markStep(slide, state);
    renderRemote(state);
    runStepActions(state, slide);
    $('black').classList.toggle('on', state.blackout);
    document.querySelectorAll('#demoMode button').forEach(b => b.classList.toggle('on', b.dataset.mode === state.demoMode));
    if (document.activeElement !== $('demoUrl')) $('demoUrl').value = state.demoUrl;
    if (document.activeElement !== $('total')) $('total').value = state.totalSeconds;
    tick();
  }

  function tick() {
    const state = sync.state;
    const elapsed = sync.elapsed();
    const total = state.totalSeconds;
    const remaining = total - elapsed;
    $('clock').textContent = window.Deck.format(elapsed);
    $('remain').textContent = remaining >= 0 ? `残り ${window.Deck.format(remaining)}` : `超過 ${window.Deck.format(-remaining)}`;
    $('remain').classList.toggle('over', remaining < 0);
    // Compare with where the plan says we should be when this slide starts (scaled to the chosen total time).
    const scale = total / plannedTotal;
    const target = plannedStart[Math.min(state.index, SLIDES.length - 1)] * scale;
    const diff = elapsed - target;
    const pace = $('pace');
    if (elapsed < 1 && !state.timer.running) { pace.textContent = ''; pace.className = 'pace'; }
    else if (Math.abs(diff) <= 10) { pace.textContent = 'ペース: 予定どおり'; pace.className = 'pace ok'; }
    else if (diff > 0) { pace.textContent = `ペース: ${Math.round(diff)} 秒 遅れ`; pace.className = 'pace late'; }
    else { pace.textContent = `ペース: ${Math.round(-diff)} 秒 早め`; pace.className = 'pace early'; }
    const total_scale = scale;
    const slide = SLIDES[Math.min(state.index, SLIDES.length - 1)];
    const slideTarget = slide.seconds * total_scale;
    const slideLeft = slideTarget - (elapsed - state.slideStart);
    const sr = $('slideRemain');
    sr.textContent = window.Deck.format(slideLeft);
    sr.classList.toggle('over', slideLeft < 0);
    $('slideTarget').textContent = `目安 ${window.Deck.format(slideTarget)}`;
    const fill = $('slideFill');
    fill.style.width = `${Math.max(0, Math.min(100, (elapsed - state.slideStart) / slideTarget * 100))}%`;
    fill.classList.toggle('over', slideLeft < 0);
    if (slide.steps) {
      const step = slide.steps[Math.max(0, Math.min(slide.steps.length - 1, state.stepIndex))];
      const stepLeft = step.sec * total_scale - (elapsed - state.stepStart);
      $('stepRemain').textContent = window.Deck.format(stepLeft);
      $('stepRemain').classList.toggle('over', stepLeft < 0);
      const cur = document.querySelector('#notes .step.cur .step-h em');
      if (cur) cur.textContent = window.Deck.format(stepLeft);
    }
    $('timeFill').style.width = `${Math.min(100, elapsed / total * 100)}%`;
    $('timeMark').style.left = `${Math.min(100, target / total * 100)}%`;
  }

  function go(delta) {
    const slide = SLIDES[sync.state.index];
    if (slide.scenes) {
      const step = sync.state.stepIndex;
      if (delta > 0 && step < slide.scenes - 1) { sync.step(step + 1); return; }
      if (delta < 0 && step > 0) { sync.step(step - 1); return; }
    }
    const next = Math.max(0, Math.min(SLIDES.length - 1, sync.state.index + delta));
    if (next === sync.state.index) return;
    if (delta > 0 && sync.state.index === 0 && !sync.state.timer.running && sync.elapsed() === 0) sync.startTimer();
    sync.goTo(next);
  }

  $('prev').onclick = () => go(-1);
  $('next').onclick = () => go(1);
  $('black').onclick = () => sync.set({blackout: !sync.state.blackout});
  $('reset').onclick = () => sync.resetAll();
  function stepBy(delta) {
    const slide = SLIDES[sync.state.index];
    if (!slide.steps) return;
    const next = Math.max(0, Math.min(slide.steps.length - 1, sync.state.stepIndex + delta));
    if (next !== sync.state.stepIndex) sync.step(next);
  }
  $('stepPrev').onclick = () => stepBy(-1);
  $('stepNext').onclick = () => stepBy(1);
  fillRemote();
  $('rStart').onclick = () => sync.demoCommand('start');
  $('rStop').onclick = () => sync.demoCommand('stop');
  $('rGuide').onclick = () => sync.demoCommand('guide');
  $('rInstrument').onchange = () => sync.demoCommand('instrument', $('rInstrument').value);
  $('rSong').onchange = () => sync.demoCommand('song', $('rSong').value);
  $('rVolume').oninput = () => { $('rVolumeValue').value = `${$('rVolume').value}%`; sync.demoCommand('volume', String(Number($('rVolume').value) / 100)); };
  $('rAuto').onchange = () => sync.set({autoDemo: $('rAuto').checked});
  $('tStart').onclick = () => sync.startTimer();
  $('tPause').onclick = () => sync.pauseTimer();
  $('tReset').onclick = () => sync.resetTimer();
  document.querySelectorAll('#demoMode button').forEach(b => b.onclick = () => sync.set({demoMode: b.dataset.mode}));
  $('demoUrl').onchange = () => sync.set({demoUrl: $('demoUrl').value.trim()});
  $('total').onchange = () => sync.set({totalSeconds: Math.max(60, Number($('total').value) || 300)});

  document.addEventListener('keydown', event => {
    if (event.target.closest && event.target.closest('input,textarea')) return;
    switch (event.key) {
      case 'ArrowRight': case 'PageDown': case ' ': case 'Enter': go(1); break;
      case 'ArrowLeft': case 'PageUp': case 'Backspace': go(-1); break;
      case 'n': case 'N': stepBy(1); break;
      case 'p': case 'P': stepBy(-1); break;
      case 'b': case 'B': case '.': sync.set({blackout: !sync.state.blackout}); break;
      case 't': case 'T': sync.state.timer.running ? sync.pauseTimer() : sync.startTimer(); break;
      case 'f': case 'F': (document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()).catch(() => {}); break;
      default: return;
    }
    event.preventDefault();
  });

  // Thumbnails are sized by CSS, so refit whenever their box changes (including the first layout).
  if ('ResizeObserver' in window) {
    const observer = new ResizeObserver(entries => entries.forEach(entry => window.Deck.fit(entry.target)));
    observer.observe($('current'));
    observer.observe($('next-thumb'));
  } else {
    window.addEventListener('resize', () => { window.Deck.fit($('current')); window.Deck.fit($('next-thumb')); });
  }
  setInterval(tick, 250);
  render(sync.state);
})();
