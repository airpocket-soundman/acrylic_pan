/* Guided song for the class instrument page: the next area lights up and an example can be played.
   The songs use the notes of the area mapping, so each song names the mapping profile it needs. */
(function () {
  const el = id => document.getElementById(id);
  // beats: duration of every note in quarter notes.
  // Every note lasts `slots` sixteenth notes (until the next note starts, rests included).  The tune is the
  // opening of the overground theme: the four-note intro bar, then the main phrase twice.
  const INTRO = [['E5', 1], ['E5', 2], ['E5', 2], ['C5', 1], ['E5', 2], ['G5', 4], ['G4', 4]];
  const PHRASE_A = [['C5', 3], ['G4', 3], ['E4', 3], ['A4', 2], ['B4', 2], ['A#4', 1], ['A4', 2]];
  const PHRASE_B = [['G4', 1], ['E5', 1], ['G5', 1], ['A5', 2], ['F5', 1], ['G5', 2], ['E5', 2], ['C5', 1], ['D5', 1], ['B4', 4]];
  const MARIO = [...INTRO, ...PHRASE_A, ...PHRASE_B, ...PHRASE_A, ...PHRASE_B];
  const SONGS = {
    mario: {
      name: 'スーパーマリオ(地上BGMの前半)', bpm: 150,
      // The tune needs D5 and F5, which the plain "mario" mapping lacks, so the song brings its own mapping.
      profile: {id: 'mapping-mario-song', name: 'マリオ(教示用)',
                notes: ['E4', 'G4', 'A4', 'A#4', 'B4', 'C5', 'D5', 'E5', 'F5', 'G5', 'A5', 'C6']},
      notes: MARIO.map(([note]) => note),
      beats: MARIO.map(([, slots]) => slots / 4),
    },
  };
  let songId = '', step = 0, guideTimer = null, guideToken = 0;

  const song = () => SONGS[songId] || null;
  const areaOf = note => settings.notes.indexOf(note);
  const grid = () => document.querySelectorAll('#hitGrid [data-class]');

  function highlight() {
    const current = song(), first = current ? areaOf(current.notes[step]) : -1;
    const second = current && step + 1 < current.notes.length ? areaOf(current.notes[step + 1]) : -1;
    grid().forEach(cell => {
      const area = Number(cell.dataset.class);
      cell.classList.toggle('next-note', area === first);
      cell.classList.toggle('second-next-note', area === second && area !== first);
    });
  }

  function render() {
    const current = song(), text = el('songProgress');
    if (!current) text.textContent = '自由演奏';
    else if (step >= current.notes.length) text.textContent = `${current.name}: 完奏`;
    else {
      const note = current.notes[step], area = areaOf(note);
      text.textContent = area < 0
        ? `次 ${note}(いまの割り当てに無い音です)  ${step + 1}/${current.notes.length}`
        : `次 ${note}・エリア${area + 1}  ${step + 1}/${current.notes.length}`;
    }
    el('guidePlay').disabled = !current;
    el('guidePlay').textContent = guideTimer ? '手本停止' : '手本演奏';
    highlight();
  }

  function stopGuide() {
    guideToken++;
    if (guideTimer) clearTimeout(guideTimer);
    guideTimer = null;
    render();
  }

  function choose(id) {
    stopGuide();
    songId = SONGS[id] ? id : '';
    step = 0;
    const current = song();
    if (current) {
      let profile = settings.mappingProfiles.find(item => item.id === current.profile.id);
      if (!profile) {
        profile = {id: current.profile.id, name: current.profile.name, notes: [...current.profile.notes]};
        settings.mappingProfiles.push(profile);
      }
      if (settings.activeMappingProfileId !== profile.id) {
        syncActiveProfile();
        settings.activeMappingProfileId = profile.id;
      }
      settings.notes = [...current.profile.notes];
      syncActiveProfile();
      renderSettings();
      saveSettings();
    }
    render();
  }

  async function guide() {
    if (guideTimer) { stopGuide(); return; }
    const current = song();
    if (!current) return;
    try { await ensureAudio(); } catch (error) { el('error').textContent = error.message; return; }
    step = 0;
    const token = ++guideToken;
    const next = () => {
      if (token !== guideToken) return;
      if (step >= current.notes.length) { guideTimer = null; render(); return; }
      const note = current.notes[step], area = areaOf(note);
      audio.play(note, 0.85, area < 0 ? 0 : area);
      if (area >= 0) displayArea(area);
      const wait = current.beats[step] * 60000 / current.bpm;
      step++;
      render();
      guideTimer = setTimeout(next, wait);
    };
    guideTimer = setTimeout(next, 0);
    render();
  }

  // Any strike that sounds moves to the next note, right or wrong.
  const originalPlayArea = playArea;
  playArea = function (area, confidence, isPreview) {
    originalPlayArea(area, confidence, isPreview);
    if (isPreview || !song()) return;
    if (guideTimer) stopGuide();          // playing along ends the example
    if (step < song().notes.length) { step++; render(); }
  };

  el('songSelect').onchange = () => { choose(el('songSelect').value); try { localStorage.setItem('acrylicPanClassSong', songId); } catch (_) { /* ignore */ } };
  el('songReset').onclick = () => { stopGuide(); step = 0; render(); };
  el('guidePlay').onclick = guide;
  let saved = '';
  try { saved = localStorage.getItem('acrylicPanClassSong') || ''; } catch (_) { /* ignore */ }
  if (SONGS[saved]) el('songSelect').value = saved;
  choose(el('songSelect').value);
  /* Select a song (and its note mapping) and return its notes, for the recognition test of the ODL screen. */
  function prepare(id) {
    el('songSelect').value = id;
    choose(id);
    return song() ? {id: songId, name: song().name, notes: [...song().notes]} : null;
  }
  window.classSong = {prepare, choose, guide, reset: () => { stopGuide(); step = 0; render(); }, get step() { return step; }};
})();
