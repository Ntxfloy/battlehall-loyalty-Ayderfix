/* Панель администратора BATTLEHALL. Без сборки, как и мини-апп: экранов
   немного, состояние плоское. api() всегда шлёт куки (credentials: same-origin
   хватает, т.к. панель и API на одном домене).

   Главное правило ошибок: потеря связи — это НЕ выход из системы. Раньше
   любая ошибка в checkAuth() выкидывала на экран входа, и при моргании Wi-Fi
   админ видел форму логина, хотя сессия была жива. */

const state = { clubs: [], usersPage: 1, usersQuery: '', queuePage: 1 };
const MOSCOW_TZ = 'Europe/Moscow';

class ApiError extends Error {
  constructor(message, { status = 0, isNetwork = false, retryAfter = null } = {}) {
    super(message);
    this.status = status;
    this.isNetwork = isNetwork;
    this.retryAfter = retryAfter;
  }
}

async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, Object.assign({
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
    }, options));
  } catch (e) {
    // fetch падает только на транспорте: нет сети, сервер не ответил, DNS.
    throw new ApiError('Нет связи с сервером', { isNetwork: true });
  }

  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch (e) {
    throw new ApiError('Сервер вернул не JSON', { status: response.status });
  }

  if (!response.ok) {
    const retryAfter = Number(response.headers.get('Retry-After')) || null;
    throw new ApiError((data && data.detail) || 'Ошибка запроса', {
      status: response.status,
      retryAfter,
    });
  }
  return data;
}

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

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
  toast.timer = setTimeout(() => { node.hidden = true; }, 3000);
}

/* Единое место, где техническая ошибка превращается в человеческую фразу. */
function describeError(error) {
  if (!error) return 'Неизвестная ошибка';
  if (error.isNetwork) return 'Нет связи с сервером — проверьте интернет и повторите';
  if (error.status >= 500) return 'Сервер ответил ошибкой — попробуйте ещё раз через минуту';
  if (error.status === 429 && error.retryAfter) {
    const minutes = Math.max(Math.ceil(error.retryAfter / 60), 1);
    return `${error.message} (повторите через ${minutes} мин.)`;
  }
  return error.message || 'Ошибка запроса';
}

/* 401 — единственный случай, когда надо вернуть человека на экран входа.
   Сейчас это стало важнее: смена пароля рвёт чужие сессии на сервере. */
function handleError(error) {
  if (error && error.status === 401) { sessionExpired(); return; }
  toast(describeError(error));
}

/* Один клик — один запрос. Пока запрос идёт, кнопка выключена: без этого
   двойной тап по «Погасить» или «Выгрузить» уходит на сервер дважды — на телефоне
   это происходит постоянно. */
async function busy(nodes, fn) {
  const list = (Array.isArray(nodes) ? nodes : [nodes]).filter(Boolean);
  const previous = list.map((node) => node.disabled);
  list.forEach((node) => { node.disabled = true; });
  document.body.classList.add('is-busy');
  try {
    return await fn();
  } finally {
    document.body.classList.remove('is-busy');
    list.forEach((node, index) => { node.disabled = previous[index]; });
  }
}

/* Загрузчики разделов асинхронные, и без этой обёртки ошибка внутри них
   улетала в консоль, а экран просто оставался пустым. */
async function guard(fn) {
  try {
    return await fn();
  } catch (error) {
    handleError(error);
    return null;
  }
}

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ru-RU', {
    day: '2-digit', month: '2-digit', year: '2-digit',
    hour: '2-digit', minute: '2-digit', timeZone: MOSCOW_TZ,
  });
}

function table(headers, rows) {
  if (!rows.length) return '<div class="empty">Пусто</div>';
  return `
    <table>
      <thead><tr>${headers.map((h) => `<th>${h}</th>`).join('')}</tr></thead>
      <tbody>${rows.join('')}</tbody>
    </table>
  `;
}

function pager(page, totalPages, total) {
  return `
    <button class="btn small" ${page <= 1 ? 'disabled' : ''} data-page="${page - 1}">← Назад</button>
    <span>${page} / ${totalPages}${total == null ? '' : ` (${total} всего)`}</span>
    <button class="btn small" ${page >= totalPages ? 'disabled' : ''} data-page="${page + 1}">Вперёд →</button>
  `;
}

// --- вход и сессия ---

let authRetryTimer = null;

