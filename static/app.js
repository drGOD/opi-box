'use strict';

// =========================================================================
// State
// =========================================================================
let statusData = null;

// =========================================================================
// Tab navigation
// =========================================================================
document.querySelectorAll('.tab').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.tab-content').forEach(s => s.classList.remove('active'));
    btn.classList.add('active');
    document.getElementById('tab-' + btn.dataset.tab).classList.add('active');

    // Lazy-load data per tab
    if (btn.dataset.tab === 'timelapse') loadTimelapse();
    if (btn.dataset.tab === 'schedule')  loadSchedule();
    if (btn.dataset.tab === 'settings')  loadSettings();
    if (btn.dataset.tab === 'camera')    stopStream(); // reset stream on enter
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

// Load version once on startup
apiFetch('/api/version').then(v => {
  const el = document.getElementById('footer-version');
  if (el) el.textContent = v.commit;
}).catch(() => {});

// =========================================================================
// Relay cards
// =========================================================================
function relayIcon(name) {
  if (/свет/i.test(name))   return '💡';
  if (/вент/i.test(name))   return '💨';
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
        ${relay.mock ? '<div class="relay-mock">GPIO mock</div>' : ''}
      `;
      card.addEventListener('click', () => toggleRelay(relay.id));
      container.appendChild(card);
    }
    const on = relay.state;
    card.className = `card relay-card ${on ? 'on' : 'off'}`;
    card.querySelector('.relay-state').textContent = on ? 'ВКЛ' : 'ВЫКЛ';
  });
}

async function toggleRelay(id) {
  try {
    const data = await apiFetch(`/api/relay/${id}/toggle`, { method: 'POST', body: '{}' });
    // Optimistic update
    const card = document.getElementById(`relay-card-${id}`);
    if (card) {
      card.className = `card relay-card ${data.state ? 'on' : 'off'}`;
      card.querySelector('.relay-state').textContent = data.state ? 'ВКЛ' : 'ВЫКЛ';
    }
    showToast(`${data.name} ${data.state ? 'включён' : 'выключен'}`);
  } catch (e) {
    showToast('Ошибка управления реле', 'err');
  }
}

// =========================================================================
// Snapshot
// =========================================================================
function refreshSnapshot() {
  const img = document.getElementById('snapshot-img');
  img.src = `/api/snapshot?t=${Date.now()}`;
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
  const img = document.getElementById('stream-img');
  img.src = `/video_feed?t=${Date.now()}`;
}

function stopStream() {
  const img = document.getElementById('stream-img');
  img.src = '';
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
    if (!files.length) {
      empty.style.display = 'block';
      return;
    }
    empty.style.display = 'none';

    files.forEach(name => {
      const item = document.createElement('div');
      item.className = 'gallery-item';

      // Timestamp from filename: frame_YYYYMMDD_HHMMSS.jpg
      const m = name.match(/(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})/);
      const ts = m ? `${m[3]}.${m[2]}.${m[1]} ${m[4]}:${m[5]}` : name;

      item.innerHTML = `
        <img src="/api/timelapse/${name}" loading="lazy" alt="${ts}">
        <div class="gallery-ts">${ts}</div>
      `;
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
let scheduleData = [];

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

  // Map relay names from cached status
  const relayMap = {};
  if (statusData) statusData.relays.forEach(r => { relayMap[r.id] = r; });

  scheduleData.forEach((sched, i) => {
    const name = relayMap[sched.relay_id]?.name ?? `Реле ${sched.relay_id}`;
    const icon = relayIcon(name);
    const row = document.createElement('div');
    row.className = 'sched-row';
    row.innerHTML = `
      <div class="sched-label">${icon} ${name}</div>
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

  // Sync inputs back to scheduleData on change
  list.querySelectorAll('input').forEach(inp => {
    inp.addEventListener('change', () => {
      const idx = +inp.dataset.i;
      const key = inp.dataset.key;
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
    const s = await apiFetch('/api/settings');
    document.getElementById('s-tg-token').value      = s.telegram_token ?? '';
    document.getElementById('s-tg-chat').value       = s.telegram_chat_id ?? '';
    document.getElementById('s-tg-timelapse').checked = s.telegram_timelapse ?? true;
    document.getElementById('s-tl-enabled').checked   = s.timelapse_enabled ?? true;
    document.getElementById('s-tl-interval').value    = s.timelapse_interval_minutes ?? 30;
    document.getElementById('s-cam-device').value     = s.camera_device ?? 0;
    document.getElementById('s-gpio-chip').value      = s.gpio_chip ?? 'gpiochip0';
  } catch {
    showToast('Ошибка загрузки настроек', 'err');
  }
}

async function saveSettings() {
  const payload = {
    telegram_token:            document.getElementById('s-tg-token').value.trim(),
    telegram_chat_id:          document.getElementById('s-tg-chat').value.trim(),
    telegram_timelapse:        document.getElementById('s-tg-timelapse').checked,
    timelapse_enabled:         document.getElementById('s-tl-enabled').checked,
    timelapse_interval_minutes: +document.getElementById('s-tl-interval').value,
    camera_device:             +document.getElementById('s-cam-device').value,
    gpio_chip:                 document.getElementById('s-gpio-chip').value.trim(),
  };
  try {
    await apiFetch('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
    showToast('Настройки сохранены');
  } catch {
    showToast('Ошибка сохранения', 'err');
  }
}

async function testTelegram() {
  try {
    const r = await apiFetch('/api/telegram/test', { method: 'POST', body: '{}' });
    showToast(r.ok ? 'Сообщение отправлено!' : 'Ошибка отправки', r.ok ? 'ok' : 'err');
  } catch {
    showToast('Ошибка Telegram', 'err');
  }
}
