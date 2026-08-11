"""Тонкий клиент AmoCRM API v4 для кнопки «Получить лид».

Авторизация: долгосрочный токен из env AMOCRM_SUBDOMAIN/AMOCRM_TOKEN
(создаётся в AmoCRM: Настройки → Интеграции → «+ Создать интеграцию» →
Внешняя интеграция → «Ключи и доступы» → «Сгенерировать токен»).
"""
import os
import time

import requests

API_PAGE_LIMIT = 250
HTTP_TIMEOUT = 60
HTTP_RETRIES = 3


class AmoCRMError(RuntimeError):
    pass


def _subdomain_and_token():
    sub = os.environ.get('AMOCRM_SUBDOMAIN', '').strip()
    tok = os.environ.get('AMOCRM_TOKEN', '').strip()
    if not sub or not tok:
        raise AmoCRMError('AMOCRM_SUBDOMAIN / AMOCRM_TOKEN не заданы в env')
    return sub, tok


def _request(path, params=None, method='GET', json_body=None):
    sub, tok = _subdomain_and_token()
    url = f'https://{sub}.amocrm.ru/api/v4{path}'
    last_exc = None
    for attempt in range(HTTP_RETRIES):
        try:
            r = requests.request(
                method,
                url,
                headers={'Authorization': f'Bearer {tok}', 'Accept': 'application/json'},
                params=params or {},
                json=json_body,
                timeout=HTTP_TIMEOUT,
            )
        except (requests.Timeout, requests.ConnectionError) as e:
            last_exc = e
            if attempt < HTTP_RETRIES - 1:
                time.sleep(2 * (attempt + 1))
                continue
            raise AmoCRMError(f'{path} → сетевой сбой после {HTTP_RETRIES} попыток: {e}') from e
        if r.status_code == 429:
            if attempt < HTTP_RETRIES - 1:
                wait = r.headers.get('Retry-After')
                time.sleep(float(wait) if wait else 2 * (attempt + 1))
                continue
            raise AmoCRMError(f'{path} → HTTP 429 после {HTTP_RETRIES} попыток')
        if r.status_code == 204 or not r.content:
            return None
        if r.status_code >= 400:
            raise AmoCRMError(f'{path} → HTTP {r.status_code}: {r.text[:300]}')
        return r.json()
    raise AmoCRMError(f'{path} → недостижимо: {last_exc}')  # pragma: no cover


def fetch_users():
    """list[dict] — пользователи AmoCRM (id, name, email и др.)."""
    data = _request('/users', {'limit': API_PAGE_LIMIT}) or {}
    return data.get('_embedded', {}).get('users', [])


def fetch_pipelines():
    """Все воронки аккаунта с их этапами — для дропдаунов на экране настроек.
    Возвращает list[dict]: {id, name, statuses: [{id, name}]}."""
    data = _request('/leads/pipelines') or {}
    pipelines = (data.get('_embedded') or {}).get('pipelines') or []
    out = []
    for p in pipelines:
        statuses = (p.get('_embedded') or {}).get('statuses') or []
        out.append({
            'id': p.get('id'),
            'name': p.get('name'),
            'statuses': [{'id': s.get('id'), 'name': s.get('name')} for s in statuses],
        })
    return out


def lead_tag_names(lead):
    """Список названий тегов сделки (пусто, если тегов нет)."""
    tags = (lead.get('_embedded') or {}).get('tags') or []
    return [t.get('name') for t in tags if t.get('name')]


def fetch_unassigned_leads(pipeline_id, status_id, limit=250):
    """Сделки конкретного статуса-источника без ответственного менеджера.
    Возвращает list[dict], каждая сделка — с тегами (with=tags)."""
    data = _request('/leads', {
        'filter[statuses][0][pipeline_id]': pipeline_id,
        'filter[statuses][0][status_id]': status_id,
        'with': 'tags',
        'limit': limit,
        'order[created_at]': 'asc',
    }) or {}
    leads = data.get('_embedded', {}).get('leads', [])
    return [l for l in leads if not l.get('responsible_user_id')]


def patch_lead(lead_id, fields):
    """Обновляет сделку (напр. responsible_user_id, status_id)."""
    return _request(f'/leads/{lead_id}', method='PATCH', json_body=fields)


def fetch_account_tags(entity_type='leads'):
    """Справочник тегов аккаунта — для выбора тега на экране настроек."""
    out = []
    page = 1
    while page <= 50:
        data = _request(f'/{entity_type}/tags', {'limit': API_PAGE_LIMIT, 'page': page})
        if not data:
            break
        items = (data.get('_embedded') or {}).get('tags') or []
        if not items:
            break
        out.extend(items)
        if len(items) < API_PAGE_LIMIT:
            break
        page += 1
    return out


# ─── «Списки» (Catalogs API v4) — хранилище групп/счётчиков/настроек ───

def create_catalog(name, catalog_type='regular'):
    data = _request('/catalogs', method='POST', json_body=[{'name': name, 'type': catalog_type}])
    return (data.get('_embedded') or {}).get('catalogs', [{}])[0]


def create_catalog_custom_fields(catalog_id, fields):
    data = _request(f'/catalogs/{catalog_id}/custom_fields', method='POST', json_body=fields)
    return (data.get('_embedded') or {}).get('custom_fields', [])


def delete_catalog_custom_field(catalog_id, field_id):
    return _request(f'/catalogs/{catalog_id}/custom_fields/{field_id}', method='DELETE')


def fetch_catalog_elements(catalog_id, limit=250):
    elements = []
    page = 1
    while page <= 50:
        data = _request(f'/catalogs/{catalog_id}/elements', {'limit': limit, 'page': page})
        if not data:
            break
        items = (data.get('_embedded') or {}).get('elements') or []
        if not items:
            break
        elements.extend(items)
        if len(items) < limit:
            break
        page += 1
    return elements


def create_catalog_element(catalog_id, name, custom_fields_values):
    data = _request(
        f'/catalogs/{catalog_id}/elements',
        method='POST',
        json_body=[{'name': name, 'custom_fields_values': custom_fields_values}],
    )
    return (data.get('_embedded') or {}).get('elements', [{}])[0]


def update_catalog_element(catalog_id, element_id, custom_fields_values=None, name=None):
    payload = {'id': element_id}
    if name is not None:
        payload['name'] = name
    if custom_fields_values is not None:
        payload['custom_fields_values'] = custom_fields_values
    return _request(f'/catalogs/{catalog_id}/elements', method='PATCH', json_body=[payload])


def custom_field_text(entity, field_id):
    """Текстовое значение кастомного поля (text/textarea/select)."""
    for cf in entity.get('custom_fields_values') or []:
        if cf.get('field_id') == field_id:
            vals = cf.get('values') or []
            if vals:
                return vals[0].get('value')
    return None


def custom_field_num(entity, field_id):
    """Числовое значение кастомного поля. 0.0, если не заполнено."""
    for cf in entity.get('custom_fields_values') or []:
        if cf.get('field_id') == field_id:
            vals = cf.get('values') or []
            if vals:
                v = vals[0].get('value')
                try:
                    return float(v)
                except (TypeError, ValueError):
                    return 0.0
    return 0.0


def build_custom_field(field_id, value):
    return {'field_id': field_id, 'values': [{'value': value}]}