function showLogin(message) {
  $('#loginScreen').hidden = false;
  $('#app').hidden = true;
  const errorBox = $('#loginError');
  if (message) {
    errorBox.textContent = message;
    errorBox.hidden = false;
  } else {
    errorBox.hidden = true;
  }
}

function sessionExpired() {
  state.me = null;
  showLogin('Сессия завершена — войдите заново.');
}

async function checkAuth() {
  clearTimeout(authRetryTimer);
  try {
    const me = await api('/api/console/auth/me');
    state.me = me;
    $('#whoami').textContent = me.display_name || me.username;
    applyPermissions(me);
    $('#loginError').hidden = true;
    $('#loginScreen').hidden = true;
    $('#app').hidden = false;
    boot();
  } catch (error) {
    if (error.status === 401) {
      // Действительно не авторизован: первый визит, логаут или отозванная кука.
      showLogin();
      return;
    }
    // Сеть или 5xx: сессия, скорее всего, жива — говорим правду и пробуем снова.
    showLogin(`${describeError(error)}. Повторяем попытку…`);
    authRetryTimer = setTimeout(checkAuth, 5000);
  }
}

$('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const errorBox = $('#loginError');
  const submitBtn = $('#loginForm button[type="submit"]');
  errorBox.hidden = true;
  clearTimeout(authRetryTimer);
  try {
    await busy(submitBtn, () => api('/api/console/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        username: $('#loginUsername').value.trim(),
        password: $('#loginPassword').value,
      }),
    }));
    $('#loginPassword').value = '';
    checkAuth();
  } catch (error) {
    errorBox.textContent = describeError(error);
    errorBox.hidden = false;
  }
});

$('#logoutBtn').addEventListener('click', async (event) => {
  try {
    await busy(event.currentTarget, () => api('/api/console/auth/logout', { method: 'POST' }));
  } catch (error) {
    // Выйти надо в любом случае: кука либо уже мёртвая, либо сервер недоступен.
  }
  location.reload();
});

// --- навигация ---

const VIEW_LOADERS = {
  overview: () => loadOverview(),
  users: () => loadUsers(),
  codes: () => loadCodesTable(),
  clubs: () => loadClubs(),
  logs: () => loadLogs(),
  queue: () => loadQueue(1),
  achievements: () => loadAchievementsAdmin(),
  rewards: () => loadRewardsAdmin(),
  loot: () => loadLootAdmin(),
  admins: () => loadAdmins(),
  oasys: () => loadOasysMap(),
  test: () => loadTestForms(),
};

function showView(name) {
  $$('.view').forEach((node) => { node.hidden = node.dataset.view !== name; });
  $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.view === name));

  const loader = VIEW_LOADERS[name];
  if (loader) guard(loader);
}

$$('.nav-item').forEach((node) => node.addEventListener('click', () => showView(node.dataset.view)));

function applyPermissions(me) {
  const granted = new Set(me.permissions || []);
  if (granted.has('clubs.edit')) granted.add('clubs.view');
  if (granted.has('reports.export')) granted.add('reports.view');
  $$('.nav-item').forEach((node) => {
    const needed = node.dataset.perm;
    node.hidden = Boolean(needed) && !granted.has(needed);
  });
}

function firstAvailableView() {
  const node = $$('.nav-item').find((n) => !n.hidden);
  return node ? node.dataset.view : 'desk';
}

function boot() {
  showView(firstAvailableView());
}

async function loadOverview() {
  const data = await api('/api/console/reports');
  $('#overviewStats').innerHTML = `
    <div class="stat-tile"><b>${data.total_users_in_program}</b><span>пользователей в программе</span></div>
    <div class="stat-tile"><b>${data.total_sessions}</b><span>сессий всего</span></div>
    <div class="stat-tile"><b>${data.total_hours}</b><span>часов всего</span></div>
    <div class="stat-tile"><b>${data.clubs.length}</b><span>клубов в сети</span></div>
  `;

  const rows = data.clubs.map((c) => `
    <tr>
      <td>${esc(c.club.name)}</td>
      <td>${c.sessions}</td>
      <td>${c.unique_guests}</td>
      <td>${c.total_hours}</td>
    </tr>
  `);
  $('#overviewClubs').innerHTML = table(['Клуб', 'Сессий', 'Уникальных гостей', 'Часов'], rows);
}

