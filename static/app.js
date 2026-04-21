'use strict';

// =========================================================================
// State
// =========================================================================
let statusData     = null;
let scheduleData   = [];
let relaySettingsData = [];
let chartHours     = 24;
let charts         = {};
let chartsInited   = false;

// =========================================================================
// Tab navigation
// =========================================================================
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    if (btn.dataset.tab === 'timelapse') loadTimelapse();
    if (btn.dataset.tab === 'schedule')  loadSchedule();
    if (btn.dataset.tab === 'settings')  loadSettings();
    if (btn.dataset.tab === 'camera')    stopStream();
  });
});

// =========================================================================
// API helpers
// =========================================================================
async function apiFetch(url, opts = {}) {
  const r = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// =========================================================================
// Toast
// =========================================================================
let _toastTimer = null;
function showToast(msg, type = 'ok') {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = `toast show ${type}`;
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => { el.className = 'toast'; }, 3000);
}

// =========================================================================
// Status polling
// =========================================================================
async function pollStatus() {
  try {
    statusData = await apiFetch('/api/status');
    renderRelayCards(statusData.relays);
    renderAutoModeBanner(statusData);
    document.getElementById('system-time').textContent = statusData.time;
    const dot = document.getElementById('conn-dot');
    dot.className = 'dot dot-on';
    dot.title = 'Онлайн';
  } catch {
    const dot = document.getElementById('conn-dot');
    dot.className = 'dot dot-off';
    dot.title = 'Нет соединения';
  }
}

setInterval(pollStatus, 3000);
pollStatus();

apiFetch('/api/version').then(v => {
  const el = document.getElementById('footer-version');
  if (el) el.textContent = v.commit;
}).catch(() => {});

// =========================================================================
// Auto mode banner
// =========================================================================
function renderAutoModeBanner(status) {
  const banner = document.getElementById('manual-banner');
  if (status.auto_mode) {
    banner.style.display = 'none';
    return;
  }
  banner.style.display = '';
  const parts = [];
  (status.relays || []).forEach(r => {
    const exp = r.schedule_expected;
    if (exp != null && r.state !== exp) {
      parts.push(`${r.name}: по расписанию ${exp ? 'ВКЛ' : 'ВЫКЛ'}, сейчас ${r.state ? 'ВКЛ' : 'ВЫКЛ'}`);
    }
    if (r.humidity_controlled && r.humidity_expected != null && r.state !== r.humidity_expected) {
      parts.push(`${r.name}: по влажности ${r.humidity_expected ? 'ВКЛ' : 'ВЫКЛ'}, сейчас ${r.state ? 'ВКЛ' : 'ВЫКЛ'}`);
    }
  });
  document.getElementById('manual-banner-desc').textContent =
    parts.length ? parts.join(' · ') : 'Реле управляются вручную';
}

async function enableAutoMode() {
  try {
    await apiFetch('/api/auto_mode', { method: 'POST', body: '{}' });
    showToast('Автоматический режим включён');
    await pollStatus();
  } catch {
    showToast('Ошибка', 'err');
  }
}

// =========================================================================
// Sensors
// =========================================================================
async function pollSensors() {
  try {
    const res = await apiFetch('/api/sensors');
    const card = document.getElementById('sensor-card');
    if (!res.available) { card.style.display = 'none'; return; }
    card.style.display = '';
    renderSensorMetrics(res.data);
  } catch { /* silent */ }
}

function renderSensorMetrics(d) {
  const grid = document.getElementById('sensor-metrics');
  grid.innerHTML = '';

  const tile = (icon, label, value) => {
    const el = document.createElement('div');
    el.className = 'metric-tile';
    el.innerHTML = `<div class="metric-icon">${icon}</div>
                    <div class="metric-value">${value}</div>
                    <div class="metric-label">${label}</div>`;
    return el;
  };

  if (d.temperature  != null) grid.appendChild(tile('🌡', 'Температура',     `${d.temperature} °C`));
  if (d.air_humidity != null) grid.appendChild(tile('💧', 'Влажность возд.',  `${d.air_humidity} %`));
  if (Array.isArray(d.soil)) {
    d.soil.forEach(s => {
      const pct = s.moisture_pct;
      const bar = `<div class="soil-bar"><div class="soil-fill" style="width:${pct}%"></div></div>`;
      const el  = document.createElement('div');
      el.className = 'metric-tile metric-tile--wide';
      el.innerHTML = `<div class="metric-icon">🪴</div>
                      <div class="metric-value">${pct} %</div>
                      <div class="metric-label">Почва A${s.channel}</div>${bar}`;
      grid.appendChild(el);
    });
  }
}

