/* Shares the deck state (slide index, timer, demo source) between the main window and the presenter window.
   BroadcastChannel is the primary path; the storage event covers browsers where it is unavailable. */
(function () {
  const KEY = 'solistpan-deck-state-v4';
  const DEFAULTS = {
    rev: 0,
    index: 0,
    slideStart: 0,
    stepIndex: 0,
    stepStart: 0,
    blackout: false,
    demoMode: 'live',
    demoUrl: 'http://127.0.0.1:8765/instrument-probability.html?embed=1',
    timer: {running: false, elapsed: 0, startedAt: 0},
    totalSeconds: 300,
    autoDemo: true,
    demoCommand: {id: 0, action: '', value: ''},
    demoState: {},
  };

  function load() {
    try { return {...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || '{}')}; }
    catch (_) { return {...DEFAULTS}; }
  }

  window.DeckSync = function DeckSync(onChange) {
    let state = load();
    const channel = 'BroadcastChannel' in window ? new BroadcastChannel('solistpan-deck') : null;

    function adopt(next) {
      if (!next || typeof next.rev !== 'number' || next.rev <= state.rev) return;
      state = {...DEFAULTS, ...next};
      onChange(state);
    }
    function persist() {
      try { localStorage.setItem(KEY, JSON.stringify(state)); } catch (_) { /* private mode */ }
    }

    if (channel) channel.onmessage = event => adopt(event.data);
    window.addEventListener('storage', event => {
      if (event.key === KEY && event.newValue) {
        try { adopt(JSON.parse(event.newValue)); } catch (_) { /* ignore */ }
      }
    });

    return {
      get state() { return state; },
      set(patch) {
        state = {...state, ...patch, rev: state.rev + 1};
        persist();
        if (channel) channel.postMessage(state);
        onChange(state);
      },
      elapsed() {
        const timer = state.timer;
        return timer.elapsed + (timer.running ? (Date.now() - timer.startedAt) / 1000 : 0);
      },
      startTimer() {
        if (!state.timer.running) this.set({timer: {...state.timer, running: true, startedAt: Date.now()}});
      },
      pauseTimer() {
        if (state.timer.running) this.set({timer: {running: false, elapsed: this.elapsed(), startedAt: 0}});
      },
      resetTimer() { this.set({timer: {running: false, elapsed: 0, startedAt: 0}}); },
      /* Move to a slide; its own clock (and the demo step clock) start from the current talk time. */
      goTo(index) {
        const now = this.elapsed();
        this.set({index, blackout: false, slideStart: now, stepIndex: 0, stepStart: now});
      },
      /* Ask the live demo (in the main window) to do something; the main window runs it once per id. */
      demoCommand(action, value = '') {
        this.set({demoCommand: {id: (state.demoCommand?.id || 0) + 1, action, value}});
      },
      step(stepIndex) {
        this.set({stepIndex, stepStart: this.elapsed()});
      },
      resetAll() {
        this.set({index: 0, blackout: false, slideStart: 0, stepIndex: 0, stepStart: 0,
                  timer: {running: false, elapsed: 0, startedAt: 0}});
      },
    };
  };
})();