async function loadUsers(page = 1) {
  state.usersPage = page;
  const params = new URLSearchParams({ page: String(page), page_size: '30' });
  if (state.usersQuery) params.set('q', state.usersQuery);

  const data = await api(`/api/console/users?${params}`);
  const rows = data.items.map((u) => `
    <tr class="clickable" data-tg="${u.telegram_id}">
      <td>${u.telegram_id}</td>
      <td>${esc(u.first_name || '—')} ${u.username ? '@' + esc(u.username) : ''}</td>
      <td>${esc(u.phone || '—')}</td>
      <td>${u.balance} PTS</td>
      <td>${esc(u.group_title)}</td>
      <td>${fmtDate(u.created_at)}</td>
    </tr>
  `);
  $('#usersTable').innerHTML = table(['Telegram ID', 'Имя', 'Телефон', 'Баланс', 'Группа', 'Регистрация'], rows);

  $$('#usersTable tr.clickable').forEach((row) => {
    row.addEventListener('click', () => guard(() => openUserDetail(Number(row.dataset.tg))));
  });

  const totalPages = Math.max(Math.ceil(data.total / data.page_size), 1);
  $('#usersPager').innerHTML = pager(page, totalPages, data.total);
  $$('#usersPager [data-page]').forEach((btn) => {
    btn.addEventListener('click', () => guard(() => loadUsers(Number(btn.dataset.page))));
  });
}

$('#userSearchBtn').addEventListener('click', () => {
  state.usersQuery = $('#userSearch').value.trim();
  guard(() => loadUsers(1));
});
$('#userSearch').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') $('#userSearchBtn').click();
});

async function openUserDetail(telegramId) {
  const data = await api(`/api/console/users/${telegramId}`);
  const u = data.user;

  $('#userDetailTitle').textContent = `${u.first_name || 'Без имени'} ${u.username ? '@' + u.username : ''}`;

  const visitsRows = data.visits.slice(0, 15).map((v) => `
    <tr>
      <td>${fmtDate(v.started_at)}</td>
      <td>${esc(v.zone)}</td>
      <td>ПК ${v.pc_number}</td>
      <td>${v.is_closed ? (v.minutes / 60).toFixed(1) + ' ч' : '<span class="badge warn">идёт</span>'}</td>
    </tr>
  `);

  const ptsRows = data.pts_history.slice(0, 15).map((t) => `
    <tr>
      <td>${fmtDate(t.created_at)}</td>
      <td>${esc(t.comment || t.reason)}</td>
      <td class="amount ${t.amount > 0 ? 'plus' : 'minus'}">${t.amount > 0 ? '+' : ''}${t.amount}</td>
      <td>${t.balance_after}</td>
    </tr>
  `);

  const redemptionRows = data.redemptions.slice(0, 15).map((r) => `
    <tr>
      <td>${fmtDate(r.created_at)}</td>
      <td>${esc(r.title)}</td>
      <td><code>${esc(r.code)}</code></td>
      <td>${statusBadge(r.status)}</td>
    </tr>
  `);

  const achRows = [];
  Object.values(data.achievements).forEach((list) => {
    list.forEach((a) => {
      if (a.is_completed) {
        achRows.push(`
          <tr>
            <td>${esc(a.title)}</td>
            <td>${a.progress}/${a.target} ${esc(a.unit)}</td>
            <td>${a.is_claimed ? '<span class="badge ok">забрано</span>' : '<span class="badge warn">не забрано</span>'}</td>
          </tr>
        `);
      }
    });
  });

  const canGrantPts = (state.me && state.me.permissions || []).includes('pts.grant');

  $('#userDetailBody').innerHTML = `
    <div class="kv-row"><span class="muted">Telegram ID</span><span>${u.telegram_id}</span></div>
    <div class="kv-row"><span class="muted">Телефон</span><span>${esc(u.phone || '—')}</span></div>
    <div class="kv-row"><span class="muted">Баланс</span><span>${u.balance} PTS</span></div>
    <div class="kv-row"><span class="muted">Группа</span><span>${esc(data.group)}</span></div>
    <div class="kv-row"><span class="muted">Часов за год</span><span>${data.stats.hours}</span></div>
    <div class="kv-row"><span class="muted">Реферальный код</span><span><code>${esc(u.referral_code)}</code></span></div>
    <div class="kv-row"><span class="muted">Регистрация</span><span>${fmtDate(u.created_at)}</span></div>

    ${canGrantPts ? `
      <h3>Начислить / списать PTS</h3>
      <form id="ptsGrantForm" class="inline-form">
        <input id="ptsAmount" type="number" placeholder="Сумма (минус — списание)" required>
        <input id="ptsComment" type="text" placeholder="Комментарий" value="Ручное начисление (демо)">
        <button class="btn primary" type="submit">Применить</button>
      </form>
      <div id="ptsGrantResult" class="small muted"></div>
    ` : ''}

    <h3>Выполненные достижения</h3>
    ${table(['Достижение', 'Прогресс', 'Статус'], achRows)}

    <h3>Последние визиты</h3>
    ${table(['Когда', 'Зона', 'ПК', 'Время'], visitsRows)}

    <h3>История PTS</h3>
    ${table(['Когда', 'За что', 'Сумма', 'Баланс после'], ptsRows)}

    <h3>Обмены на награды</h3>
    ${table(['Когда', 'Награда', 'Код', 'Статус'], redemptionRows)}
  `;

  if (canGrantPts) {
    $('#ptsGrantForm').addEventListener('submit', async (event) => {
      event.preventDefault();
      const amount = Number($('#ptsAmount').value);
      const comment = $('#ptsComment').value.trim() || 'Ручное начисление (демо)';
      if (!amount) { toast('Сумма не может быть нулевой'); return; }
      // Ручные деньги — необратимая операция, спрашиваем подтверждение.
      const verb = amount > 0 ? 'Начислить' : 'Списать';
      if (!confirm(`${verb} ${Math.abs(amount)} PTS гостю ${telegramId}?`)) return;
      const submitBtn = $('#ptsGrantForm button[type="submit"]');
      try {
        const result = await busy(submitBtn, () => api(`/api/console/users/${telegramId}/pts`, {
          method: 'POST',
          body: JSON.stringify({ amount, comment }),
        }));
        $('#ptsGrantResult').textContent = `Новый баланс: ${result.balance} PTS`;
        toast('Готово');
        guard(() => openUserDetail(telegramId));
        guard(() => loadUsers(state.usersPage));
      } catch (error) {
        $('#ptsGrantResult').textContent = describeError(error);
        if (error.status === 401) sessionExpired();
      }
    });
  }

  $('#userDetail').hidden = false;
  $('#userDetailBackdrop').hidden = false;
}

