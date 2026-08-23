/* Разделы админки, появившиеся вместе с ролями и ЛУДЛЕНТой.
   Отдельный файл, чтобы app.js не разрастался: помощники ($, api, table,
   toast, state) объявлены там и доступны здесь — оба скрипта обычные,
   в одной глобальной области, и этот подключается после app.js. */

// --- СТОЙКА ---

async function deskSearch() {
  const q = $('#deskSearch').value.trim();
  if (q.length < 2) { toast('Введите минимум 2 символа'); return; }

  try {
    const data = await api(`/api/console/desk/search?q=${encodeURIComponent(q)}`);
    if (!data.items.length) {
      $('#deskResults').innerHTML = '<div class="empty">Ничего не найдено</div>';
      return;
    }
    const rows = data.items.map((r) => `
      <tr>
        <td><code>${esc(r.code)}</code></td>
        <td>${esc(r.guest.first_name || '')} ${r.guest.username ? '@' + esc(r.guest.username) : ''}</td>
        <td>${esc(r.guest.phone || '—')}</td>
        <td>${esc(r.reward)}</td>
        <td>${statusBadge(r.status)}</td>
        <td>${r.status === 'pending' ? `<button class="btn small primary" data-desk-submit="${esc(r.code)}">Внести</button>` : ''}</td>
      </tr>
    `);
    $('#deskResults').innerHTML = table(['Код', 'Гость', 'Телефон', 'Награда', 'Статус', ''], rows);

    $$('#deskResults [data-desk-submit]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await api('/api/console/desk/submit', {
            method: 'POST',
            body: JSON.stringify({ code: btn.dataset.deskSubmit }),
          });
          toast('Код внесён, ждёт подтверждения');
          deskSearch();
        } catch (error) {
          toast(error.message);
          btn.disabled = false;
        }
      });
    });
  } catch (error) {
    $('#deskResults').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

// --- ОЧЕРЕДЬ ПОДТВЕРЖДЕНИЯ ---

async function loadQueue() {
  const data = await api('/api/console/desk/queue');
  const rows = data.items.map((r) => `
    <tr>
      <td><code>${esc(r.code)}</code></td>
      <td>${esc(r.guest.username ? '@' + r.guest.username : (r.guest.phone || r.guest.telegram_id))}</td>
      <td>${esc(r.reward)}</td>
      <td>${r.payout_value} ${r.payout_unit === 'RUB' ? '₽' : 'мес.'}</td>
      <td>${esc(r.used_by || '')}</td>
      <td>${fmtDate(r.used_at)}</td>
      <td>
        <button class="btn small primary" data-approve="${esc(r.code)}">Подтвердить</button>
        <button class="btn small danger" data-reject="${esc(r.code)}">Отклонить</button>
      </td>
    </tr>
  `);
  $('#queueTable').innerHTML = table(
    ['Код', 'Гость', 'Награда', 'Номинал', 'Внёс', 'Когда', ''], rows
  );

  const bind = (attr, path, message) => {
    $$(`#queueTable [${attr}]`).forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await api(path, {
            method: 'POST',
            body: JSON.stringify({ code: btn.getAttribute(attr) }),
          });
          toast(message);
          loadQueue();
        } catch (error) {
          toast(error.message);
          btn.disabled = false;
        }
      });
    });
  };
  bind('data-approve', '/api/console/desk/approve', 'Подтверждено');
  bind('data-reject', '/api/console/desk/reject', 'Отклонено');

  loadSheetsStatus();
}

// --- Google Sheets ---

async function loadSheetsStatus() {
  try {
    const s = await api('/api/console/sheets/status');
    $('#sheetsStatus').innerHTML = s.configured
      ? `Подключено. Лист «${esc(s.worksheet)}», автовыгрузка ${s.autoexport ? 'включена' : 'выключена'}. Ждёт выгрузки: <b>${s.pending}</b>`
      : `Не настроено — выгрузка отдаёт JSON для ручного переноса. Ждёт выгрузки: <b>${s.pending}</b>`;
  } catch (error) {
    $('#sheetsStatus').textContent = error.message;
  }
}

