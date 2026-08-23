/* Mini App BATTLEHALL.
   Без сборки и фреймворка: экранов четыре, состояние плоское — React здесь
   стоил бы дороже, чем даёт. Всё общение с бэком идёт через api(), которая
   подкладывает подпись Telegram в заголовок. */

const tg = window.Telegram && window.Telegram.WebApp ? window.Telegram.WebApp : null;

if (tg) {
  tg.ready();
  tg.expand();
  try {
    tg.setHeaderColor('#0b0d12');
    tg.setBackgroundColor('#0b0d12');
  } catch (e) {
    /* старые клиенты не умеют — не страшно */
  }
}

const state = {
  me: null,
  achievements: null,
  rewards: null,
  loot: null,
  category: 'weekly',
  loaded: {},
};

const MOSCOW_TZ = 'Europe/Moscow';

// --- сеть ---

async function api(path, options = {}) {
  const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
  if (tg && tg.initData) headers['X-Telegram-Init-Data'] = tg.initData;

  const response = await fetch(path, Object.assign({}, options, { headers }));
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    throw new Error(response.ok ? 'Сервер вернул не JSON' : 'Что-то пошло не так');
  }

  if (!response.ok) {
    throw new Error((data && data.detail) || 'Что-то пошло не так');
  }
  return data;
}

// --- утилиты ---

const $ = (selector) => document.querySelector(selector);
const el = (html) => {
  const wrap = document.createElement('div');
  wrap.innerHTML = html.trim();
  return wrap.firstElementChild;
};

function esc(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, (ch) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]
  ));
}

function toast(message) {
  const node = $('#toast');
  node.textContent = message;
  node.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { node.hidden = true; }, 2600);
}

function haptic(type) {
  if (tg && tg.HapticFeedback) {
    try { tg.HapticFeedback.notificationOccurred(type); } catch (e) { /* no-op */ }
  }
}

function hapticImpact(style) {
  if (tg && tg.HapticFeedback) {
    try { tg.HapticFeedback.impactOccurred(style); } catch (e) { /* no-op */ }
  }
}

let audioCtx = null;
let masterBus = null;

function unlockAudio() {
  if (!audioCtx) {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if (!Ctx) return null;
    audioCtx = new Ctx();
    masterBus = audioCtx.createDynamicsCompressor();
    masterBus.threshold.value = -14;
    masterBus.knee.value = 20;
    masterBus.ratio.value = 8;
    masterBus.attack.value = 0.002;
    masterBus.release.value = 0.18;
    masterBus.connect(audioCtx.destination);
  }
  if (audioCtx.state === 'suspended') audioCtx.resume().catch(() => { /* no-op */ });
  return audioCtx;
}

function tone(freq, duration, { type = 'sine', peak = 0.26, delay = 0, slideTo = null, detune = 0 } = {}) {
  const ctx = audioCtx;
  if (!ctx) return;
  const start = ctx.currentTime + delay;
  const osc = ctx.createOscillator();
  const gain = ctx.createGain();
  osc.type = type;
  osc.frequency.setValueAtTime(freq, start);
  if (detune) osc.detune.value = detune;
  if (slideTo) osc.frequency.exponentialRampToValueAtTime(slideTo, start + duration);
  gain.gain.setValueAtTime(0.0001, start);
  gain.gain.exponentialRampToValueAtTime(peak, start + Math.min(0.02, duration * 0.3));
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);
  osc.connect(gain).connect(masterBus);
  osc.start(start);
  osc.stop(start + duration + 0.02);
}

function chordTone(freq, duration, opts = {}) {
  tone(freq, duration, { ...opts, detune: -6 });
  tone(freq, duration, { ...opts, detune: 6, peak: (opts.peak ?? 0.26) * 0.85 });
}

function noiseTick({ delay = 0, duration = 0.03, filterFreq = 2200, peak = 0.2 } = {}) {
  const ctx = audioCtx;
  if (!ctx) return;
  const start = ctx.currentTime + delay;
  const size = Math.ceil(ctx.sampleRate * duration);
  const buffer = ctx.createBuffer(1, size, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < size; i += 1) data[i] = Math.random() * 2 - 1;

  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const filter = ctx.createBiquadFilter();
  filter.type = 'bandpass';
  filter.frequency.value = filterFreq;
  const gain = ctx.createGain();
  gain.gain.setValueAtTime(peak, start);
  gain.gain.exponentialRampToValueAtTime(0.0001, start + duration);

  src.connect(filter).connect(gain).connect(masterBus);
  src.start(start);
  src.stop(start + duration + 0.01);
}

