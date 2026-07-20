(function () {
  let currentId = null;
  function applyPanel(panel) {
    if (!panel) return;
    currentId = panel.id;
    const root = document.documentElement;
    root.style.setProperty('--panel-columns', panel.columns);
    root.style.setProperty('--panel-rows', panel.rows);
    root.style.setProperty('--panel-class-count', panel.class_count);
    root.style.setProperty('--panel-aspect', `${panel.width_mm} / ${panel.height_mm}`);
    document.querySelectorAll('.panel-size,.panel-axis').forEach(element => {
      element.innerHTML = `<span>← ${panel.width_mm} mm →</span><span>高さ ${panel.height_mm} mm / 厚さ ${panel.thickness_mm} mm</span>`;
    });
    document.querySelectorAll('[data-panel-class-count]').forEach(element => {
      element.textContent = panel.class_count;
    });
    document.querySelectorAll('[data-panel-class-label]').forEach(element => {
      element.textContent = `${panel.class_count}${element.dataset.panelClassLabel}`;
    });
    for (const id of ['hitGrid', 'collectionGrid', 'positionPanel']) {
      const element = document.getElementById(id);
      if (element) element.setAttribute('aria-label', `${panel.width_mm} × ${panel.height_mm} × ${panel.thickness_mm} mm アクリル板`);
    }
  }
  function sync(status) {
    const select = document.getElementById('panelProfile');
    applyPanel(status.panel);
    if (!select) return;
    const profiles = status.panel_profiles || [];
    const signature = profiles.map(item => item.id).join('|');
    if (select.dataset.signature !== signature) {
      select.replaceChildren(...profiles.map(item => new Option(item.label, item.id)));
      select.dataset.signature = signature;
    }
    select.value = status.panel_profile_id;
    select.disabled = Boolean(status.collection?.active || select.dataset.changing === 'true');
    if (select.dataset.bound) return;
    select.dataset.bound = 'true';
    select.addEventListener('change', async () => {
      select.dataset.changing = 'true'; select.disabled = true;
      try {
        const response = await fetch('/api/panel', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({panel_profile_id:select.value})});
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || response.statusText);
        location.reload();
      } catch (error) {
        select.dataset.changing = 'false'; select.disabled = false;
        const target = document.getElementById('error');
        if (target) target.textContent = error.message;
      }
    });
  }
  window.panelProfileUi = {sync, applyPanel, get currentId() { return currentId; }};
})();
