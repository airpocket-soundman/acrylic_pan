const $ = id => document.getElementById(id);
let lastSequence = null;
let lastAiSequence = null;
let collectionCompletedSamples = 0;
const library = {sessionId: null, events: [], selected: null};
const POINT_LABELS = {
  center: '中心', up_left: '左上', up_right: '右上', down_left: '左下', down_right: '右下'
};

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.error || response.statusText);
  return data;
}

async function ports() {
  const data = await api('/api/ports');
  $('port').innerHTML = data.ports.map(port => `<option>${port}</option>`).join('');
  if (data.ports.includes('COM3')) $('port').value = 'COM3';
}

function stat(label, value) { return `<div class="stat"><b>${value}</b>${label}</div>`; }

function setButtonState(id, disabled, running = false) {
  const button = $(id);
  if (!button) return;
  button.disabled = disabled;
  button.classList.toggle('is-running', running);
  button.setAttribute('aria-pressed', running ? 'true' : 'false');
}

function updateActionState(data) {
  const connected = Boolean(data.connected);
  const collection = data.collection || {};
  const collecting = Boolean(collection.active);
  const inferring = Boolean(data.inference_active);
  setButtonState('connect', connected, connected);
  setButtonState('disconnect', !connected);
  setButtonState('ping', !connected);
  setButtonState('capture', !connected || collecting || data.device_mode !== 'collection');
  setButtonState('collectionStart', !connected || collecting, collecting);
  setButtonState('collectionStop', !connected || !collecting);
  setButtonState('collectionUndo', !connected || !collecting || collection.completed_samples <= 0);
  setButtonState('inferenceStart', !connected || inferring, inferring);
  setButtonState('inferenceStop', !connected || !inferring);
  if ($('port')) $('port').disabled = connected;
}

async function status() {
  try {
    const data = await api('/api/status');
    window.panelProfileUi?.sync(data);
    ensureAreaGrid(data.panel);
    const stats = data.stats;
    $('connection').textContent = data.connected ? `接続中 ${data.port}` : '未接続';
    $('connection').classList.toggle('online', data.connected);
    if ($('firmwareMode')) {
      const labels = {collection: 'データ採取モード', inference: '推論モード', unknown: 'モード不明'};
      $('firmwareMode').textContent = labels[data.device_mode] || data.device_mode;
      $('firmwareMode').classList.toggle('online', data.device_mode === 'inference' && data.inference_active);
    }
    $('output').value = $('output').value || data.output_root;
    $('sessionPath').textContent = data.session_dir ? `記録先: ${data.session_dir}` : '';
    $('error').textContent = data.last_error || '';
    if (data.last_control) $('controlStatus').textContent = `ボードAPI: ${JSON.stringify(data.last_control)}`;
    if (data.assembly && data.assembly.progress) {
      const progress = data.assembly.progress;
      $('controlStatus').textContent = `長時間波形を受信中: ${progress.received_chunks} / ${progress.total_chunks}`;
    } else if (data.assembly && data.assembly.retry_required) {
      $('controlStatus').textContent = '長時間波形が欠落したため、同じ打点を再測定します。';
    }
    $('stats').innerHTML = stat('受信', stats.events_received) + stat('保存', stats.events_saved) +
      stat('欠落', stats.missing_sequences) + stat('CRC等', stats.decoder_errors) +
      stat('重複', stats.duplicate_sequences) + stat('順序逆転', stats.out_of_order_sequences) +
      stat('保存失敗', stats.save_errors);
    if ($('collectionGrid')) drawCollection(data.collection);
    if ($('aiSummary') && data.latest_ai && data.latest_ai.sequence !== lastAiSequence) {
      lastAiSequence = data.latest_ai.sequence;
      drawAiResult(data.latest_ai);
    }
    updateActionState(data);
    const event = await api('/api/events/latest');
    if (event.sequence !== undefined && (event.sequence !== lastSequence || event.source === 'demo')) {
      lastSequence = event.sequence;
      drawEvent(event);
    }
  } catch (error) { $('error').textContent = error.message; }
}

