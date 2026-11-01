/* Shared by the three exhibit pages: navigation bar and board connection light. */
(() => {
  const pages = [
    ['play.html', '演奏'],
    ['odl.html', '追加学習'],
    ['video.html', 'デモ動画'],
    ['admin.html', '管理'],
  ];
  const icons = {
    'play.html': '<path d="M9 17V5l11-2v12M9 8l11-2"/><ellipse cx="6" cy="17" rx="3" ry="2"/><ellipse cx="17" cy="15" rx="3" ry="2"/>',
    'odl.html': '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
    'video.html': '<rect x="3" y="5" width="18" height="15" rx="2"/><path d="M3 9h18M7 5l3 4m4-4l3 4m-7 3l5 3-5 3z"/>',
    'admin.html': '<circle cx="12" cy="12" r="4"/><path d="M12 2v4m0 12v4M2 12h4m12 0h4M5 5l3 3m8 8l3 3M5 19l3-3m8-8l3-3"/>',
  };
  const current = location.pathname.split('/').pop() || 'play.html';
  const bar = document.createElement('div');
  bar.className = 'exhibit-bar';
  bar.innerHTML = `<h1>Acrylic Pan<small>SOLIST-AI TOUCH INSTRUMENT</small></h1>
    <nav class="exhibit-nav" aria-label="ページ">${pages.map(([href, label]) =>
      `<a href="/exhibit/${href}"${href === current ? ' aria-current="page"' : ''}><svg aria-hidden="true" width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="vertical-align:middle;margin-right:7px">${icons[href]}</svg>${label}</a>`).join('')}</nav>
    <div class="exhibit-link"><i id="boardDot" class="dot"></i><span id="boardText">センサ未接続</span></div>`;
  document.body.prepend(bar);
  const dot = bar.querySelector('#boardDot'), text = bar.querySelector('#boardText');
  async function poll() {
    try {
      const status = await (await fetch('/api/status', { cache: 'no-store' })).json();
      dot.classList.toggle('on', Boolean(status.connected));
      text.textContent = status.connected ? 'センサ接続中' : 'センサ未接続';
    } catch (_) {
      dot.classList.remove('on');
      text.textContent = 'サーバ未接続';
    }
  }
  poll();
  setInterval(poll, 2000);
  window.exhibit = {
    lockNav: value => bar.querySelector('.exhibit-nav').classList.toggle('locked', Boolean(value)),
  };
})();
