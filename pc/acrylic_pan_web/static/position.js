const $ = id => document.getElementById(id);
let lastSequence = null;
let loopRunning = true;
let cameraStream = null;
let activePanel = {id:'400x200x3', width_mm:400, height_mm:200, columns:4, rows:2, class_count:8};
const CAMERA_STORAGE_KEY = 'acrylicPanCameraDevice';
const modeForSource = () => $('positionSource').value === 'device' ? 'device_position' : 'inference';

async function api(path, body) {
  const options = body === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
  };
  const response = await fetch(path, options);
  const text = await response.text();
  const data = text ? JSON.parse(text) : {};
  if (!response.ok) throw new Error(data.error || `${response.status} ${response.statusText}`);
  return data;
}

function setButtonState(id, disabled, active = false) {
  $(id).disabled = disabled;
  $(id).classList.toggle('primary', active);
}

async function ports() {
  const data = await api('/api/ports');
  $('port').innerHTML = data.ports.map(port => `<option>${port}</option>`).join('');
  if (data.ports.includes('COM3')) $('port').value = 'COM3';
}

function updateControls(data) {
  const connected = Boolean(data.connected);
  const running = Boolean(data.inference_active);
  if (data.device_mode === 'device_position') $('positionSource').value = 'device';
  else if (data.device_mode === 'inference') $('positionSource').value = 'pc';
  $('connection').textContent = connected ? `接続中 ${data.port}` : '未接続';
  $('connection').classList.toggle('online', connected);
  $('firmwareMode').textContent = running ? '位置推定中' :
    (data.device_mode === 'device_position' ? 'デバイス確率推論モード' :
      (data.device_mode === 'inference' ? 'PC確率推論モード' :
      (data.device_mode === 'collection' ? 'データ採取モード' :
        (data.device_mode === 'instrument' ? '楽器モード' : 'モード不明'))));
  $('firmwareMode').classList.toggle('online', running);
  $('port').disabled = connected;
  $('positionSource').disabled = running;
  setButtonState('connect', connected, connected);
  setButtonState('disconnect', !connected);
  setButtonState('positionStart', !connected || running, running);
  setButtonState('positionStop', !connected || !running);
  $('positionStatus').classList.toggle('active', running);
  if (running) $('positionStatus').textContent = '位置推定中です。アクリル板をたたいてください。';
}

async function refreshStatus() {
  try { const data = await api('/api/status'); activePanel = data.panel; window.panelProfileUi?.sync(data); applyPanelGeometry(); if($('areaProbabilities').children.length!==activePanel.class_count)renderProbabilities(Array(activePanel.class_count).fill(1/activePanel.class_count)); updateControls(data); }
  catch (error) { $('error').textContent = error.message; }
}

function applyPanelGeometry() {
  const canvas = $('positionHeatmap');
  const canvasHeight = Math.round(canvas.width * activePanel.height_mm / activePanel.width_mm);
  if (canvas.height !== canvasHeight) canvas.height = canvasHeight;
  $('positionPanel').setAttribute('aria-label', `${activePanel.width_mm} × ${activePanel.height_mm} mm アクリル板上の条件付き座標確率分布`);
  const grid = document.querySelector('.panel-grid');
  if (grid) grid.style.backgroundImage =
    `repeating-linear-gradient(90deg,transparent 0,transparent calc(${100 / activePanel.columns}% - 1px),#ffffff42 calc(${100 / activePanel.columns}% - 1px),#ffffff42 ${100 / activePanel.columns}%),` +
    `repeating-linear-gradient(0deg,transparent 0,transparent calc(${100 / activePanel.rows}% - 1px),#ffffff42 calc(${100 / activePanel.rows}% - 1px),#ffffff42 ${100 / activePanel.rows}%)`;
  const clamp = activePanel.clamp;
  const marker = document.querySelector('.clamp-marker');
  if (marker && clamp) {
    marker.style.left = `${clamp.x_min / activePanel.width_mm * 100}%`;
    marker.style.top = `${clamp.y_min / activePanel.height_mm * 100}%`;
    marker.style.width = `${(clamp.x_max - clamp.x_min) / activePanel.width_mm * 100}%`;
    marker.style.height = `${(clamp.y_max - clamp.y_min) / activePanel.height_mm * 100}%`;
  }
  document.querySelector('.position-axis span:last-child').textContent = `${activePanel.width_mm} mm`;
}