function scheduleReelTicks(durationMs, registry) {
  const count = 22;
  for (let i = 0; i < count; i += 1) {
    const t = durationMs * Math.pow(i / count, 1.8);
    const id = setTimeout(() => noiseTick({ filterFreq: 1700 + Math.random() * 900, peak: 0.16 }), t);
    if (registry) registry.push(id);
  }
}

function playSpinWhoosh() {
  tone(180, 0.35, { type: 'sawtooth', peak: 0.09, slideTo: 60 });
  tone(45, 0.3, { type: 'sine', peak: 0.2 });
  noiseTick({ duration: 0.3, filterFreq: 3600, peak: 0.16 });
}

function playLandingSound(prize) {
  if (prize.kind === 'nothing') {
    noiseTick({ duration: 0.05, filterFreq: 300, peak: 0.15 });
    return;
  }
  if (prize.rarity === 'legendary') {
    tone(48, 0.16, { type: 'sine', peak: 0.42 });
    noiseTick({ duration: 0.08, filterFreq: 250, peak: 0.3 });
    [523, 659, 784, 1046, 1318, 1568].forEach((f, i) =>
      chordTone(f, 0.55, { type: 'triangle', peak: 0.28, delay: 0.06 + i * 0.065 }));
    tone(90, 0.7, { type: 'sine', peak: 0.32, delay: 0.06 });
    noiseTick({ duration: 0.5, filterFreq: 7500, peak: 0.16, delay: 0.4 });
  } else if (prize.rarity === 'epic') {
    [523, 659, 784].forEach((f, i) => chordTone(f, 0.3, { type: 'triangle', peak: 0.22, delay: i * 0.06 }));
    noiseTick({ duration: 0.15, filterFreq: 5000, peak: 0.12, delay: 0.16 });
  } else if (prize.rarity === 'rare') {
    tone(587, 0.18, { type: 'sine', peak: 0.22 });
    tone(880, 0.24, { type: 'sine', peak: 0.18, delay: 0.08 });
  } else {
    tone(392, 0.12, { type: 'sine', peak: 0.15 });
  }
}

function playWinChime() {
  [660, 880, 1108, 1320].forEach((f, i) => tone(f, 0.22, { type: 'square', peak: 0.22, delay: i * 0.06 }));
  tone(1760, 0.5, { type: 'sine', peak: 0.16, delay: 0.2 });
  noiseTick({ duration: 0.4, filterFreq: 6500, peak: 0.14, delay: 0.05 });
}

function playCoinJingle() {
  const notes = [1046, 1318, 1568, 1760, 2093];
  for (let i = 0; i < 12; i += 1) {
    const f = notes[Math.floor(Math.random() * notes.length)];
    tone(f, 0.12, { type: 'sine', peak: 0.09, delay: Math.random() * 1.1 });
  }
}

function playWasted() {
  noiseTick({ duration: 0.12, filterFreq: 200, peak: 0.32 });
  tone(70, 0.7, { type: 'sine', peak: 0.4 });
  tone(260, 0.9, { type: 'sawtooth', peak: 0.14, slideTo: 55 });
}

function playAllInStack() {
  for (let i = 0; i < 6; i += 1) {
    noiseTick({ delay: i * 0.05, filterFreq: 900 - i * 60, duration: 0.05, peak: 0.22 });
  }
}

function playUiClick() {
  noiseTick({ duration: 0.02, filterFreq: 3200, peak: 0.13 });
}

let allinConfirmResolve = null;

function confirmAllIn(message) {
  return new Promise((resolve) => {
    allinConfirmResolve = resolve;
    $('#allinConfirmMessage').textContent = message;
    $('#allinConfirmOverlay').hidden = false;
    playAllInStack();
  });
}

function resolveAllInConfirm(value) {
  $('#allinConfirmOverlay').hidden = true;
  if (allinConfirmResolve) {
    allinConfirmResolve(value);
    allinConfirmResolve = null;
  }
}

