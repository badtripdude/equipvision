// EquipVision client: camera -> server -> highlighted areas over the video, diagnosis, telemetry,
// equipment card, passport. Plain modules, no build step; the look is Material Design 3 (styles.css).

const $ = (id) => document.getElementById(id);
const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const SVG = 'http://www.w3.org/2000/svg';
const icon = (name, className = 'icon') => {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('class', className);
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(SVG, 'use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
};
const num = (value, digits = 0) =>
  value.toLocaleString('ru-RU', { minimumFractionDigits: digits, maximumFractionDigits: digits });

/** A Material list item: leading icon, headline, supporting text, trailing text or icon. */
function listItem({ lead, tone, headline, supporting, trailing, chevron, photo, onClick }) {
  const item = el(onClick ? 'button' : 'li', `list-item${onClick ? ' interactive' : ''}`);
  if (onClick) {
    item.type = 'button';
    item.addEventListener('click', onClick);
  }
  if (lead) {
    const leading = el('span', `leading ${tone || ''}`);
    leading.append(icon(lead));
    item.append(leading);
  }
  const content = el('span', 'content');
  content.append(el('span', 'headline', headline));
  for (const line of [].concat(supporting || [])) content.append(el('span', 'supporting body-small', line));
  item.append(content);
  if (photo) {
    const thumb = el('img', 'thumb');
    thumb.src = `/api/photos/${photo}`;
    thumb.alt = 'Фото дефекта';
    thumb.loading = 'lazy';
    item.append(thumb);
  }
  if (trailing || chevron) {
    const tail = el('span', 'trailing', trailing || '');
    if (chevron) tail.append(icon('chevron_right'));
    item.append(tail);
  }
  return item;
}

let snackTimer = 0;
function snack(text) {
  const bar = $('snackbar');
  bar.textContent = text;
  bar.classList.add('show');
  clearTimeout(snackTimer);
  snackTimer = setTimeout(() => bar.classList.remove('show'), 4000);
}

let user = null;                      // signed-in user: {username, name, role, role_title, can: {...}}
// Trainee mode: a guided walk through the components; `result` is set once the run is scored.
const training = { active: false, steps: [], index: 0, answers: {}, result: null };
const guiding = () => training.active && !training.result;
let signTitles = {};
const PHOTO_SIGN = 'photo';          // a finding made with the shutter button: the frame itself is the record

const api = async (path, body) => {
  const res = await fetch(path, body === undefined ? undefined : {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  });
  if (res.status === 401 && user) signedOut();          // the session expired or was closed elsewhere
  if (!res.ok) {
    const detail = await res.json().then((data) => data.detail, () => null);
    throw new Error(typeof detail === 'string' ? detail : `${path}: ${res.status}`);
  }
  return res.json();
};

/** Runs an action started by a button; a failure is shown instead of being lost. */
const act = (fn) => async (event) => {
  try {
    await fn(event);
  } catch (err) {
    snack(err.message);
  }
};

// Development only: ?src=test replaces the camera with a generated picture, so the
// pipeline can be checked on a machine without a camera. Not a photo upload.
// ?src=photo&n=3 (development server only) shows one of the annotated example photos
// with a slow zoom, to check the model without a camera.
const QUERY = new URLSearchParams(location.search);
// ?src=label&label=PCP-3[&qr=1] draws that unit's nameplate (or its QR code) to check identification.
const TEST_SOURCE = ['test', 'photo', 'label'].includes(QUERY.get('src'));

const STATUS_TEXT = { ok: 'норма', warn: 'предупреждение', alarm: 'тревога' };
const LEVEL_ICON = { ok: 'check_circle', warn: 'warning', alarm: 'error' };

// ---------- camera ----------

const video = $('video');
const overlay = $('overlay');

function stageMessage(text, showButton = true) {
  $('stage-text').textContent = text;
  $('camera-button').hidden = !showButton;
  $('stage-message').hidden = false;
}

async function makePhotoStream() {
  const photo = new Image();
  photo.src = `/api/dev/photo/${Number(QUERY.get('n')) || 0}`;
  await photo.decode();
  const canvas = document.createElement('canvas');
  canvas.width = 640; canvas.height = 480;
  const ctx = canvas.getContext('2d');
  const fit = Math.min(640 / photo.width, 480 / photo.height);
  let t = 0;
  const draw = () => {
    t += 1;
    const zoom = fit * (1.08 + 0.08 * Math.sin(t / 40));              // slow breathing zoom, like a hand-held phone
    const w = photo.width * zoom, h = photo.height * zoom;
    ctx.fillStyle = '#20252b'; ctx.fillRect(0, 0, 640, 480);
    ctx.drawImage(photo, (640 - w) / 2 + 6 * Math.sin(t / 23), (480 - h) / 2 + 4 * Math.cos(t / 31), w, h);
    ctx.fillStyle = 'rgba(0,0,0,.6)'; ctx.fillRect(0, 0, 640, 22);
    ctx.fillStyle = '#fff'; ctx.font = '13px sans-serif';
    ctx.fillText('ТЕСТОВЫЙ ИСТОЧНИК: фото из примеров (не камера)', 8, 15);
  };
  draw();
  setInterval(draw, 66);
  return canvas.captureStream(15);
}

async function makeLabelStream() {
  const units = await api('/api/equipment');
  const unit = units.find((u) => u.id === QUERY.get('label')) || units[0];
  const qr = new Image();
  if (QUERY.get('qr')) {
    qr.src = `/api/equipment/${encodeURIComponent(unit.id)}/qr.png`;
    await qr.decode();
  }
  const canvas = document.createElement('canvas');
  canvas.width = 1280; canvas.height = 720;
  const ctx = canvas.getContext('2d');
  let t = 0;
  const draw = () => {
    t += 1;
    ctx.fillStyle = '#4a545e'; ctx.fillRect(0, 0, 1280, 720);
    const x = 330 + 10 * Math.sin(t / 25), y = 200 + 6 * Math.cos(t / 33);
    ctx.fillStyle = '#c9cdd1'; ctx.fillRect(x, y, 620, 300);
    if (QUERY.get('qr')) {
      ctx.drawImage(qr, x + 180, y + 20, 260, 260);
    } else {
      ctx.fillStyle = '#1a1a1a'; ctx.font = '34px Arial, sans-serif';
      ctx.fillText(unit.model.slice(0, 30), x + 30, y + 70);
      ctx.fillText(`S/N ${unit.serial}`, x + 30, y + 140);
      ctx.fillText('MADE IN RUSSIA', x + 30, y + 210);
    }
    ctx.fillStyle = '#fff'; ctx.font = '18px sans-serif';
    ctx.fillText('ТЕСТОВЫЙ ИСТОЧНИК: нарисованный шильдик (не камера)', 16, 28);
  };
  draw();
  setInterval(draw, 66);
  return canvas.captureStream(15);
}

function makeTestStream() {
  const canvas = document.createElement('canvas');
  canvas.width = 640; canvas.height = 480;
  const ctx = canvas.getContext('2d');
  let t = 0;
  const draw = () => {
    t += 1;
    ctx.fillStyle = '#6f7a85'; ctx.fillRect(0, 0, 640, 480);
    ctx.fillStyle = '#3f4a55'; ctx.fillRect(0, 200, 640, 90);          // "pipe"
    ctx.fillStyle = '#56616c'; ctx.fillRect(300, 170, 40, 150);         // "flange"
    const x = 320 + Math.sin(t / 25) * 200;
    ctx.fillStyle = '#b5541c';                                         // rust-coloured patch
    ctx.beginPath(); ctx.ellipse(x, 245, 60, 35, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = '#fff'; ctx.font = '16px sans-serif';
    ctx.fillText('ТЕСТОВЫЙ ИСТОЧНИК (не камера)', 16, 28);
  };
  draw();
  setInterval(draw, 66); // not requestAnimationFrame: must keep running in a background tab
  return canvas.captureStream(15);
}

async function startCamera() {
  $('stage-message').hidden = true;
  try {
    if (TEST_SOURCE) {
      const source = QUERY.get('src');
      video.srcObject = source === 'photo' ? await makePhotoStream()
        : source === 'label' ? await makeLabelStream() : makeTestStream();
    } else {
      if (!navigator.mediaDevices?.getUserMedia) {
        stageMessage(window.isSecureContext
          ? 'Этот браузер не даёт доступ к камере.'
          : 'Камера доступна только по HTTPS. Откройте адрес сервера, начинающийся с https://', false);
        return;
      }
      video.srcObject = await navigator.mediaDevices.getUserMedia({
        audio: false,
        video: { facingMode: { ideal: 'environment' }, width: { ideal: 1280 }, height: { ideal: 720 } },
      });
    }
    await video.play();
  } catch (err) {
    const reason = {
      NotAllowedError: 'Доступ к камере запрещён. Разрешите его в настройках сайта и нажмите кнопку.',
      NotFoundError: 'Камера не найдена на этом устройстве.',
      NotReadableError: 'Камера занята другим приложением.',
    }[err.name] || `Не удалось включить камеру: ${err.message}`;
    stageMessage(reason);
  }
}
$('camera-button').addEventListener('click', startCamera);

// ---------- frames to the server ----------

const FRAME_MAX_SIDE = 640;
const FRAME_INTERVAL_MS = 200;      // at most 5 frames per second
const ANSWER_TIMEOUT_MS = 3000;

const grab = document.createElement('canvas');
let socket = null;
let awaiting = false;
let sentAt = 0;
let detections = [];
let answers = [];                   // timestamps of recent answers, for the fps figure

function setConnected(on) {
  $('conn').classList.toggle('on', on);
  $('conn-text').textContent = on ? 'на связи' : 'нет связи';
}

function setStats(text) {
  $('stats').textContent = text;
  $('stats').hidden = !text;
}

function connect() {
  const scheme = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${scheme}://${location.host}/ws/vision`);
  socket.onopen = () => {
    setConnected(true);
    sendFocus();
  };
  socket.onclose = () => {
    setConnected(false);
    awaiting = false;
    detections = [];
    drawOverlay();
    if (user) setTimeout(connect, 1000);        // signed out: stay disconnected
  };
  socket.onmessage = (event) => {
    const msg = JSON.parse(event.data);
    awaiting = false;
    const now = performance.now();
    answers = answers.filter((t) => now - t < 2000).concat(now);
    if (msg.error) {
      setStats(msg.error);
      return;
    }
    detections = msg.detections;
    drawOverlay();
    showVerdict(msg);
    if (msg.recorded.length) pollDiagnosis();   // a new finding was stored: show its diagnosis at once
    setStats(`${num(answers.length / 2, 1)} кадр/с · ${Math.round(now - sentAt)} мс`);
  };
}

function sendFrame() {
  if (!equipmentId || !socket || socket.readyState !== WebSocket.OPEN) return;
  if (!video.offsetParent) return;              // the camera is not on screen (another tab on a phone)
  const now = performance.now();
  if (awaiting && now - sentAt < ANSWER_TIMEOUT_MS) return;
  if (video.readyState < 2 || !video.videoWidth) return;
  const scale = Math.min(1, FRAME_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
  grab.width = Math.round(video.videoWidth * scale);
  grab.height = Math.round(video.videoHeight * scale);
  grab.getContext('2d').drawImage(video, 0, 0, grab.width, grab.height);
  awaiting = true;
  sentAt = now;
  grab.toBlob((blob) => {
    if (blob && socket.readyState === WebSocket.OPEN) socket.send(blob);
    else awaiting = false;
  }, 'image/jpeg', 0.7);
}

// ---------- overlay ----------

// Low confidence still gets shown: missing a defect is worse than a false alarm.
const levelOf = (score) => (score >= 0.6 ? 'alarm' : 'warn');
const SIGN_COLOR = { corrosion: '#ff9f1a', leak: '#29b6f6', overheat: '#ff5252' };

function drawOverlay() {
  const dpr = window.devicePixelRatio || 1;
  const w = video.clientWidth, h = video.clientHeight;
  if (overlay.width !== Math.round(w * dpr) || overlay.height !== Math.round(h * dpr)) {
    overlay.width = Math.round(w * dpr);
    overlay.height = Math.round(h * dpr);
  }
  const ctx = overlay.getContext('2d');
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  ctx.font = '500 13px Roboto, system-ui, sans-serif';
  ctx.textBaseline = 'middle';
  if (guiding()) {
    drawTrainingHint(ctx, w, h);               // the trainee looks first; the system's findings come at the end
    return;
  }

  // First the highlighted area of every sign, then outlines and labels on top.
  for (const d of detections) {
    if (!d.cells || d.unusual) continue;
    const [cols, rows] = d.grid;
    ctx.fillStyle = SIGN_COLOR[d.label] || '#ff00ff';
    ctx.globalAlpha = 0.32;
    for (const [cx, cy] of d.cells) ctx.fillRect((cx / cols) * w, (cy / rows) * h, w / cols + 0.5, h / rows + 0.5);
  }
  ctx.globalAlpha = 1;
  for (const d of detections) {
    const [x1, y1, x2, y2] = d.box;
    // Grey and dotted: a sign that does not belong on the inspected component; it is not recorded.
    const color = d.unusual ? '#c5ccd3' : SIGN_COLOR[d.label] || '#ff00ff';
    const x = x1 * w, y = y1 * h, bw = (x2 - x1) * w, bh = (y2 - y1) * h;
    ctx.lineWidth = !d.unusual && levelOf(d.score) === 'alarm' ? 3 : 2;
    ctx.setLineDash(d.unusual ? [2, 4] : levelOf(d.score) === 'alarm' ? [] : [6, 4]);   // dashed: low confidence
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.roundRect(x, y, bw, bh, 8);
    ctx.stroke();
    ctx.setLineDash([]);
    const text = d.unusual ? `${d.title}? не характерно для узла` : `${d.title} · ${Math.round(d.score * 100)}%`;
    const tw = Math.min(ctx.measureText(text).width + 16, w);
    const tx = Math.max(0, Math.min(x, w - tw));
    const ty = y >= 28 ? y - 26 : y + 2;
    ctx.fillStyle = color;
    ctx.beginPath();
    ctx.roundRect(tx, ty, tw, 22, 8);
    ctx.fill();
    ctx.fillStyle = '#111';
    ctx.fillText(text, tx + 8, ty + 11.5, tw - 16);
  }
}
new ResizeObserver(drawOverlay).observe(video);

function setVerdict(text, level = '') {
  $('verdict').textContent = text;
  $('verdict').className = `chip status ${level}`;
}

function showVerdict(msg) {
  if (guiding()) {
    setVerdict('Режим обучения: что нашла система, покажем в конце');
    return;
  }
  const suffix = msg.stub ? ' (заглушка)' : '';
  if (!msg.detections.length) {
    // Deliberately not "исправно": no visible signs is not proof of a healthy unit.
    setVerdict('Видимых проблем в кадре не обнаружено' + suffix, 'ok');
    return;
  }
  const usual = msg.detections.filter((d) => !d.unusual);
  if (!usual.length) {
    setVerdict('Признак, не характерный для узла: проверьте и отметьте вручную', 'warn');
    return;
  }
  const worst = usual.some((d) => levelOf(d.score) === 'alarm') ? 'alarm' : 'warn';
  setVerdict((worst === 'alarm' ? 'Признаки возможного дефекта' : 'Рекомендуется проверка') + suffix, worst);
}

// ---------- telemetry ----------

const DIGITS = { rpm: 0, vib_rms: 2, vib_1x: 2, vib_2x: 2, vib_hf: 2, temp_bearing: 1, temp_winding: 1 };
let paused = false;

function renderTelemetry(snap) {
  paused = snap.paused;
  $('tm-pause-icon').setAttribute('href', paused ? '#i-play' : '#i-pause');
  $('tm-pause').setAttribute('aria-label', paused ? 'Продолжить' : 'Пауза');
  $('tm-time').textContent = `${snap.ts} · запись ${snap.index + 1} из ${snap.total}` +
    (snap.running ? '' : ' · остановлен') + (paused ? ' · пауза' : '');
  $('tm-state').textContent = STATUS_TEXT[snap.status];
  $('tm-state').className = `chip status ${snap.status}`;
  $('badge-sensors').hidden = snap.status === 'ok' || !lastCard?.has_telemetry;

  const tiles = $('tiles');
  tiles.replaceChildren();
  for (const [tag, item] of Object.entries(snap.values)) {
    const tile = el('div', `tile ${item.status || ''}`);
    tile.append(el('div', 'label-medium', item.title));
    const value = el('div', 'tile-value', num(item.value, DIGITS[tag] ?? 1) + ' ');
    value.append(el('small', '', item.unit));
    tile.append(value);
    tile.append(el('div', 'body-small', item.warn != null
      ? `уставки ${num(item.warn, 1)} / ${num(item.alarm, 1)}` : 'без уставок'));
    tiles.append(tile);
  }
}

async function pollTelemetry() {
  if (!user) return;
  try {
    renderTelemetry(await api('/api/telemetry/current'));
  } catch {
    $('tm-time').textContent = 'нет данных от сервера';
  }
}

async function control(body) {
  renderTelemetry(await api('/api/telemetry/control', body));
  await refreshCard();
  pollDiagnosis();
}

async function loadEpisodes() {
  const box = $('episodes');
  box.replaceChildren();
  for (const ep of await api('/api/telemetry/episodes')) {
    const alarms = ep.labels.filter((l) => l.startsWith('Тревога')).map((l) => l.replace('Тревога: ', ''));
    const [, month, day] = ep.start_ts.slice(0, 10).split('-');
    const chip = el('button', 'chip assist', `${day}.${month} · ${(alarms.length ? alarms : ep.labels).join(' + ')}`);
    chip.type = 'button';
    // An episode lasts well under a minute of replay, so jumping to it also pauses the replay there.
    chip.addEventListener('click', act(async () => {
      await control({ index: ep.alarm, paused: true });
      snack(`Эпизод ${day}.${month}: запись остановлена на тревоге`);
    }));
    box.append(chip);
  }
}
$('tm-pause').addEventListener('click', act(() => control({ paused: !paused })));
$('tm-start').addEventListener('click', act(() => control({ index: 0, paused: false })));

// ---------- inspection focus and diagnosis ----------

let equipmentId = QUERY.get('eq') || null;      // ?eq=P-101 skips identification (development)
let lastCard = null;
let lastDiagnosis = null;
let lastDiagnosisJson = '';

const focusSelect = $('focus');
const DEFAULT_HINT = 'Находки камеры записываются на выбранный узел.';

function sendFocus() {
  $('focus-hint').textContent = DEFAULT_HINT;
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  socket.send(JSON.stringify({ type: 'focus', equipment_id: equipmentId, component: focusSelect.value || null }));
}
focusSelect.addEventListener('change', sendFocus);

function setFocus(code) {
  focusSelect.value = code;
  sendFocus();
  setTab('inspect');
  $('video').scrollIntoView({ block: 'center', behavior: 'smooth' });
  snack(`Осматриваю узел: ${focusSelect.selectedOptions[0].textContent}`);
}

function needFocus(message) {
  if (focusSelect.value) return true;
  $('focus-hint').textContent = message;
  focusSelect.focus();
  snack(message);
  return false;
}

const LEVEL_SHORT = { warn: 'Рекомендуется проверка', alarm: 'Признаки возможного дефекта' };
const diagnosisOpen = new Map();     // which diagnoses the engineer unfolded or folded: id -> open

function renderDiagnosis(dg) {
  const level = dg.diagnoses.length || dg.status !== 'ok' ? dg.status : '';
  $('dg-banner').className = `banner ${level}`;
  $('dg-icon').setAttribute('href', `#i-${LEVEL_ICON[level] || 'info'}`);
  $('dg-status').textContent = dg.status_text;
  $('dg-sub').textContent = dg.diagnoses.length
    ? `Замечаний: ${dg.diagnoses.length}` + (dg.telemetry ? ` · данные датчиков на ${dg.ts}` : '')
    : 'Выберите узел и наведите на него камеру.';

  const badge = $('badge-inspect');
  badge.hidden = dg.diagnoses.length === 0;
  badge.textContent = dg.diagnoses.length;
  $('badge-parts').hidden = !Object.values(dg.components).some((c) => c.level !== 'ok');

  $('guidance').hidden = dg.guidance.length === 0;
  const guide = $('guidance-list');
  guide.replaceChildren();
  for (const g of dg.guidance) {
    guide.append(listItem({ headline: g.name, supporting: g.why, chevron: true, onClick: () => setFocus(g.component) }));
  }

  const names = Object.fromEntries((lastCard?.components || []).map((c) => [c.code, c.name]));
  const box = $('diagnoses');
  box.replaceChildren();
  dg.diagnoses.forEach((d, index) => {
    // Folded to a heading so that the screen stays short; the first, most serious one is open.
    const card = el('details', `card outlined flush fold diagnosis ${d.level}`);
    card.open = diagnosisOpen.has(d.id) ? diagnosisOpen.get(d.id) : index === 0;
    card.addEventListener('toggle', () => diagnosisOpen.set(d.id, card.open));
    const head = el('summary', 'diagnosis-head');
    const titles = el('div');
    titles.append(el('div', 'title-medium', d.title), el('div', 'body-small muted',
      `${LEVEL_SHORT[d.level]} · ${d.component ? names[d.component] || d.component : 'агрегат в целом'}`));
    head.append(icon(LEVEL_ICON[d.level]), titles);
    const body = el('div', 'fold-body diagnosis-body');
    const reasons = el('ul', 'evidence');
    for (const text of d.evidence) reasons.append(el('li', '', text));
    const advice = el('div', 'recommendation');
    advice.append(el('span', 'label-medium', 'Что делать'), el('span', '', d.recommendation));
    body.append(el('p', '', d.diagnosis), reasons, advice, el('p', 'body-small muted', `Основание: ${d.basis}`));
    card.append(head, body);
    box.append(card);
  });

  const found = $('findings');
  found.replaceChildren();
  $('findings-card').hidden = dg.findings.length === 0;
  $('findings-count').textContent = ` · ${dg.findings.length}`;
  for (const f of dg.findings) {
    const how = f.source === 'manual' ? 'отмечено вручную' : `камера, уверенность ${Math.round((f.score || 0) * 100)}%`;
    const title = `${f.sign_title[0].toUpperCase()}${f.sign_title.slice(1)} · ${f.component_name}`;
    const row = el('div', 'finding-row');
    const remove = el('button', 'icon-button');
    remove.type = 'button';
    remove.setAttribute('aria-label', `Удалить: ${title}`);
    remove.append(icon('delete'));
    remove.addEventListener('click', act(async () => {
      if (!window.confirm(`Удалить из осмотра «${title}»?`)) return;
      lastDiagnosisJson = '';
      renderDiagnosis(await post('findings/remove', { component: f.component, sign: f.sign }));
      snack('Дефект удалён из осмотра. Камера не запишет его снова до нового осмотра.');
    }));
    row.append(listItem({
      lead: 'warning', tone: 'warn', headline: title,
      supporting: how + (f.photo ? ' · фото сохранено' : ' · без фото'), photo: f.photo,
      onClick: f.photo ? () => openPhoto(f.photo, `${f.sign_title} · ${f.component_name}`) : undefined,
    }), remove);
    found.append(row);
  }
  renderTraining();
}

async function pollDiagnosis() {
  if (!user || !equipmentId) return;
  try {
    const dg = await api(`/api/equipment/${encodeURIComponent(equipmentId)}/diagnosis`);
    const json = JSON.stringify([dg.status, dg.diagnoses, dg.guidance, dg.components, dg.findings]);
    lastDiagnosis = dg;
    if (json === lastDiagnosisJson) return;      // nothing changed: do not rebuild the DOM every second
    lastDiagnosisJson = json;
    renderDiagnosis(dg);
    if (lastCard) renderCard(lastCard);
  } catch {
    $('dg-status').textContent = 'Нет данных от сервера';
  }
}

const post = (path, body = {}) => api(`/api/equipment/${encodeURIComponent(equipmentId)}/${path}`, body);

$('manual-add').addEventListener('click', act(async () => {
  if (!needFocus('Сначала выберите узел, на который записать признак.')) return;
  const sign = $('manual-sign');
  await post('findings', { component: focusSelect.value, sign: sign.value, frame: currentFrame() });
  snack(`Записано: ${sign.selectedOptions[0].textContent} — ${focusSelect.selectedOptions[0].textContent}`);
  pollDiagnosis();
}));
// The shutter: the engineer takes the frame on purpose, and it goes to the findings with its photo.
$('shutter').addEventListener('click', act(async () => {
  if (!needFocus('Сначала выберите узел, к которому относится фото.')) return;
  const frame = currentFrame();
  if (!frame) {
    snack('Камера ещё не готова');
    return;
  }
  lastDiagnosisJson = '';
  renderDiagnosis(await post('findings', { component: focusSelect.value, sign: PHOTO_SIGN, frame }));
  snack(`Фото сохранено в дефекты: ${focusSelect.selectedOptions[0].textContent}`);
}));

$('insp-save').addEventListener('click', act(async () => {
  const saved = await post('inspection/complete');
  focusSelect.value = '';          // the saved inspection is closed: stop recording onto its last component
  sendFocus();
  leaveTraining();
  lastDiagnosisJson = '';
  if (saved.on_route) {            // on a round the engineer goes on to the next unit
    closeEquipment();
    snack(saved.next ? `Осмотр сохранён. Следующий по маршруту: ${saved.next}` : 'Осмотр сохранён. Все агрегаты маршрута пройдены');
    return;
  }
  await refreshCard();
  loadUnitStats();
  pollDiagnosis();
  snack(`Осмотр сохранён в историю. Находок: ${saved.saved_findings}`);
}));
$('insp-reset').addEventListener('click', act(async () => {
  await post('inspection/reset');
  focusSelect.value = '';
  sendFocus();
  leaveTraining();
  pollDiagnosis();
  snack('Начат новый осмотр: находки сброшены');
}));

async function registerWork(event, message) {
  await post('events', event);
  lastDiagnosisJson = '';
  await refreshCard();
  pollDiagnosis();
  snack(message);
}
$('work-maintenance').addEventListener('click', act(() =>
  registerWork({ kind: 'maintenance', note: 'Техническое обслуживание по регламенту' }, 'ТО записано в историю')));
$('work-replacement').addEventListener('click', act(async () => {
  if (!needFocus('Сначала выберите узел, который заменён.')) return;
  const component = lastCard.components.find((c) => c.code === focusSelect.value);
  await registerWork({ kind: 'replacement', component_id: component.id, note: `Замена узла: ${component.name}` },
    `Замена записана: ${component.name}. Счётчик ресурса узла обнулён`);
}));

async function loadSigns() {
  const select = $('manual-sign');
  select.replaceChildren();
  signTitles = await api('/api/signs');
  for (const [code, title] of Object.entries(signTitles)) {
    if (code === PHOTO_SIGN) continue;
    const option = el('option', '', title);
    option.value = code;
    select.append(option);
  }
}

// ---------- passport and documentation ----------

let specsJson = '';

function renderSpecs(sections) {
  const json = JSON.stringify(sections);
  if (json === specsJson) return;              // the card is refreshed every few seconds: keep opened sections open
  specsJson = json;
  const box = $('specs');
  box.replaceChildren();
  let first = true;
  for (const [title, fields] of Object.entries(sections)) {
    const block = el('details', 'card flush');
    block.open = first;
    first = false;
    block.append(el('summary', '', title));
    const list = el('dl', 'specs');
    for (const [key, value] of Object.entries(fields)) list.append(el('dt', '', key), el('dd', '', value));
    block.append(list);
    box.append(block);
  }
}

async function loadDocuments() {
  const box = $('docs');
  box.replaceChildren();
  const data = await api(`/api/equipment/${encodeURIComponent(equipmentId)}/documents`);
  $('docs-note-box').hidden = !data.note;
  $('docs-note').textContent = data.note || '';
  let group = '';
  for (const doc of data.documents) {
    if (doc.group !== group) {
      group = doc.group;
      box.append(el('div', 'overline', group));
    }
    box.append(listItem({ lead: 'description', headline: doc.title, trailing: `${doc.pages} стр.`, chevron: true,
      onClick: () => openViewer(doc) }));
  }
}

const viewer = { doc: null, page: 1, rotate: 0 };

function openPhoto(name, title) {
  viewer.doc = null;
  $('viewer-title').textContent = title;
  $('viewer-page').textContent = 'Фото дефекта';
  for (const id of ['viewer-prev', 'viewer-next', 'viewer-rotate']) $(id).hidden = true;
  $('viewer-image').classList.remove('zoomed');
  $('viewer-image').src = `/api/photos/${name}`;
  $('viewer').showModal();
}

function showPage() {
  const { doc, page, rotate } = viewer;
  for (const id of ['viewer-prev', 'viewer-next', 'viewer-rotate']) $(id).hidden = false;
  $('viewer-title').textContent = doc.title;
  $('viewer-page').textContent = `Страница ${page} из ${doc.pages}`;
  $('viewer-prev').disabled = page <= 1;
  $('viewer-next').disabled = page >= doc.pages;
  $('viewer-image').classList.remove('zoomed');
  $('viewer-image').src =
    `/api/equipment/${encodeURIComponent(equipmentId)}/documents/${doc.index}/pages/${page}?rotate=${rotate}`;
}

function openViewer(doc) {
  Object.assign(viewer, { doc, page: 1, rotate: 0 });
  showPage();
  $('viewer').showModal();
}
$('viewer-prev').addEventListener('click', () => { viewer.page -= 1; showPage(); });
$('viewer-next').addEventListener('click', () => { viewer.page += 1; showPage(); });
$('viewer-rotate').addEventListener('click', () => { viewer.rotate = (viewer.rotate + 90) % 360; showPage(); });
$('viewer-close').addEventListener('click', () => $('viewer').close());
$('viewer-image').addEventListener('click', (event) => event.target.classList.toggle('zoomed'));   // tap to zoom

// ---------- equipment card ----------

const EVENT_TITLE = { inspection: 'Осмотр', defect: 'Дефект', maintenance: 'Обслуживание', replacement: 'Замена' };
const EVENT_ICON = { inspection: 'assignment', defect: 'warning', maintenance: 'build', replacement: 'swap' };

function renderCard(card) {
  lastCard = card;
  $('eq-name').textContent = card.id;
  $('eq-meta').textContent = `${card.name} · зав. № ${card.serial}`;

  $('eq-hours').textContent = `${num(card.hours)} моточасов`;
  $('eq-hours-note').textContent = card.has_telemetry
    ? 'Считаются по телеметрии, только когда агрегат в работе.'
    : 'Последнее известное значение.';
  $('eq-resource').hidden = !card.resource;
  if (card.resource) {
    const r = card.resource;
    $('eq-resource-bar').className = `progress ${r.status === 'ok' ? '' : r.status}`;
    $('eq-resource-bar').firstElementChild.style.width = `${Math.min(100, (r.used / r.norm) * 100)}%`;
    $('eq-resource-text').textContent = (r.remaining > 0
      ? `Ресурс ${num(r.norm)} ч · расчётный остаток ${num(r.remaining)} ч`
      : `Ресурс ${num(r.norm)} ч превышен на ${num(-r.remaining)} ч`) + ` · ${r.source}`;
  }

  $('tm-none').hidden = card.has_telemetry;
  $('tm-live').hidden = !card.has_telemetry;

  renderSpecs(card.specs);

  if (focusSelect.options.length === 1) {
    for (const c of card.components) {
      const option = el('option', '', c.name);
      option.value = c.code;
      focusSelect.append(option);
    }
  }

  const history = $('history');
  history.replaceChildren();
  $('history-empty').hidden = card.events.length > 0;
  history.hidden = card.events.length === 0;
  for (const e of card.events) {
    const hours = e.equipment_hours == null ? '' : ` · при наработке ${num(e.equipment_hours)} ч`;
    history.append(listItem({
      lead: EVENT_ICON[e.kind], tone: e.kind === 'defect' ? 'warn' : '',
      headline: EVENT_TITLE[e.kind] + (e.component_name ? ` · ${e.component_name}` : ''),
      supporting: [e.note, `${e.created_at.slice(0, 10)}${hours}${e.author ? ` · ${e.author}` : ''}`].filter(Boolean),
      photo: e.photo,
      onClick: e.photo ? () => openPhoto(e.photo, `${e.component_name || 'Агрегат'} · ${e.created_at.slice(0, 10)}`) : undefined,
    }));
  }

  const list = $('components');
  list.replaceChildren();
  for (const c of card.components) {
    const r = c.resource;
    const state = lastDiagnosis?.components[c.code];
    const tone = !state ? '' : state.level !== 'ok' ? state.level : state.inspected ? 'ok' : '';
    const item = el('li', 'part');
    const head = el('div', 'part-head');
    const lead = el('span', `leading ${tone}`);
    lead.append(icon(tone ? LEVEL_ICON[tone] : 'circle'));
    head.append(lead, el('span', 'headline', c.name));
    // Short wording for a narrow list; the full category is on the inspection tab.
    const short = { alarm: 'Признаки дефекта', warn: 'Нужна проверка', ok: 'Замечаний нет', '': 'Не осмотрен' };
    if (state) {
      const chip = el('span', `chip status ${tone}`, short[tone]);
      chip.title = state.text;
      head.append(chip);
    }
    item.append(head);
    if (r.remaining_hours === null) {
      item.append(el('div', 'body-small muted', r.note));
    } else {
      const meter = el('div', `progress ${r.status === 'ok' ? '' : r.status}`);
      const fill = el('span');
      fill.style.width = `${Math.min(100, (r.used_hours / c.norm_hours) * 100)}%`;
      meter.append(fill);
      const left = r.remaining_hours > 0
        ? `расчётный остаток ${num(r.remaining_hours)} ч`
        : `норматив превышен на ${num(-r.remaining_hours)} ч`;
      item.append(meter, el('div', 'body-small muted tabular',
        `${num(r.used_hours)} из ${num(c.norm_hours)} ч · ${left} · норматив: ${c.norm_source}`));
    }
    list.append(item);
  }
}

async function refreshCard() {
  try {
    if (!user || !equipmentId) return;
    renderCard(await api(`/api/equipment/${encodeURIComponent(equipmentId)}`));
  } catch {
    $('eq-meta').textContent = 'нет связи с сервером';
  }
}

// ---------- screens and navigation ----------

let tab = 'inspect';

function showMode() {
  const mode = !user ? 'login' : equipmentId ? 'inspect' : 'identify';
  document.body.dataset.mode = mode;
  document.body.dataset.tab = tab;
  for (const node of document.querySelectorAll('[data-mode], [data-permission], [data-tab]')) {
    if (node === document.body) continue;
    const modes = node.dataset.mode;
    const allowed = !node.dataset.permission || Boolean(user?.can[node.dataset.permission]);
    const offTab = node.dataset.tab !== undefined && node.dataset.tab !== tab;
    node.hidden = (modes !== undefined && !modes.split(' ').includes(mode)) || !allowed || offTab || (!user && !modes);
  }
  for (const item of document.querySelectorAll('.nav-item')) {
    item.setAttribute('aria-selected', String(item.dataset.go === tab));
  }
  $('eq-change').hidden = mode !== 'inspect';
  $('account').hidden = $('conn').hidden = !user;
  $('account-menu').hidden = true;
  if (user) {
    $('user-name').textContent = user.name;
    $('user-role').textContent = `${user.role_title} · ${user.username}`;
  }
  if (mode === 'login') {
    $('eq-name').textContent = 'EquipVision';
    $('eq-meta').textContent = '';
  } else if (mode === 'identify') {
    $('eq-name').textContent = 'Идентификация';
    $('eq-meta').textContent = 'шильдик, QR-код или ручной выбор';
    setVerdict('ищу шильдик или QR-код');
    setStats('');
  }
}

function setTab(name) {
  tab = name;
  showMode();
  if (video.offsetParent && video.paused && video.srcObject) video.play().catch(() => {});
  drawOverlay();
  window.scrollTo({ top: 0 });
}
for (const item of document.querySelectorAll('.nav-item')) {
  item.addEventListener('click', () => setTab(item.dataset.go));
}

$('account').addEventListener('click', (event) => {
  event.stopPropagation();
  $('account-menu').hidden = !$('account-menu').hidden;
});
document.addEventListener('click', (event) => {
  if (!$('account-menu').contains(event.target)) $('account-menu').hidden = true;
});

async function openEquipment(id) {
  equipmentId = id;
  tab = 'inspect';
  $('report-pdf').href = `/api/equipment/${encodeURIComponent(id)}/report.pdf`;
  $('report-xlsx').href = `/api/equipment/${encodeURIComponent(id)}/report.xlsx`;
  specsJson = '';
  loadDocuments().catch(() => {});
  lastCard = lastDiagnosis = null;
  lastDiagnosisJson = '';
  focusSelect.length = 1;                      // components of the previous unit
  focusSelect.value = '';
  leaveTraining();
  loadUnitStats();
  $('id-candidates').replaceChildren();
  setVerdict('ожидание кадра');
  showMode();
  sendFocus();
  await refreshCard();
  pollDiagnosis();
}

function closeEquipment() {
  equipmentId = null;
  focusSelect.value = '';
  sendFocus();                                 // tells the server to stop recording findings
  detections = [];
  drawOverlay();
  seenMatch = { id: null, count: 0 };
  $('id-status').textContent = 'Жду кадр…';
  leaveTraining();
  showMode();
  loadHome();
}
$('eq-change').addEventListener('click', closeEquipment);

// ---------- identification ----------

function unitItem(unit, note) {
  return listItem({
    lead: 'pump', headline: `${unit.id} · ${unit.name}`,
    supporting: [note, `${unit.model} · зав. № ${unit.serial}`].filter(Boolean),
    chevron: true, onClick: () => openEquipment(unit.id),
  });
}

async function loadManualList() {
  const box = $('id-manual');
  box.replaceChildren();
  for (const unit of await api('/api/equipment')) box.append(unitItem(unit));
}

const IDENTIFY_MAX_SIDE = 1280;                // nameplate text needs more pixels than defect analysis
let identifying = false;
let seenMatch = { id: null, count: 0 };

async function identifyFrame() {
  if (!user || equipmentId || identifying || video.readyState < 2 || !video.videoWidth) return;
  identifying = true;
  try {
    const scale = Math.min(1, IDENTIFY_MAX_SIDE / Math.max(video.videoWidth, video.videoHeight));
    const frame = document.createElement('canvas');
    frame.width = Math.round(video.videoWidth * scale);
    frame.height = Math.round(video.videoHeight * scale);
    frame.getContext('2d').drawImage(video, 0, 0, frame.width, frame.height);
    const blob = await new Promise((resolve) => frame.toBlob(resolve, 'image/jpeg', 0.85));
    const res = await fetch('/api/identify', { method: 'POST', headers: { 'Content-Type': 'image/jpeg' }, body: blob });
    if (!res.ok || equipmentId) return;
    const answer = await res.json();

    const read = answer.qr ? `QR-код: ${answer.qr}` : answer.texts.length
      ? `Прочитано: ${answer.texts.slice(0, 3).join(' / ')}` : 'Текст и QR-код в кадре не найдены';
    $('id-status').textContent = `${read} · ${answer.ms} мс`;

    const box = $('id-candidates');
    box.replaceChildren();
    for (const unit of answer.candidates) box.append(unitItem(unit, 'Похоже на этот агрегат — подтвердите'));

    if (!answer.match) {
      seenMatch = { id: null, count: 0 };
      return;
    }
    // A QR code cannot be misread; a serial number must repeat in two frames before it is trusted.
    seenMatch = { id: answer.match.id, count: seenMatch.id === answer.match.id ? seenMatch.count + 1 : 1 };
    if (answer.by === 'qr' || seenMatch.count >= 2) {
      openEquipment(answer.match.id);
      snack(`Агрегат определён ${answer.by === 'qr' ? 'по QR-коду' : 'по серийному номеру'}: ${answer.match.id}`);
    } else {
      box.append(unitItem(answer.match, 'Найден по серийному номеру'));
    }
  } catch {
    $('id-status').textContent = 'Нет связи с сервером';
  } finally {
    identifying = false;
  }
}

// ---------- photo of the current frame ----------

/** The live camera frame as base64 JPEG, attached to a finding recorded by hand. */
function currentFrame() {
  if (video.readyState < 2 || !video.videoWidth) return null;
  const scale = Math.min(1, 960 / Math.max(video.videoWidth, video.videoHeight));
  const frame = document.createElement('canvas');
  frame.width = Math.round(video.videoWidth * scale);
  frame.height = Math.round(video.videoHeight * scale);
  frame.getContext('2d').drawImage(video, 0, 0, frame.width, frame.height);
  return frame.toDataURL('image/jpeg', 0.8).split(',')[1];
}

// ---------- defect statistics ----------

function bars(title, rows) {
  const box = el('div', 'bars');
  box.append(el('div', 'label-medium', title));
  const max = Math.max(1, ...rows.map(([, count]) => count));
  for (const [label, count] of rows) {
    const row = el('div', 'bar-row');
    const meter = el('div', 'progress');
    const fill = el('span');
    fill.style.width = `${(count / max) * 100}%`;
    meter.append(fill);
    row.append(el('span', '', label), meter, el('span', 'count', String(count)));
    box.append(row);
  }
  return box;
}

function renderStats(box, stats, byUnit) {
  box.replaceChildren();
  const totals = el('div', 'stat-totals');
  for (const [value, label] of [[stats.defects, 'дефектов в истории'], [stats.inspections, 'осмотров']]) {
    const cell = el('div');
    cell.append(el('span', 'value', String(value)), el('span', 'body-small muted', label));
    totals.append(cell);
  }
  box.append(totals);
  if (!stats.defects) {
    box.append(el('p', 'muted', 'Дефектов в истории нет.'));
    return;
  }
  box.append(bars('По признаку', stats.by_sign.map((row) => [row.title, row.count])));
  if (byUnit) box.append(bars('По агрегатам', stats.units.map((u) => [u.id, u.defects])));
  box.append(bars('По узлам', Object.entries(stats.by_component).slice(0, 6)));
  box.append(bars('По месяцам', Object.entries(stats.by_month).map(([month, n]) => [`${month.slice(5)}.${month.slice(0, 4)}`, n])));
}

async function loadUnitStats() {
  if (!equipmentId) return;
  try {
    renderStats($('unit-stats'), await api(`/api/equipment/${encodeURIComponent(equipmentId)}/stats`), false);
  } catch { /* the card stays as it was */ }
}

// ---------- walk-around route ----------

const clock = (iso) => new Date(iso).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
const day = (iso) => new Date(iso).toLocaleDateString('ru-RU');

function renderRound({ active, last }) {
  const list = $('route-stops');
  list.replaceChildren();
  $('route-start').hidden = Boolean(active);
  $('route-finish').hidden = $('route-chip').hidden = $('route-progress').hidden = !active;
  $('route-last').hidden = !last;
  if (last) {
    $('route-last').textContent = `Прошлый обход ${day(last.finished_at)}: осмотрено ${last.inspected} из ${last.total}, ` +
      `пропущено с причиной ${last.skipped}, не осмотрено ${last.pending}.`;
  }
  if (!active) {
    $('route-sub').textContent = 'Обход не начат. Приложение проведёт по всем агрегатам и отметит осмотренные и пропущенные.';
    return;
  }
  $('route-sub').textContent = `Начат ${day(active.started_at)} в ${clock(active.started_at)} · ${active.started_by}`;
  $('route-chip').textContent = `Осмотрено ${active.inspected} из ${active.total}`;
  $('route-chip').className = `chip status ${active.pending ? '' : 'ok'}`;
  $('route-progress').firstElementChild.style.width = `${((active.inspected + active.skipped) / active.total) * 100}%`;
  $('route-finish').textContent = active.pending ? `Завершить обход (не осмотрено: ${active.pending})` : 'Завершить обход';

  let place = '';
  for (const stop of active.stops) {
    if (stop.location !== place) {
      place = stop.location;
      const head = el('li', 'route-place label-medium');
      head.append(icon('place', 'icon small'), el('span', '', place || 'Без указания места'));
      list.append(head);
    }
    const isNext = stop.equipment_id === active.next;
    const row = el('li', `stop ${stop.status}${isNext ? ' next' : ''}`);
    const marker = el('span', 'stop-marker');
    if (stop.status === 'inspected') marker.append(icon('check'));
    else if (stop.status === 'skipped') marker.append(icon('block'));
    else marker.textContent = stop.position;
    const state = stop.status === 'inspected' ? `Осмотрен в ${clock(stop.at)}`
      : stop.status === 'skipped' ? `Пропущен: ${stop.note}`
      : isNext ? 'Следующий по маршруту' : 'Ожидает осмотра';
    row.append(marker, listItem({
      headline: stop.equipment_id, supporting: [state, `${stop.name} · зав. № ${stop.serial}`],
      chevron: true, onClick: () => openEquipment(stop.equipment_id),
    }));
    if (stop.status === 'pending') {
      const skip = el('button', 'btn text', 'Пропустить');
      skip.type = 'button';
      skip.addEventListener('click', () => {
        $('skip-unit').textContent = `${stop.equipment_id} · ${stop.name}`;
        $('skip-reason').value = '';
        $('skip-dialog').dataset.unit = stop.equipment_id;
        $('skip-dialog').showModal();
      });
      row.append(skip);
    }
    list.append(row);
  }
}

$('route-start').addEventListener('click', act(async () => {
  renderRound(await api('/api/round/start', {}));
  snack('Обход начат');
}));
$('route-finish').addEventListener('click', act(async () => {
  const state = await api('/api/round/finish', {});
  renderRound(state);
  snack(`Обход завершён: осмотрено ${state.last.inspected}, пропущено ${state.last.skipped}, не осмотрено ${state.last.pending}`);
}));
$('skip-cancel').addEventListener('click', () => $('skip-dialog').close());
$('skip-form').addEventListener('submit', act(async (event) => {
  event.preventDefault();
  renderRound(await api('/api/round/skip', { equipment_id: $('skip-dialog').dataset.unit, reason: $('skip-reason').value }));
  $('skip-dialog').close();
  snack('Агрегат отмечен как пропущенный');
}));

async function loadHome() {
  if (!user) return;
  try {
    renderRound(await api('/api/round'));
    renderStats($('fleet-stats'), await api('/api/stats'), true);
    const results = await api('/api/training');
    const list = $('training-results');
    list.replaceChildren();
    $('training-empty').hidden = results.length > 0;
    for (const r of results) {
      list.append(listItem({ lead: 'school', headline: `${r.name} · ${r.equipment_id}`,
        supporting: `${day(r.created_at)} в ${clock(r.created_at)}`, trailing: `${r.score} из 100` }));
    }
  } catch (err) {
    snack(err.message);
  }
}

// ---------- trainee mode ----------

function leaveTraining() {
  Object.assign(training, { active: false, result: null, answers: {}, index: 0 });
  $('train-switch').checked = false;
  renderTraining();
}

async function startTraining() {
  training.steps = await api(`/api/equipment/${encodeURIComponent(equipmentId)}/training`);
  await post('inspection/reset');              // a training run starts from a clean inspection
  Object.assign(training, { active: true, index: 0, answers: {}, result: null });
  lastDiagnosisJson = '';
  goToStep(0);
  pollDiagnosis();
  snack('Режим обучения: пройдите узлы по шагам, оценка — в конце');
}

function goToStep(index) {
  training.index = index;
  focusSelect.value = training.steps[index].component;
  sendFocus();
  renderTraining();
}

function answerChip(code, sign, title) {
  const picked = training.answers[code];
  const on = sign === null ? Array.isArray(picked) && picked.length === 0 : Boolean(picked?.includes(sign));
  const chip = el('button', 'chip filter');
  chip.type = 'button';
  chip.setAttribute('aria-pressed', String(on));
  if (on) chip.append(icon('check', 'icon small'));
  chip.append(title);
  chip.addEventListener('click', () => {
    const now = new Set(training.answers[code] || []);
    if (sign === null) now.clear();
    else if (now.has(sign)) now.delete(sign);
    else now.add(sign);
    // Un-ticking the last sign leaves the step unanswered, not "nothing found".
    if (sign !== null && !now.size) delete training.answers[code];
    else training.answers[code] = [...now];
    renderTraining();
  });
  return chip;
}

function renderTraining() {
  const card = $('train-card'), result = $('train-result');
  card.hidden = !guiding();
  result.hidden = !training.result;
  $('live').hidden = guiding();                // the system's findings are revealed after the run
  $('manual-row').hidden = guiding();
  $('shutter').classList.toggle('off', guiding());
  focusSelect.disabled = guiding();
  drawOverlay();
  if (training.result) renderTrainingResult();
  if (!guiding()) return;

  const step = training.steps[training.index];
  const seen = Boolean(lastDiagnosis?.components[step.component]?.inspected);
  card.replaceChildren();
  card.append(el('div', 'label-medium', `Шаг ${training.index + 1} из ${training.steps.length}`),
    el('div', 'title-large', step.name));
  const progress = el('div', 'progress');
  const fill = el('span');
  fill.style.width = `${((training.index + 1) / training.steps.length) * 100}%`;
  progress.append(fill);
  card.append(progress, el('div', 'title-small', 'Что проверять'));
  const what = el('ul', 'check');
  for (const text of step.what) what.append(el('li', '', text));
  card.append(what, el('div', 'title-small', 'Как смотреть'), el('p', '', step.how),
    el('div', 'title-small', 'Норма'), el('p', '', step.normal));
  const hint = el('div', 'hint-row');
  hint.append(icon(seen ? 'check_circle' : 'camera'),
    el('span', '', seen ? 'Камера зафиксировала узел' : 'Наведите камеру на узел и подержите 2–3 секунды'));
  card.append(hint, el('div', 'title-small', 'Что вы видите?'));
  const chips = el('div', 'chip-wrap');
  chips.append(answerChip(step.component, null, 'Замечаний нет'));
  for (const [sign, title] of Object.entries(signTitles)) {
    if (sign !== PHOTO_SIGN) chips.append(answerChip(step.component, sign, title));
  }
  card.append(chips);

  const actions = el('div', 'actions');
  const back = el('button', 'btn outlined', 'Назад');
  back.type = 'button';
  back.disabled = training.index === 0;
  back.addEventListener('click', () => goToStep(training.index - 1));
  const last = training.index === training.steps.length - 1;
  const next = el('button', 'btn filled', last ? 'Завершить и оценить' : 'Далее');
  next.type = 'button';
  next.addEventListener('click', act(async () => {
    if (!last) return goToStep(training.index + 1);
    training.result = await post('training', { answers: training.answers });
    focusSelect.value = '';
    sendFocus();
    lastDiagnosisJson = '';
    renderTraining();
    pollDiagnosis();
  }));
  actions.append(back, next);
  card.append(actions);
}

function renderTrainingResult() {
  const r = training.result, box = $('train-result');
  box.replaceChildren();
  box.append(el('div', 'label-medium', 'Оценка осмотра'), el('div', 'score', `${r.score} из 100`),
    el('div', 'title-medium', r.grade),
    el('p', '', `Осмотрено узлов: ${r.inspected} из ${r.total}. ` + (r.attention === null
      ? 'Признаков дефектов на узлах нет: оценка поставлена за полноту осмотра.'
      : `Найдено признаков: ${r.found} из ${r.expected}, зафиксированных камерой.`)));
  const steps = el('div', 'list');
  for (const s of r.steps) {
    steps.append(listItem({ lead: s.ok ? 'check_circle' : s.looked ? 'warning' : 'circle', headline: s.name, supporting: s.comment }));
  }
  box.append(steps, el('p', 'body-small muted',
    'Ниже показано, что нашла система. Спорные случаи разберите с наставником.'));
  const actions = el('div', 'actions');
  const again = el('button', 'btn filled', 'Пройти заново');
  again.type = 'button';
  again.addEventListener('click', act(startTraining));
  const leave = el('button', 'btn outlined', 'Выйти из обучения');
  leave.type = 'button';
  leave.addEventListener('click', leaveTraining);
  actions.append(again, leave);
  box.append(actions);
}

$('train-switch').addEventListener('change', act(async (event) => {
  if (event.target.checked) await startTraining();
  else leaveTraining();
}));

/** The AR hint of the trainee mode: a framing guide and what to look for on this component. */
function drawTrainingHint(ctx, w, h) {
  const step = training.steps[training.index];
  if (!step) return;
  const m = Math.min(w, h) * 0.12, arm = Math.min(w, h) * 0.1;
  ctx.strokeStyle = '#fff';
  ctx.lineWidth = 3;
  ctx.beginPath();
  for (const [x, y, dx, dy] of [[m, m, 1, 1], [w - m, m, -1, 1], [m, h - m, 1, -1], [w - m, h - m, -1, -1]]) {
    ctx.moveTo(x + dx * arm, y);
    ctx.lineTo(x, y);
    ctx.lineTo(x, y + dy * arm);
  }
  ctx.stroke();
  ctx.fillStyle = 'rgba(0, 0, 0, .68)';
  ctx.beginPath();
  ctx.roundRect(8, 30, w - 16, 48, 12);
  ctx.fill();
  ctx.fillStyle = '#fff';
  ctx.font = '500 14px Roboto, system-ui, sans-serif';
  ctx.fillText(`Шаг ${training.index + 1}/${training.steps.length} · ${step.name}`, 20, 45, w - 40);
  ctx.font = '400 13px Roboto, system-ui, sans-serif';
  ctx.fillText(`Ищите: ${step.what[0].toLowerCase()}`, 20, 64, w - 40);
}

// ---------- choosing a unit by voice ----------

const VOICE_SECONDS = 3.5;
let listening = false;

/** The microphone; with a development test source, a tone that only exercises the path. */
async function micStream() {
  if (TEST_SOURCE) {
    const ctx = new AudioContext();
    const tone = ctx.createOscillator();
    const out = ctx.createMediaStreamDestination();
    tone.connect(out);
    tone.start();
    return out.stream;
  }
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error(window.isSecureContext ? 'Этот браузер не даёт доступ к микрофону.'
      : 'Микрофон доступен только по HTTPS. Откройте адрес сервера, начинающийся с https://');
  }
  return navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
}

/** Records a few seconds and returns 16-bit mono samples with their sample rate. */
async function recordPhrase() {
  const stream = await micStream();
  let ctx;
  try {
    ctx = new AudioContext({ sampleRate: 16000 });      // what the recogniser works at
  } catch {
    ctx = new AudioContext();                           // the server is told the real rate
  }
  const source = ctx.createMediaStreamSource(stream);
  const tap = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  tap.onaudioprocess = (event) => chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  source.connect(tap);
  tap.connect(ctx.destination);
  await new Promise((resolve) => setTimeout(resolve, VOICE_SECONDS * 1000));
  tap.disconnect();
  source.disconnect();
  for (const track of stream.getTracks()) track.stop();
  const rate = ctx.sampleRate;
  await ctx.close();

  const pcm = new Int16Array(chunks.reduce((n, chunk) => n + chunk.length, 0));
  let offset = 0;
  for (const chunk of chunks) {
    for (const value of chunk) pcm[offset++] = Math.max(-1, Math.min(1, value)) * 32767;
  }
  return { pcm, rate };
}

async function listen() {
  if (listening) return;
  listening = true;
  const button = $('voice-button'), status = $('voice-status'), box = $('voice-candidates');
  button.disabled = true;
  button.classList.add('listening');
  box.replaceChildren();
  try {
    status.textContent = 'Слушаю… назовите агрегат, например: «насос сто один».';
    const { pcm, rate } = await recordPhrase();
    button.classList.remove('listening');
    status.textContent = 'Распознаю…';
    const res = await fetch(`/api/voice?rate=${rate}`, {
      method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: pcm.buffer });
    const answer = await res.json();
    if (!res.ok) throw new Error(typeof answer.detail === 'string' ? answer.detail : 'Не удалось распознать речь');

    if (answer.match) {
      status.textContent = `Услышано: «${answer.text}».`;
      await openEquipment(answer.match.id);
      snack(`Агрегат выбран голосом: ${answer.match.id}`);
    } else if (answer.candidates.length) {
      // Heard unclearly, or the phrase fits several units: the engineer confirms.
      status.textContent = `Услышано: «${answer.text}». Подтвердите выбор:`;
      for (const unit of answer.candidates) box.append(unitItem(unit, 'Выбрать этот агрегат'));
    } else {
      status.textContent = answer.text
        ? `Услышано: «${answer.text}». Не удалось понять, какой это агрегат — повторите или выберите из списка.`
        : 'Ничего не разобрал. Скажите, например: «насос сто один».';
    }
  } catch (err) {
    status.textContent = err.name === 'NotAllowedError'
      ? 'Доступ к микрофону запрещён. Разрешите его в настройках сайта.' : err.message;
  } finally {
    listening = false;
    button.disabled = false;
    button.classList.remove('listening');
  }
}
$('voice-button').addEventListener('click', listen);

// ---------- sign-in and users ----------

async function loadUsers() {
  if (!user?.can.manage_users) return;
  const list = $('users');
  list.replaceChildren();
  for (const u of await api('/api/users')) {
    list.append(listItem({ lead: 'person', headline: u.name, supporting: u.username, trailing: u.role_title }));
  }
}

function signedIn(account) {
  user = account;
  showMode();
  if (!video.srcObject) startCamera();         // ask for the camera only after sign-in
  connect();
  loadEpisodes();
  loadSigns();
  loadManualList();
  loadUsers();
  loadHome();
  pollTelemetry();
  if (equipmentId) openEquipment(equipmentId);
}

function signedOut() {
  user = null;
  equipmentId = null;
  if (socket) socket.close();
  $('login-pass').value = '';
  showMode();
}

$('login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('login-error').hidden = true;
  try {
    signedIn(await api('/api/login', { username: $('login-user').value, password: $('login-pass').value }));
  } catch (err) {
    $('login-error').textContent = err.message;
    $('login-error').hidden = false;
  }
});

$('logout').addEventListener('click', async () => {
  await api('/api/logout', {}).catch(() => {});
  signedOut();
});

$('user-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  $('user-error').hidden = true;
  try {
    const added = await api('/api/users', Object.fromEntries(new FormData(event.target)));
    event.target.reset();
    loadUsers();
    snack(`Пользователь добавлен: ${added.name}`);
  } catch (err) {
    $('user-error').textContent = err.message;
    $('user-error').hidden = false;
  }
});

// ---------- start ----------

showMode();
fetch('/api/me').then((res) => (res.ok ? res.json() : null)).then((account) => account && signedIn(account));
setInterval(sendFrame, FRAME_INTERVAL_MS);
setInterval(pollTelemetry, 1000);
setInterval(identifyFrame, 1000);
setInterval(refreshCard, 5000);
setInterval(pollDiagnosis, 1000);

// Pinch zoom breaks the fixed navigation on a phone; iOS ignores the viewport setting, so stop its gesture too.
for (const type of ['gesturestart', 'gesturechange']) {
  document.addEventListener(type, (event) => event.preventDefault());
}