function heatColor(value) {
  const t = Math.max(0, Math.min(1, value));
  const stops = [
    [0.00, 3, 7, 30], [0.14, 20, 30, 140], [0.30, 0, 105, 255],
    [0.46, 0, 220, 235], [0.62, 45, 210, 80], [0.76, 245, 235, 30],
    [0.89, 255, 120, 10], [1.00, 220, 15, 5]
  ];
  for (let index = 1; index < stops.length; index++) {
    if (t <= stops[index][0]) {
      const a = stops[index - 1], b = stops[index];
      const f = (t - a[0]) / (b[0] - a[0]);
      return [1, 2, 3].map(channel => Math.round(a[channel] + (b[channel] - a[channel]) * f));
    }
  }
  return stops.at(-1).slice(1);
}

function drawHeatmap(position) {
  const canvas = $('positionHeatmap');
  const context = canvas.getContext('2d');
  const layer = $('positionProbabilityCells');
  const map = position.probability_map || {};
  const support = Array.isArray(map.support_xy_mm) ? map.support_xy_mm : [];
  const probability = Array.isArray(map.probabilities) ? map.probabilities : [];
  const hasDistribution = support.length > 0 && support.length === probability.length;
  if (!hasDistribution) {
    context.clearRect(0, 0, canvas.width, canvas.height);
    layer.removeAttribute('title');
    return;
  }
  const rasterWidth = 40;
  const rasterHeight = Math.max(1, Math.round(
    rasterWidth * activePanel.height_mm / activePanel.width_mm
  ));
  const raster = document.createElement('canvas');
  raster.width = rasterWidth;
  raster.height = rasterHeight;
  const rasterContext = raster.getContext('2d');
  const image = rasterContext.createImageData(rasterWidth, rasterHeight);
  const sigmaMm = 27;
  const inverseTwoSigmaSquared = 1 / (2 * sigmaMm * sigmaMm);
  const values = new Float32Array(rasterWidth * rasterHeight);
  let peak = 1e-12;
  for (let row = 0; row < rasterHeight; row++) {
    const y = (row + .5) * activePanel.height_mm / rasterHeight;
    for (let column = 0; column < rasterWidth; column++) {
      const x = (column + .5) * activePanel.width_mm / rasterWidth;
      let density = 0;
      for (let index = 0; index < support.length; index++) {
        const dx = x - Number(support[index][0]);
        const dy = y - Number(support[index][1]);
        const weight = Math.exp(-(dx * dx + dy * dy) * inverseTwoSigmaSquared);
        density += Math.max(0, Number(probability[index]) || 0) * weight;
      }
      const offset = row * rasterWidth + column;
      values[offset] = density;
      peak = Math.max(peak, density);
    }
  }
  for (let index = 0; index < values.length; index++) {
    const normalized = Math.pow(values[index] / peak, 0.52);
    const quantized = Math.round(normalized * 9) / 9;
    const [red, green, blue] = heatColor(quantized);
    const offset = index * 4;
    image.data[offset] = red;
    image.data[offset + 1] = green;
    image.data[offset + 2] = blue;
    image.data[offset + 3] = 255;
  }
  rasterContext.putImageData(image, 0, 0);
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.imageSmoothingEnabled = false;
  context.drawImage(raster, 0, 0, canvas.width, canvas.height);
  const maximumIndex = probability.indexOf(Math.max(...probability));
  layer.title = `最大確率: X ${Number(support[maximumIndex][0]).toFixed(0)} / Y ${Number(support[maximumIndex][1]).toFixed(0)} mm、${(probability[maximumIndex] * 100).toFixed(2)}%`;
}