function closeUserDetail() {
  $('#userDetail').hidden = true;
  $('#userDetailBackdrop').hidden = true;
}

$('#userDetailClose').addEventListener('click', closeUserDetail);
$('#userDetailBackdrop').addEventListener('click', closeUserDetail);

function statusBadge(status) {
  const map = {
    pending: '<span class="badge warn">ожидает</span>',
    submitted: '<span class="badge warn">ждёт подтверждения</span>',
    approved: '<span class="badge ok">подтверждён</span>',
    used: '<span class="badge ok">погашен</span>',
    expired: '<span class="badge bad">сгорел</span>',
    cancelled: '<span class="badge">отменён</span>',
  };
  return map[status] || esc(status);
}

let lookedUpCode = null;

async function lookupCode() {
  const code = $('#codeInput').value.trim();
  if (!code) return;
  try {
    const data = await busy([$('#codeLookupBtn')], () => api(`/api/admin/redemptions/${encodeURIComponent(code)}`));
    lookedUpCode = data;
    $('#codeResult').innerHTML = `
      <div class="kv-row"><span class="muted">Награда</span><span>${esc(data.reward)}</span></div>
      <div class="kv-row"><span class="muted">Статус</span><span>${statusBadge(data.status)}</span></div>
      <div class="kv-row"><span class="muted">Гость</span><span>${esc(data.guest.first_name || '')} ${data.guest.username ? '@' + esc(data.guest.username) : ''} (${data.guest.telegram_id})</span></div>
      <div class="kv-row"><span class="muted">Истекает</span><span>${fmtDate(data.expires_at)}</span></div>
    `;
    $('#codeUseBtn').disabled = data.status !== 'pending';
  } catch (error) {
    lookedUpCode = null;
    $('#codeResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    $('#codeUseBtn').disabled = true;
    if (error.status === 401) sessionExpired();
  }
}

$('#codeLookupBtn').addEventListener('click', lookupCode);
$('#codeInput').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') lookupCode();
});

$('#codeUseBtn').addEventListener('click', async (event) => {
  if (!lookedUpCode) return;
  // Погашение откатить нельзя, поэтому спрашиваем явно.
  if (!confirm(`Погасить код ${lookedUpCode.code} (${lookedUpCode.reward})?`)) return;
  try {
    await busy(event.currentTarget, () => api('/api/admin/redemptions/use', {
      method: 'POST',
      body: JSON.stringify({ code: lookedUpCode.code }),
    }));
    toast('Код погашен');
    await lookupCode();
    guard(() => loadCodesTable());
  } catch (error) {
    handleError(error);
  }
});

