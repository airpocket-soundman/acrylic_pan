(function () {
  const data = window.ACRYLIC_SENSOR_DATA;
  const buttons = document.getElementById('sensor-hit-buttons');
  const waveform = document.getElementById('waveform-chart');
  const fft = document.getElementById('fft-chart');
  const readout = document.getElementById('sensor-readout');
  if (!data || !buttons || !waveform || !fft) return;

  data.hits.forEach((hit, index) => {
    const button = document.createElement('button');
    button.className = 'pill'; button.type = 'button';
    button.textContent = `${hit.note} · (${hit.x_mm}, ${hit.y_mm})`;
    button.addEventListener('click', () => select(index));
    buttons.appendChild(button);
  });

  function chart(svg, xs, ys, options) {
    const width = 760, height = 250, left = 62, right = 18, top = 18, bottom = 42;
    const plotW = width-left-right, plotH = height-top-bottom;
    const xMax = options.xMax;
    const filtered = [];
    const step = Math.max(1, Math.floor(xs.length / 700));
    for (let i=0; i<xs.length; i+=step) if (xs[i] <= xMax) filtered.push([xs[i], ys[i]]);
    const xPos = x => left + x/xMax*plotW;
    const yPos = y => top + (options.yMax-y)/(options.yMax-options.yMin)*plotH;
    const path = filtered.map((p,i)=>`${i?'L':'M'}${xPos(p[0]).toFixed(2)},${yPos(p[1]).toFixed(2)}`).join(' ');
    const xTicks = options.xTicks.map(v=>`<line x1="${xPos(v)}" y1="${top}" x2="${xPos(v)}" y2="${top+plotH}"/><text x="${xPos(v)}" y="${height-17}" text-anchor="middle">${v}</text>`).join('');
    const yTicks = options.yTicks.map(v=>`<line x1="${left}" y1="${yPos(v)}" x2="${left+plotW}" y2="${yPos(v)}"/><text x="${left-10}" y="${yPos(v)+4}" text-anchor="end">${v.toFixed(options.yDigits)}</text>`).join('');
    svg.setAttribute('viewBox',`0 0 ${width} ${height}`);
    svg.innerHTML=`<title>${options.title}</title><g class="sensor-grid">${xTicks}${yTicks}</g><path class="sensor-axis" d="M${left},${top}V${top+plotH}H${left+plotW}"/><path class="sensor-line" d="${path}"/><text class="sensor-axis-label" x="${left+plotW/2}" y="${height-1}" text-anchor="middle">${options.xLabel}</text><text class="sensor-axis-label" transform="translate(15 ${top+plotH/2}) rotate(-90)" text-anchor="middle">${options.yLabel}</text>`;
  }

  function select(index) {
    const hit=data.hits[index];
    buttons.querySelectorAll('.pill').forEach((b,i)=>b.classList.toggle('active',i===index));
    chart(waveform,data.time_ms,hit.waveform,{title:`${hit.note} 中央センサ時間波形`,xMax:data.duration_ms,yMin:-1,yMax:1,xTicks:[0,80,160,240,320],yTicks:[-1,-0.5,0,0.5,1],yDigits:1,xLabel:'時間 [ms]',yLabel:'正規化Z加速度'});
    chart(fft,data.frequency_hz,hit.fft,{title:`${hit.note} 中央センサFFT`,xMax:1200,yMin:0,yMax:1,xTicks:[0,200,400,600,800,1000,1200],yTicks:[0,0.25,0.5,0.75,1],yDigits:2,xLabel:'周波数 [Hz]',yLabel:'正規化振幅'});
    if(readout) readout.textContent=`${hit.note} (${hit.x_mm}, ${hit.y_mm}) mm · 相対ピーク ${hit.peak_relative.toFixed(3)} · ${data.sample_rate_hz.toLocaleString()} Hz / ${data.sample_count.toLocaleString()}点`;
  }
  select(0);
})();