setInterval(pollSensors, 15000);
pollSensors();

// =========================================================================
// Charts
// =========================================================================
const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { labels: { color: '#e2e8f0', font: { size: 11 }, boxWidth: 14 } },
    tooltip: { mode: 'index', intersect: false },
  },
  scales: {
    x: {
      type: 'time',
      time: { displayFormats: { minute: 'HH:mm', hour: 'HH:mm', day: 'dd.MM' } },
      ticks: { color: '#94a3b8', maxTicksLimit: 8, maxRotation: 0 },
      grid:  { color: '#2d3148' },
    },
  },
};

function yScale(color, position = 'left', opts = {}) {
  return {
    position,
    ticks: { color, maxTicksLimit: 6 },
    grid: position === 'left' ? { color: '#2d3148' } : { drawOnChartArea: false },
    ...opts,
  };
}

function initCharts() {
  if (chartsInited) return;
  chartsInited = true;

  // Air: temperature + humidity
  charts.air = new Chart(document.getElementById('chart-air'), {
    type: 'line',
    data: { datasets: [
      { label: 'Темп. °C',    data: [], borderColor: '#f97316', backgroundColor: 'transparent',
        yAxisID: 'y',  tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Влажн. возд. %', data: [], borderColor: '#38bdf8', backgroundColor: 'transparent',
        yAxisID: 'y2', tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]},
    options: { ...CHART_DEFAULTS, scales: { x: CHART_DEFAULTS.scales.x,
      y:  yScale('#f97316', 'left'),
      y2: yScale('#38bdf8', 'right'),
    }},
  });

  // Soil moisture
  charts.soil = new Chart(document.getElementById('chart-soil'), {
    type: 'line',
    data: { datasets: [
      { label: 'Почва A0 %', data: [], borderColor: '#86efac',
        backgroundColor: 'rgba(134,239,172,0.08)', fill: true,
        tension: 0.3, pointRadius: 0, borderWidth: 2 },
      { label: 'Почва A1 %', data: [], borderColor: '#22d3ee',
        backgroundColor: 'rgba(34,211,238,0.08)', fill: true,
        tension: 0.3, pointRadius: 0, borderWidth: 2 },
    ]},
    options: { ...CHART_DEFAULTS, scales: { x: CHART_DEFAULTS.scales.x,
      y: { ...yScale('#94a3b8', 'left'), min: 0, max: 100 },
    }},
  });

  // Relay states (step lines)
  charts.relays = new Chart(document.getElementById('chart-relays'), {
    type: 'line',
    data: { datasets: [
      { label: 'Реле 1', data: [], borderColor: '#fbbf24',
        backgroundColor: 'rgba(251,191,36,0.08)', fill: true,
        stepped: true, pointRadius: 0, borderWidth: 2 },
      { label: 'Реле 2', data: [], borderColor: '#60a5fa',
        backgroundColor: 'rgba(96,165,250,0.08)', fill: true,
        stepped: true, pointRadius: 0, borderWidth: 2 },
      { label: 'Реле 3', data: [], borderColor: '#34d399',
        backgroundColor: 'rgba(52,211,153,0.08)', fill: true,
        stepped: true, pointRadius: 0, borderWidth: 2 },
    ]},
    options: { ...CHART_DEFAULTS, scales: { x: CHART_DEFAULTS.scales.x,
      y: { ...yScale('#94a3b8', 'left'), min: 0, max: 1,
        ticks: { callback: v => v === 1 ? 'ВКЛ' : v === 0 ? 'ВЫКЛ' : '', stepSize: 1 } },
    }},
  });
}

async function loadCharts() {
  initCharts();
  try {
    const res = await apiFetch(`/api/history?hours=${chartHours}`);
    const s   = res.sensors;

    // Helper: map rows to {x, y}
    const pts = (col) => s.filter(r => r[col] != null).map(r => ({ x: r.ts * 1000, y: r[col] }));

    charts.air.data.datasets[0].data = pts('temperature');
    charts.air.data.datasets[1].data = pts('air_humidity');
    charts.air.update('none');

    charts.soil.data.datasets[0].data = pts('soil0_pct');
    charts.soil.data.datasets[1].data = pts('soil1_pct');
    charts.soil.update('none');

    // Relay charts
    const rd = res.relays || {};
    const relayKeys = statusData ? statusData.relays.map(r => String(r.id)) : Object.keys(rd);
    relayKeys.forEach((rid, i) => {
      if (!charts.relays.data.datasets[i]) return;
      charts.relays.data.datasets[i].data = (rd[rid] || []).map(e => ({ x: e.ts * 1000, y: e.state }));
      if (statusData) {
        const info = statusData.relays.find(r => String(r.id) === rid);
        if (info) charts.relays.data.datasets[i].label = info.name;
      }
    });
    charts.relays.update('none');

    // Show card only if there's data
    const hasData = s.length > 0 || Object.keys(rd).length > 0;
    document.getElementById('charts-card').style.display = hasData ? '' : 'none';
  } catch (e) {
    console.error('loadCharts error', e);
  }
}

// Chart range buttons
document.querySelectorAll('.chart-range-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.chart-range-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    chartHours = +btn.dataset.h;
    loadCharts();
  });
});

