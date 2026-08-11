"""Бизнес-логика кнопки «Получить лид».

Хранилище — целиком в самом AmoCRM, через «Списки» (Catalogs API v4):
- список групп (кто в какой группе, по какому тегу, с каким личным лимитом)
- список счётчиков (сколько лидов уже получил сотрудник в текущем месяце)

Конфигурация (ID списков/полей, воронки-источник/назначение) — через env,
см. `_env_int` ниже и .env.example. Значения по умолчанию отсутствуют
намеренно: пока пользователь не создал списки в AmoCRM и не прислал ID
воронок/этапов, модуль должен явно падать, а не тихо работать не с теми
данными.
"""
import json
import os
import threading
import time

import amocrm

# ─── Конфигурация (заполняется после ручной настройки в AmoCRM) ───────
def _env_int(name):
    v = os.environ.get(name, '').strip()
    return int(v) if v else None


CATALOG_GROUPS_ID = _env_int('LEAD_BUTTON_CATALOG_GROUPS_ID')
CATALOG_COUNTERS_ID = _env_int('LEAD_BUTTON_CATALOG_COUNTERS_ID')

# ID кастомных полей внутри списка «Распределение: группы».
FIELD_GROUP_TAG = _env_int('LEAD_BUTTON_FIELD_GROUP_TAG')
FIELD_GROUP_MEMBERS = _env_int('LEAD_BUTTON_FIELD_GROUP_MEMBERS')  # textarea JSON
FIELD_GROUP_ACTIVE = _env_int('LEAD_BUTTON_FIELD_GROUP_ACTIVE')

# ID кастомных полей внутри списка «Распределение: счётчики».
FIELD_COUNTER_USER_ID = _env_int('LEAD_BUTTON_FIELD_COUNTER_USER_ID')
FIELD_COUNTER_MONTH = _env_int('LEAD_BUTTON_FIELD_COUNTER_MONTH')
FIELD_COUNTER_COUNT = _env_int('LEAD_BUTTON_FIELD_COUNTER_COUNT')

# Список «Распределение: настройки» — ОДИН элемент хранит воронку/этап,
# откуда берём кандидатов, и куда переезжает лид после выдачи. Сознательно не
# зашито в env константами (как остальные ID списков/полей ниже) — это
# значение админ должен уметь поменять сам через экран настроек виджета, не
# трогая код и не дёргая разработчика, как и остальные настройки кнопки.
CATALOG_SETTINGS_ID = _env_int('LEAD_BUTTON_CATALOG_SETTINGS_ID')
FIELD_SETTINGS_SOURCE_PIPELINE = _env_int('LEAD_BUTTON_FIELD_SETTINGS_SOURCE_PIPELINE')
FIELD_SETTINGS_SOURCE_STATUS = _env_int('LEAD_BUTTON_FIELD_SETTINGS_SOURCE_STATUS')
FIELD_SETTINGS_TARGET_PIPELINE = _env_int('LEAD_BUTTON_FIELD_SETTINGS_TARGET_PIPELINE')
FIELD_SETTINGS_TARGET_STATUS = _env_int('LEAD_BUTTON_FIELD_SETTINGS_TARGET_STATUS')
# Необязательное уточнение источника: брать из статуса только сделки, которые
# сейчас висят на конкретном ответственном (обычно техническом/интеграционном
# пользователе-боте приёма лидов) — иначе в пул случайно попадают чужие
# сделки реальных менеджеров, оказавшиеся в том же статусе. Не входит в
# _require_config — старые инсталляции без этого поля продолжают работать
# (просто берут статус целиком, как раньше).
FIELD_SETTINGS_SOURCE_RESPONSIBLE = _env_int('LEAD_BUTTON_FIELD_SETTINGS_SOURCE_RESPONSIBLE')

# AmoCRM живёt по Москве (см. тот же выбор в app.py, MSK_OFFSET) — месяц
# для лимита/счётчика должен совпадать с тем, что менеджер видит у себя.
MSK_OFFSET = 3 * 3600

# Гонка при одновременном клике: Render free-план поднимает один воркер
# (gunicorn -w 1, см. render.yaml), поэтому обычный process-local Lock
# полностью сериализует критическую секцию «выбрать лид → PATCH». Если
# число воркеров когда-нибудь вырастет — лок перестанет защищать, нужна
# будет доп. проверка на стороне AmoCRM (перечитать лид перед PATCH).
_ASSIGN_LOCK = threading.Lock()


class ConfigError(RuntimeError):
    """Списки/поля/воронки ещё не настроены (см. .env.example)."""