async function loadCodesTable() {
  const data = await api('/api/admin/redemptions?status=approved&only_new=true');
  const rows = data.items.map((r) => `
    <tr>
      <td><code>${esc(r.code)}</code></td>
      <td>${esc(r.reward)}</td>
      <td>${esc(r.username ? '@' + r.username : (r.phone || r.telegram_id))}</td>
      <td>${fmtDate(r.used_at)}</td>
    </tr>
  `);
  $('#codesTable').innerHTML = table(['Код', 'Награда', 'Гость', 'Погашен'], rows);
}

async function loadClubs() {
  const data = await api('/api/console/clubs');
  state.clubs = data.items;

  const rows = data.items.map((c) => `
    <tr>
      <td>${esc(c.name)}</td>
      <td><code>${esc(c.slug)}</code></td>
      <td>${c.is_active ? '<span class="badge ok">активен</span>' : '<span class="badge bad">выключен</span>'}</td>
      <td>${c.webhook_configured ? '<span class="badge ok">токен задан</span>' : '<span class="badge bad">токен не задан</span>'}</td>
      <td>
        <button class="btn small" data-toggle="${c.id}" data-active="${c.is_active}">${c.is_active ? 'Выключить' : 'Включить'}</button>
        <button class="btn small" data-rotate="${c.id}">Обновить токен</button>
      </td>
    </tr>
  `);
  $('#clubsTable').innerHTML = table(['Название', 'Slug', 'Статус', 'Вебхук', 'Действия'], rows);

  $$('#clubsTable [data-toggle]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const turningOff = btn.dataset.active === 'true';
      if (turningOff && !confirm('Выключенный клуб перестанет принимать вебхуки OASys. Продолжить?')) return;
      try {
        await busy(btn, () => api(`/api/console/clubs/${btn.dataset.toggle}`, {
          method: 'PATCH',
          body: JSON.stringify({ is_active: !turningOff }),
        }));
        guard(() => loadClubs());
      } catch (error) {
        handleError(error);
      }
    });
  });
  $$('#clubsTable [data-rotate]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Старый токен перестанет работать. Продолжить?')) return;
      try {
        await busy(btn, () => api(`/api/console/clubs/${btn.dataset.rotate}/rotate-token`, { method: 'POST' }));
        toast('Токен обновлён');
        guard(() => loadClubs());
      } catch (error) {
        handleError(error);
      }
    });
  });
}

$('#clubCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    await busy(submitBtn, () => api('/api/console/clubs', {
      method: 'POST',
      body: JSON.stringify({ slug: $('#clubSlug').value.trim(), name: $('#clubName').value.trim() }),
    }));
    $('#clubCreateForm').reset();
    guard(() => loadClubs());
    toast('Клуб создан');
  } catch (error) {
    handleError(error);
  }
});

$('#reportRunBtn').addEventListener('click', async (event) => {
  const params = new URLSearchParams();
  if ($('#reportFrom').value) params.set('date_from', $('#reportFrom').value);
  if ($('#reportTo').value) params.set('date_to', $('#reportTo').value);

  let data;
  try {
    data = await busy(event.currentTarget, () => api(`/api/console/reports?${params}`));
  } catch (error) {
    handleError(error);
    return;
  }

  $('#reportTotals').innerHTML = `
    <div class="stat-tile"><b>${data.total_sessions}</b><span>сессий</span></div>
    <div class="stat-tile"><b>${data.total_hours}</b><span>часов</span></div>
    <div class="stat-tile"><b>${data.total_users_in_program}</b><span>пользователей в программе</span></div>
  `;

  const rows = data.clubs.map((c) => {
    const zones = Object.entries(c.minutes_by_zone_type)
      .map(([type, minutes]) => `${esc(type)}: ${(minutes / 60).toFixed(1)}ч`)
      .join(', ') || '—';
    return `
      <tr>
        <td>${esc(c.club.name)}</td>
        <td>${c.sessions}</td>
        <td>${c.unique_guests}</td>
        <td>${c.unique_game_days}</td>
        <td>${c.total_hours}</td>
        <td class="small muted">${zones}</td>
      </tr>
    `;
  });
  $('#reportsTable').innerHTML = table(
    ['Клуб', 'Сессий', 'Уникальных гостей', 'Игровых дней', 'Часов', 'По типам зон'],
    rows
  );
});

