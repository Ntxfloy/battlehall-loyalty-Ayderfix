/* Панель администратора BATTLEHALL. Без сборки, как и мини-апп: экранов
   немного, состояние плоское. api() всегда шлёт куки (credentials: same-origin
   хватает, т.к. панель и API на одном домене). */

const state = { clubs: [], usersPage: 1, usersQuery: '' };

async function api(path, options = {}) {
  const response = await fetch(path, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options));
  const text = await response.text();
  const data = text ? JSON.parse(text) : null;
  if (!response.ok) {
    const error = new Error((data && data.detail) || 'Ошибка запроса');
    error.status = response.status;
    throw error;
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

function fmtDate(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit', hour: '2-digit', minute: '2-digit' });
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

// --- вход / выход ---

async function checkAuth() {
  try {
    const me = await api('/api/console/auth/me');
    state.me = me;
    $('#whoami').textContent = me.display_name || me.username;
    applyPermissions(me);
    $('#loginScreen').hidden = true;
    $('#app').hidden = false;
    boot();
  } catch (error) {
    $('#loginScreen').hidden = false;
    $('#app').hidden = true;
  }
}

$('#loginForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const errorBox = $('#loginError');
  errorBox.hidden = true;
  try {
    await api('/api/console/auth/login', {
      method: 'POST',
      body: JSON.stringify({
        username: $('#loginUsername').value.trim(),
        password: $('#loginPassword').value,
      }),
    });
    checkAuth();
  } catch (error) {
    errorBox.textContent = error.message;
    errorBox.hidden = false;
  }
});

$('#logoutBtn').addEventListener('click', async () => {
  await api('/api/console/auth/logout', { method: 'POST' });
  location.reload();
});

// --- навигация ---

function showView(name) {
  $$('.view').forEach((node) => { node.hidden = node.dataset.view !== name; });
  $$('.nav-item').forEach((node) => node.classList.toggle('active', node.dataset.view === name));

  if (name === 'overview') loadOverview();
  if (name === 'users') loadUsers();
  if (name === 'codes') loadCodesTable();
  if (name === 'clubs') loadClubs();
  if (name === 'logs') loadLogs();
  if (name === 'queue') loadQueue();
  if (name === 'achievements') loadAchievementsAdmin();
  if (name === 'rewards') loadRewardsAdmin();
  if (name === 'loot') loadLootAdmin();
  if (name === 'admins') loadAdmins();
  if (name === 'oasys') loadOasysMap();
  if (name === 'test') loadTestForms();
}

$$('.nav-item').forEach((node) => node.addEventListener('click', () => showView(node.dataset.view)));

function applyPermissions(me) {
  // Сервер всё равно проверяет права на каждой ручке; это только чтобы
  // сотрудник не видел разделов, куда ему всё равно ответят 403.
  const granted = new Set(me.permissions || []);
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

// --- ОБЗОР ---

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

// --- ПОЛЬЗОВАТЕЛИ ---

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
    row.addEventListener('click', () => openUserDetail(Number(row.dataset.tg)));
  });

  const totalPages = Math.max(Math.ceil(data.total / data.page_size), 1);
  $('#usersPager').innerHTML = `
    <button class="btn small" ${page <= 1 ? 'disabled' : ''} id="pagerPrev">← Назад</button>
    <span>${page} / ${totalPages} (${data.total} всего)</span>
    <button class="btn small" ${page >= totalPages ? 'disabled' : ''} id="pagerNext">Вперёд →</button>
  `;
  const prev = $('#pagerPrev');
  const next = $('#pagerNext');
  if (prev) prev.addEventListener('click', () => loadUsers(page - 1));
  if (next) next.addEventListener('click', () => loadUsers(page + 1));
}