def _require_config():
    missing = [
        name for name, val in [
            ('LEAD_BUTTON_CATALOG_GROUPS_ID', CATALOG_GROUPS_ID),
            ('LEAD_BUTTON_CATALOG_COUNTERS_ID', CATALOG_COUNTERS_ID),
            ('LEAD_BUTTON_FIELD_GROUP_TAG', FIELD_GROUP_TAG),
            ('LEAD_BUTTON_FIELD_GROUP_MEMBERS', FIELD_GROUP_MEMBERS),
            ('LEAD_BUTTON_FIELD_GROUP_ACTIVE', FIELD_GROUP_ACTIVE),
            ('LEAD_BUTTON_FIELD_COUNTER_USER_ID', FIELD_COUNTER_USER_ID),
            ('LEAD_BUTTON_FIELD_COUNTER_MONTH', FIELD_COUNTER_MONTH),
            ('LEAD_BUTTON_FIELD_COUNTER_COUNT', FIELD_COUNTER_COUNT),
            ('LEAD_BUTTON_CATALOG_SETTINGS_ID', CATALOG_SETTINGS_ID),
            ('LEAD_BUTTON_FIELD_SETTINGS_SOURCE_PIPELINE', FIELD_SETTINGS_SOURCE_PIPELINE),
            ('LEAD_BUTTON_FIELD_SETTINGS_SOURCE_STATUS', FIELD_SETTINGS_SOURCE_STATUS),
            ('LEAD_BUTTON_FIELD_SETTINGS_TARGET_PIPELINE', FIELD_SETTINGS_TARGET_PIPELINE),
            ('LEAD_BUTTON_FIELD_SETTINGS_TARGET_STATUS', FIELD_SETTINGS_TARGET_STATUS),
        ] if val is None
    ]
    if missing:
        raise ConfigError(
            'Кнопка «Получить лид» не настроена, отсутствуют env: ' + ', '.join(missing)
        )


def _current_month_msk():
    return time.strftime('%Y-%m', time.gmtime(time.time() + MSK_OFFSET))


# ─── Группы ─────────────────────────────────────────────────────────────

def _load_groups():
    """Список групп из каталога, распарсенный в удобный вид.

    Возвращает list[dict]: {id, name, tag, active, members: [{user_id, limit}]}.
    Элементы с нечитаемым JSON в «Сотрудники и лимиты» пропускаются (не роняют
    всю выдачу лида из-за опечатки в одной группе).
    """
    elements = amocrm.fetch_catalog_elements(CATALOG_GROUPS_ID)
    groups = []
    for el in elements:
        members_raw = amocrm.custom_field_text(el, FIELD_GROUP_MEMBERS) or '[]'
        try:
            members = json.loads(members_raw)
        except (TypeError, ValueError):
            members = []
        active_val = amocrm.custom_field_text(el, FIELD_GROUP_ACTIVE)
        groups.append({
            'id': el.get('id'),
            'name': el.get('name'),
            'tag': amocrm.custom_field_text(el, FIELD_GROUP_TAG),
            'active': bool(active_val) and str(active_val).lower() not in ('0', 'false', 'нет'),
            'members': members,
        })
    return groups


def _find_group_for_user(groups, user_id):
    """Первая активная группа, где состоит user_id. Возвращает (group, limit)
    или (None, None), если сотрудник ни в одной группе не числится."""
    for g in groups:
        if not g['active']:
            continue
        for m in g['members']:
            if int(m.get('user_id', -1)) == int(user_id):
                return g, int(m.get('limit', 0))
    return None, None


# ─── Счётчики ───────────────────────────────────────────────────────────

def _read_counter(user_id, month_str):
    """Только чтение — сколько уже выдано в этом месяце, 0 если записи ещё
    нет. В отличие от `_get_or_create_counter` ничего не создаёт в AmoCRM —
    для статуса «сколько доступно» до нажатия кнопки лишний элемент не нужен."""
    elements = amocrm.fetch_catalog_elements(CATALOG_COUNTERS_ID)
    for el in elements:
        el_user = amocrm.custom_field_text(el, FIELD_COUNTER_USER_ID)
        el_month = amocrm.custom_field_text(el, FIELD_COUNTER_MONTH)
        if el_user == str(user_id) and el_month == month_str:
            return int(amocrm.custom_field_num(el, FIELD_COUNTER_COUNT))
    return 0


def _get_or_create_counter(user_id, month_str):
    """Элемент-счётчик (user_id × месяц). Создаёт с нулём, если ещё нет.
    Возвращает (element, count)."""
    elements = amocrm.fetch_catalog_elements(CATALOG_COUNTERS_ID)
    for el in elements:
        el_user = amocrm.custom_field_text(el, FIELD_COUNTER_USER_ID)
        el_month = amocrm.custom_field_text(el, FIELD_COUNTER_MONTH)
        if el_user == str(user_id) and el_month == month_str:
            count = amocrm.custom_field_num(el, FIELD_COUNTER_COUNT)
            return el, int(count)

    el = amocrm.create_catalog_element(
        CATALOG_COUNTERS_ID,
        name=f'user_{user_id} / {month_str}',
        custom_fields_values=[
            amocrm.build_custom_field(FIELD_COUNTER_USER_ID, str(user_id)),
            amocrm.build_custom_field(FIELD_COUNTER_MONTH, month_str),
            amocrm.build_custom_field(FIELD_COUNTER_COUNT, 0),
        ],
    )
    return el, 0