// Refresh charts periodically when on dashboard
setInterval(() => {
  if (document.getElementById('tab-dashboard').classList.contains('active')) {
    loadCharts();
  }
}, 60000);
loadCharts();

// =========================================================================
// Relay cards
// =========================================================================
function relayIcon(name) {
  if (/свет/i.test(name))   return '💡';
  if (/вент/i.test(name))   return '💨';
  if (/увлаж|humid/i.test(name)) return '💧';
  return '🔌';
}

function renderRelayCards(relays) {
  const container = document.getElementById('relay-cards');
  relays.forEach(relay => {
    let card = document.getElementById(`relay-card-${relay.id}`);
    if (!card) {
      card = document.createElement('div');
      card.id = `relay-card-${relay.id}`;
      card.className = 'card relay-card';
      card.innerHTML = `
        <div class="relay-icon">${relayIcon(relay.name)}</div>
        <div class="relay-name">${relay.name}</div>
        <div class="relay-state"></div>
        <div class="relay-mode-note"></div>
        ${relay.mock ? '<div class="relay-mock">GPIO mock</div>' : ''}
      `;
      card.addEventListener('click', () => toggleRelay(relay.id));
      container.appendChild(card);
    }
    const on = relay.state;
    card.className = `card relay-card ${on ? 'on' : 'off'}`;
    card.querySelector('.relay-state').textContent = on ? 'ВКЛ' : 'ВЫКЛ';
    card.querySelector('.relay-mode-note').textContent = relay.humidity_controlled ? 'Авто: по влажности' : '';
  });
}

async function toggleRelay(id) {
  try {
    const data = await apiFetch(`/api/relay/${id}/toggle`, { method: 'POST', body: '{}' });
    const card = document.getElementById(`relay-card-${id}`);
    if (card) {
      card.className = `card relay-card ${data.state ? 'on' : 'off'}`;
      card.querySelector('.relay-state').textContent = data.state ? 'ВКЛ' : 'ВЫКЛ';
    }
    showToast(`${data.name} ${data.state ? 'включён' : 'выключен'}`);
    await pollStatus(); // update banner
  } catch {
    showToast('Ошибка управления реле', 'err');
  }
}

// =========================================================================
// Snapshot
// =========================================================================
function refreshSnapshot() {
  document.getElementById('snapshot-img').src = `/api/snapshot?t=${Date.now()}`;
}
function downloadSnapshot() {
  const a = document.createElement('a');
  a.href = `/api/snapshot?t=${Date.now()}`;
  a.download = `growbox_${new Date().toISOString().slice(0,19).replace(/:/g,'-')}.jpg`;
  a.click();
}

// =========================================================================
// Camera stream
// =========================================================================
function startStream() {
  document.getElementById('stream-img').src = `/video_feed?t=${Date.now()}`;
}
function stopStream() {
  document.getElementById('stream-img').src = '';
}

// =========================================================================
// Timelapse gallery
// =========================================================================
async function loadTimelapse() {
  const gallery = document.getElementById('timelapse-gallery');
  const empty   = document.getElementById('timelapse-empty');
  gallery.innerHTML = '';
  try {
    const files = await apiFetch('/api/timelapse');
    if (!files.length) { empty.style.display = 'block'; return; }
    empty.style.display = 'none';
    files.forEach(name => {
      const item = document.createElement('div');
      item.className = 'gallery-item';
      const m  = name.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
      const ts = m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : name;
      item.innerHTML = `<img src="/api/timelapse/${name}" loading="lazy" alt="${ts}">
                        <div class="gallery-ts">${ts}</div>`;
      item.addEventListener('click', () => openLightbox(`/api/timelapse/${name}`));
      gallery.appendChild(item);
    });
  } catch {
    showToast('Ошибка загрузки таймлапса', 'err');
  }
}
function openLightbox(src) {
  document.getElementById('lightbox-img').src = src;
  document.getElementById('lightbox').classList.add('open');
}
function closeLightbox() {
  document.getElementById('lightbox').classList.remove('open');
  document.getElementById('lightbox-img').src = '';
}
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeLightbox(); });

