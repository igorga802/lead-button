/*
 * Виджет «Получить лид».
 *
 * ПЕРЕД УПАКОВКОЙ В ZIP: замените BACKEND_URL на реальный адрес этого сервиса
 * на Render (появится после первого деплоя — см. README.md в корне проекта).
 * WIDGET_SECRET уже заполнен тем же значением, что и в .env/Render.
 *
 * ВАЖНО (проверить при первой установке в реальном аккаунте, см. widget/README.md):
 *   - self.system().area — так классический JS SDK amoCRM обычно сообщает
 *     виджету, в какой локации он сейчас рендерится (см. "locations" в
 *     manifest.json: "everywhere" — плавающая кнопка, "advanced_settings" —
 *     экран настроек групп). Если в вашей версии SDK поле называется иначе —
 *     поправьте ветвление в define() ниже по подсказке из консоли браузера.
 *   - self.render_template(...) — стандартный способ классических виджетов
 *     вставить HTML в контейнер, который amoCRM выделяет виджету на странице
 *     (используется только для экрана настроек — сама кнопка рисуется поверх
 *     интерфейса напрямую, см. renderFloatingButton).
 */
define(['jquery'], function ($) {
  'use strict';

  var BACKEND_URL = 'https://REPLACE-ME.onrender.com';
  var WIDGET_SECRET = '5fb99341e0f9376eb01898a9bbfe06653bd226e8809b22a1';

  function api(path, method, body) {
    return fetch(BACKEND_URL + path, {
      method: method,
      mode: 'cors',
      headers: {
        'Content-Type': 'application/json',
        'X-Widget-Secret': WIDGET_SECRET,
      },
      body: body ? JSON.stringify(body) : undefined,
    }).then(function (r) {
      return r.json().then(function (data) {
        return { status: r.status, data: data };
      });
    });
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      if (k === 'text') {
        node.textContent = attrs[k];
      } else if (k.indexOf('on') === 0 && typeof attrs[k] === 'function') {
        node.addEventListener(k.slice(2), attrs[k]);
      } else {
        node.setAttribute(k, attrs[k]);
      }
    });
    (children || []).forEach(function (c) {
      if (c) node.appendChild(c);
    });
    return node;
  }

  var RESULT_MESSAGES = {
    not_allowed: 'Вы не состоите ни в одной активной группе распределения. Обратитесь к администратору.',
    limit_reached: 'Лимит на этот месяц исчерпан.',
    no_leads: 'Сейчас нет подходящих лидов для вашей группы. Попробуйте позже.',
    not_configured: 'Кнопка ещё не настроена администратором.',
    funnel_not_configured: 'Не выбрана воронка источника/назначения. Откройте настройки виджета.',
    amocrm_error: 'Ошибка при обращении к AmoCRM. Попробуйте ещё раз.',
    unknown_user: 'Не удалось определить пользователя.',
    missing_user_id: 'Не удалось определить пользователя.',
  };

  // ─── Локация "everywhere": плавающая перетаскиваемая кнопка ─────────
  // AmoCRM не даёт штатного drag-n-drop для позиции виджета, поэтому кнопка
  // рисуется НАШИМ кодом поверх интерфейса (position: fixed) и сама
  // реализует перетаскивание мышью. Позиция запоминается в localStorage
  // браузера — переживает перезагрузку страницы и переходы между разделами
  // AmoCRM, но привязана к конкретному браузеру/устройству менеджера
  // (не синхронизируется между компьютерами — это простое и достаточное
  // решение для v1, синхронизация через бэкенд по user_id — по запросу).
  var STORAGE_KEY = 'lead_button_widget_position_v1';
  var DRAG_THRESHOLD = 4; // px — меньше считаем кликом, не перетаскиванием

  function renderFloatingButton(userId) {
    if (document.querySelector('.lead-button-widget-floating')) return; // уже отрисована

    var saved = null;
    try { saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null'); } catch (e) { /* ignore */ }
    var right = saved && typeof saved.right === 'number' ? saved.right : 24;
    var bottom = saved && typeof saved.bottom === 'number' ? saved.bottom : 24;

    var wrap = el('div', {
      class: 'lead-button-widget-floating',
      style:
        'position:fixed; z-index:99999; right:' + right + 'px; bottom:' + bottom + 'px; ' +
        'cursor:grab; user-select:none; display:flex; flex-direction:column; align-items:flex-end; gap:4px;',
    });

    var btn = el('button', {
      text: 'Получить лид',
      style:
        'padding:12px 22px;font-size:15px;border:none;border-radius:24px;cursor:inherit;' +
        'background:#2864ff;color:#fff;box-shadow:0 2px 10px rgba(0,0,0,.25);',
    });
    var status = el('div', {
      style:
        'max-width:260px;font-size:13px;background:#fff;color:#333;padding:6px 10px;' +
        'border-radius:8px;box-shadow:0 2px 8px rgba(0,0,0,.2);display:none;',
    });

    wrap.appendChild(btn);
    wrap.appendChild(status);
    document.body.appendChild(wrap);

    // ── Перетаскивание ──
    var dragging = false;
    var moved = false;
    var startX, startY, startRight, startBottom;

    wrap.addEventListener('mousedown', function (e) {
      dragging = true;
      moved = false;
      startX = e.clientX;
      startY = e.clientY;
      var rect = wrap.getBoundingClientRect();
      startRight = window.innerWidth - rect.right;
      startBottom = window.innerHeight - rect.bottom;
      wrap.style.cursor = 'grabbing';
      e.preventDefault();
    });

    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var dx = e.clientX - startX;
      var dy = e.clientY - startY;
      if (Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) moved = true;
      if (!moved) return;
      var newRight = Math.max(0, startRight - dx);
      var newBottom = Math.max(0, startBottom - dy);
      wrap.style.right = newRight + 'px';
      wrap.style.bottom = newBottom + 'px';
    });

    document.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      wrap.style.cursor = 'grab';
      if (moved) {
        var rect = wrap.getBoundingClientRect();
        localStorage.setItem(STORAGE_KEY, JSON.stringify({
          right: window.innerWidth - rect.right,
          bottom: window.innerHeight - rect.bottom,
        }));
      }
    });

    // ── Клик = «получить лид», только если это не был drag ──
    btn.addEventListener('click', function () {
      if (moved) { moved = false; return; }

      btn.disabled = true;
      status.style.display = 'block';
      status.textContent = 'Запрашиваю…';
      api('/api/widget/get-lead', 'POST', { user_id: userId })
        .then(function (res) {
          var d = res.data;
          if (d.ok) {
            status.textContent =
              'Вам назначен лид «' + (d.lead_name || d.lead_id) + '» ' +
              '(группа «' + d.group + '», выдано ' + d.count + '/' + d.limit + ' в этом месяце).';
          } else {
            status.textContent = RESULT_MESSAGES[d.error || d.reason] ||
              ('Не удалось получить лид (' + (d.error || d.reason) + ').');
          }
        })
        .catch(function () {
          status.textContent = 'Не удалось связаться с сервером. Попробуйте позже.';
        })
        .finally(function () {
          btn.disabled = false;
        });
    });
  }

  // Блок «воронка-источник → воронка/этап-назначение» на экране настроек.
  // Каскадные дропдауны: выбор воронки перестраивает список её этапов.
  function renderFunnelBox(state) {
    function pipelinePicker(pipelineKey, statusKey, label) {
      var pipelineSelect = el('select', {});
      var statusSelect = el('select', { style: 'margin-left:8px;' });

      function fillStatuses(pipelineId) {
        statusSelect.innerHTML = '';
        var pipeline = state.pipelines.filter(function (p) { return p.id === pipelineId; })[0];
        (pipeline ? pipeline.statuses : []).forEach(function (s) {
          var opt = el('option', { value: s.id, text: s.name });
          if (s.id === state.funnel[statusKey]) opt.selected = true;
          statusSelect.appendChild(opt);
        });
        state.funnel[statusKey] = statusSelect.value ? parseInt(statusSelect.value, 10) : null;
      }

      pipelineSelect.appendChild(el('option', { value: '', text: '— воронка —' }));
      state.pipelines.forEach(function (p) {
        var opt = el('option', { value: p.id, text: p.name });
        if (p.id === state.funnel[pipelineKey]) opt.selected = true;
        pipelineSelect.appendChild(opt);
      });

      pipelineSelect.addEventListener('change', function () {
        var id = pipelineSelect.value ? parseInt(pipelineSelect.value, 10) : null;
        state.funnel[pipelineKey] = id;
        fillStatuses(id);
      });
      statusSelect.addEventListener('change', function () {
        state.funnel[statusKey] = statusSelect.value ? parseInt(statusSelect.value, 10) : null;
      });

      if (state.funnel[pipelineKey]) fillStatuses(state.funnel[pipelineKey]);

      return el('div', { style: 'margin:6px 0;' }, [
        el('span', { text: label + ': ', style: 'display:inline-block;width:170px;' }),
        pipelineSelect, statusSelect,
      ]);
    }

    return el('div', {}, [
      el('div', { text: 'Воронка и этапы', style: 'font-weight:bold;margin-bottom:6px;' }),
      pipelinePicker('source_pipeline', 'source_status', 'Источник (откуда берём)'),
      pipelinePicker('target_pipeline', 'target_status', 'Назначение (куда переносим)'),
    ]);
  }

  // ─── Локация "advanced_settings": экран настройки групп ────────────
  function renderSettingsPage(container) {
    container.appendChild(el('div', { text: 'Загрузка…' }));

    api('/api/widget/settings', 'GET').then(function (res) {
      container.innerHTML = '';
      if (!res.data.ok) {
        container.appendChild(el('div', {
          text: RESULT_MESSAGES[res.data.error] || 'Ошибка настроек: ' + (res.data.detail || res.data.error),
        }));
        return;
      }

      var state = {
        groups: res.data.groups, tags: res.data.tags, users: res.data.users,
        pipelines: res.data.pipelines, funnel: res.data.funnel,
      };

      container.appendChild(renderFunnelBox(state));
      container.appendChild(el('hr', {}));
      var groupsBox = el('div', {});
      container.appendChild(groupsBox);

      function renderGroups() {
        groupsBox.innerHTML = '';
        state.groups.forEach(function (group, gIdx) {
          groupsBox.appendChild(renderGroupRow(group, gIdx));
        });
      }

      function renderGroupRow(group, gIdx) {
        var nameInput = el('input', { type: 'text', value: group.name || '', placeholder: 'Название группы' });
        nameInput.addEventListener('input', function () { group.name = nameInput.value; });

        var tagSelect = el('select', {});
        tagSelect.appendChild(el('option', { value: '', text: '— выберите тег —' }));
        state.tags.forEach(function (tag) {
          var opt = el('option', { value: tag, text: tag });
          if (tag === group.tag) opt.selected = true;
          tagSelect.appendChild(opt);
        });
        tagSelect.addEventListener('change', function () { group.tag = tagSelect.value; });

        var activeCheckbox = el('input', { type: 'checkbox' });
        activeCheckbox.checked = !!group.active;
        activeCheckbox.addEventListener('change', function () { group.active = activeCheckbox.checked; });

        var membersById = {};
        (group.members || []).forEach(function (m) { membersById[m.user_id] = m; });

        var usersBox = el('div', { style: 'margin:8px 0 8px 16px;' });
        state.users.forEach(function (u) {
          var existing = membersById[u.id];
          var checkbox = el('input', { type: 'checkbox' });
          checkbox.checked = !!existing;

          var limitInput = el('input', {
            type: 'number', min: '0', style: 'width:70px;margin-left:8px;',
            value: existing ? existing.limit : 0,
          });
          limitInput.disabled = !existing;

          var countLabel = el('span', {
            text: existing && existing.count != null ? ' (нажато: ' + existing.count + ')' : '',
            style: 'color:#888;margin-left:8px;',
          });

          checkbox.addEventListener('change', function () {
            limitInput.disabled = !checkbox.checked;
            syncMembers();
          });
          limitInput.addEventListener('input', syncMembers);

          function syncMembers() {
            var members = (group.members || []).filter(function (m) { return m.user_id !== u.id; });
            if (checkbox.checked) {
              members.push({ user_id: u.id, limit: parseInt(limitInput.value, 10) || 0 });
            }
            group.members = members;
          }

          usersBox.appendChild(el('div', { style: 'margin:2px 0;' }, [
            checkbox, el('span', { text: ' ' + u.name, style: 'margin-left:4px;' }), limitInput, countLabel,
          ]));
        });

        return el('div', { style: 'border:1px solid #ddd;border-radius:6px;padding:12px;margin-bottom:12px;' }, [
          el('div', { style: 'display:flex;gap:12px;align-items:center;' }, [
            nameInput, tagSelect,
            el('label', { style: 'margin-left:auto;' }, [activeCheckbox, el('span', { text: ' активна' })]),
          ]),
          usersBox,
        ]);
      }

      renderGroups();

      var addBtn = el('button', {
        text: '+ Добавить группу',
        style: 'margin:8px 0;',
        onclick: function () {
          state.groups.push({ name: '', tag: '', active: true, members: [] });
          renderGroups();
        },
      });

      var saveStatus = el('span', { style: 'margin-left:12px;' });
      var saveBtn = el('button', {
        text: 'Сохранить',
        class: 'button button-input',
        onclick: function () {
          saveStatus.textContent = 'Сохраняю…';
          api('/api/widget/settings', 'POST', { groups: state.groups, funnel: state.funnel }).then(function (res2) {
            saveStatus.textContent = res2.data.ok ? 'Сохранено.' : 'Ошибка сохранения.';
          });
        },
      });

      container.appendChild(addBtn);
      container.appendChild(el('div', {}, [saveBtn, saveStatus]));
    });
  }

  return function () {
    var self = this;

    this.callbacks = {
      render: function () {
        return true;
      },
      init: function () {
        var sys = self.system ? self.system() : {};
        var area = sys.area || (self.get_settings ? self.get_settings().area : null);

        if (area === 'advanced_settings') {
          // Экран настроек — обычная страница внутри контейнера, который
          // выделяет AmoCRM для этой локации; здесь render_template уместен.
          var container = document.createElement('div');
          container.className = 'lead-button-widget-settings';
          renderSettingsPage(container);
          if (self.render_template) {
            self.render_template({ caption: { class_name: '' }, body: { class_name: '' }, render: '' });
          }
          var host = document.querySelector('.lead-button-widget-settings-host') || document.body;
          host.appendChild(container);
        } else {
          // "everywhere" — сама кнопка рисуется поверх интерфейса
          // (position: fixed), без привязки к контейнеру AmoCRM, см.
          // renderFloatingButton. init() на этой локации может сработать
          // повторно при переходах между разделами — renderFloatingButton
          // сам проверяет, не отрисована ли кнопка уже, и не дублирует её.
          renderFloatingButton(sys.amouser_id);
        }

        return true;
      },
      bind_actions: function () {
        return true;
      },
      settings: function () {
        return true;
      },
    };

    return this;
  };
});
