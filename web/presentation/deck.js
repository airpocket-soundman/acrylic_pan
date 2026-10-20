/* Shared helpers: mounting a slide into a scaled 1920 x 1080 stage. */
(function () {
  function fit(container) {
    const scale = Math.min(container.clientWidth / 1920, container.clientHeight / 1080);
    if (scale > 0) container.style.setProperty('--s', String(scale));
    else if (!container.style.getPropertyValue('--s')) container.style.setProperty('--s', '0.1');
  }

  function mount(container, slide, ctx) {
    const demoClass = slide.demo ? ' demo' : '';
    container.innerHTML =
      `<div class="stage-box"><div class="stage${demoClass}">${slide.render(ctx || {})}</div></div>`;
    fit(container);
    const media = container.querySelector('video[data-autoplay]');
    if (media) media.play().catch(() => { /* needs a user gesture; controls stay available */ });
  }

  function format(seconds) {
    const sign = seconds < 0 ? '-' : '';
    const total = Math.abs(Math.round(seconds));
    return `${sign}${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
  }

  window.Deck = {fit, mount, format};
})();
