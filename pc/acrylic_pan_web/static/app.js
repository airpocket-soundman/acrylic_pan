const $ = id => document.getElementById(id);
let lastSequence = null;
let lastAiSequence = null;

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

async function status() {
  try {
    const data = await api('/api/status');
    const stats = data.stats;
    $('connection').textContent = data.connected ? `接続中 ${data.port}` : '未接続';
    $('connection').classList.toggle('online', data.connected);
    $('output').value = $('output').value || data.output_root;
    $('sessionPath').textContent = data.session_dir ? `記録先: ${data.session_dir}` : '';
    $('error').textContent = data.last_error || '';
    if (data.last_control) $('controlStatus').textContent = `ボードAPI: ${JSON.stringify(data.last_control)}`;
    $('stats').innerHTML = stat('受信', stats.events_received) + stat('保存', stats.events_saved) +
      stat('欠落', stats.missing_sequences) + stat('CRC等', stats.decoder_errors) +
      stat('重複', stats.duplicate_sequences) + stat('順序逆転', stats.out_of_order_sequences) +
      stat('保存失敗', stats.save_errors);
    if ($('aiSummary') && data.latest_ai && data.latest_ai.sequence !== lastAiSequence) {
      lastAiSequence = data.latest_ai.sequence;
      drawAiResult(data.latest_ai);
    }
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
  let message = `テスト ${result.case_id} / 実機クラス ${result.predicted_class}`;
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
    return `<div class="ai-output"><b>class ${index}</b><span>${value.toFixed(6)}</span>` +
      (expected === null ? '' : `<small>PC ${expected.toFixed(6)}<br>差 ${error.toFixed(6)}</small>`) + '</div>';
  }).join('');
}

function plot(canvas, x, y, color, xlabel, ylabel, marker) {
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
    context.fillText((ymax - range * i / 4).toFixed(0), 5, py + 4);
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

function drawEvent(event) {
  plot('wave', event.time_ms, event.samples, '#1777c8', '時間 [ms]', '加速度 [raw LSB]', event.trigger_time_ms);
  plot('fft', event.frequency_hz, event.magnitude_db, '#dd7b16', '周波数 [Hz]', '振幅 [dB]');
  $('eventInfo').textContent = `${event.source} / sequence ${event.sequence} / ` +
    `${event.sample_rate_hz.toLocaleString()} Hz / peak ${event.peak_abs.toLocaleString()} LSB / ` +
    `trigger ${event.trigger_time_ms.toFixed(2)} ms`;
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
$('refresh').onclick = ports;
$('connect').onclick = async () => { try { await api('/api/connect', {port: $('port').value}); await status(); } catch (error) { $('error').textContent = error.message; } };
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
ports(); status(); setInterval(status, 500);