function renderProbabilities(values) {
  $('areaProbabilities').innerHTML = values.map((value, index) =>
    `<div class="area-probability"><span>エリア${index + 1}</span><i><b style="width:${Math.max(0, Math.min(100, value * 100))}%"></b></i><output>${(value * 100).toFixed(1)}%</output></div>`
  ).join('');
}

function renderPosition(position) {
  if (!position || !Number.isFinite(position.x_mm) || !Number.isFinite(position.y_mm)) return;
  drawHeatmap(position);
  const x = Math.max(0, Math.min(activePanel.width_mm, Number(position.x_mm)));
  const y = Math.max(0, Math.min(activePanel.height_mm, Number(position.y_mm)));
  const marker = $('positionMarker');
  marker.hidden = false;
  marker.style.left = `${x / activePanel.width_mm * 100}%`;
  marker.style.top = `${y / activePanel.height_mm * 100}%`;
  marker.querySelector('span').textContent = `最尤 X ${x.toFixed(1)} / Y ${y.toFixed(1)}`;
  $('coordinateReadout').textContent = `最尤 X ${x.toFixed(1)} / Y ${y.toFixed(1)} mm`;
  $('metricCoordinate').textContent = `${x.toFixed(1)}, ${y.toFixed(1)} mm`;
  const expectedX = Number.isFinite(Number(position.expected_x_mm)) ? Number(position.expected_x_mm) : x;
  const expectedY = Number.isFinite(Number(position.expected_y_mm)) ? Number(position.expected_y_mm) : y;
  $('metricExpectedCoordinate').textContent = `${expectedX.toFixed(1)}, ${expectedY.toFixed(1)} mm`;
  const level = Number(position.confidence_level || 0);
  const map = position.probability_map || {};
  const credibleCells = Array.isArray(map.credible_90_indices) ? map.credible_90_indices.length : 0;
  const peakProbability = Number(position.distribution_peak_probability || 0);
  const entropy = Number(position.distribution_entropy || 0);
  $('metricConfidence').textContent = peakProbability > 0
    ? `最大セル ${(peakProbability * 100).toFixed(1)}%` : '—';
  $('metricRegion').textContent = credibleCells > 0
    ? `${(level * 100).toFixed(0)}%信用領域 ${credibleCells}セル` : '—';
  $('metricSigma').textContent = position.model_available
    ? `σx ${Number(position.sigma_x_mm).toFixed(1)} / σy ${Number(position.sigma_y_mm).toFixed(1)} / H ${entropy.toFixed(2)}` : '—';
  $('metricMethod').textContent = position.inference_source === 'device'
    ? 'デバイス60座標確率モデル' : (map.probabilities ? 'PC 60座標条件付き確率モデル' : 'エリア分類（確率マップなし）');
  const timing = position.device_timing_us || {};
  $('metricDeviceTiming').textContent = Number.isFinite(Number(timing.total))
    ? `推論 ${(Number(timing.solist_inference) / 1000).toFixed(2)} + softmax ${(Number(timing.softmax) / 1000).toFixed(2)} = ${(Number(timing.total) / 1000).toFixed(2)} ms`
    : 'PC推論（デバイス計測なし）';
  $('scopeNote').textContent = position.scope || '選択したパネル用PCモデルによる座標推定です。';
  renderProbabilities(position.class_probabilities || Array(activePanel.class_count).fill(1 / activePanel.class_count));
}