function drawAiResult(result) {
  const comparison = result.comparison || {};
  const summary = $('aiSummary');
  let message = result.case_id === 0xFF
    ? `判定: エリア${result.predicted_class + 1}`
    : `テスト ${result.case_id} / 実機クラス ${result.predicted_class}`;
  summary.classList.remove('pass', 'fail');
  if (comparison.available) {
    message += ` / PC基準クラス ${comparison.expected_class}` +
      ` / 最大絶対誤差 ${comparison.max_absolute_error.toFixed(6)}` +
      ` / ${comparison.passed ? '合格' : '不一致'}`;
    summary.classList.add(comparison.passed ? 'pass' : 'fail');
  } else message += ' / 比較基準なし';
  summary.textContent = message;
  $('aiOutputs').innerHTML = result.outputs.map((value, index) => {
    const expected = comparison.expected_outputs ? comparison.expected_outputs[index] : null;
    const error = comparison.absolute_errors ? comparison.absolute_errors[index] : null;
    return `<div class="ai-output"><b>エリア${index + 1}</b><span>${value.toFixed(6)}</span>` +
      (expected === null ? '' : `<small>PC ${expected.toFixed(6)}<br>差 ${error.toFixed(6)}</small>`) + '</div>';
  }).join('');
  if (result.input_plot) drawDummyInput(result.input_plot);
  if ($('hitGrid')) {
    $('hitGrid').querySelectorAll('[data-class]').forEach(cell => {
      cell.classList.toggle('active', Number(cell.dataset.class) === result.predicted_class);
    });
  }
}

function drawCollection(collection) {
  if (!collection) return;
  collectionCompletedSamples = collection.completed_samples;
  const summary = $('collectionSummary');
  const pointLabels = POINT_LABELS;
  if (collection.active) {
    summary.textContent = `エリア${collection.current_class_id + 1}の${pointLabels[collection.current_point_name] || collection.current_point_name}` +
      `（x=${collection.current_x_mm} mm, y=${collection.current_y_mm} mm）を叩いてください ` +
      `（この位置 ${collection.current_repetition}/${collection.repetitions}、全体 ${collection.completed_samples}/${collection.total_samples}）`;
  } else if (collection.finished) {
    summary.textContent = `採取完了：${collection.completed_samples}/${collection.total_samples}件を保存しました。`;
  } else {
    summary.textContent = collection.total_samples ?
      `採取停止：${collection.completed_samples}/${collection.total_samples}件` :
      '採取を開始するとエリア1から順に案内します。';
  }
  document.querySelectorAll('.collection-cell').forEach(cell => {
    const area = Number(cell.dataset.area);
    const count = collection.per_class_counts[area] || 0;
    cell.querySelector('span').textContent = `${count} / ${collection.samples_per_class}`;
    cell.classList.toggle('active', collection.active && area === collection.current_class_id);
    cell.classList.toggle('complete', collection.samples_per_class > 0 && count >= collection.samples_per_class);
  });
  const positionProgress = $('positionProgress');
  if (positionProgress) {
    const positions = collection.per_position_counts || [];
    const points = positions.filter(item => item.class_id === 0);
    const byKey = new Map(positions.map(item => [`${item.class_id}:${item.point_id}`, item]));
    positionProgress.style.setProperty('--point-count', Math.max(points.length, 1));
    if (points.length === 0) {
      positionProgress.textContent = '採取を開始すると、位置ごとの件数を表示します。';
    } else {
      const header = `<div class="position-row position-header"><b>ラベル</b>${points.map(point =>
        `<b>${pointLabels[point.point_name] || point.point_name}</b>`).join('')}<b>合計</b></div>`;
      const rows = Array.from({length: collection.panel.class_count}, (_, classId) => {
        const cells = points.map(point => {
          const item = byKey.get(`${classId}:${point.point_id}`);
          const count = item ? item.count : 0;
          const active = collection.active && classId === collection.current_class_id && point.point_id === collection.current_point_id;
          const complete = collection.repetitions > 0 && count >= collection.repetitions;
          return `<span class="position-count${active ? ' active' : ''}${complete ? ' complete' : ''}">${count}/${collection.repetitions}</span>`;
        }).join('');
        return `<div class="position-row"><b>エリア${classId + 1}</b>${cells}<b>${collection.per_class_counts[classId] || 0}/${collection.samples_per_class}</b></div>`;
      }).join('');
      positionProgress.innerHTML = header + rows;
    }
  }
  // Before the first run the server still holds the default pattern, so the
  // panel previews whatever the operator has picked in the dropdown instead.
  const selectedPattern = $('collectionPattern').value;
  const selectedPatternDiffers = !collection.active &&
    selectedPattern !== collection.position_pattern;
  const showCollectionTargets = !selectedPatternDiffers &&
    (collection.active || collection.finished || collection.completed_samples > 0);
  if (showCollectionTargets) {
    renderPoints(collection.per_position_counts, collection.panel,
      collection.current_target_index, collection.active);
  } else {
    previewPoints().catch(error => { $('error').textContent = error.message; });
  }
  const marker = $('collectionMarker');
  marker.hidden = !collection.active;
  if (collection.active) {
    marker.style.left = `${collection.current_x_mm / collection.panel.width_mm * 100}%`;
    marker.style.top = `${collection.current_y_mm / collection.panel.height_mm * 100}%`;
    marker.classList.toggle('right-edge', collection.current_x_mm > collection.panel.width_mm * 0.75);
    marker.querySelector('span').textContent = `エリア${collection.current_class_id + 1} ${pointLabels[collection.current_point_name] || collection.current_point_name}`;
  }
  $('collectionStart').disabled = collection.active;
  $('collectionStop').disabled = !collection.active;
  $('collectionUndo').disabled = !collection.active || collection.completed_samples <= 0;
  $('collectionRepetitions').disabled = collection.active;
  $('collectionPattern').disabled = collection.active;
}