$('#allinConfirmBtn').addEventListener('click', () => { unlockAudio(); playUiClick(); resolveAllInConfirm(true); });
$('#allinCancelBtn').addEventListener('click', () => { unlockAudio(); resolveAllInConfirm(false); });

function formatProgress(item) {
  if (item.unit === 'мин') {
    const done = (item.progress / 60).toFixed(1).replace('.0', '');
    const total = (item.target / 60).toFixed(1).replace('.0', '');
    return `${done} / ${total} ч`;
  }
  return `${item.progress} / ${item.target} ${item.unit}`;
}

function timeLeft(iso) {
  if (!iso) return '';
  const diff = new Date(iso) - new Date();
  if (diff <= 0) return 'истекло';
  const hours = Math.floor(diff / 3600000);
  const minutes = Math.floor((diff % 3600000) / 60000);
  if (hours >= 24) return `${Math.floor(hours / 24)} дн.`;
  if (hours >= 1) return `${hours} ч ${minutes} мин`;
  return `${minutes} мин`;
}

function formatDate(iso) {
  return new Date(iso).toLocaleDateString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit', timeZone: MOSCOW_TZ,
  });
}

function formatTime(iso) {
  return new Date(iso).toLocaleTimeString('ru-RU', {
    hour: '2-digit', minute: '2-digit', timeZone: MOSCOW_TZ,
  });
}

function showScreen(name) {
  document.querySelectorAll('[data-screen]').forEach((node) => {
    node.hidden = node.dataset.screen !== name;
  });
  document.querySelectorAll('.tab').forEach((node) => {
    node.classList.toggle('active', node.dataset.tab === name);
  });

  if (name === 'achievements' && !state.loaded.achievements) loadAchievements();
  if (name === 'rewards' && !state.loaded.rewards) loadRewards();
  if (name === 'loot' && !state.loaded.loot) loadLoot();
  if (name === 'profile' && !state.loaded.profile) loadProfile();
}

document.querySelectorAll('.tab').forEach((node) => {
  node.addEventListener('click', () => showScreen(node.dataset.tab));
});

document.querySelectorAll('.seg').forEach((node) => {
  node.addEventListener('click', () => {
    document.querySelectorAll('.seg').forEach((s) => s.classList.remove('active'));
    node.classList.add('active');
    state.category = node.dataset.cat;
    renderAchievements();
  });
});

async function loadHome() {
  try {
    state.me = await api('/api/me');
  } catch (error) {
    $('#groupCard').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    return;
  }
  renderHome();
}

function renderHome() {
  const me = state.me;
  $('#topBalance').textContent = `${me.balance} PTS`;

  const group = me.group;
  const next = group.next;
  $('#groupCard').innerHTML = `
    <div class="group-title">${esc(group.title)}</div>
    <div class="discount">Скидка ${group.discount_percent}%</div>
    ${next ? `
      <div class="progress"><i style="width:${next.percent}%"></i></div>
      <div class="progress-legend">
        <span>${group.hours_year} ч</span>
        <span>до «${esc(next.title)}» ещё ${next.hours_left} ч</span>
      </div>` : '<div class="small muted">Максимальная группа — дальше некуда расти.</div>'}
  `;

  $('#statsRow').innerHTML = `
    <div class="stat"><b>${me.stats.visits_year}</b><span>визитов за год</span></div>
    <div class="stat"><b>${me.stats.hours_year}</b><span>часов за год</span></div>
    <div class="stat"><b>${me.stats.achievements_completed}</b><span>достижений выполнено</span></div>
  `;

  renderDaily(me.daily);
}

function renderDaily(daily) {
  const card = $('#dailyCard');
  if (!daily) {
    card.innerHTML = '<div class="small muted">Ежедневная награда сейчас недоступна.</div>';
    return;
  }

  let action;
  if (daily.can_claim) {
    action = `<button class="btn" data-claim="${esc(daily.code)}">Забрать ${daily.reward_pts} PTS</button>`;
  } else if (daily.is_claimed) {
    action = '<span class="badge ok">Забрано</span>';
  } else {
    action = '<span class="badge">Начни сессию</span>';
  }

  card.innerHTML = `
    <div class="grow">
      <div class="item-title">${esc(daily.title)}</div>
      <div class="item-desc" style="margin-bottom:0">${esc(daily.description)}</div>
    </div>
    ${action}
  `;
}