def _increment_counter(element, new_count):
    amocrm.update_catalog_element(
        CATALOG_COUNTERS_ID,
        element['id'],
        custom_fields_values=[amocrm.build_custom_field(FIELD_COUNTER_COUNT, new_count)],
    )


def _funnel_is_configured(funnel):
    """`source_responsible` необязателен — проверяем только 4 обязательных
    поля, иначе optional-поле ломало бы старые/неполные настройки."""
    required = ('source_pipeline', 'source_status', 'target_pipeline', 'target_status')
    return all(funnel.get(k) for k in required)


# ─── Настройки воронки (источник/назначение) ────────────────────────────
# Список «Распределение: настройки» задуман как один элемент-«синглтон»,
# хранящий 4 числа. Читаем/пишем именно так — без кеша, чтобы правка через
# экран настроек применялась сразу к следующему клику по кнопке.

def _load_funnel_settings():
    """Возвращает dict {source_pipeline, source_status, source_responsible,
    target_pipeline, target_status} — int или None по каждому полю, если ещё
    не выбрано. `source_responsible` необязателен даже когда остальное
    заполнено — None означает «брать статус целиком, без уточнения»."""
    elements = amocrm.fetch_catalog_elements(CATALOG_SETTINGS_ID)
    el = elements[0] if elements else None
    if not el:
        return {'source_pipeline': None, 'source_status': None, 'source_responsible': None,
                'target_pipeline': None, 'target_status': None}

    def _int_or_none(field_id):
        if not field_id:
            return None
        v = amocrm.custom_field_num(el, field_id)
        return int(v) if v else None

    return {
        'source_pipeline': _int_or_none(FIELD_SETTINGS_SOURCE_PIPELINE),
        'source_status': _int_or_none(FIELD_SETTINGS_SOURCE_STATUS),
        'source_responsible': _int_or_none(FIELD_SETTINGS_SOURCE_RESPONSIBLE),
        'target_pipeline': _int_or_none(FIELD_SETTINGS_TARGET_PIPELINE),
        'target_status': _int_or_none(FIELD_SETTINGS_TARGET_STATUS),
    }


def load_funnel_settings():
    _require_config()
    return _load_funnel_settings()


def save_funnel_settings(settings):
    """Записывает воронку-источник/назначение (один синглтон-элемент —
    создаётся при первом сохранении, дальше только обновляется)."""
    _require_config()
    cfs = [
        amocrm.build_custom_field(FIELD_SETTINGS_SOURCE_PIPELINE, int(settings['source_pipeline'])),
        amocrm.build_custom_field(FIELD_SETTINGS_SOURCE_STATUS, int(settings['source_status'])),
        amocrm.build_custom_field(FIELD_SETTINGS_TARGET_PIPELINE, int(settings['target_pipeline'])),
        amocrm.build_custom_field(FIELD_SETTINGS_TARGET_STATUS, int(settings['target_status'])),
    ]
    if FIELD_SETTINGS_SOURCE_RESPONSIBLE:
        # 0 = «не уточнено», тот же язык, что и у остальных числовых полей
        # (custom_field_num возвращает 0.0 для пустого -> _int_or_none даёт None).
        cfs.append(amocrm.build_custom_field(
            FIELD_SETTINGS_SOURCE_RESPONSIBLE, int(settings.get('source_responsible') or 0)
        ))
    elements = amocrm.fetch_catalog_elements(CATALOG_SETTINGS_ID)
    if elements:
        amocrm.update_catalog_element(CATALOG_SETTINGS_ID, elements[0]['id'], custom_fields_values=cfs)
    else:
        amocrm.create_catalog_element(CATALOG_SETTINGS_ID, name='Настройки кнопки', custom_fields_values=cfs)


# ─── Для настроечного экрана виджета ────────────────────────────────────

def list_groups():
    """Группы с добавленным по каждому сотруднику `count` — фактическим
    числом выданных лидов в текущем месяце (рядом с лимитом на экране
    настроек)."""
    _require_config()
    groups = _load_groups()
    month_str = _current_month_msk()
    counters = amocrm.fetch_catalog_elements(CATALOG_COUNTERS_ID)
    count_by_user = {}
    for el in counters:
        if amocrm.custom_field_text(el, FIELD_COUNTER_MONTH) == month_str:
            uid = amocrm.custom_field_text(el, FIELD_COUNTER_USER_ID)
            count_by_user[uid] = int(amocrm.custom_field_num(el, FIELD_COUNTER_COUNT))
    for g in groups:
        for m in g['members']:
            m['count'] = count_by_user.get(str(m.get('user_id')), 0)
    return groups