$('#sheetsCheckBtn').addEventListener('click', async () => {
  $('#sheetsResult').innerHTML = '<div class="small muted">Проверяем доступ…</div>';
  try {
    const r = await api('/api/console/sheets/check', { method: 'POST' });
    $('#sheetsResult').innerHTML = `<pre>Таблица: ${esc(r.spreadsheet)}
Лист: ${esc(r.worksheet)}
Строк: ${r.rows}</pre>`;
  } catch (error) {
    $('#sheetsResult').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
});

$('#sheetsExportBtn').addEventListener('click', async () => {
  try {
    const r = await api('/api/console/sheets/export', { method: 'POST' });
    toast(r.exported ? `Выгружено строк: ${r.exported}` : 'Нечего выгружать');
    loadSheetsStatus();
  } catch (error) {
    toast(error.message);
  }
});

// --- общий сборщик значений строки таблицы ---

function collectRow(scope, attr, id) {
  const payload = {};
  $$(`${scope} [${attr}="${id}"]`).forEach((input) => {
    if (input.type === 'checkbox') payload[input.dataset.field] = input.checked;
    else if (input.type === 'number') payload[input.dataset.field] = Number(input.value);
    else payload[input.dataset.field] = input.value;
  });
  return payload;
}

// --- ДОСТИЖЕНИЯ ---

async function loadAchievementsAdmin() {
  const data = await api('/api/console/achievements');
  const rows = data.items.map((a) => `
    <tr>
      <td><code>${esc(a.code)}</code></td>
      <td><input data-ach="${a.id}" data-field="title" value="${esc(a.title)}" style="min-width:190px"></td>
      <td>${esc(a.category)}</td>
      <td><input data-ach="${a.id}" data-field="target" type="number" min="1" value="${a.target}" style="width:80px"></td>
      <td>${esc(a.unit)}</td>
      <td><input data-ach="${a.id}" data-field="reward_pts" type="number" min="0" value="${a.reward_pts}" style="width:90px"></td>
      <td>${a.is_implemented ? '<span class="badge ok">считается</span>' : '<span class="badge warn">нет счётчика</span>'}</td>
      <td><input data-ach="${a.id}" data-field="is_active" type="checkbox" ${a.is_active ? 'checked' : ''}></td>
      <td><button class="btn small primary" data-ach-save="${a.id}">Сохранить</button></td>
    </tr>
  `);
  $('#achTable').innerHTML = table(
    ['Код', 'Название', 'Категория', 'Цель', 'Ед.', 'PTS', 'Счётчик', 'Вкл.', ''], rows
  );

  $$('#achTable [data-ach-save]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.achSave;
      btn.disabled = true;
      try {
        await api(`/api/console/achievements/${id}`, {
          method: 'PATCH',
          body: JSON.stringify(collectRow('#achTable', 'data-ach', id)),
        });
        toast('Сохранено');
      } catch (error) {
        toast(error.message);
      }
      btn.disabled = false;
    });
  });
}

// --- НАГРАДЫ ---

async function loadRewardsAdmin() {
  const data = await api('/api/console/rewards');
  const rows = data.items.map((r) => `
    <tr>
      <td><code>${esc(r.code)}</code></td>
      <td><input data-rw="${r.id}" data-field="title" value="${esc(r.title)}" style="min-width:200px"></td>
      <td><input data-rw="${r.id}" data-field="cost_pts" type="number" min="0" value="${r.cost_pts}" style="width:100px"></td>
      <td><input data-rw="${r.id}" data-field="payout_value" type="number" step="0.01" value="${r.payout_value}" style="width:90px"></td>
      <td>${r.payout_unit === 'RUB' ? '₽' : 'мес.'}</td>
      <td><input data-rw="${r.id}" data-field="is_active" type="checkbox" ${r.is_active ? 'checked' : ''}></td>
      <td><button class="btn small primary" data-rw-save="${r.id}">Сохранить</button></td>
    </tr>
  `);
  $('#rewardsTable').innerHTML = table(['Код', 'Название', 'Цена PTS', 'Номинал', 'Ед.', 'Вкл.', ''], rows);

  $$('#rewardsTable [data-rw-save]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.rwSave;
      btn.disabled = true;
      try {
        await api(`/api/console/rewards/${id}`, {
          method: 'PATCH',
          body: JSON.stringify(collectRow('#rewardsTable', 'data-rw', id)),
        });
        toast('Сохранено');
      } catch (error) {
        toast(error.message);
      }
      btn.disabled = false;
    });
  });
}

// --- ЛУДЛЕНТА ---

