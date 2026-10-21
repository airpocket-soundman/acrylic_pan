/* Main window: shows the current slide full screen. State is shared with the presenter window. */
(function () {
  const {SLIDES} = window.DECK;
  const viewport = document.getElementById('viewport');
  const slideHost = document.getElementById('slideHost');
  const demoHost = document.getElementById('demoHost');
  const toolbar = document.getElementById('toolbar');
  const hint = document.getElementById('hint');
  let shownIndex = -1;
  let demoLayer = null, demoFrame = null, demoVideo = null, loadedUrl = '';
  let presenterWindow = null;
  let doneCommand = 0;
  let frameReady = false;
  let layout = {viewport: {x: 508, y: 246, w: 1387, h: 650}, aspect: 16 / 9};   // camera picture inside the demo page
  let lastFocus = null;
  let lastSpot = null;
  let lastHitKey = '';
  let focusTimer = null;
  let layerSlide = -1;
  let autoStarted = false;   // the first visit to the demo connects and starts; later visits keep the state as it is

  const sync = window.DeckSync(render);
  doneCommand = sync.state.demoCommand?.id || 0;

  /* The demo (live iframe or video) lives outside the slide so it survives navigation. */
  function ensureDemoLayer() {
    if (demoLayer) return;
    demoLayer = document.createElement('div');
    demoLayer.className = 'demo-layer';
    demoFrame = document.createElement('iframe');
    demoFrame.title = '楽器のWebUI';
    demoFrame.dataset.loads = '0';
    demoFrame.addEventListener('load', () => { demoFrame.dataset.loads = String(Number(demoFrame.dataset.loads) + 1); });
    demoFrame.allow = 'autoplay; microphone; camera';
    demoVideo = document.createElement('video');
    demoVideo.src = '../assets/pv/Solistpan_REHC2026_PV.mp4';
    demoVideo.controls = true;
    demoVideo.playsInline = true;
    demoVideo.className = 'hidden';
    demoLayer.append(demoFrame, demoVideo);
    // The frame is always 1920 x 1080 CSS pixels and is scaled like the slide, so the page layout does not depend on the monitor.
    demoLayer.style.left = '0';
    demoLayer.style.top = '0';
    demoLayer.style.width = '1920px';
    demoLayer.style.height = '1080px';
    demoLayer.style.transform = 'scale(var(--s))';
    demoLayer.style.transformOrigin = '0 0';
    demoLayer.style.transition = 'transform 1.3s cubic-bezier(.4,0,.2,1), filter 1s ease';
    demoHost.append(demoLayer);
  }

  function applyDemo(state, onDemoSlide) {
    ensureDemoLayer();
    const live = state.demoMode !== 'video';
    demoFrame.classList.toggle('hidden', !live);
    demoVideo.classList.toggle('hidden', live);
    if (live && loadedUrl !== state.demoUrl) {
      loadedUrl = state.demoUrl;
      frameReady = false;
      demoFrame.src = state.demoUrl;
    }
    if (!live && !onDemoSlide) demoVideo.pause();
    demoLayer.classList.toggle('on', onDemoSlide);
  }

  function fitAll() { window.Deck.fit(viewport); window.Deck.fit(slideHost); applyScene(sync.state, false); }

  /* ---- scenes of the demo slide: the camera picture full screen, a steel pan over it, then the whole demo page ---- */
  const MOVE_MS = 1300;   // duration of the picture's shrink-and-slide

  function sceneOf(state, slide) {
    return slide.scenes ? Math.min(Math.max(0, state.stepIndex), slide.scenes - 1) : -1;
  }

  function currentScale() { return Number(viewport.style.getPropertyValue('--s')) || 1; }

  // Transform that puts the camera picture over the whole stage.
  function zoomTransform() {
    const s = currentScale(), box = layout.viewport;
    let width = box.w, height = box.w / layout.aspect;
    if (height > box.h) { height = box.h; width = box.h * layout.aspect; }
    const centerX = box.x + box.w / 2, centerY = box.y + box.h / 2;
    const k = Math.max(1920 / width, 1080 / height);
    return `translate(${s * (960 - centerX * k)}px, ${s * (540 - centerY * k)}px) scale(${s * k})`;
  }

  function applyScene(state, entering) {
    const slide = SLIDES[Math.max(0, Math.min(SLIDES.length - 1, state.index))];
    if (!demoLayer) return;
    const scene = sceneOf(state, slide);
    const zoomed = Boolean(slide.demo) && scene >= 0 && scene < (slide.zoomSteps || 0);
    const target = zoomed ? zoomTransform() : `scale(${currentScale()})`;
    if (entering) {                                  // arriving on the slide: jump, do not animate
      demoLayer.style.transition = 'none';
      demoLayer.style.transform = target;
      void demoLayer.offsetWidth;
      demoLayer.style.transition = 'transform 1.3s cubic-bezier(.4,0,.2,1), filter 1s ease';
    } else if (demoLayer.style.transform !== target) {
      demoLayer.style.transform = target;
    }
    demoLayer.style.filter = slide.demo && scene === 1 ? 'brightness(.3)' : 'none';   // the camera picture darkens behind the steel pan
    document.getElementById('steelpan').classList.toggle('on', Boolean(slide.demo) && scene === 1);
    document.getElementById('panCaption').classList.toggle('on', Boolean(slide.demo) && scene === 1);
    document.getElementById('panCredit').classList.toggle('on', Boolean(slide.demo) && scene === 1);
    // Slides with a focus per step (e.g. the two themes): the chosen element is raised, the others darken.
    const focusStep = !slide.demo && slide.steps ? slide.steps[Math.max(0, Math.min(slide.steps.length - 1, state.stepIndex))] : null;
    const focus = focusStep && typeof focusStep.focus === 'number' ? focusStep.focus : -1;
    slideHost.classList.toggle('focusing', focus >= 0);
    slideHost.querySelectorAll('[data-focus]').forEach(item => item.classList.toggle('focus-on', Number(item.dataset.focus) === focus));
    // A step with `hit` shakes the elements marked data-hit, as if they had been struck (once per arrival).
    const hitKey = focusStep && focusStep.hit ? `${state.index}:${state.stepIndex}` : '';
    if (hitKey !== lastHitKey) {
      lastHitKey = hitKey;
      slideHost.querySelectorAll('[data-hit]').forEach(item => {
        item.classList.remove('hit');
        if (hitKey) { void item.offsetWidth; item.classList.add('hit'); }
      });
    }
    // The card being explained is raised and the others are dimmed (embedded page).
    const spot = slide.demo && !zoomed && slide.steps ? (slide.steps[Math.max(0, Math.min(slide.steps.length - 1, state.stepIndex))].spot || '') : '';
    if (spot !== lastSpot && (slide.demo || lastSpot)) { lastSpot = spot; sendDemoCommand(state, 'spot', spot); }
    // Only the camera picture is shown while it is enlarged.  When it moves back, the rest of the page waits for the
    // end of that movement and then fades in.
    if (slide.demo) {
      if (zoomed) {
        if (focusTimer) { clearTimeout(focusTimer); focusTimer = null; }
        if (lastFocus !== true) { lastFocus = true; sendDemoCommand(state, 'camfocus', '1'); }
      } else if (lastFocus !== false && !focusTimer) {
        focusTimer = setTimeout(() => {
          focusTimer = null;
          lastFocus = false;
          sendDemoCommand(sync.state, 'camfocus', '0');
        }, entering || lastFocus === null ? 0 : MOVE_MS + 50);
      }
    }
  }

  function demoOrigin(state) {
    try { return new URL(state.demoUrl).origin; } catch (_) { return '*'; }
  }

  function sendDemoCommand(state, action, value, attempts = 20) {
    if (!demoFrame || state.demoMode === 'video') return;
    if (!frameReady && attempts > 0) {
      demoFrame.contentWindow?.postMessage({type: 'apan-demo', action: 'hello'}, demoOrigin(state));
      setTimeout(() => sendDemoCommand(sync.state, action, value, attempts - 1), 300);
      return;
    }
    demoFrame.contentWindow?.postMessage({type: 'apan-demo', action, value}, demoOrigin(state));
  }

  function runDemoCommand(state, command) {
    const [action, value] = command.action.includes(':') ? command.action.split(/:(.*)/s) : [command.action, command.value];
    sendDemoCommand(state, action, value);
  }

  window.addEventListener('message', event => {
    const data = event.data;
    if (!data || !demoFrame || event.source !== demoFrame.contentWindow) return;
    if (data.type === 'apan-note') { window.DeckAudio.note(data.url, Number(data.velocity) || .5, Number(data.volume) || 0); return; }
    if (data.type === 'apan-prime') { window.DeckAudio.prime(data.urls || []); return; }
    if (data.type === 'apan-key') { handleKey(data.key); return; }
    if (data.type === 'apan-layout') { layout = {viewport: data.viewport, aspect: Number(data.aspect) || 16 / 9}; applyScene(sync.state, false); return; }
    if (data.type !== 'apan-demo-state') return;
    if (!frameReady) { lastFocus = null; lastSpot = null; }   // a freshly loaded page needs the focus state again
    frameReady = true;
    const next = {connected: data.connected, performing: data.performing, instrument: data.instrument,
                  song: data.song, guide: data.guide, error: data.error};
    if (JSON.stringify(next) !== JSON.stringify(sync.state.demoState)) sync.set({demoState: next});
  });

  function handleDemoLogic(state, slide, index) {
    const command = state.demoCommand;
    if (command && command.id !== doneCommand) {
      doneCommand = command.id;
      runDemoCommand(state, command);
    }
    // The step that asks for it connects and starts playing by itself (first time only).
    const step = slide.steps ? slide.steps[Math.max(0, Math.min(slide.steps.length - 1, state.stepIndex))] : null;
    if (slide.demo && state.autoDemo && !autoStarted && state.demoMode !== 'video' && step && (step.run || []).includes('start')) {
      autoStarted = true;
      sendDemoCommand(state, 'start', '');
    }
  }

  function render(state) {
    const index = Math.max(0, Math.min(SLIDES.length - 1, state.index));
    const slide = SLIDES[index];
    if (index !== shownIndex) {
      window.Deck.mount(slideHost, slide, {preview: false});
      fitAll();
      shownIndex = index;
      document.title = `${index + 1}/${SLIDES.length} ${slide.title} - Solistpan`;
      document.getElementById('counter').textContent = `${index + 1} / ${SLIDES.length}`;
    }
    const entering = layerSlide !== index;
    layerSlide = index;
    applyDemo(state, Boolean(slide.demo));
    applyScene(state, entering);
    handleDemoLogic(state, slide, index);
    let black = viewport.querySelector('.blackout');
    if (state.blackout && !black) {
      black = document.createElement('div');
      black.className = 'blackout';
      viewport.append(black);
    } else if (!state.blackout && black) {
      black.remove();
    }
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

  async function openPresenter() {
    let features = 'width=1280,height=800';
    try {
      if ('getScreenDetails' in window) {
        const details = await window.getScreenDetails();
        const other = details.screens.find(screen => screen !== details.currentScreen);
        if (other) features = `left=${other.availLeft},top=${other.availTop},width=${other.availWidth},height=${other.availHeight}`;
      }
    } catch (_) { /* permission denied: place the window by hand */ }
    presenterWindow = window.open('presenter.html', 'solistpan-presenter', features);
  }

  function toggleFullscreen() {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen().catch(() => {});
  }

  function handleKey(key) {
    switch (key) {
      case 'ArrowRight': case 'PageDown': case ' ': case 'Enter': go(1); break;
      case 'ArrowLeft': case 'PageUp': case 'Backspace': go(-1); break;
      case 'Home': sync.goTo(0); break;
      case 'End': sync.goTo(SLIDES.length - 1); break;
      case 'f': case 'F': toggleFullscreen(); break;
      case 'b': case 'B': case '.': sync.set({blackout: !sync.state.blackout}); break;
      case 'p': case 'P': openPresenter(); break;
      case 's': case 'S': sync.demoCommand(sync.state.demoState?.performing ? 'stop' : 'start'); break;
      case 'r': case 'R': sync.resetAll(); break;
      default: return false;
    }
    return true;
  }

  document.addEventListener('keydown', event => {
    if (event.target.closest && event.target.closest('input,textarea')) return;
    if (handleKey(event.key)) event.preventDefault();
  });

  document.getElementById('prev').onclick = () => go(-1);
  document.getElementById('next').onclick = () => go(1);
  document.getElementById('presenter').onclick = openPresenter;
  document.getElementById('full').onclick = toggleFullscreen;

  let idle = null;
  function showControls() {
    toolbar.classList.add('show');
    hint.classList.add('show');
    clearTimeout(idle);
    idle = setTimeout(() => { toolbar.classList.remove('show'); hint.classList.remove('show'); }, 2500);
  }
  document.addEventListener('mousemove', showControls);
  showControls();

  // Click on the slide (not on the live demo) advances; right-click goes back.
  viewport.addEventListener('click', event => {
    if (event.target.closest('.demo-layer')) return;
    go(1);
  });
  viewport.addEventListener('contextmenu', event => { event.preventDefault(); go(-1); });

  // Watch the viewport box itself: a window 'resize' event is not reliable when entering full screen.
  if ('ResizeObserver' in window) new ResizeObserver(fitAll).observe(viewport);
  window.addEventListener('resize', fitAll);
  document.addEventListener('fullscreenchange', () => { fitAll(); setTimeout(fitAll, 150); });
  setInterval(fitAll, 500);   // safety net: a missed resize must never leave the stage at an old scale
  render(sync.state);
})();
