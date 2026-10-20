/* Plays the notes the embedded instrument page asks for.  The deck window receives the presenter's key
   presses and clicks, so its AudioContext is allowed to start; the embedded frame's would not be. */
(function () {
  let context = null, master = null;
  const buffers = new Map(), loads = new Map();

  function ensure() {
    if (!context) {
      context = new (window.AudioContext || window.webkitAudioContext)({latencyHint: 0});
      master = context.createGain();
      const shaper = context.createWaveShaper(), curve = new Float32Array(2049);
      for (let i = 0; i < curve.length; i++) { const x = i / 1024 - 1; curve[i] = Math.tanh(x * 1.2) / Math.tanh(1.2); }
      shaper.curve = curve;
      master.connect(shaper).connect(context.destination);
    }
    if (context.state !== 'running') context.resume().catch(() => {});
    return context;
  }

  function load(url) {
    if (buffers.has(url)) return Promise.resolve(buffers.get(url));
    if (!loads.has(url)) {
      loads.set(url, fetch(url, {cache: 'force-cache'})
        .then(response => { if (!response.ok) throw new Error(`${response.status}`); return response.arrayBuffer(); })
        .then(bytes => ensure().decodeAudioData(bytes))
        .then(buffer => { buffers.set(url, buffer); loads.delete(url); return buffer; })
        .catch(error => { loads.delete(url); throw error; }));
    }
    return loads.get(url);
  }

  function start(buffer, velocity, volume) {
    const ctx = ensure(), source = ctx.createBufferSource(), gain = ctx.createGain();
    master.gain.value = volume;
    source.buffer = buffer;
    gain.gain.value = Math.min(.95, Math.max(.08, velocity));
    source.connect(gain).connect(master);
    source.start();
  }

  window.DeckAudio = {
    ensure,
    prime: urls => Promise.all(urls.map(url => load(url).catch(() => null))),
    note(url, velocity, volume) {
      const buffer = buffers.get(url);
      if (buffer) { start(buffer, velocity, volume); return; }
      load(url).then(loaded => start(loaded, velocity, volume)).catch(() => {});
    },
    get state() { return context ? context.state : 'none'; },
  };
  for (const type of ['keydown', 'pointerdown', 'click']) document.addEventListener(type, () => ensure(), true);
})();