function prizeKindLabel(kind) {
  return { pts: 'PTS', reward: 'награда', nothing: 'пусто' }[kind] || kind;
}

async function loadLootAdmin() {
  const data = await api('/api/console/wheels');
  state.rewardsForPrizes = data.rewards;

  if (!data.items.length) {
    $('#wheelsList').innerHTML = '<div class="empty">Лент пока нет</div>';
    return;
  }

  $('#wheelsList').innerHTML = data.items.map((w) => {
    const totalWeight = w.prizes.filter((p) => p.is_active).reduce((sum, p) => sum + p.weight, 0);
    const rows = w.prizes.map((p) => `
      <tr>
        <td><input data-prize="${p.id}" data-field="title" value="${esc(p.title)}" style="min-width:150px"></td>
        <td>${esc(prizeKindLabel(p.kind))}</td>
        <td><input data-prize="${p.id}" data-field="pts_amount" type="number" min="0" value="${p.pts_amount}" style="width:90px"></td>
        <td><input data-prize="${p.id}" data-field="weight" type="number" min="0" value="${p.weight}" style="width:70px"></td>
        <td><b style="color:var(--bh-cyan)">${p.chance}%</b></td>
        <td><input data-prize="${p.id}" data-field="is_active" type="checkbox" ${p.is_active ? 'checked' : ''}></td>
        <td><button class="btn small primary" data-prize-save="${p.id}">Сохранить</button></td>
      </tr>
    `);

    return `
      <div class="panel">
        <div class="toolbar" style="justify-content:space-between">
          <h2 style="margin:0">${esc(w.title)} <span class="muted small">(${esc(w.code)})</span></h2>
          <span class="badge">${w.cost_pts} PTS за прокрутку · сумма весов ${totalWeight}</span>
        </div>
        ${table(['Приз', 'Тип', 'PTS', 'Вес', 'Шанс', 'Вкл.', ''], rows)}
        <form class="inline-form" data-add-prize="${w.id}" style="margin-top:14px">
          <input name="title" placeholder="Название приза" required>
          <select name="kind">
            <option value="pts">PTS</option>
            <option value="reward">Награда из каталога</option>
            <option value="nothing">Пусто</option>
          </select>
          <input name="pts_amount" type="number" min="0" placeholder="Сколько PTS" value="0">
          <select name="reward_id">
            <option value="">— награда —</option>
            ${data.rewards.map((r) => `<option value="${r.id}">${esc(r.title)}</option>`).join('')}
          </select>
          <select name="rarity">
            <option value="common">обычный</option>
            <option value="rare">редкий</option>
            <option value="epic">эпический</option>
            <option value="legendary">легендарный</option>
          </select>
          <input name="weight" type="number" min="0" value="10" style="width:80px" placeholder="Вес">
          <button class="btn" type="submit">Добавить приз</button>
        </form>
      </div>
    `;
  }).join('');

  $$('#wheelsList [data-prize-save]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const id = btn.dataset.prizeSave;
      btn.disabled = true;
      try {
        await api(`/api/console/prizes/${id}`, {
          method: 'PATCH',
          body: JSON.stringify(collectRow('#wheelsList', 'data-prize', id)),
        });
        toast('Сохранено');
        loadLootAdmin();
      } catch (error) {
        toast(error.message);
        btn.disabled = false;
      }
    });
  });

  $$('#wheelsList [data-add-prize]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      const fd = new FormData(form);
      try {
        await api(`/api/console/wheels/${form.dataset.addPrize}/prizes`, {
          method: 'POST',
          body: JSON.stringify({
            title: fd.get('title'),
            kind: fd.get('kind'),
            rarity: fd.get('rarity'),
            pts_amount: Number(fd.get('pts_amount') || 0),
            reward_id: fd.get('reward_id') ? Number(fd.get('reward_id')) : null,
            weight: Number(fd.get('weight') || 1),
          }),
        });
        toast('Приз добавлен');
        loadLootAdmin();
      } catch (error) {
        toast(error.message);
      }
    });
  });
}

// --- АДМИНИСТРАТОРЫ ---

function permLabel(code) {
  const found = (state.permissionCatalog || []).find((p) => p.code === code);
  return found ? found.label : code;
}

function closePermissionEditor() {
  $('#permEditor').hidden = true;
  $('#permEditorBackdrop').hidden = true;
}