async function loadTestForms() {
  if (!state.clubs.length) {
    const data = await api('/api/console/clubs');
    state.clubs = data.items;
  }
  const options = state.clubs.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join('');
  $('#testStartClub').innerHTML = options;
  $('#testEndClub').innerHTML = options;
  $('#testBookingClub').innerHTML = options;
  $('#testPurchaseClub').innerHTML = options;
  $('#testBalanceOpClub').innerHTML = options;
}

$('#testStartForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    const data = await busy(submitBtn, () => api('/api/console/test/session-start', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testStartClub').value,
        telegram_id: Number($('#testStartTelegramId').value),
        pc_number: Number($('#testStartPc').value),
      }),
    }));
    $('#testStartResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
    $('#testEndSessionId').value = data.session_id;
  } catch (error) {
    $('#testStartResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    if (error.status === 401) sessionExpired();
  }
});

$('#testEndForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    const minutes = $('#testEndMinutes').value ? Number($('#testEndMinutes').value) : undefined;
    const data = await busy(submitBtn, () => api('/api/console/test/session-end', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testEndClub').value,
        telegram_id: Number($('#testEndTelegramId').value),
        session_id: $('#testEndSessionId').value.trim(),
        duration_minutes: minutes,
      }),
    }));
    $('#testEndResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (error) {
    $('#testEndResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    if (error.status === 401) sessionExpired();
  }
});

$('#testBookingForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    const data = await busy(submitBtn, () => api('/api/console/test/booking', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testBookingClub').value,
        telegram_id: Number($('#testBookingTelegramId').value),
        status: $('#testBookingStatus').value,
        pc_number: $('#testBookingPc').value ? Number($('#testBookingPc').value) : undefined,
      }),
    }));
    $('#testBookingResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (error) {
    $('#testBookingResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    if (error.status === 401) sessionExpired();
  }
});

$('#testPurchaseForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    const data = await busy(submitBtn, () => api('/api/console/test/purchase', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testPurchaseClub').value,
        telegram_id: Number($('#testPurchaseTelegramId').value),
        sku: $('#testPurchaseSku').value,
        amount: $('#testPurchaseAmount').value ? Number($('#testPurchaseAmount').value) : 0,
      }),
    }));
    $('#testPurchaseResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (error) {
    $('#testPurchaseResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    if (error.status === 401) sessionExpired();
  }
});

$('#testBalanceOpForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submitBtn = event.target.querySelector('button[type="submit"]');
  try {
    const data = await busy(submitBtn, () => api('/api/console/test/balance-operation', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testBalanceOpClub').value,
        telegram_id: Number($('#testBalanceOpTelegramId').value),
        operation_type: $('#testBalanceOpType').value,
        amount: Number($('#testBalanceOpAmount').value),
      }),
    }));
    $('#testBalanceOpResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (error) {
    $('#testBalanceOpResult').innerHTML = `<div class="empty">${esc(describeError(error))}</div>`;
    if (error.status === 401) sessionExpired();
  }
});

async function loadLogs() {
  const data = await api('/api/console/logs?page_size=100');
  const rows = data.items.map((l) => `
    <tr>
      <td>${fmtDate(l.created_at)}</td>
      <td>${esc(l.admin)}</td>
      <td>${esc(l.action)}</td>
      <td>${esc(l.target_type || '')} ${esc(l.target_id || '')}</td>
      <td class="small muted">${esc(l.detail || '')}</td>
    </tr>
  `);
  $('#logsTable').innerHTML = table(['Когда', 'Админ', 'Действие', 'Объект', 'Детали'], rows);

  const inbox = await api('/api/console/webhook-inbox?page_size=100');
  const inboxRows = inbox.items.map((e) => `
    <tr>
      <td>${fmtDate(e.created_at)}</td>
      <td>${esc(e.endpoint)}</td>
      <td>${esc(e.status)}${e.error ? `: ${esc(e.error)}` : ''}</td>
      <td class="small muted"><code>${esc(e.raw_body)}</code></td>
    </tr>
  `);
  $('#webhookInboxTable').innerHTML = table(['Когда', 'Эндпоинт', 'Статус', 'Сырое тело'], inboxRows);
}

// Последний рубеж: любая непойманная ошибка запроса всё равно показывается человеку.
window.addEventListener('unhandledrejection', (event) => {
  const error = event.reason;
  if (error instanceof ApiError) {
    event.preventDefault();
    handleError(error);
  }
});

checkAuth();
