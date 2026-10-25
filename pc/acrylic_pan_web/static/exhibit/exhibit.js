/* Shared by the three exhibit pages: navigation bar and board connection light. */
(() => {
  const pages = [
    ['play.html', '🎵 演奏'],
    ['odl.html', '🎯 追加学習'],
    ['video.html', '🎬 デモ動画'],
    ['admin.html', '⚙ 管理'],
  ];
  const current = location.pathname.split('/').pop() || 'play.html';
  const bar = document.createElement('div');
  bar.className = 'exhibit-bar';
  bar.innerHTML = `<h1>Acrylic Pan<small>SOLIST-AI TOUCH INSTRUMENT</small></h1>
    <nav class="exhibit-nav" aria-label="ページ">${pages.map(([href, label]) =>
      `<a href="/exhibit/${href}"${href === current ? ' aria-current="page"' : ''}>${label}</a>`).join('')}</nav>
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
