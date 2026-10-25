(() => {
  const $ = id => document.getElementById(id);
  let nonce = '', operationBlocked = false, running = false;
  const wait = ms => new Promise(resolve => setTimeout(resolve, ms));

  async function json(path, options = {}) {
    const response = await fetch(path, {cache: 'no-store', ...options});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(body.error || `HTTP ${response.status}`);
    return body;
  }
  const post = (path, body = {}) => json(path, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...body, nonce}),
  });

  function checkNode(item) {
    const marks = {pass: '✓', warn: '!', fail: '×'};
    const node = document.createElement('article');
    node.className = `check ${item.status}`;
    node.innerHTML = `<span class="check-mark">${marks[item.status] || '?'}</span><div><strong></strong><p></p></div><small></small>`;
    node.querySelector('strong').textContent = item.label;
    node.querySelector('p').textContent = item.summary;
    node.querySelector('small').textContent = item.action || '';
    node.title = JSON.stringify(item.details || {}, null, 2);
    return node;
  }

  function render(report) {
    const overall = $('overall'), map = {
      pass: ['✓', '展示可能', '必要な機能はすべて正常です'],
      warn: ['!', '確認項目あり', '演奏できますが、黄色の項目を確認してください'],
      fail: ['×', '点検が必要', '赤色の項目を直してから展示してください'],
    }, values = map[report.overall] || map.fail;
    overall.className = `overall ${report.overall}`;
    overall.querySelector('.overall-icon').textContent = values[0];
    overall.querySelector('strong').textContent = values[1];
    overall.querySelector('span').textContent = values[2];
    const system = report.system || {};
    $('systemInfo').innerHTML = `${system.hostname || ''}<br>${system.architecture || ''} / ${system.temperature_c ?? '-'} ℃`;
    $('checkedAt').textContent = `最終検査: ${new Date(report.checked_at).toLocaleString('ja-JP')}`;
    $('requiredChecks').replaceChildren(...report.checks.filter(item => item.required).map(checkNode));
    $('optionalChecks').replaceChildren(...report.checks.filter(item => !item.required).map(checkNode));
  }

  async function diagnose() {
    if (running) return;
    running = true; $('runDiagnostics').disabled = true; $('actionMessage').textContent = '';
    try {
      const current = await json('/api/status');
      operationBlocked = Boolean(current.collection?.active || current.device_mode === 'calibration');
      const report = await post('/api/admin/diagnostics');
      render(report);
      document.querySelectorAll('.hold-button').forEach(button => button.disabled = operationBlocked);
      if (operationBlocked) $('actionMessage').textContent = '追加学習または収録を終了するまでシステム操作はできません。';
    } catch (error) {
      $('overall').className = 'overall fail';
      $('overall').querySelector('.overall-icon').textContent = '×';
      $('overall').querySelector('strong').textContent = '検査できません';
      $('overall').querySelector('span').textContent = error.message;
    } finally { running = false; $('runDiagnostics').disabled = false; }
  }

  async function execute(action) {
    const labels = {
      service_restart: ['Web UIを再起動中', '数秒後に自動で再接続します。'],
      reboot: ['ラズパイを再起動中', '約1分後に展示画面が自動で戻ります。'],
      poweroff: ['安全にシャットダウン中', '画面が消え、アクセスLEDの点滅が止まるまで電源を抜かないでください。'],
    };
    const overlay = $('shutdownOverlay'), message = labels[action];
    try {
      await post('/api/admin/system', {action});
      $('shutdownTitle').textContent = message[0]; $('shutdownText').textContent = message[1]; overlay.hidden = false;
      if (action === 'service_restart') {
        await wait(3000);
        let serverReady = false;
        for (let i = 0; i < 30; i++) {
          try {
            const status = await json('/api/status');
            serverReady = true;
            if (status.connected) { location.reload(); return; }
          } catch (_) {}
          await wait(1000);
        }
        if (serverReady) location.reload();
        else $('actionMessage').textContent = 'Web UIの再起動確認がタイムアウトしました。';
      }
    } catch (error) { $('actionMessage').textContent = error.message; }
  }

  function installHold(button) {
    let timer = null, started = 0, frame = 0;
    const cancel = () => { clearTimeout(timer); cancelAnimationFrame(frame); timer = null; button.style.setProperty('--hold', '0%'); };
    const draw = () => { if (!timer) return; button.style.setProperty('--hold', `${Math.min(100, (performance.now()-started)/30)}%`); frame = requestAnimationFrame(draw); };
    button.addEventListener('pointerdown', event => {
      if (button.disabled || timer) return;
      event.preventDefault(); button.setPointerCapture(event.pointerId); started = performance.now();
      timer = setTimeout(() => { const action = button.dataset.action; cancel(); execute(action); }, 3000); draw();
    });
    for (const name of ['pointerup','pointercancel','pointerleave']) button.addEventListener(name, cancel);
  }

  $('runDiagnostics').onclick = diagnose;
  document.querySelectorAll('.hold-button').forEach(installHold);
  (async () => { try { nonce = (await json('/api/admin/session')).nonce; await diagnose(); } catch (error) { $('checkedAt').textContent = error.message; } })();
})();