function ensureAreaGrid(panel) {
  if (!panel) return;
  const grid = $('collectionGrid') || $('hitGrid');
  if (!grid || grid.classList.contains('instrument-grid')) return;
  const selector = grid.id === 'collectionGrid' ? '.collection-cell' : '[data-class]';
  const current = grid.querySelectorAll(selector);
  if (current.length === panel.class_count) return;
  current.forEach(element => element.remove());
  const fragment = document.createDocumentFragment();
  for (let area = 0; area < panel.class_count; area++) {
    const cell = document.createElement('div');
    if (grid.id === 'collectionGrid') {
      cell.className = 'collection-cell'; cell.dataset.area = area;
      cell.innerHTML = `<b>エリア${area + 1}</b><span>0 / 0</span>`;
    } else {
      cell.dataset.class = area; cell.textContent = `エリア${area + 1}`;
    }
    fragment.appendChild(cell);
  }
  grid.insertBefore(fragment, grid.firstChild);
}

let pointsSignature = null;
let previewPattern = null;
let previewTargets = null;

function renderClamp(panel) {
  const element = $('collectionClamp');
  const clamp = panel && panel.clamp;
  if (!element || !clamp) return;
  const percent = (value, span) => `${value / span * 100}%`;
  element.style.left = percent(clamp.x_min, panel.width_mm);
  element.style.top = percent(clamp.y_min, panel.height_mm);
  element.style.width = percent(clamp.x_max - clamp.x_min, panel.width_mm);
  element.style.height = percent(clamp.y_max - clamp.y_min, panel.height_mm);
  element.title = `パネル固定具 x=${clamp.x_min}～${clamp.x_max} mm、` +
    `y=${clamp.y_min}～${clamp.y_max} mm（この範囲は叩かない）`;
  element.hidden = false;
}

function renderPoints(points, panel, activeIndex, interactive) {
  renderClamp(panel);
  // status() runs twice a second; rebuilding the DOM only on a real change
  // keeps hover states and avoids flicker.
  const signature = JSON.stringify([
    points.map(point => [point.target_index, point.count, point.complete]),
    activeIndex, interactive,
  ]);
  if (signature === pointsSignature) return;
  pointsSignature = signature;
  const container = $('collectionPoints');
  container.classList.toggle('preview', !interactive);
  container.innerHTML = points.map(point => {
    const classes = ['collection-point'];
    if (point.complete) classes.push('complete');
    if (point.target_index === activeIndex) classes.push('active');
    const name = POINT_LABELS[point.point_name] || point.point_name;
    const title = `エリア${point.class_id + 1} ${name}（x=${point.x_mm} mm, y=${point.y_mm} mm）` +
      (interactive ? `　${point.count}回採取済み${point.complete ? '（完了）' : ''}` : '');
    return `<button type="button" class="${classes.join(' ')}" data-target="${point.target_index}"` +
      ` style="left:${point.x_mm / panel.width_mm * 100}%;top:${point.y_mm / panel.height_mm * 100}%"` +
      ` title="${escapeHtml(title)}"${interactive ? '' : ' disabled'}>` +
      `${interactive ? point.count : ''}</button>`;
  }).join('');
  if (!interactive) return;
  container.querySelectorAll('.collection-point').forEach(button => {
    button.onclick = () => selectTarget(Number(button.dataset.target));
  });
}

