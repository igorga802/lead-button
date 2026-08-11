"""Разовый скрипт первоначальной настройки кнопки «Получить лид»: создаёт в
AmoCRM три списка («Распределение: группы», «Распределение: счётчики»,
«Распределение: настройки») с нужными полями и печатает готовый блок для
.env. Идемпотентен — если список с таким названием уже есть, повторно не
создаёт, только доводит поля до нужного набора.

Запуск: python3 _setup_lead_button.py
"""
from dotenv import load_dotenv
load_dotenv()

import amocrm

GROUPS_NAME = 'Распределение: группы'
COUNTERS_NAME = 'Распределение: счётчики'
SETTINGS_NAME = 'Распределение: настройки'

# Поля товарного шаблона, которые AmoCRM в этом аккаунте автоматически вешает
# на любой новый список, даже если он не для товаров — не нужны нам, удаляем.
_JUNK_FIELD_NAMES = {'Артикул', 'Цена', 'Единица измерения', 'Спец цена 1', 'Оптовая цена'}


def _existing_catalogs():
    data = amocrm._request('/catalogs', {'limit': 250}) or {}
    return (data.get('_embedded') or {}).get('catalogs') or []


def _fields(catalog_id):
    data = amocrm._request(f'/catalogs/{catalog_id}/custom_fields', {'limit': 250}) or {}
    return (data.get('_embedded') or {}).get('custom_fields') or []


def ensure_catalog(existing, name, wanted_fields):
    """wanted_fields — list[{'name', 'type'}]. Возвращает (catalog_id, {name: field_id})."""
    cat = next((c for c in existing if c['name'] == name), None)
    if cat:
        print(f'  список «{name}» уже есть, id={cat["id"]}')
    else:
        cat = amocrm.create_catalog(name)
        print(f'  создан список «{name}», id={cat["id"]}')

    existing_fields = _fields(cat['id'])

    junk = [f for f in existing_fields if f['name'] in _JUNK_FIELD_NAMES]
    for f in junk:
        amocrm.delete_catalog_custom_field(cat['id'], f['id'])
        print(f'    удалено служебное поле товарного шаблона «{f["name"]}»')

    by_name = {f['name']: f['id'] for f in existing_fields if f['name'] not in _JUNK_FIELD_NAMES}
    missing = [f for f in wanted_fields if f['name'] not in by_name]
    if missing:
        created = amocrm.create_catalog_custom_fields(cat['id'], missing)
        for f in created:
            by_name[f['name']] = f['id']
            print(f'    создано поле «{f["name"]}», id={f["id"]}')
    else:
        print('    все нужные поля уже существуют')

    return cat['id'], by_name


if __name__ == '__main__':
    print('Проверяю существующие списки…')
    existing = _existing_catalogs()

    print(f'Настраиваю «{GROUPS_NAME}»…')
    groups_id, gf = ensure_catalog(existing, GROUPS_NAME, [
        {'name': 'Тег', 'type': 'text'},
        {'name': 'Сотрудники и лимиты', 'type': 'textarea'},
        {'name': 'Активна', 'type': 'checkbox'},
    ])

    print(f'Настраиваю «{COUNTERS_NAME}»…')
    counters_id, cf = ensure_catalog(existing, COUNTERS_NAME, [
        {'name': 'user_id', 'type': 'text'},
        {'name': 'Месяц', 'type': 'text'},
        {'name': 'Количество', 'type': 'numeric'},
    ])

    print(f'Настраиваю «{SETTINGS_NAME}»…')
    settings_id, sf = ensure_catalog(existing, SETTINGS_NAME, [
        {'name': 'Воронка-источник', 'type': 'numeric'},
        {'name': 'Этап-источник', 'type': 'numeric'},
        {'name': 'Воронка-назначение', 'type': 'numeric'},
        {'name': 'Этап-назначение', 'type': 'numeric'},
    ])

    print('\nГотово. Добавь/обнови в .env:\n')
    print(f'LEAD_BUTTON_CATALOG_GROUPS_ID={groups_id}')
    print(f'LEAD_BUTTON_FIELD_GROUP_TAG={gf["Тег"]}')
    print(f'LEAD_BUTTON_FIELD_GROUP_MEMBERS={gf["Сотрудники и лимиты"]}')
    print(f'LEAD_BUTTON_FIELD_GROUP_ACTIVE={gf["Активна"]}')
    print(f'LEAD_BUTTON_CATALOG_COUNTERS_ID={counters_id}')
    print(f'LEAD_BUTTON_FIELD_COUNTER_USER_ID={cf["user_id"]}')
    print(f'LEAD_BUTTON_FIELD_COUNTER_MONTH={cf["Месяц"]}')
    print(f'LEAD_BUTTON_FIELD_COUNTER_COUNT={cf["Количество"]}')
    print(f'LEAD_BUTTON_CATALOG_SETTINGS_ID={settings_id}')
    print(f'LEAD_BUTTON_FIELD_SETTINGS_SOURCE_PIPELINE={sf["Воронка-источник"]}')
    print(f'LEAD_BUTTON_FIELD_SETTINGS_SOURCE_STATUS={sf["Этап-источник"]}')
    print(f'LEAD_BUTTON_FIELD_SETTINGS_TARGET_PIPELINE={sf["Воронка-назначение"]}')
    print(f'LEAD_BUTTON_FIELD_SETTINGS_TARGET_STATUS={sf["Этап-назначение"]}')