// =========================================================================
// Schedule
// =========================================================================
async function loadSchedule() {
  try {
    scheduleData = await apiFetch('/api/schedule');
    renderSchedule();
  } catch {
    showToast('Ошибка загрузки расписания', 'err');
  }
}

function renderSchedule() {
  const list = document.getElementById('schedule-list');
  list.innerHTML = '';
  const relayMap = {};
  if (statusData) statusData.relays.forEach(r => { relayMap[r.id] = r; });

  scheduleData.forEach((sched, i) => {
    if (relayMap[sched.relay_id]?.humidity_controlled) return;
    const name = relayMap[sched.relay_id]?.name ?? `Реле ${sched.relay_id}`;
    const row  = document.createElement('div');
    row.className = 'sched-row';
    row.innerHTML = `
      <div class="sched-label">${relayIcon(name)} ${name}</div>
      <div class="form-group" style="margin:0">
        <label>Включить</label>
        <input type="time" data-i="${i}" data-key="on_time" value="${sched.on_time}">
      </div>
      <div class="form-group" style="margin:0">
        <label>Выключить</label>
        <input type="time" data-i="${i}" data-key="off_time" value="${sched.off_time}">
      </div>
      <div class="form-group sched-enabled" style="margin:0">
        <label>Активно</label>
        <input type="checkbox" data-i="${i}" data-key="enabled" ${sched.enabled ? 'checked' : ''}
               style="width:18px;height:18px;accent-color:var(--accent);margin-top:8px">
      </div>
    `;
    list.appendChild(row);
  });

  list.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = +inp.dataset.i, key = inp.dataset.key;
      scheduleData[idx][key] = inp.type === 'checkbox' ? inp.checked : inp.value;
    });
  });
}

async function saveSchedule() {
  try {
    await apiFetch('/api/schedule', { method: 'POST', body: JSON.stringify(scheduleData) });
    showToast('Расписание сохранено');
  } catch {
    showToast('Ошибка сохранения', 'err');
  }
}

// =========================================================================
// Settings
// =========================================================================
async function loadSettings() {
  try {
    const s  = await apiFetch('/api/settings');
    document.getElementById('s-tl-enabled').checked    = s.timelapse_enabled ?? true;
    document.getElementById('s-tl-interval').value     = s.timelapse_interval_minutes ?? 30;
    document.getElementById('s-cam-device').value      = s.camera_device ?? 0;
    document.getElementById('s-gpio-chip').value       = s.gpio_chip ?? 'gpiochip0';
    const hc = s.humidity_control ?? {};
    document.getElementById('s-hum-enabled').checked   = hc.enabled ?? false;
    document.getElementById('s-hum-target').value      = hc.target_humidity ?? 65;
    document.getElementById('s-hum-band').value        = hc.hysteresis ?? 6;
    document.getElementById('s-hum-min-switch').value  = hc.min_switch_interval_seconds ?? 180;
    const sc = s.sensors ?? {};
    document.getElementById('s-sens-enabled').checked  = sc.enabled ?? true;
    document.getElementById('s-sens-bus').value        = sc.i2c_bus ?? 2;
    document.getElementById('s-sens-interval').value   = sc.read_interval_seconds ?? 30;
    const dry = sc.soil_dry ?? [26000, 26000];
    const wet = sc.soil_wet ?? [13000, 13000];
    document.getElementById('s-soil0-dry').value = dry[0];
    document.getElementById('s-soil0-wet').value = wet[0];
    document.getElementById('s-soil1-dry').value = dry[1];
    document.getElementById('s-soil1-wet').value = wet[1];
    renderRelaySettings(s.relays ?? []);
    renderHumidityRelayOptions(s.relays ?? [], hc.relay_id ?? 3);
    const cv = s.climate_ventilation ?? {};
    document.getElementById('s-cv-enabled').checked    = cv.enabled ?? false;
    document.getElementById('s-cv-max-hum').value      = cv.max_humidity ?? 80;
    document.getElementById('s-cv-min-hum').value      = cv.min_humidity ?? 40;
    document.getElementById('s-cv-max-temp').value     = cv.max_temperature ?? 35;
    document.getElementById('s-cv-min-temp').value     = cv.min_temperature ?? 18;
    document.getElementById('s-cv-min-switch').value   = cv.min_switch_interval_seconds ?? 180;
    renderClimateVentRelayOptions(s.relays ?? [], cv.relay_id ?? 2);
  } catch {
    showToast('Ошибка загрузки настроек', 'err');
  }
}