$('#userSearchBtn').addEventListener('click', () => {
  state.usersQuery = $('#userSearch').value.trim();
  loadUsers(1);
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
      try {
        const params = new URLSearchParams({ amount: String(amount), comment });
        const result = await api(`/api/console/users/${telegramId}/pts?${params}`, { method: 'POST' });
        $('#ptsGrantResult').textContent = `Новый баланс: ${result.balance} PTS`;
        toast('Готово');
        openUserDetail(telegramId);
        loadUsers(state.usersPage);
      } catch (error) {
        $('#ptsGrantResult').textContent = error.message;
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

// --- КОДЫ ---

let lookedUpCode = null;

$('#codeLookupBtn').addEventListener('click', async () => {
  const code = $('#codeInput').value.trim();
  if (!code) return;
  try {
    const data = await api(`/api/admin/redemptions/${encodeURIComponent(code)}`);
    lookedUpCode = data;
    $('#codeResult').innerHTML = `
      <div class="kv-row"><span class="muted">Награда</span><span>${esc(data.reward)}</span></div>
      <div class="kv-row"><span class="muted">Статус</span><span>${statusBadge(data.status)}</span></div>
      <div class="kv-row"><span class="muted">Гость</span><span>${esc(data.guest.first_name || '')} ${data.guest.username ? '@' + esc(data.guest.username) : ''} (${data.guest.telegram_id})</span></div>
      <div class="kv-row"><span class="muted">Истекает</span><span>${fmtDate(data.expires_at)}</span></div>
    `;
    $('#codeUseBtn').disabled = data.status !== 'pending';
  } catch (error) {
    $('#codeResult').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    $('#codeUseBtn').disabled = true;
  }
});

$('#codeUseBtn').addEventListener('click', async () => {
  if (!lookedUpCode) return;
  try {
    await api('/api/admin/redemptions/use', {
      method: 'POST',
      body: JSON.stringify({ code: lookedUpCode.code }),
    });
    toast('Код погашен');
    $('#codeLookupBtn').click();
    loadCodesTable();
  } catch (error) {
    toast(error.message);
  }
});

async function loadCodesTable() {
  const data = await api('/api/admin/redemptions?status=used&only_new=true');
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

// --- КЛУБЫ ---

async function loadClubs() {
  const data = await api('/api/console/clubs');
  state.clubs = data.items;

  const rows = data.items.map((c) => `
    <tr>
      <td>${esc(c.name)}</td>
      <td><code>${esc(c.slug)}</code></td>
      <td>${c.is_active ? '<span class="badge ok">активен</span>' : '<span class="badge bad">выключен</span>'}</td>
      <td><code>${esc(c.webhook_token)}</code></td>
      <td>
        <button class="btn small" data-toggle="${c.id}" data-active="${c.is_active}">${c.is_active ? 'Выключить' : 'Включить'}</button>
        <button class="btn small" data-rotate="${c.id}">Обновить токен</button>
      </td>
    </tr>
  `);
  $('#clubsTable').innerHTML = table(['Название', 'Slug', 'Статус', 'Токен вебхука', 'Действия'], rows);

  $$('#clubsTable [data-toggle]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      await api(`/api/console/clubs/${btn.dataset.toggle}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: btn.dataset.active !== 'true' }),
      });
      loadClubs();
    });
  });
  $$('#clubsTable [data-rotate]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Старый токен перестанет работать. Продолжить?')) return;
      await api(`/api/console/clubs/${btn.dataset.rotate}/rotate-token`, { method: 'POST' });
      toast('Токен обновлён');
      loadClubs();
    });
  });
}

$('#clubCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/console/clubs', {
      method: 'POST',
      body: JSON.stringify({ slug: $('#clubSlug').value.trim(), name: $('#clubName').value.trim() }),
    });
    $('#clubCreateForm').reset();
    loadClubs();
    toast('Клуб создан');
  } catch (error) {
    toast(error.message);
  }
});

// --- ОТЧЁТЫ ---

$('#reportRunBtn').addEventListener('click', async () => {
  const params = new URLSearchParams();
  if ($('#reportFrom').value) params.set('date_from', $('#reportFrom').value);
  if ($('#reportTo').value) params.set('date_to', $('#reportTo').value);

  const data = await api(`/api/console/reports?${params}`);
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

// --- ТЕСТОВЫЕ ЗАПРОСЫ ---

async function loadTestForms() {
  if (!state.clubs.length) {
    const data = await api('/api/console/clubs');
    state.clubs = data.items;
  }
  const options = state.clubs.map((c) => `<option value="${esc(c.slug)}">${esc(c.name)}</option>`).join('');
  $('#testStartClub').innerHTML = options;
  $('#testEndClub').innerHTML = options;
}

$('#testStartForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await api('/api/console/test/session-start', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testStartClub').value,
        telegram_id: Number($('#testStartTelegramId').value),
        pc_number: Number($('#testStartPc').value),
      }),
    });
    $('#testStartResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
    $('#testEndSessionId').value = data.session_id;
  } catch (error) {
    $('#testStartResult').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
});

$('#testEndForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const minutes = $('#testEndMinutes').value ? Number($('#testEndMinutes').value) : undefined;
    const data = await api('/api/console/test/session-end', {
      method: 'POST',
      body: JSON.stringify({
        club_slug: $('#testEndClub').value,
        telegram_id: Number($('#testEndTelegramId').value),
        session_id: $('#testEndSessionId').value.trim(),
        duration_minutes: minutes,
      }),
    });
    $('#testEndResult').innerHTML = `<pre>${esc(JSON.stringify(data, null, 2))}</pre>`;
  } catch (error) {
    $('#testEndResult').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
});

// --- ЖУРНАЛ ---

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
}

// --- старт ---

checkAuth();