async function loadAdmins() {
  const data = await api('/api/console/admins');
  state.permissionCatalog = data.available_permissions;

  $('#adPermissions').innerHTML = data.available_permissions
    .filter((p) => !p.owner_only)
    .map((p) => `
      <label class="perm-item">
        <input type="checkbox" name="perm" value="${esc(p.code)}">
        <span>${esc(p.label)}</span>
      </label>
    `).join('');

  const rows = data.items.map((a) => `
    <tr>
      <td><b>${esc(a.username)}</b></td>
      <td>${esc(a.display_name || '')}</td>
      <td>${a.role === 'owner' ? '<span class="badge ok">владелец</span>' : '<span class="badge">сотрудник</span>'}</td>
      <td class="small muted">${a.role === 'owner' ? 'все права' : (a.permissions.map(permLabel).join(', ') || '—')}</td>
      <td>${a.is_active ? '<span class="badge ok">активна</span>' : '<span class="badge bad">выключена</span>'}</td>
      <td>${a.last_login_at ? fmtDate(a.last_login_at) : '—'}</td>
      <td>${a.role === 'owner' ? '' : `
        <button class="btn small" data-admin-edit="${a.id}">Права</button>
        <button class="btn small" data-admin-pass="${a.id}">Пароль</button>
        <button class="btn small danger" data-admin-del="${a.id}">Удалить</button>
      `}</td>
    </tr>
  `);
  $('#adminsTable').innerHTML = table(
    ['Логин', 'Имя', 'Роль', 'Права', 'Статус', 'Последний вход', ''], rows
  );

  $$('#adminsTable [data-admin-edit]').forEach((btn) => {
    btn.addEventListener('click', () => {
      openPermissionEditor(data.items.find((a) => a.id === Number(btn.dataset.adminEdit)));
    });
  });

  $$('#adminsTable [data-admin-pass]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const password = prompt('Новый пароль (от 8 символов):');
      if (!password) return;
      try {
        await api(`/api/console/admins/${btn.dataset.adminPass}/password`, {
          method: 'POST',
          body: JSON.stringify({ password }),
        });
        toast('Пароль обновлён');
      } catch (error) {
        toast(error.message);
      }
    });
  });

  $$('#adminsTable [data-admin-del]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      if (!confirm('Удалить учётку?')) return;
      try {
        await api(`/api/console/admins/${btn.dataset.adminDel}`, { method: 'DELETE' });
        toast('Учётка удалена');
        loadAdmins();
      } catch (error) {
        toast(error.message);
      }
    });
  });
}