def save_groups(groups_payload):
    """Перезаписывает группы из настроечного экрана. Каждый элемент
    `groups_payload` — dict {id (опционально, для новой группы отсутствует),
    name, tag, active, members: [{user_id, limit}]}. Группы с `id` —
    обновляются, без `id` — создаются. Удаление групп через этот вызов не
    производится (деактивация через `active=False` достаточна для v1)."""
    _require_config()
    for g in groups_payload:
        members = [
            {'user_id': int(m['user_id']), 'limit': int(m.get('limit', 0))}
            for m in g.get('members', [])
        ]
        cfs = [
            amocrm.build_custom_field(FIELD_GROUP_TAG, g.get('tag') or ''),
            amocrm.build_custom_field(
                FIELD_GROUP_MEMBERS, json.dumps(members, ensure_ascii=False)
            ),
            amocrm.build_custom_field(FIELD_GROUP_ACTIVE, bool(g.get('active', True))),
        ]
        if g.get('id'):
            amocrm.update_catalog_element(
                CATALOG_GROUPS_ID, g['id'], custom_fields_values=cfs, name=g.get('name')
            )
        else:
            amocrm.create_catalog_element(
                CATALOG_GROUPS_ID, name=g.get('name') or 'Группа', custom_fields_values=cfs
            )


def get_status_for_manager(user_id):
    """Только чтение — сколько лидов реально доступно и сколько осталось
    лимита, для отображения на странице ДО нажатия кнопки (ничего не
    назначает, не трогает счётчик).

    Возвращает dict:
      {'ok': True, 'group': ..., 'available_leads': N, 'limit': L, 'used': C, 'remaining': L-C}
      {'ok': False, 'reason': 'not_allowed' | 'funnel_not_configured'}
    """
    _require_config()

    funnel = _load_funnel_settings()
    if not _funnel_is_configured(funnel):
        return {'ok': False, 'reason': 'funnel_not_configured'}

    groups = _load_groups()
    group, limit = _find_group_for_user(groups, user_id)
    if group is None:
        return {'ok': False, 'reason': 'not_allowed'}

    month_str = _current_month_msk()
    count = _read_counter(user_id, month_str)

    candidates = amocrm.fetch_unassigned_leads(
            funnel['source_pipeline'], funnel['source_status'], funnel.get('source_responsible')
        )
    available = sum(1 for c in candidates if group['tag'] in amocrm.lead_tag_names(c))

    return {
        'ok': True,
        'group': group['name'],
        'available_leads': available,
        'limit': limit,
        'used': count,
        'remaining': max(0, limit - count),
    }


# ─── Основной сценарий ──────────────────────────────────────────────────

def get_lead_for_manager(user_id):
    """Обрабатывает нажатие кнопки менеджером `user_id`.

    Возвращает dict:
      {'ok': True, 'lead_id': ..., 'lead_name': ..., 'group': ...}
      {'ok': False, 'reason': 'not_allowed' | 'limit_reached' | 'no_leads', ...}
    Кидает ConfigError, если кнопка ещё не настроена (список/поля/воронки).
    """
    _require_config()

    with _ASSIGN_LOCK:
        funnel = _load_funnel_settings()
        if not _funnel_is_configured(funnel):
            return {'ok': False, 'reason': 'funnel_not_configured'}

        groups = _load_groups()
        group, limit = _find_group_for_user(groups, user_id)
        if group is None:
            return {'ok': False, 'reason': 'not_allowed'}

        month_str = _current_month_msk()
        counter_el, count = _get_or_create_counter(user_id, month_str)
        if count >= limit:
            return {'ok': False, 'reason': 'limit_reached', 'limit': limit, 'count': count}

        candidates = amocrm.fetch_unassigned_leads(
            funnel['source_pipeline'], funnel['source_status'], funnel.get('source_responsible')
        )
        eligible = [c for c in candidates if group['tag'] in amocrm.lead_tag_names(c)]
        if not eligible:
            return {'ok': False, 'reason': 'no_leads'}

        lead = eligible[0]  # order[created_at]=asc в fetch_unassigned_leads — старые вперёд
        amocrm.patch_lead(lead['id'], {
            'responsible_user_id': int(user_id),
            'pipeline_id': funnel['target_pipeline'],
            'status_id': funnel['target_status'],
        })
        _increment_counter(counter_el, count + 1)

        return {
            'ok': True,
            'lead_id': lead['id'],
            'lead_name': lead.get('name'),
            'group': group['name'],
            'count': count + 1,
            'limit': limit,
        }