async function inferenceLoop() {
  while (loopRunning) {
    try {
      const after = lastSequence === null ? '' : String(lastSequence);
      const result = await api(`/api/ai/wait?after=${encodeURIComponent(after)}&timeout=1.0`);
      if (result.sequence !== undefined && result.sequence !== lastSequence && result.position) {
        lastSequence = result.sequence;
        renderPosition(result.position);
        $('error').textContent = result.position.error || '';
      }
    } catch (error) {
      if (!String(error.message).includes('204')) $('error').textContent = error.message;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
  }
}

function renderDemo() {
  const support = [];
  for (let y = 25; y < activePanel.height_mm; y += 50) {
    for (let x = 25; x < activePanel.width_mm; x += 50) support.push([x, y]);
  }
  for (let y = 50; y < activePanel.height_mm; y += 100) {
    for (let x = 50; x < activePanel.width_mm; x += 100) support.push([x, y]);
  }
  const raw = support.map(([x, y]) =>
    Math.exp(-((x-activePanel.width_mm*.53)**2+(y-activePanel.height_mm*.59)**2)/2400) +
    .35*Math.exp(-((x-activePanel.width_mm*.72)**2+(y-activePanel.height_mm*.32)**2)/1800));
  const total = raw.reduce((sum, value) => sum + value, 0);
  const probability = raw.map(value => value / total);
  const expectedX = probability.reduce((sum, value, index) => sum + value * support[index][0], 0);
  const expectedY = probability.reduce((sum, value, index) => sum + value * support[index][1], 0);
  const maximumIndex = probability.indexOf(Math.max(...probability));
  const order = probability.map((value,index)=>({value,index})).sort((a,b)=>b.value-a.value);
  let cumulative = 0; const credible = [];
  for (const item of order) { credible.push(item.index); cumulative += item.value; if(cumulative >= .9) break; }
  renderPosition({
    x_mm: support[maximumIndex][0], y_mm: support[maximumIndex][1],
    expected_x_mm: expectedX, expected_y_mm: expectedY,
    map_x_mm: support[maximumIndex][0], map_y_mm: support[maximumIndex][1],
    sigma_x_mm: 48.2, sigma_y_mm: 41.4, rho_xy: 0.18,
    confidence: 0.90, confidence_level: 0.90, empirical_coverage: 0.90,
    probability_map: {support_xy_mm:support, probabilities:probability, credible_90_indices:credible, normalization:'sum_1'},
    distribution_peak_probability: Math.max(...probability), distribution_entropy: .61,
    class_probabilities: Array.from({length:activePanel.class_count},(_,i)=>i===Math.min(6,activePanel.class_count-1)?.53:.47/(activePanel.class_count-1)), model_available: true,
    scope: '表示デモです。60測定座標の確率を表示用に細密補間しています。数値計算には補間前の確率を使います。'
  });
}

function cameraErrorMessage(error) {
  if (error?.name === 'NotAllowedError') return 'カメラの使用が許可されていません。ブラウザのカメラ権限を確認してください。';
  if (error?.name === 'NotFoundError') return '使用できるUSBカメラが見つかりません。';
  if (error?.name === 'NotReadableError') return 'カメラを開始できません。他のアプリが使用していないか確認してください。';
  return `カメラを開始できません: ${error?.message || String(error)}`;
}

function releaseCamera() {
  if (cameraStream) cameraStream.getTracks().forEach(track => track.stop());
  cameraStream = null;
  $('usbCamera').srcObject = null;
  $('cameraPlaceholder').hidden = false;
  $('cameraStart').disabled = false;
  $('cameraStop').disabled = true;
  $('cameraState').classList.remove('is-running');
}

function stopCamera() {
  releaseCamera();
  $('cameraState').textContent = '停止中';
}

async function refreshCameras(preferredId = '') {
  const select = $('cameraDevice');
  if (!navigator.mediaDevices?.enumerateDevices) {
    select.replaceChildren(new Option('このブラウザでは利用できません', ''));
    select.disabled = true;
    $('cameraStart').disabled = true;
    $('cameraState').textContent = '非対応';
    return [];
  }
  const devices = (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'videoinput');
  const activeId = cameraStream?.getVideoTracks()[0]?.getSettings().deviceId || '';
  const current = preferredId || activeId || select.value || localStorage.getItem(CAMERA_STORAGE_KEY) || '';
  const options = devices.length
    ? devices.map((device, index) => new Option(device.label || `USBカメラ ${index + 1}`, device.deviceId))
    : [new Option('USBカメラが見つかりません', '')];
  select.replaceChildren(...options);
  if (devices.some(device => device.deviceId === current)) select.value = current;
  select.disabled = !devices.length;
  $('cameraStart').disabled = !devices.length || Boolean(cameraStream);
  if (!devices.length && !cameraStream) $('cameraState').textContent = '未検出';
  return devices;
}

async function startCamera() {
  if (!navigator.mediaDevices?.getUserMedia) throw new Error('このブラウザはカメラ入力に対応していません。');
  const selectedId = $('cameraDevice').value;
  releaseCamera();
  $('cameraState').textContent = '接続中…';
  const video = {width: {ideal: 1280}, height: {ideal: 720}, frameRate: {ideal: 30}};
  if (selectedId) video.deviceId = {exact: selectedId};
  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({audio: false, video});
    $('usbCamera').srcObject = cameraStream;
    await $('usbCamera').play().catch(() => {});
    const actualId = cameraStream.getVideoTracks()[0]?.getSettings().deviceId || selectedId;
    if (actualId) localStorage.setItem(CAMERA_STORAGE_KEY, actualId);
    $('cameraPlaceholder').hidden = true;
    $('cameraStart').disabled = true;
    $('cameraStop').disabled = false;
    $('cameraState').textContent = '映像表示中';
    $('cameraState').classList.add('is-running');
    await refreshCameras(actualId);
  } catch (error) {
    releaseCamera();
    $('cameraState').textContent = '開始できません';
    $('cameraPlaceholder').textContent = cameraErrorMessage(error);
    throw error;
  }
}