async function previewPoints(force) {
  const pattern = $('collectionPattern').value;
  if (force || pattern !== previewPattern || !previewTargets) {
    const profile = $('panelProfile')?.value || window.panelProfileUi?.currentId || '';
    previewTargets = await api(`/api/collection/targets?pattern=${encodeURIComponent(pattern)}&panel_profile_id=${encodeURIComponent(profile)}`);
    previewPattern = pattern;
    pointsSignature = null;
  }
  renderPoints(previewTargets.targets, previewTargets.panel, null, false);
}

async function selectTarget(targetIndex) {
  try {
    await api('/api/collection/select', {target_index: targetIndex});
    $('error').textContent = '';
    await status();
  } catch (error) { $('error').textContent = error.message; }
}

function plot(canvas, x, y, color, xlabel, ylabel, marker, yDecimals = 0) {
  const context = $(canvas).getContext('2d');
  const element = $(canvas), width = element.width, height = element.height;
  const pad = {l: 66, r: 18, t: 16, b: 42};
  context.clearRect(0, 0, width, height);
  context.font = '13px "Yu Gothic UI","Yu Gothic",Meiryo,sans-serif';
  context.fillStyle = '#506070'; context.strokeStyle = '#dce3e9'; context.lineWidth = 1;
  const xmin = x[0] || 0, xmax = x[x.length - 1] || 1;
  const ymin = Math.min(...y), ymax = Math.max(...y), range = ymax - ymin || 1;
  for (let i = 0; i <= 4; i++) {
    const py = pad.t + (height - pad.t - pad.b) * i / 4;
    context.beginPath(); context.moveTo(pad.l, py); context.lineTo(width - pad.r, py); context.stroke();
    context.fillText((ymax - range * i / 4).toFixed(yDecimals), 5, py + 4);
  }
  const px = value => pad.l + (value - xmin) / (xmax - xmin || 1) * (width - pad.l - pad.r);
  const py = value => pad.t + (ymax - value) / range * (height - pad.t - pad.b);
  context.strokeStyle = color; context.lineWidth = 1.4; context.beginPath();
  x.forEach((value, index) => index ? context.lineTo(px(value), py(y[index])) : context.moveTo(px(value), py(y[index])));
  context.stroke();
  if (marker !== undefined) {
    context.strokeStyle = '#e23b2e'; context.setLineDash([6, 4]); context.beginPath();
    context.moveTo(px(marker), pad.t); context.lineTo(px(marker), height - pad.b); context.stroke(); context.setLineDash([]);
  }
  context.fillStyle = '#384957'; context.fillText(xlabel, width / 2 - 35, height - 8);
  context.save(); context.translate(15, height / 2 + 25); context.rotate(-Math.PI / 2); context.fillText(ylabel, 0, 0); context.restore();
  context.fillText(xmin.toFixed(1), pad.l - 10, height - pad.b + 18);
  context.fillText(xmax.toFixed(1), width - pad.r - 36, height - pad.b + 18);
}

function drawDummyInput(input) {
  plot('wave', input.time_ms, input.samples, '#1777c8', '時間 [ms]', '正規化入力値', undefined, 2);
  plot('fft', input.frequency_hz, input.magnitude_db, '#dd7b16', '周波数 [Hz]', '振幅 [dB]');
  $('eventInfo').textContent = `ダミーモデル入力 / テスト ${input.case_id} / ` +
    `${input.samples.length}点 / ${input.sample_rate_hz.toLocaleString()} Hz / ` +
    `正規化された合成データ（実センサ波形ではありません）`;
}

function drawEvent(event) {
  plot('wave', event.time_ms, event.samples, '#1777c8', '時間 [ms]', '加速度 [raw LSB]', event.trigger_time_ms);
  plot('fft', event.frequency_hz, event.magnitude_db, '#dd7b16', '周波数 [Hz]', '振幅 [dB]');
  const stored = event.stored;
  const origin = stored
    ? `保存データ ${stored.session_id} No.${stored.index}（${areaLabel(stored.class_id)}）`
    : event.source;
  $('eventInfo').textContent = `${origin} / sequence ${event.sequence} / ` +
    `${event.samples.length}点 / ${event.sample_rate_hz.toLocaleString()} Hz / ` +
    `peak ${event.peak_abs.toLocaleString()} LSB / trigger ${event.trigger_time_ms.toFixed(2)} ms`;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character =>
    ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[character]));
}