async function loadAchievements() {
  $('#achList').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    state.achievements = await api('/api/achievements');
    state.loaded.achievements = true;
    renderAchievements();
  } catch (error) {
    $('#achList').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

function renderAchievements() {
  const items = (state.achievements && state.achievements[state.category]) || [];
  const list = $('#achList');

  if (!items.length) {
    list.innerHTML = '<div class="empty">В этой категории пока пусто.</div>';
    return;
  }

  list.innerHTML = '';
  items.forEach((item) => list.appendChild(achievementCard(item)));
}

function achievementCard(item) {
  const percent = item.target
    ? Math.min(Math.round((item.progress / item.target) * 100), 100)
    : 0;

  let action = '';
  if (item.can_claim) {
    action = `<button class="btn" data-claim="${esc(item.code)}">Забрать</button>`;
  } else if (item.is_claimed) {
    action = '<span class="badge ok">Награда забрана</span>';
  } else if (!item.is_available) {
    action = '<span class="badge warn">Скоро</span>';
  } else if (item.code === 'special_channel_sub') {
    action = '<button class="btn ghost" data-check-sub="1">Проверить подписку</button>';
  }

  const deadline = item.period_ends_at && !item.is_claimed
    ? `<span class="badge">обнулится через ${timeLeft(item.period_ends_at)}</span>`
    : '<span></span>';

  const classes = ['item'];
  if (item.is_completed) classes.push('done');
  if (!item.is_available) classes.push('locked');

  return el(`
    <div class="${classes.join(' ')}">
      <div class="item-head">
        <div class="item-title">${esc(item.title)}</div>
        <div class="item-pts">+${item.reward_pts} PTS</div>
      </div>
      <div class="item-desc">${esc(item.description)}</div>
      <div class="progress"><i style="width:${percent}%"></i></div>
      <div class="progress-legend"><span>${formatProgress(item)}</span><span>${percent}%</span></div>
      <div class="item-footer">${deadline}${action}</div>
    </div>
  `);
}

async function loadRewards() {
  $('#rewardList').innerHTML = '<div class="skeleton"></div><div class="skeleton"></div>';
  try {
    state.rewards = await api('/api/rewards');
    state.loaded.rewards = true;
    renderRewards();
  } catch (error) {
    $('#rewardList').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

function renderRewards() {
  const data = state.rewards;
  $('#topBalance').textContent = `${data.balance} PTS`;

  const active = data.active_code;
  $('#activeCode').innerHTML = active ? `
    <div class="code-card">
      <div class="small muted">${esc(active.title)}</div>
      <div class="code-value">${esc(active.code)}</div>
      <div class="small muted">Покажи код администратору — сгорит через ${timeLeft(active.expires_at)}</div>
    </div>
  ` : '';

  const list = $('#rewardList');
  list.innerHTML = '';
  data.items.forEach((item) => {
    const value = item.payout_unit === 'RUB'
      ? `${item.payout_value} ₽`
      : `${item.payout_value} мес.`;
    list.appendChild(el(`
      <div class="item">
        <div class="item-head">
          <div class="item-title">${esc(item.title)}</div>
          <div class="item-pts">${item.cost_pts} PTS</div>
        </div>
        <div class="item-desc">${esc(item.description)}</div>
        <div class="item-footer">
          <span class="badge">получаешь ${esc(value)}</span>
          <button class="btn" data-redeem="${item.id}" ${item.affordable && !active ? '' : 'disabled'}>
            ${active ? 'Есть активный код' : (item.affordable ? 'Обменять' : 'Не хватает PTS')}
          </button>
        </div>
      </div>
    `));
  });
}

async function loadProfile() {
  try {
    const ref = await api('/api/referral');
    state.loaded.profile = true;
    $('#referralCard').innerHTML = `
      <div class="item-title">Приглашай друзей</div>
      <div class="item-desc">
        Друг получит бонус, а тебе засчитаем приглашение, когда он отыграет
        ${ref.min_minutes} минут. Приглашено: ${ref.invited_total}, засчитано: ${ref.invited_credited}.
      </div>
      <div class="ref-link">
        <input id="refInput" readonly value="${esc(ref.link)}">
        <button class="btn ghost" id="copyRef">Копировать</button>
      </div>
    `;
    $('#copyRef').addEventListener('click', () => copyText(ref.link));
  } catch (error) {
    $('#referralCard').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

function copyText(text) {
  const done = () => { toast('Ссылка скопирована'); haptic('success'); };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done) {
  const input = $('#refInput');
  if (!input) return;
  input.select();
  input.setSelectionRange(0, 99999);
  document.execCommand('copy');
  done();
}

document.querySelectorAll('.acc-head').forEach((head) => {
  head.addEventListener('click', async () => {
    const key = head.dataset.acc;
    const body = document.querySelector(`[data-acc-body="${key}"]`);
    body.hidden = !body.hidden;
    if (body.hidden || body.dataset.filled) return;

    body.innerHTML = '<div class="empty">Загружаем…</div>';
    try {
      body.innerHTML = await accordionContent(key);
      body.dataset.filled = '1';
    } catch (error) {
      body.innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    }
  });
});

async function accordionContent(key) {
  if (key === 'visits') {
    const data = await api('/api/history/visits');
    if (!data.items.length) return '<div class="empty">Посещений пока нет.</div>';
    return data.items.map((v) => `
      <div class="row">
        <div class="r-main">
          <span>${formatDate(v.started_at)} · ${esc(v.zone)}</span>
          <span class="r-sub">ПК ${v.pc_number} · ${formatTime(v.started_at)}${v.ended_at ? '–' + formatTime(v.ended_at) : ' · идёт'}</span>
        </div>
        <span>${v.hours} ч</span>
      </div>
    `).join('');
  }

  if (key === 'pts') {
    const data = await api('/api/history/pts');
    if (!data.items.length) return '<div class="empty">Операций пока нет.</div>';
    return data.items.map((t) => `
      <div class="row">
        <div class="r-main">
          <span>${esc(t.comment || t.reason)}</span>
          <span class="r-sub">${formatDate(t.created_at)} ${formatTime(t.created_at)}</span>
        </div>
        <span class="amount ${t.amount > 0 ? 'plus' : 'minus'}">${t.amount > 0 ? '+' : ''}${t.amount}</span>
      </div>
    `).join('');
  }

  const data = await api('/api/rules');
  if (key === 'rules') {
    return data.rules.map((r) => `<div class="row"><span>${esc(r)}</span></div>`).join('');
  }
  return data.faq.map((f) => `
    <div class="row">
      <div class="r-main">
        <span>${esc(f.q)}</span>
        <span class="r-sub">${esc(f.a)}</span>
      </div>
    </div>
  `).join('');
}

document.addEventListener('click', async (event) => {
  unlockAudio();

  const claim = event.target.closest('[data-claim]');
  const redeem = event.target.closest('[data-redeem]');
  const checkSub = event.target.closest('[data-check-sub]');
  const spinBtn = event.target.closest('[data-spin]');

  if (spinBtn) {
    playUiClick();
    const allIn = spinBtn.dataset.allin === '1';
    spin(spinBtn.dataset.spin, Number(spinBtn.dataset.count || 1), spinBtn, allIn);
  }

  if (claim) {
    claim.disabled = true;
    try {
      const result = await api('/api/achievements/claim', {
        method: 'POST',
        body: JSON.stringify({ code: claim.dataset.claim }),
      });
      toast(`+${result.credited_pts} PTS`);
      haptic('success');
      await refreshAll();
    } catch (error) {
      toast(error.message);
      haptic('error');
      claim.disabled = false;
    }
  }

  if (redeem) {
    redeem.disabled = true;
    try {
      await api('/api/rewards/redeem', {
        method: 'POST',
        body: JSON.stringify({ reward_id: Number(redeem.dataset.redeem) }),
      });
      toast('Код готов — покажи его администратору');
      haptic('success');
      await refreshAll();
    } catch (error) {
      toast(error.message);
      haptic('error');
      redeem.disabled = false;
    }
  }

  if (checkSub) {
    checkSub.disabled = true;
    try {
      const result = await api('/api/achievements/check-subscription', { method: 'POST' });
      toast(result.subscribed ? 'Подписка найдена' : 'Подписки на канал не видно');
      await loadAchievements();
    } catch (error) {
      toast(error.message);
      checkSub.disabled = false;
    }
  }
});

async function refreshAll() {
  state.loaded = {};
  await loadHome();
  await loadAchievements();
  await loadRewards();
  if (state.loaded.loot !== undefined) await loadLoot();
}

loadHome();

const REEL_CELL_WIDTH = 116;
const REEL_CELL_GAP = 8;
const REEL_STEP = REEL_CELL_WIDTH + REEL_CELL_GAP;
const SPIN_DURATION_MS = 5200;
const SPIN_DURATION_MULTI_MS = 1800;
const REEL_STAGGER_MS = 220;
const SPIN_COUNTS = [1, 5, 10];
const ALL_IN_CAP = 20;

async function loadLoot() {
  $('#lootList').innerHTML = '<div class="skeleton"></div>';
  try {
    state.loot = await api('/api/wheels');
    state.loaded.loot = true;
    renderLoot();
  } catch (error) {
    $('#lootList').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

function renderLoot() {
  const data = state.loot;
  $('#topBalance').textContent = `${data.balance} PTS`;

  if (!data.items.length) {
    $('#lootList').innerHTML = '<div class="empty">Ленты пока не настроены.</div>';
    return;
  }

  $('#lootList').innerHTML = data.items.map((wheel) => `
    <div class="card loot-card">
      <div class="item-head">
        <div class="item-title">${esc(wheel.title)}</div>
        <div class="item-pts">${wheel.cost_pts} PTS</div>
      </div>
      <div class="item-desc">${esc(wheel.description)}</div>
      <div class="loot-prizes">
        ${wheel.prizes.map((p) => `
          <div class="loot-chip rarity-${esc(p.rarity)}">
            <span>${esc(p.title)}</span>
            <b>${p.chance}%</b>
          </div>
        `).join('')}
      </div>
      <div class="loot-spin-row">
        ${SPIN_COUNTS.map((count) => {
          const cost = wheel.cost_pts * count;
          const affordable = data.balance >= cost;
          return `
            <button class="btn" data-spin="${wheel.id}" data-count="${count}" ${affordable ? '' : 'disabled'}>
              x${count}<span class="loot-spin-cost">${cost} PTS</span>
            </button>
          `;
        }).join('')}
      </div>
      ${(() => {
        const allInCount = Math.min(Math.floor(data.balance / wheel.cost_pts), ALL_IN_CAP);
        return `
          <button class="btn allin block" data-spin="${wheel.id}" data-allin="1" ${allInCount > 0 ? '' : 'disabled'}>
            ${allInCount > 0
              ? `ALL IN — x${allInCount} · ${allInCount * wheel.cost_pts} PTS`
              : 'ALL IN — не хватает PTS'}
          </button>
        `;
      })()}
    </div>
  `).join('');
}

function buildReelLanes(count) {
  const grid = $('#reelsGrid');
  grid.innerHTML = Array.from({ length: count }, () => `
    <div class="reel-window ${count > 1 ? 'mini' : ''}">
      <div class="reel-marker"></div>
      <div class="reel"></div>
    </div>
  `).join('');
  return Array.from(grid.querySelectorAll('.reel-window')).map((windowEl) => ({
    windowEl,
    reelEl: windowEl.querySelector('.reel'),
  }));
}

function renderReel(reelEl, reel) {
  reelEl.style.transition = 'none';
  reelEl.style.transform = 'translateX(0)';
  reelEl.style.filter = 'blur(0px)';
  reelEl.innerHTML = reel.map((cell) => `
    <div class="reel-cell rarity-${esc(cell.rarity)}">
      <span>${esc(cell.title)}</span>
    </div>
  `).join('');
  void reelEl.offsetWidth;
}

const REEL_OVERSHOOT = REEL_STEP * 0.4;
const REEL_SETTLE_SHARE = 0.18;

function runReel(windowEl, reelEl, winningIndex, duration, skipRegistry) {
  return new Promise((resolve) => {
    const windowWidth = windowEl.clientWidth;
    const jitter = (Math.random() - 0.5) * (REEL_CELL_WIDTH * 0.5);
    const offset = winningIndex * REEL_STEP + REEL_CELL_WIDTH / 2 - windowWidth / 2 + jitter;
    const settleDuration = Math.round(duration * REEL_SETTLE_SHARE);
    const runDuration = duration - settleDuration;
    let done = false;
    let pendingTimeout;

    function finish() {
      if (done) return;
      done = true;
      clearTimeout(pendingTimeout);
      resolve();
    }

    if (skipRegistry) {
      skipRegistry.push(() => {
        if (done) return;
        reelEl.style.transition = 'none';
        reelEl.style.filter = 'blur(0px)';
        reelEl.style.transform = `translateX(${-offset}px)`;
        finish();
      });
    }

    reelEl.style.filter = 'blur(3px)';
    reelEl.style.transition = `transform ${runDuration}ms cubic-bezier(0.12, 0.72, 0.12, 1)`;
    reelEl.style.transform = `translateX(${-(offset + REEL_OVERSHOOT)}px)`;

    pendingTimeout = setTimeout(() => {
      reelEl.style.transition = `transform ${settleDuration}ms var(--ease-spring), filter ${settleDuration}ms linear`;
      reelEl.style.filter = 'blur(0px)';
      reelEl.style.transform = `translateX(${-offset}px)`;
      pendingTimeout = setTimeout(finish, settleDuration + 60);
    }, runDuration);
  });
}

function applyLandingEffect(windowEl, prize) {
  playLandingSound(prize);
  const cls = { rare: 'reel-hit-rare', epic: 'reel-hit-epic', legendary: 'reel-hit-legendary' }[prize.rarity];
  if (!cls) return;
  windowEl.classList.add(cls);
  if (prize.rarity === 'legendary') {
    hapticImpact('heavy');
    document.body.classList.add('jackpot-shake');
    setTimeout(() => document.body.classList.remove('jackpot-shake'), 500);
  } else if (prize.rarity === 'epic') {
    hapticImpact('medium');
  } else {
    hapticImpact('light');
  }
  setTimeout(() => windowEl.classList.remove(cls), 800);
}

function renderSingleResult(prize) {
  const isEmpty = prize.kind === 'nothing';
  haptic(isEmpty ? 'warning' : 'success');
  $('#spinResult').innerHTML = `
    <div class="prize-label">${isEmpty ? 'Не повезло' : 'Твой приз'}</div>
    <div class="prize-title rarity-${esc(prize.rarity)}">${esc(prize.title)}</div>
    ${prize.code ? `<div class="prize-code">Код: <b>${esc(prize.code)}</b></div>
       <div class="small muted">Покажи код администратору клуба</div>` : ''}
  `;
}

const RARITY_ORDER = { legendary: 3, epic: 2, rare: 1, common: 0 };

function renderMultiResult(spins) {
  const wins = spins.filter((s) => s.prize.kind !== 'nothing');
  const totalPts = spins.reduce((sum, s) => sum + (s.prize.pts_won || 0), 0);
  const codes = spins.map((s) => s.prize).filter((p) => p.code);
  haptic(wins.length ? 'success' : 'warning');
  const sorted = [...spins].sort(
    (a, b) => (RARITY_ORDER[b.prize.rarity] || 0) - (RARITY_ORDER[a.prize.rarity] || 0)
  );
  $('#spinResult').innerHTML = `
    <div class="prize-label">Результаты пачки — x${spins.length}</div>
    <div class="multi-prize-grid">
      ${sorted.map(({ prize }) => `
        <div class="multi-prize-chip rarity-${esc(prize.rarity)}">${esc(prize.title)}</div>
      `).join('')}
    </div>
    ${totalPts > 0 ? `<div class="small muted" style="margin-top:10px">Выиграно PTS: <b>${totalPts}</b></div>` : ''}
    ${codes.length ? `
      <div class="small muted" style="margin-top:4px">Коды наград: ${codes.map((p) => `<b>${esc(p.code)}</b>`).join(', ')}</div>
      <div class="small muted">Покажи коды администратору клуба</div>
    ` : ''}
  `;
}

function evaluateOutcome(spins, costPts) {
  const totalWon = spins.reduce((sum, s) => sum + (s.prize.pts_won || 0), 0);
  const gotReward = spins.some((s) => s.prize.code);
  return { net: totalWon - costPts, gotReward };
}

function spawnCoinRain(count = 26) {
  const container = $('#coinRain');
  container.innerHTML = '';
  const frag = document.createDocumentFragment();
  for (let i = 0; i < count; i += 1) {
    const coin = document.createElement('div');
    coin.className = 'coin';
    coin.style.setProperty('--x', `${Math.random() * 94}%`);
    coin.style.setProperty('--fall-duration', `${1.3 + Math.random() * 1.1}s`);
    coin.style.setProperty('--fall-delay', `${Math.random() * 0.5}s`);
    coin.style.setProperty('--fall-distance', `${window.innerHeight + 80}px`);
    coin.style.setProperty('--spin', `${360 + Math.random() * 360}deg`);
    frag.appendChild(coin);
  }
  container.appendChild(frag);
  setTimeout(() => { container.innerHTML = ''; }, 2700);
}

function triggerWinEffect() {
  haptic('success');
  const flash = $('#winFlash');
  flash.classList.remove('play');
  void flash.offsetWidth;
  flash.classList.add('play');
  setTimeout(() => flash.classList.remove('play'), 900);
  spawnCoinRain();
  playCoinJingle();
}

function triggerWasted() {
  hapticImpact('heavy');
  const overlay = $('#wastedOverlay');
  overlay.hidden = false;
  overlay.classList.remove('play');
  void overlay.offsetWidth;
  overlay.classList.add('play');
  setTimeout(() => {
    overlay.classList.remove('play');
    overlay.hidden = true;
  }, 2200);
}

function applyOutcomeEffect(outcome) {
  if (outcome.gotReward || outcome.net > 0) {
    $('#spinResult').innerHTML += `
      <div class="outcome-banner win">
        <span class="outcome-amount">${outcome.net > 0 ? `+${outcome.net} PTS В ПЛЮСЕ` : 'ПРИЗ ПОЙМАН!'}</span>
      </div>
    `;
    triggerWinEffect();
    playWinChime();
  } else if (outcome.net < 0) {
    triggerWasted();
    playWasted();
  }
}

async function spin(wheelId, count, button, allIn = false) {
  const wheel = state.loot.items.find((w) => w.id === Number(wheelId));

  if (allIn) {
    const sure = await confirmAllIn(
      `Спустить весь баланс на «${wheel ? wheel.title : 'ленту'}»? Обратно не вернуть.`
    );
    if (!sure) return;
  }

  const card = button.closest('.loot-card');
  const siblingButtons = card ? Array.from(card.querySelectorAll('button')) : [button];
  siblingButtons.forEach((b) => { b.disabled = true; });

  let result;
  try {
    result = await api('/api/wheels/spin', {
      method: 'POST',
      body: JSON.stringify(allIn ? { wheel_id: Number(wheelId), all_in: true } : { wheel_id: Number(wheelId), count }),
    });
  } catch (error) {
    toast(error.message);
    haptic('error');
    siblingButtons.forEach((b) => { b.disabled = false; });
    return;
  }

  $('#spinTitle').textContent = wheel ? wheel.title : 'ЛУДЛЕНТА';
  $('#spinResult').hidden = true;
  $('#spinClose').hidden = true;
  $('#spinOverlay').hidden = false;

  const spins = result.spins;
  const duration = spins.length > 1 ? SPIN_DURATION_MULTI_MS : SPIN_DURATION_MS;
  const lanes = buildReelLanes(spins.length);
  lanes.forEach((lane, i) => renderReel(lane.reelEl, spins[i].reel));
  playSpinWhoosh();

  const skipLanes = [];
  const tickTimeouts = [];
  const skipBtn = $('#spinSkipBtn');
  skipBtn.hidden = spins.length < 5;
  skipBtn.onclick = () => {
    playUiClick();
    tickTimeouts.forEach(clearTimeout);
    skipLanes.forEach((fn) => fn());
    skipBtn.hidden = true;
  };

  await Promise.all(
    lanes.map((lane, i) => {
      const laneDuration = duration + i * REEL_STAGGER_MS;
      scheduleReelTicks(laneDuration, tickTimeouts);
      return runReel(lane.windowEl, lane.reelEl, spins[i].winning_index, laneDuration, skipLanes)
        .then(() => applyLandingEffect(lane.windowEl, spins[i].prize));
    })
  );
  skipBtn.hidden = true;

  if (spins.length > 1) {
    renderMultiResult(spins);
  } else {
    renderSingleResult(spins[0].prize);
  }
  applyOutcomeEffect(evaluateOutcome(spins, result.cost_pts));

  $('#spinResult').hidden = false;
  $('#spinClose').hidden = false;
  siblingButtons.forEach((b) => { b.disabled = false; });
}

$('#spinClose').addEventListener('click', async () => {
  $('#spinOverlay').hidden = true;
  await loadHome();
  await loadLoot();
});