async function setupCamera() {
  try {
    const devices = await refreshCameras();
    if (devices.length) $('cameraState').textContent = '開始待ち';
  } catch (_) {
    $('cameraState').textContent = '確認できません';
  }
  $('cameraStart').onclick = () => startCamera().catch(() => {});
  $('cameraStop').onclick = stopCamera;
  $('cameraDevice').onchange = async event => {
    localStorage.setItem(CAMERA_STORAGE_KEY, event.target.value);
    if (cameraStream) await startCamera().catch(() => {});
  };
  navigator.mediaDevices?.addEventListener?.('devicechange', () => refreshCameras().catch(() => {}));
  window.addEventListener('pagehide', releaseCamera);
}

$('refresh').onclick = () => ports().catch(error => $('error').textContent = error.message);
$('connect').onclick = async () => { try { await api('/api/connect', {port: $('port').value}); await api('/api/device/mode', {mode:modeForSource()}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('disconnect').onclick = async () => { try { await api('/api/disconnect', {}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionStart').onclick = async () => { try { await api('/api/inference/start', {mode:modeForSource()}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionStop').onclick = async () => { try { await api('/api/inference/stop', {}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionDemo').onclick = renderDemo;
$('positionSource').onchange = async () => { try { const current = await api('/api/status'); if (current.connected && !current.inference_active && current.device_mode !== modeForSource()) await api('/api/device/mode', {mode:modeForSource()}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
document.querySelectorAll('.app-tabs a').forEach(link => link.addEventListener('click', async event => {
  event.preventDefault();
  try {
    const current = await api('/api/status');
    if (!current.connected) { window.location.href = link.href; return; }
    const href = link.getAttribute('href');
    const mode = href === '/collector.html' ? 'collection' : (href === '/instrument.html' ? 'instrument' : 'inference');
    if (current.collection && current.collection.active) throw new Error('データ採取中はタブを切り替えられません。');
    if (current.inference_active && current.device_mode !== mode) await api('/api/inference/stop', {});
    if (current.device_mode !== mode) await api('/api/device/mode', {mode});
    window.location.href = link.href;
  } catch (error) { $('error').textContent = error.message; }
}));

renderProbabilities(Array(activePanel.class_count).fill(1 / activePanel.class_count));
ports().catch(error => $('error').textContent = error.message);
refreshStatus();
setInterval(refreshStatus, 500);
inferenceLoop();
setupCamera();
