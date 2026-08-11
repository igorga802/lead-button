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
  };

  // ─── Страница «Получить лид» ────────────────────────────────────────
  // Кто именно нажимает — сервер уже знает из сессии (вход через OAuth
  // AmoCRM на /login), поэтому здесь никакого выбора пользователя и
  // никакого user_id в запросах — только сам факт клика.
  function renderGetLeadPage(container) {
    var btn = el('button', { text: 'Получить лид' });
    btn.disabled = true; // включится после того, как узнаем статус

    var availabilityBox = el('div', { class: 'lb-availability' });
    var status = el('div', { class: 'lb-status' });

    function refreshAvailability() {
      availabilityBox.textContent = 'Загрузка…';
      api('/api/status', 'POST').then(function (res) {
        var d = res.data;
        if (d.ok) {
          availabilityBox.textContent =
            'Группа «' + d.group + '» · доступно лидов: ' + d.available_leads +
            ' · остаток лимита в этом месяце: ' + d.remaining + ' из ' + d.limit;
          btn.disabled = d.available_leads <= 0 || d.remaining <= 0;
        } else {
          availabilityBox.textContent = RESULT_MESSAGES[d.error || d.reason] ||
            ('Недоступно (' + (d.error || d.reason) + ').');
          btn.disabled = true;
        }
      });
    }

    refreshAvailability();

    btn.addEventListener('click', function () {
      btn.disabled = true;
      status.textContent = 'Запрашиваю…';
      api('/api/get-lead', 'POST')
        .then(function (res) {
          var d = res.data;
          if (d.ok) {
            status.innerHTML = '';
            status.appendChild(el('span', { text: 'Вам назначен лид ' }));
            if (d.lead_url) {
              status.appendChild(el('a', {
                text: d.lead_name || ('#' + d.lead_id), href: d.lead_url, target: '_blank', rel: 'noopener',
              }));
            } else {
              status.appendChild(el('strong', { text: d.lead_name || ('#' + d.lead_id) }));
            }
            status.appendChild(el('span', { text: '.' }));
          } else {
            status.textContent = RESULT_MESSAGES[d.error || d.reason] ||
              ('Не удалось получить лид (' + (d.error || d.reason) + ').');
          }
        })
        .catch(function () {
          status.textContent = 'Не удалось связаться с сервером. Попробуйте позже.';
        })
        .finally(function () {
          refreshAvailability(); // обновить счётчики после попытки, кнопка сама включится/выключится
        });
    });

    container.appendChild(el('div', { class: 'lb-row' }, [btn]));
    container.appendChild(availabilityBox);
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
        pipelineSelect, statusSelect,
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
      responsibleSelect,
    ]);

    return el('div', {}, [
      el('h2', { text: 'Воронка и этапы' }),
      pipelinePicker('source_pipeline', 'source_status', 'Источник (откуда берём)'),
      responsibleRow,
      pipelinePicker('target_pipeline', 'target_status', 'Назначение (куда переносим)'),
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
        pipelines: res.data.pipelines, funnel: res.data.funnel,
      };

      container.appendChild(renderFunnelBox(state));
      container.appendChild(el('hr', {}));
      var groupsBox = el('div', {});
      container.appendChild(groupsBox);

      function renderGroups() {
        groupsBox.innerHTML = '';
        state.groups.forEach(function (group) {
          groupsBox.appendChild(renderGroupRow(group));
        });
      }

      function renderGroupRow(group) {
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

          var countLabel = el('span', {
            text: existing && existing.count != null ? '(нажато: ' + existing.count + ')' : '',
            class: 'lb-count',
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

          usersBox.appendChild(el('div', {}, [
            checkbox, el('span', { text: u.name }), limitInput, countLabel,
          ]));
        });

        return el('div', { class: 'lb-group' }, [
          el('div', { class: 'lb-group-head' }, [
            nameInput, tagSelect,
            el('label', {}, [activeCheckbox, el('span', { text: 'активна' })]),
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
          state.groups.push({ name: '', tag: '', active: true, members: [] });
          renderGroups();
        },
      });

      var saveStatus = el('span', { class: 'lb-status', style: 'margin-left:12px;' });
      var saveBtn = el('button', {
        text: 'Сохранить',
        onclick: function () {
          saveStatus.textContent = 'Сохраняю…';
          api('/api/settings', 'POST', { groups: state.groups, funnel: state.funnel }).then(function (res2) {
            saveStatus.textContent = res2.data.ok ? 'Сохранено.' : 'Ошибка сохранения.';
          });
        },
      });

      container.appendChild(addBtn);
      container.appendChild(el('div', {}, [saveBtn, saveStatus]));
    });
  }

  window.LeadButton = { renderGetLeadPage: renderGetLeadPage, renderSettingsPage: renderSettingsPage };
})();
