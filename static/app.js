/* Общий JS для обеих страниц (get-lead и settings). Обычная same-origin
 * страница за HTTP Basic Auth — никакого AmoCRM SDK, никакого CORS/секрета:
 * браузер сам шлёт Basic-Auth заголовок на каждый fetch к тому же домену. */
(function () {
  'use strict';

  function api(path, method, body) {
    return fetch(path, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
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
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }

  var RESULT_MESSAGES = {
    not_allowed: 'Вы не состоите ни в одной активной группе распределения. Обратитесь к администратору.',
    limit_reached: 'Лимит на этот месяц исчерпан.',
    no_leads: 'Сейчас нет подходящих лидов для вашей группы. Попробуйте позже.',
    not_configured: 'Кнопка ещё не настроена администратором.',
    funnel_not_configured: 'Не выбрана воронка источника/назначения. Откройте настройки.',
    amocrm_error: 'Ошибка при обращении к AmoCRM. Попробуйте ещё раз.',
    unknown_user: 'Не удалось определить пользователя.',
    missing_user_id: 'Не удалось определить пользователя.',
    cooldown: 'Пауза после предыдущего нажатия ещё не закончилась.',
  };

  function formatCountdown(seconds) {
    var m = Math.floor(seconds / 60);
    var s = seconds % 60;
    return m + ':' + (s < 10 ? '0' : '') + s;
  }

  function pluralLeads(n) {
    var mod100 = Math.abs(n) % 100;
    var mod10 = mod100 % 10;
    if (mod100 > 10 && mod100 < 20) return 'лидов';
    if (mod10 > 1 && mod10 < 5) return 'лида';
    if (mod10 === 1) return 'лид';
    return 'лидов';
  }

  // ─── Страница «Получить лид» ────────────────────────────────────────
  // Кто именно нажимает — сервер уже знает из сессии (вход через OAuth
  // AmoCRM на /login), поэтому здесь никакого выбора пользователя и
  // никакого user_id в запросах — только сам факт клика.
  function renderGetLeadPage(container) {
    var btn = el('button', { text: 'Получить лид', class: 'lb-btn-lg' });
    btn.disabled = true; // включится после того, как узнаем статус

    var availabilityBox = el('div', {});
    var status = el('div', {});
    var countdownTimer = null;

    function stopCountdown() {
      if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
    }

    function renderStat(n) {
      availabilityBox.innerHTML = '';
      availabilityBox.appendChild(el('div', { class: 'lb-stat-row' }, [
        el('span', { class: 'lb-stat-num', text: String(n) }),
        el('span', { class: 'lb-stat-label', text: pluralLeads(n) + ' доступно' }),
      ]));
    }

    function renderNote(text) {
      availabilityBox.innerHTML = '';
      availabilityBox.appendChild(el('div', { class: 'lb-note', text: text }));
    }

    function startCountdown(seconds) {
      stopCountdown();
      var remaining = seconds;
      btn.disabled = true;
      function render() {
        availabilityBox.innerHTML = '';
        availabilityBox.appendChild(el('div', { class: 'lb-wait-row' }, [
          el('span', { text: 'Кнопка будет доступна через ' }),
          el('strong', { text: formatCountdown(remaining) }),
        ]));
      }
      render();
      countdownTimer = setInterval(function () {
        remaining -= 1;
        if (remaining <= 0) {
          stopCountdown();
          refreshAvailability(); // пауза закончилась — пересчитать реальную доступность
          return;
        }
        render();
      }, 1000);
    }

    function refreshAvailability() {
      api('/api/status', 'POST').then(function (res) {
        var d = res.data;
        if (!d.ok) {
          stopCountdown();
          renderNote(RESULT_MESSAGES[d.error || d.reason] || ('Недоступно (' + (d.error || d.reason) + ').'));
          btn.disabled = true;
          return;
        }

        if (d.cooldown_remaining > 0) {
          startCountdown(d.cooldown_remaining);
          return;
        }

        stopCountdown();
        // Менеджеру не показываем разбивку по группам/тирам — только общее
        // число реально доступных лидов (с учётом остатка лимита).
        var total = d.groups.reduce(function (sum, g) {
          return sum + (g.remaining > 0 ? Math.min(g.available_leads, g.remaining) : 0);
        }, 0);
        renderStat(total);
        btn.disabled = total <= 0;
      });
    }

    refreshAvailability();

    btn.addEventListener('click', function () {
      btn.disabled = true;
      status.innerHTML = '';
      status.appendChild(el('div', { class: 'lb-note', text: 'Запрашиваю…' }));
      api('/api/get-lead', 'POST')
        .then(function (res) {
          var d = res.data;
          status.innerHTML = '';
          if (d.ok) {
            var link;
            if (d.lead_url) {
              link = el('a', {
                text: d.lead_name || ('#' + d.lead_id), href: d.lead_url, target: '_blank', rel: 'noopener',
              });
            } else {
              link = el('strong', { text: d.lead_name || ('#' + d.lead_id) });
            }
            status.appendChild(el('div', { class: 'lb-result' }, [
              el('span', { class: 'lb-result-icon', text: '✓' }),
              el('span', { class: 'lb-result-text' }, [
                el('span', { text: 'Вам назначен лид ' }), link, el('span', { text: '.' }),
              ]),
            ]));
          } else {
            var msg = RESULT_MESSAGES[d.error || d.reason] ||
              ('Не удалось получить лид (' + (d.error || d.reason) + ').');
            status.appendChild(el('div', { class: 'lb-result' }, [
              el('span', { class: 'lb-result-icon is-error', text: '!' }),
              el('span', { class: 'lb-result-text', text: msg }),
            ]));
          }
        })
        .catch(function () {
          status.innerHTML = '';
          status.appendChild(el('div', { class: 'lb-result' }, [
            el('span', { class: 'lb-result-icon is-error', text: '!' }),
            el('span', { class: 'lb-result-text', text: 'Не удалось связаться с сервером. Попробуйте позже.' }),
          ]));
        })
        .finally(function () {
          refreshAvailability(); // обновить счётчики после попытки, кнопка сама включится/выключится
        });
    });

    container.appendChild(availabilityBox);
    container.appendChild(el('div', { class: 'lb-row' }, [btn]));
    container.appendChild(status);
  }

  // ─── Блок воронки (общий на экране настроек) ────────────────────────
  function renderFunnelBox(state) {
    function pipelinePicker(pipelineKey, statusKey, label) {
      var pipelineSelect = el('select', {});
      var statusSelect = el('select', {});

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

      return el('div', { class: 'lb-funnel-row' }, [
        el('span', { text: label + ': ', class: 'lb-label' }),
        el('div', { class: 'lb-funnel-value' }, [pipelineSelect, statusSelect]),
      ]);
    }

    var responsibleSelect = el('select', {});
    responsibleSelect.appendChild(el('option', { value: '', text: '— не уточнено, весь статус —' }));
    state.users.forEach(function (u) {
      var opt = el('option', { value: u.id, text: u.name });
      if (u.id === state.funnel.source_responsible) opt.selected = true;
      responsibleSelect.appendChild(opt);
    });
    responsibleSelect.addEventListener('change', function () {
      state.funnel.source_responsible = responsibleSelect.value ? parseInt(responsibleSelect.value, 10) : null;
    });
    var responsibleRow = el('div', { class: 'lb-funnel-row' }, [
      el('span', { text: 'Забирать у ответственного: ', class: 'lb-label' }),
      el('div', { class: 'lb-funnel-value lb-funnel-value-single' }, [responsibleSelect]),
    ]);

    return el('div', { class: 'lb-panel' }, [
      el('h2', { text: 'Воронка и этапы' }),
      pipelinePicker('source_pipeline', 'source_status', 'Источник (откуда берём)'),
      responsibleRow,
      pipelinePicker('target_pipeline', 'target_status', 'Назначение (куда переносим)'),
    ]);
  }

  // ─── Блок паузы/порядка распределения (только в памяти сервера) ─────
  function renderRuntimeBox(state) {
    var cooldownInput = el('input', {
      type: 'number', min: '0', style: 'width:90px;', value: state.runtime.cooldown_seconds,
    });
    cooldownInput.addEventListener('input', function () {
      state.runtime.cooldown_seconds = parseInt(cooldownInput.value, 10) || 0;
    });

    var orderSelect = el('select', {});
    [
      ['oldest_first', 'Сначала старые'],
      ['newest_first', 'Сначала новые'],
      ['random', 'Случайно'],
    ].forEach(function (pair) {
      var opt = el('option', { value: pair[0], text: pair[1] });
      if (pair[0] === state.runtime.distribution_order) opt.selected = true;
      orderSelect.appendChild(opt);
    });
    orderSelect.addEventListener('change', function () {
      state.runtime.distribution_order = orderSelect.value;
    });

    return el('div', { class: 'lb-panel' }, [
      el('h2', { text: 'Пауза и порядок распределения' }),
      el('div', { class: 'lb-runtime-row' }, [
        el('span', { text: 'Пауза между лидами (сек): ', class: 'lb-label' }),
        cooldownInput,
      ]),
      el('div', { class: 'lb-runtime-row' }, [
        el('span', { text: 'Порядок распределения: ', class: 'lb-label' }),
        orderSelect,
      ]),
    ]);
  }

  // ─── Блок истории изменений ──────────────────────────────────────────
  function renderHistoryBox(history) {
    var box = el('div', { class: 'lb-history' });
    if (!history.length) {
      box.appendChild(el('div', { class: 'lb-history-item', text: 'Изменений пока не было.' }));
    }
    history.forEach(function (h) {
      var d = new Date(h.timestamp * 1000);
      var dateStr = d.toLocaleString('ru-RU');
      box.appendChild(el('div', { class: 'lb-history-item' }, [
        el('div', { class: 'lb-history-meta', text: h.admin_name + ' · ' + dateStr }),
        el('div', { class: 'lb-history-summary', text: h.summary }),
      ]));
    });
    return el('div', { class: 'lb-panel' }, [
      el('h2', { text: 'История изменений' }),
      box,
    ]);
  }

  // ─── Страница настроек (группы + воронка) ───────────────────────────
  function renderSettingsPage(container) {
    container.appendChild(el('div', { text: 'Загрузка…' }));

    api('/api/settings', 'GET').then(function (res) {
      container.innerHTML = '';
      if (!res.data.ok) {
        container.appendChild(el('div', {
          text: RESULT_MESSAGES[res.data.error] || 'Ошибка настроек: ' + (res.data.detail || res.data.error),
        }));
        return;
      }

      var state = {
        groups: res.data.groups, tags: res.data.tags, users: res.data.users,
        pipelines: res.data.pipelines, funnel: res.data.funnel, runtime: res.data.runtime,
      };

      container.appendChild(el('div', { class: 'lb-section' }, [renderFunnelBox(state)]));
      container.appendChild(el('div', { class: 'lb-section' }, [renderRuntimeBox(state)]));
      var groupsPanel = el('div', { class: 'lb-panel' });
      container.appendChild(el('div', { class: 'lb-section' }, [groupsPanel]));
      groupsPanel.appendChild(el('h2', { text: 'Группы менеджеров' }));
      var groupsBox = el('div', {});
      groupsPanel.appendChild(groupsBox);
      var groupsSection = groupsPanel;

      // Свёрнуто/развёрнуто — только на клиенте, не уходит на сервер и не
      // трогает данные группы (WeakMap ключом на сам объект group).
      var collapsedGroups = new WeakMap();

      // Список групп загружается в память один раз при открытии страницы.
      // Если сохранять весь state.groups целиком, вкладка, простоявшая
      // открытой (или открытая параллельно с другой), при сохранении
      // затирает чужие изменения по группам, которые сама не трогала, своей
      // устаревшей копией. Поэтому на сервер уходят только группы, реально
      // изменённые в ЭТОЙ вкладке — остальные сервер не трогает вообще.
      var dirtyGroups = new WeakSet();
      function markDirty(group) { dirtyGroups.add(group); }

      function renderGroups() {
        groupsBox.innerHTML = '';
        state.groups.forEach(function (group) {
          groupsBox.appendChild(renderGroupRow(group));
        });
      }

      function renderGroupRow(group) {
        var nameInput = el('input', { type: 'text', value: group.name || '', placeholder: 'Название группы' });
        nameInput.addEventListener('input', function () { group.name = nameInput.value; markDirty(group); });

        var tagSelect = el('select', {});
        tagSelect.appendChild(el('option', { value: '', text: '— выберите тег —' }));
        state.tags.forEach(function (tag) {
          var opt = el('option', { value: tag, text: tag });
          if (tag === group.tag) opt.selected = true;
          tagSelect.appendChild(opt);
        });
        tagSelect.addEventListener('change', function () { group.tag = tagSelect.value; markDirty(group); });

        var activeCheckbox = el('input', { type: 'checkbox' });
        activeCheckbox.checked = !!group.active;
        activeCheckbox.addEventListener('change', function () { group.active = activeCheckbox.checked; markDirty(group); });

        var membersById = {};
        (group.members || []).forEach(function (m) { membersById[m.user_id] = m; });

        var usersBox = el('div', { class: 'lb-users' });
        state.users.forEach(function (u) {
          var existing = membersById[u.id];
          var checkbox = el('input', { type: 'checkbox' });
          checkbox.checked = !!existing;

          var limitInput = el('input', {
            type: 'number', min: '0', style: 'width:70px;',
            value: existing ? existing.limit : 0,
          });
          limitInput.disabled = !existing;

          // Лимит исчерпан в этом месяце — подсвечиваем тёплым акцентом,
          // чтобы было видно сразу, без сравнения двух чисел глазами.
          var limitReached = !!(existing && existing.limit > 0 && existing.count >= existing.limit);

          var countLabel = el('span', {
            text: existing && existing.count != null ? '(нажато: ' + existing.count + ')' : '',
            class: limitReached ? 'lb-count lb-count-limit' : 'lb-count',
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
            markDirty(group);
          }

          usersBox.appendChild(el('div', { class: limitReached ? 'lb-user-limit-reached' : '' }, [
            checkbox, el('span', { text: u.name }), limitInput, countLabel,
          ]));
        });

        var toggleBtn = el('button', { class: 'lb-group-toggle', type: 'button' });
        function applyCollapsed() {
          var collapsed = !!collapsedGroups.get(group);
          usersBox.style.display = collapsed ? 'none' : '';
          toggleBtn.textContent = collapsed ? '▸' : '▾';
          toggleBtn.setAttribute('aria-label', collapsed ? 'Развернуть группу' : 'Свернуть группу');
        }
        toggleBtn.addEventListener('click', function () {
          collapsedGroups.set(group, !collapsedGroups.get(group));
          applyCollapsed();
        });
        applyCollapsed();

        var deleteBtn = el('button', {
          text: 'Удалить', class: 'lb-secondary', style: 'padding:4px 12px;font-size:12px;',
          onclick: function () {
            if (!confirm('Удалить группу «' + (group.name || '(без названия)') + '»? Отменить нельзя.')) return;
            if (!group.id) {
              // ещё не сохранённая группа — просто убрать из формы, на сервере её нет
              state.groups = state.groups.filter(function (g) { return g !== group; });
              renderGroups();
              return;
            }
            deleteBtn.disabled = true;
            api('/api/settings/delete-group', 'POST', { group_id: group.id }).then(function (res2) {
              if (res2.data.ok) {
                state.groups = state.groups.filter(function (g) { return g !== group; });
                renderGroups();
              } else {
                deleteBtn.disabled = false;
                alert('Не удалось удалить группу.');
              }
            });
          },
        });

        return el('div', { class: 'lb-group' }, [
          el('div', { class: 'lb-group-head' }, [
            toggleBtn, nameInput, tagSelect,
            el('label', {}, [activeCheckbox, el('span', { text: 'активна' })]),
            deleteBtn,
          ]),
          usersBox,
        ]);
      }

      renderGroups();

      var addBtn = el('button', {
        text: '+ Добавить группу',
        class: 'lb-secondary',
        style: 'margin-bottom:16px;',
        onclick: function () {
          var newGroup = { name: '', tag: '', active: true, members: [] };
          state.groups.push(newGroup);
          markDirty(newGroup);
          renderGroups();
        },
      });
      groupsSection.appendChild(addBtn);

      var saveStatus = el('span', { class: 'lb-save-status' });
      var historyBox = el('div', {});
      var saveBtn = el('button', {
        text: 'Сохранить', class: 'lb-btn-lg',
        onclick: function () {
          saveStatus.textContent = 'Сохраняю…';
          saveStatus.className = 'lb-save-status';
          // Только реально изменённые в этой вкладке группы — см. markDirty
          // выше. Нетронутые группы на сервер не уходят вообще, поэтому
          // устаревшая копия в памяти вкладки не может затереть чужие правки.
          var changedGroups = state.groups.filter(function (g) { return dirtyGroups.has(g); });
          api('/api/settings', 'POST', {
            groups: changedGroups, funnel: state.funnel, runtime: state.runtime,
          }).then(function (res2) {
            saveStatus.textContent = res2.data.ok ? 'Сохранено.' : 'Ошибка сохранения.';
            saveStatus.className = res2.data.ok ? 'lb-save-status is-ok' : 'lb-save-status';
            if (res2.data.ok) {
              dirtyGroups = new WeakSet();
              refreshHistory();
            }
          });
        },
      });

      function refreshHistory() {
        api('/api/settings', 'GET').then(function (res3) {
          historyBox.innerHTML = '';
          historyBox.appendChild(renderHistoryBox(res3.data.history || []));
        });
      }

      container.appendChild(el('div', { class: 'lb-section lb-save-row' }, [saveBtn, saveStatus]));

      var historySection = el('div', { class: 'lb-section' });
      historyBox.appendChild(renderHistoryBox(res.data.history || []));
      historySection.appendChild(historyBox);
      container.appendChild(historySection);
    });
  }

  window.LeadButton = { renderGetLeadPage: renderGetLeadPage, renderSettingsPage: renderSettingsPage };
})();