function openPermissionEditor(admin) {
  if (!admin) return;
  const available = state.permissionCatalog.filter((p) => !p.owner_only);
  const checked = new Set(admin.permissions);

  $('#permEditorTitle').textContent = `Права: ${admin.username}`;
  $('#permEditorBody').innerHTML = `
    <div class="perm-grid">
      ${available.map((p) => `
        <label class="perm-item">
          <input type="checkbox" value="${esc(p.code)}" ${checked.has(p.code) ? 'checked' : ''}>
          <span>${esc(p.label)}</span>
        </label>
      `).join('')}
    </div>
    <button class="btn primary block" id="permSave" style="margin-top:18px">Сохранить права</button>
  `;
  $('#permEditor').hidden = false;
  $('#permEditorBackdrop').hidden = false;

  $('#permSave').addEventListener('click', async () => {
    const permissions = $$('#permEditorBody input[type="checkbox"]:checked').map((i) => i.value);
    try {
      await api(`/api/console/admins/${admin.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ permissions }),
      });
      toast('Права сохранены');
      closePermissionEditor();
      loadAdmins();
    } catch (error) {
      toast(error.message);
    }
  });
}

$('#permEditorClose').addEventListener('click', closePermissionEditor);
$('#permEditorBackdrop').addEventListener('click', closePermissionEditor);

// --- формы создания ---

$('#rewardCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const unit = $('#rwUnit').value;
  try {
    await api('/api/console/rewards', {
      method: 'POST',
      body: JSON.stringify({
        code: $('#rwCode').value.trim(),
        title: $('#rwTitle').value.trim(),
        cost_pts: Number($('#rwCost').value),
        payout_value: Number($('#rwValue').value),
        payout_unit: unit,
        kind: unit === 'RUB' ? 'cash' : 'premium',
      }),
    });
    event.target.reset();
    toast('Награда создана');
    loadRewardsAdmin();
  } catch (error) {
    toast(error.message);
  }
});

$('#wheelCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    await api('/api/console/wheels', {
      method: 'POST',
      body: JSON.stringify({
        code: $('#whCode').value.trim(),
        title: $('#whTitle').value.trim(),
        cost_pts: Number($('#whCost').value),
      }),
    });
    event.target.reset();
    toast('Лента создана');
    loadLootAdmin();
  } catch (error) {
    toast(error.message);
  }
});

$('#adminCreateForm').addEventListener('submit', async (event) => {
  event.preventDefault();
  const permissions = $$('#adPermissions input[name="perm"]:checked').map((i) => i.value);
  try {
    await api('/api/console/admins', {
      method: 'POST',
      body: JSON.stringify({
        username: $('#adUsername').value.trim(),
        display_name: $('#adDisplay').value.trim(),
        password: $('#adPassword').value,
        permissions,
      }),
    });
    event.target.reset();
    toast('Учётка создана');
    loadAdmins();
  } catch (error) {
    toast(error.message);
  }
});

$('#deskSearchBtn').addEventListener('click', deskSearch);
$('#deskSearch').addEventListener('keydown', (event) => {
  if (event.key === 'Enter') deskSearch();
});

// --- ЖИВАЯ КАРТА (OASys) ---

function pcStatusBadge(device) {
  if (device.in_tech) return '<span class="badge">в тех. работах</span>';
  if (!device.in_service) return '<span class="badge bad">не в сервисе</span>';
  if (device.in_use) return '<span class="badge warn">занят</span>';
  return '<span class="badge ok">свободен</span>';
}

async function loadOasysMap() {
  $('#oasysError').innerHTML = '';
  $('#oasysStats').innerHTML = '<div class="small muted">Загружаем…</div>';
  $('#oasysMapTable').innerHTML = '';
  $('#oasysDiscountsTable').innerHTML = '';

  let map;
  try {
    map = await api('/api/console/oasys/map');
  } catch (error) {
    $('#oasysStats').innerHTML = '';
    $('#oasysError').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
    return;
  }

  const rows = map.devices.map((d) => {
    const session = d.session || null;
    return `
      <tr>
        <td>${esc(d.number)}</td>
        <td>${esc(d.loyalty_zone_title || d.zone_name || '—')}</td>
        <td>${pcStatusBadge(d)}</td>
        <td>${session ? esc(session.username || session.user_type_id || '') : '—'}</td>
      </tr>
    `;
  });
  $('#oasysMapTable').innerHTML = table(['ПК №', 'Зона', 'Статус', 'Пользователь'], rows);

  const stats = [
    `<div class="stat-tile"><b>${esc(map.in_use)}</b><span>занято из ${esc(map.count)}</span></div>`,
  ];

  try {
    const cashier = await api('/api/console/oasys/cashier-stats');
    stats.push(`<div class="stat-tile"><b>${esc(cashier.earnings)}</b><span>выручка за смену</span></div>`);
    stats.push(`<div class="stat-tile"><b>${esc(cashier.cashier_card_balance)}</b><span>баланс безнал</span></div>`);
  } catch (error) {
    // Кассовая статистика необязательна для карты — просто не показываем плитки.
  }
  $('#oasysStats').innerHTML = stats.join('');

  try {
    const discounts = await api('/api/console/oasys/discounts');
    const discountRows = discounts.club_discounts.map((d) => `
      <tr><td>${esc(d.name)}</td><td>${esc(d.discount)}%</td></tr>
    `);
    const promoRows = discounts.promo_codes.map((p) => `
      <tr><td><code>${esc(p.code)}</code></td><td>${esc(p.type)}</td><td>${esc(p.count)}/${esc(p.max_count)}</td></tr>
    `);
    $('#oasysDiscountsTable').innerHTML = `
      <h3>Клубные скидки</h3>
      ${table(['Название', 'Скидка'], discountRows)}
      <h3>Промокоды</h3>
      ${table(['Код', 'Тип', 'Использован/лимит'], promoRows)}
    `;
  } catch (error) {
    $('#oasysDiscountsTable').innerHTML = `<div class="empty">${esc(error.message)}</div>`;
  }
}

$('#oasysRefreshBtn').addEventListener('click', loadOasysMap);
