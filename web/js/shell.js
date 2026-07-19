const pages = new Set(['overview','hardware','mechanics','simulation-plate','simulation','simulation-calculix','fem-measured-report','ai','firmware','implementation-plan','solist-simulator','model-report','experiment','references']);
const frame = document.getElementById('content-frame');
const sidebar = document.querySelector('.sidebar');
const menuButton = document.querySelector('.menu-button');

function openPage(name, updateHash = true) {
  if (!pages.has(name)) name = 'overview';
  frame.src = `web/pages/${name}.html`;
  document.querySelectorAll('[data-page]').forEach(link => link.classList.toggle('active', link.dataset.page === name));
  if (updateHash) history.replaceState(null, '', `#${name}`);
  sidebar.classList.remove('open');
  menuButton.setAttribute('aria-expanded', 'false');
}

document.querySelectorAll('[data-page]').forEach(link => link.addEventListener('click', event => {
  event.preventDefault();
  openPage(link.dataset.page);
}));
menuButton.addEventListener('click', () => {
  const open = sidebar.classList.toggle('open');
  menuButton.setAttribute('aria-expanded', String(open));
});
window.addEventListener('hashchange', () => openPage(location.hash.slice(1), false));
openPage(location.hash.slice(1) || 'overview', false);