function shortTime(iso) {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? (iso || '—') : parsed.toLocaleString('ja-JP', {hour12: false});
}

function areaLabel(classId) {
  return classId === null || classId === undefined ? '未ラベル' : `エリア${classId + 1}`;
}

function pointLabel(annotations) {
  const name = annotations && annotations.target_point_name;
  if (!name) return '—';
  const repetition = annotations.repetition ? ` #${annotations.repetition}` : '';
  return `${POINT_LABELS[name] || name}${repetition}`;
}

function libraryRoot() {
  return $('output') ? $('output').value : '';
}

async function loadSessions() {
  const data = await api(`/api/library/sessions?root=${encodeURIComponent(libraryRoot())}`);
  const select = $('librarySession');
  const previous = library.sessionId;
  select.innerHTML = data.sessions.map(session => {
    const label = session.error
      ? `${session.session_id}（読み取り不可）`
      : `${session.session_id}　${session.event_count}件` +
        (session.mode === 'guided_8area_points' ? '　ガイド採取' : '');
    return `<option value="${escapeHtml(session.session_id)}">${escapeHtml(label)}</option>`;
  }).join('');
  library.sessions = data.sessions;
  if (data.sessions.length === 0) {
    library.sessionId = null;
    library.events = [];
    library.selected = null;
    $('librarySummary').textContent = `${data.root} に保存済みセッションがありません。`;
    $('librarySummary').classList.remove('warn');
    $('libraryList').innerHTML = '<div class="library-empty">データがありません。</div>';
    return drawLibrarySelection();
  }
  const keep = data.sessions.some(session => session.session_id === previous);
  library.sessionId = keep ? previous : data.sessions[0].session_id;
  select.value = library.sessionId;
  await loadEvents();
}

async function loadEvents() {
  if (!library.sessionId) return;
  const data = await api(`/api/library/events?session=${encodeURIComponent(library.sessionId)}` +
    `&root=${encodeURIComponent(libraryRoot())}`);
  library.events = data.events;
  if (!library.events.some(event => event.index === library.selected)) library.selected = null;
  drawLibrarySummary();
  drawLibraryList();
  drawLibrarySelection();
}

function drawLibrarySummary() {
  const session = (library.sessions || []).find(item => item.session_id === library.sessionId);
  const summary = $('librarySummary');
  if (!session) return;
  if (session.error) {
    summary.textContent = `${session.session_id}: ${session.error}`;
    return summary.classList.add('warn');
  }
  const areas = session.class_ids.length ? session.class_ids.map(id => id + 1).join(', ') : 'なし';
  summary.textContent = `${session.session_id}　${library.events.length}件　` +
    `作成 ${shortTime(session.created_at)}　` +
    `${session.closed_at ? '終了済み' : '記録中またはクローズ未了'}　ラベル: エリア ${areas}`;
  summary.classList.toggle('warn', !session.consistent);
  if (!session.consistent) {
    summary.textContent += `　⚠ session.jsonのevent_count(${session.declared_event_count})と` +
      `manifest行数(${library.events.length})が不一致です。`;
  }
}

function drawLibraryList() {
  const list = $('libraryList');
  if (library.events.length === 0) {
    list.innerHTML = '<div class="library-empty">このセッションにはイベントがありません。</div>';
    return;
  }
  const header = '<div class="library-row library-header"><span>No.</span><span>ラベル</span>' +
    '<span>打点</span><span>peak</span><span>受信時刻</span></div>';
  const rows = library.events.map(event => {
    const classes = ['library-row'];
    if (event.index === library.selected) classes.push('selected');
    if (!event.exists) classes.push('missing');
    return `<button type="button" class="${classes.join(' ')}" data-index="${event.index}">` +
      `<span>${event.index}</span><span>${escapeHtml(areaLabel(event.class_id))}</span>` +
      `<span>${escapeHtml(pointLabel(event.annotations))}</span>` +
      `<span>${event.peak_abs.toLocaleString()}</span>` +
      `<span>${escapeHtml(shortTime(event.received_at))}${event.exists ? '' : '（ファイル欠落）'}</span></button>`;
  }).join('');
  list.innerHTML = header + rows;
  list.querySelectorAll('.library-row[data-index]').forEach(row => {
    row.onclick = () => showStoredEvent(Number(row.dataset.index));
  });
  const selectedRow = list.querySelector(`.library-row[data-index="${library.selected}"]`);
  if (selectedRow) selectedRow.scrollIntoView({block: 'nearest'});
}

