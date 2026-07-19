const $ = id => document.getElementById(id);
let lastSequence = null;
let loopRunning = true;
let cameraStream = null;
const CAMERA_STORAGE_KEY = 'acrylicPanCameraDevice';

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
  $('connection').textContent = connected ? `接続中 ${data.port}` : '未接続';
  $('connection').classList.toggle('online', connected);
  $('firmwareMode').textContent = running ? '位置推定中' :
    (data.device_mode === 'inference' ? '推論モード' :
      (data.device_mode === 'collection' ? 'データ採取モード' :
        (data.device_mode === 'instrument' ? '楽器モード' : 'モード不明')));
  $('firmwareMode').classList.toggle('online', running);
  $('port').disabled = connected;
  setButtonState('connect', connected, connected);
  setButtonState('disconnect', !connected);
  setButtonState('positionStart', !connected || running, running);
  setButtonState('positionStop', !connected || !running);
  $('positionStatus').classList.toggle('active', running);
  if (running) $('positionStatus').textContent = '位置推定中です。アクリル板をたたいてください。';
}

async function refreshStatus() {
  try { updateControls(await api('/api/status')); }
  catch (error) { $('error').textContent = error.message; }
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

function gaussian(x, y, cx, cy, sx, sy, rho) {
  const dx = (x - cx) / Math.max(sx, 1);
  const dy = (y - cy) / Math.max(sy, 1);
  const correlation = Math.max(-0.99, Math.min(0.99, Number(rho) || 0));
  const denominator = Math.max(1 - correlation * correlation, 0.02);
  const distance = (dx * dx - 2 * correlation * dx * dy + dy * dy) / denominator;
  return Math.exp(-0.5 * distance);
}

function drawHeatmap(position) {
  const canvas = $('positionHeatmap');
  const context = canvas.getContext('2d');
  const width = 160, height = 80;
  const image = context.createImageData(width, height);
  const sigmaX = Number(position.sigma_x_mm) || 0;
  const sigmaY = Number(position.sigma_y_mm) || 0;
  const hasDistribution = Boolean(position.model_available && sigmaX > 0 && sigmaY > 0);
  const density = new Float32Array(width * height);
  let peak = 0;
  for (let py = 0; py < height; py++) {
    const y = (py + 0.5) * 200 / height;
    for (let px = 0; px < width; px++) {
      const x = (px + 0.5) * 400 / width;
      const value = hasDistribution
        ? gaussian(x, y, position.x_mm, position.y_mm, sigmaX, sigmaY, position.rho_xy)
        : 0;
      density[py * width + px] = value;
      peak = Math.max(peak, value);
    }
  }
  for (let index = 0; index < density.length; index++) {
    const normalized = peak > 0 ? Math.pow(density[index] / peak, 0.72) : 0;
    const [r, g, b] = heatColor(normalized);
    image.data[index * 4] = r;
    image.data[index * 4 + 1] = g;
    image.data[index * 4 + 2] = b;
    image.data[index * 4 + 3] = 255;
  }
  const buffer = document.createElement('canvas');
  buffer.width = width; buffer.height = height;
  buffer.getContext('2d').putImageData(image, 0, 0);
  context.imageSmoothingEnabled = true;
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.drawImage(buffer, 0, 0, canvas.width, canvas.height);
}

function renderProbabilities(values) {
  $('areaProbabilities').innerHTML = values.map((value, index) =>
    `<div class="area-probability"><span>エリア${index + 1}</span><i><b style="width:${Math.max(0, Math.min(100, value * 100))}%"></b></i><output>${(value * 100).toFixed(1)}%</output></div>`
  ).join('');
}

function renderPosition(position) {
  if (!position || !Number.isFinite(position.x_mm) || !Number.isFinite(position.y_mm)) return;
  drawHeatmap(position);
  const x = Math.max(0, Math.min(400, Number(position.x_mm)));
  const y = Math.max(0, Math.min(200, Number(position.y_mm)));
  const marker = $('positionMarker');
  marker.hidden = false;
  marker.style.left = `${x / 4}%`;
  marker.style.top = `${y / 2}%`;
  marker.querySelector('span').textContent = `X ${x.toFixed(1)} / Y ${y.toFixed(1)}`;
  $('coordinateReadout').textContent = `X ${x.toFixed(1)} / Y ${y.toFixed(1)} mm`;
  $('metricCoordinate').textContent = `${x.toFixed(1)}, ${y.toFixed(1)} mm`;
  const level = Number(position.confidence_level || 0);
  const coverage = Number(position.empirical_coverage || 0);
  $('metricConfidence').textContent = level > 0
    ? `${(level * 100).toFixed(0)}%（実測 ${(coverage * 100).toFixed(1)}%）` : '—';
  const ellipse = position.confidence_ellipse_90 || {};
  $('metricRegion').textContent = Number.isFinite(ellipse.semi_major_mm)
    ? `±${ellipse.semi_major_mm.toFixed(1)} / ±${ellipse.semi_minor_mm.toFixed(1)} mm` : '—';
  $('metricSigma').textContent = position.model_available
    ? `σx ${Number(position.sigma_x_mm).toFixed(1)} / σy ${Number(position.sigma_y_mm).toFixed(1)} / ρ ${Number(position.rho_xy).toFixed(2)}` : '—';
  $('metricMethod').textContent = position.model_available ? 'XY回帰＋校正ガウス' : 'エリア分類（座標モデルなし）';
  $('scopeNote').textContent = position.scope || '8中心点教師からの補間推定です。';
  renderProbabilities(position.class_probabilities || Array(8).fill(1 / 8));
}

async function inferenceLoop() {
  while (loopRunning) {
    try {
      const result = await api('/api/ai/latest');
      if (result.sequence !== undefined && result.sequence !== lastSequence && result.position) {
        lastSequence = result.sequence;
        renderPosition(result.position);
        $('error').textContent = result.position.error || '';
      }
    } catch (error) {
      if (!String(error.message).includes('204')) $('error').textContent = error.message;
    }
    await new Promise(resolve => setTimeout(resolve, 180));
  }
}

function renderDemo() {
  renderPosition({
    x_mm: 212.0, y_mm: 118.0, sigma_x_mm: 18.2, sigma_y_mm: 8.4, rho_xy: 0.38,
    confidence: 0.90, confidence_level: 0.90, empirical_coverage: 0.90,
    confidence_ellipse_90: {semi_major_mm: 40.1, semi_minor_mm: 16.5, angle_deg: 11.2},
    class_probabilities: [0.01,0.04,0.08,0.01,0.03,0.27,0.53,0.03], model_available: true,
    scope: '表示デモです。XY推定座標を中心に、検証誤差で校正した二次元ガウスを表示しています。'
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
$('connect').onclick = async () => { try { await api('/api/connect', {port: $('port').value}); await api('/api/device/mode', {mode:'inference'}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('disconnect').onclick = async () => { try { await api('/api/disconnect', {}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionStart').onclick = async () => { try { await api('/api/inference/start', {mode:'inference'}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionStop').onclick = async () => { try { await api('/api/inference/stop', {}); await refreshStatus(); } catch (error) { $('error').textContent = error.message; } };
$('positionDemo').onclick = renderDemo;
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

renderProbabilities(Array(8).fill(1 / 8));
ports().catch(error => $('error').textContent = error.message);
refreshStatus();
setInterval(refreshStatus, 500);
inferenceLoop();
setupCamera();