function renderHumidityRelayOptions(relays, selectedId) {
  const select = document.getElementById('s-hum-relay');
  select.innerHTML = '';
  relays.forEach(relay => {
    const option = document.createElement('option');
    option.value = relay.id;
    option.textContent = `${relay.id}: ${relay.name}`;
    select.appendChild(option);
  });
  if (selectedId != null) {
    select.value = String(selectedId);
  }
}

// Relay GPIO settings
function renderRelaySettings(relays) {
  relaySettingsData = relays.map(r => ({ ...r }));
  const list = document.getElementById('s-relays-list');
  list.innerHTML = '';

  relaySettingsData.forEach((r, i) => {
    const row = document.createElement('div');
    row.className = 'sched-row';
    row.innerHTML = `
      <div class="sched-label">${relayIcon(r.name)} Реле ${r.id}</div>
      <div class="form-group" style="margin:0">
        <label>Название</label>
        <input type="text" data-ri="${i}" data-rk="name" value="${r.name}">
      </div>
      <div class="form-group" style="margin:0">
        <label>GPIO пин</label>
        <input type="number" data-ri="${i}" data-rk="gpio_pin" value="${r.gpio_pin}" min="0" max="255" style="width:70px">
      </div>
      <div class="form-group sched-enabled" style="margin:0">
        <label>Инверт.</label>
        <input type="checkbox" data-ri="${i}" data-rk="active_low" ${r.active_low ? 'checked' : ''}
               style="width:18px;height:18px;accent-color:var(--accent);margin-top:8px">
      </div>
    `;
    list.appendChild(row);
  });

  list.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = +inp.dataset.ri, key = inp.dataset.rk;
      relaySettingsData[idx][key] = inp.type === 'checkbox' ? inp.checked
                                  : inp.type === 'number'   ? +inp.value
                                  : inp.value;
    });
  });
}

async function saveRelaySettings() {
  try {
    await apiFetch('/api/relays', { method: 'POST', body: JSON.stringify(relaySettingsData) });
    showToast('Настройки реле сохранены');
    pollStatus();
  } catch {
    showToast('Ошибка сохранения реле', 'err');
  }
}

async function saveSettings() {
  const payload = {
    timelapse_enabled:          document.getElementById('s-tl-enabled').checked,
    timelapse_interval_minutes: +document.getElementById('s-tl-interval').value,
    camera_device:              +document.getElementById('s-cam-device').value,
    gpio_chip:                  document.getElementById('s-gpio-chip').value.trim(),
    humidity_control: {
      enabled:                     document.getElementById('s-hum-enabled').checked,
      relay_id:                    +document.getElementById('s-hum-relay').value,
      target_humidity:             +document.getElementById('s-hum-target').value,
      hysteresis:                  +document.getElementById('s-hum-band').value,
      min_switch_interval_seconds: +document.getElementById('s-hum-min-switch').value,
    },
    sensors: {
      enabled:               document.getElementById('s-sens-enabled').checked,
      i2c_bus:               +document.getElementById('s-sens-bus').value,
      read_interval_seconds: +document.getElementById('s-sens-interval').value,
      soil_dry: [+document.getElementById('s-soil0-dry').value,
                 +document.getElementById('s-soil1-dry').value],
      soil_wet: [+document.getElementById('s-soil0-wet').value,
                 +document.getElementById('s-soil1-wet').value],
    },
    climate_ventilation: {
      enabled:                     document.getElementById('s-cv-enabled').checked,
      relay_id:                    +document.getElementById('s-cv-relay').value,
      max_humidity:                +document.getElementById('s-cv-max-hum').value,
      min_humidity:                +document.getElementById('s-cv-min-hum').value,
      max_temperature:             +document.getElementById('s-cv-max-temp').value,
      min_temperature:             +document.getElementById('s-cv-min-temp').value,
      min_switch_interval_seconds: +document.getElementById('s-cv-min-switch').value,
    },
  };
  try {
    await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Настройки сохранены');
  } catch {
    showToast('Ошибка сохранения', 'err');
  }
}

function renderClimateVentRelayOptions(relays, selectedId) {
  const select = document.getElementById('s-cv-relay');
  select.innerHTML = '';
  relays.forEach(relay => {
    const option = document.createElement('option');
    option.value = relay.id;
    option.textContent = `${relay.id}: ${relay.name}`;
    select.appendChild(option);
  });
  if (selectedId != null) {
    select.value = String(selectedId);
  }
}