function drawLibrarySelection() {
  const event = library.events.find(item => item.index === library.selected);
  const position = library.events.findIndex(item => item.index === library.selected);
  $('libraryDelete').disabled = !event;
  $('libraryPrevious').disabled = position <= 0;
  $('libraryNext').disabled = library.events.length === 0 || position >= library.events.length - 1;
  $('librarySelection').textContent = event
    ? `表示中: No.${event.index} / ${areaLabel(event.class_id)} / ${pointLabel(event.annotations)} / sequence ${event.sequence}`
    : '波形を表示するデータを選んでください。';
}

async function stepStoredEvent(direction) {
  if (library.events.length === 0) return;
  const position = library.events.findIndex(item => item.index === library.selected);
  const nextPosition = position < 0 ? 0 : position + direction;
  if (nextPosition < 0 || nextPosition >= library.events.length) return;
  await showStoredEvent(library.events[nextPosition].index);
}

async function showStoredEvent(index) {
  try {
    const event = await api(`/api/library/event?session=${encodeURIComponent(library.sessionId)}` +
      `&index=${index}&root=${encodeURIComponent(libraryRoot())}`);
    library.selected = index;
    drawEvent(event);
    drawLibraryList();
    drawLibrarySelection();
    $('error').textContent = '';
  } catch (error) { $('error').textContent = error.message; }
}

if ($('libraryList')) {
  $('libraryRefresh').onclick = async () => {
    try { await loadSessions(); $('error').textContent = ''; }
    catch (error) { $('error').textContent = error.message; }
  };
  $('librarySession').onchange = async () => {
    library.sessionId = $('librarySession').value;
    library.selected = null;
    try { await loadEvents(); } catch (error) { $('error').textContent = error.message; }
  };
  $('libraryPrevious').onclick = async () => {
    await stepStoredEvent(-1);
  };
  $('libraryNext').onclick = async () => {
    await stepStoredEvent(1);
  };
  $('libraryDelete').onclick = async () => {
    const event = library.events.find(item => item.index === library.selected);
    if (!event) return;
    if (!confirm(`No.${event.index}（${areaLabel(event.class_id)} / ${pointLabel(event.annotations)}）を削除します。\n` +
      '波形ファイルとmanifestの記録が消え、元に戻せません。よろしいですか？')) return;
    try {
      await api('/api/library/delete', {
        session: library.sessionId, index: event.index, root: libraryRoot()
      });
      const deletedPosition = library.events.findIndex(item => item.index === event.index);
      const replacement = library.events[deletedPosition + 1] || library.events[deletedPosition - 1];
      library.selected = null;
      await loadSessions();
      if (replacement && library.events.some(item => item.index === replacement.index)) {
        await showStoredEvent(replacement.index);
      }
      $('error').textContent = '';
    } catch (error) { $('error').textContent = error.message; }
  };
  $('libraryDeleteSession').onclick = async () => {
    if (!library.sessionId) return;
    if (!confirm(`セッション ${library.sessionId} を丸ごと削除します。\n` +
      `${library.events.length}件の波形がすべて消え、元に戻せません。よろしいですか？`)) return;
    try {
      await api('/api/library/delete_session', {session: library.sessionId, root: libraryRoot()});
      library.sessionId = null;
      library.selected = null;
      await loadSessions();
      $('error').textContent = '';
    } catch (error) { $('error').textContent = error.message; }
  };
}

