/* Показываем блокирующий оверлей до сетевого запроса прокрутки.
   Основная анимация остаётся в app.js; этот слой закрывает окно двойного тапа
   и гарантированно убирает загрузку, если запрос упал до получения результата. */

(() => {
  const baseApi = api;

  api = async function guardedApi(path, options = {}) {
    const isSpinRequest = path === '/api/wheels/spin';

    if (isSpinRequest) {
      $('#spinTitle').textContent = 'Готовим прокрутку';
      $('#spinResult').hidden = true;
      $('#spinClose').hidden = true;
      $('#spinSkipBtn').hidden = true;
      $('#reelsGrid').innerHTML = '<div class="spin-loading"><span>Фиксируем ставку и определяем приз…</span></div>';
      $('#spinOverlay').hidden = false;
    }

    try {
      return await baseApi(path, options);
    } catch (error) {
      if (isSpinRequest) {
        $('#spinOverlay').hidden = true;
        $('#reelsGrid').innerHTML = '';
      }
      throw error;
    }
  };
})();