if ($('aiSelftest')) $('aiSelftest').onclick = async () => {
  try {
    await api('/api/ai/selftest', {case_id: Number($('aiCase').value)});
    $('aiSummary').textContent = '実機AI推論を実行中…';
  } catch (error) { $('error').textContent = error.message; }
};
if ($('aiRunAll')) $('aiRunAll').onclick = async () => {
  try {
    for (let caseId = 0; caseId < 8; caseId++) {
      $('aiCase').value = caseId;
      $('aiSummary').textContent = `8ケース連続実行中… ${caseId + 1}/8`;
      await api('/api/ai/selftest', {case_id: caseId});
      await new Promise(resolve => setTimeout(resolve, 180));
    }
  } catch (error) { $('error').textContent = error.message; }
};
if ($('collectionPattern')) $('collectionPattern').onchange = async () => {
  try {
    $('collectionRepetitions').value = $('collectionPattern').value === 'center' ? 50 : 10;
    await previewPoints(true);
    $('error').textContent = '';
  }
  catch (error) { $('error').textContent = error.message; }
};
if ($('collectionStart')) $('collectionStart').onclick = async () => {
  try {
    const repetitions = Number($('collectionRepetitions').value);
    await api('/api/collection/start', {
      repetitions,
      output_root: $('output').value,
      position_pattern: $('collectionPattern').value,
      panel_profile_id: $('panelProfile')?.value
    });
    await status();
  } catch (error) { $('error').textContent = error.message; }
};
if ($('collectionStop')) $('collectionStop').onclick = async () => {
  try { await api('/api/collection/stop', {}); await status(); }
  catch (error) { $('error').textContent = error.message; }
};
if ($('inferenceStart')) $('inferenceStart').onclick = async () => {
  try {
    await api('/api/inference/start', {});
    $('controlStatus').textContent = '推論中です。アクリル板をたたいてください。';
    await status();
  } catch (error) { $('error').textContent = error.message; }
};
if ($('inferenceStop')) $('inferenceStop').onclick = async () => {
  try { await api('/api/inference/stop', {}); await status(); }
  catch (error) { $('error').textContent = error.message; }
};
if ($('collectionUndo')) $('collectionUndo').onclick = async () => {
  if (!confirm('直前に保存した1件を削除し、同じ位置を取り直します。よろしいですか？')) return;
  const button = $('collectionUndo');
  button.disabled = true;
  try {
    const result = await api('/api/collection/undo', {
      expected_completed_samples: collectionCompletedSamples
    });
    const undone = result.undone_event;
    await status();
    if ($('libraryList')) await loadSessions();
    $('error').textContent = '';
    $('controlStatus').textContent =
      `直前の1件（エリア${undone.class_id + 1}、記録No.${undone.index}）を削除しました。同じ位置をもう一度たたいてください。`;
  } catch (error) {
    await status();
    $('error').textContent = error.message;
  }
};
$('refresh').onclick = ports;
$('connect').onclick = async () => {
  try {
    await api('/api/connect', {port: $('port').value});
    await api('/api/device/mode', {mode: $('inferenceStart') ? 'inference' : 'collection'});
    await status();
  } catch (error) { $('error').textContent = error.message; }
};
$('disconnect').onclick = async () => { await api('/api/disconnect', {}); await status(); };
$('ping').onclick = async () => { try { await api('/api/command', {command: 'ping'}); await status(); } catch (error) { $('error').textContent = error.message; } };
$('capture').onclick = async () => { try { await api('/api/command', {command: 'capture'}); await status(); } catch (error) { $('error').textContent = error.message; } };
$('demo').onclick = async () => { drawEvent(await api('/api/demo', {})); await status(); };
$('newSession').onclick = async () => {
  try {
    const raw = $('classId').value;
    const data = await api('/api/session', {output_root: $('output').value, class_id: raw === '' ? null : Number(raw)});
    $('sessionPath').textContent = `記録先: ${data.session_dir}`;
  } catch (error) { $('error').textContent = error.message; }
};
ports();
document.querySelectorAll('.app-tabs a').forEach(link => {
  link.addEventListener('click', async event => {
    event.preventDefault();
    try {
      const current = await api('/api/status');
      if (!current.connected) { window.location.href = link.href; return; }
      const href = link.getAttribute('href');
      const mode = href === '/collector.html' ? 'collection' :
        (href === '/instrument.html' ? 'instrument' : 'inference');
      if (current.collection && current.collection.active) {
        throw new Error('データ採取中はタブを切り替えられません。先に採取を停止してください。');
      }
      if (current.inference_active && current.device_mode !== mode) {
        await api('/api/inference/stop', {});
      }
      if (current.device_mode !== mode) await api('/api/device/mode', {mode});
      window.location.href = link.href;
    } catch (error) { $('error').textContent = error.message; }
  });
});
// status() fills the 保存先 field, which is the root the library browser reads.
status().then(() => {
  if ($('libraryList')) return loadSessions().catch(error => { $('error').textContent = error.message; });
});
setInterval(status, 500);
