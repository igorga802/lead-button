"""Бэкенд + веб-страница кнопки «Получить лид» — отдельный сервис, не зависит
от дашборда.

Обычное веб-приложение, два уровня HTTP Basic Auth:
  - LEAD_BUTTON_USER/PASS       — общий логин менеджеров, видят только
                                   страницу кнопки (сколько лидов доступно + сама кнопка).
  - LEAD_BUTTON_ADMIN_USER/PASS — отдельный логин для настроек (группы,
                                   лимиты, воронка) — только владелец/руководители.

Не виджет AmoCRM — открывается отдельной вкладкой/ссылкой. Менеджер сам
выбирает себя из списка на странице.

Роуты:
  GET  /                  — страница «Получить лид» (менеджерский логин)
  GET  /settings          — страница настроек (админский логин)
  POST /api/get-lead       — нажатие кнопки (менеджерский логин)
  POST /api/status         — сколько доступно/осталось лимита (менеджерский логин)
  GET/POST /api/settings   — чтение/сохранение настроек (админский логин)
"""
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, Response

load_dotenv()

import amocrm
import lead_distribution

app = Flask(__name__)


def _env_credentials(user_key, pass_key, default_user, fallback_pass_warning):
    user = os.environ.get(user_key, default_user)
    pw = os.environ.get(pass_key)
    if not pw:
        pw = 'changeme'
        print(f'⚠️  {pass_key} env var not set — using default "changeme". '
              f'DO NOT use this in production. {fallback_pass_warning}')
    return user, pw


LEAD_BUTTON_USER, LEAD_BUTTON_PASS = _env_credentials(
    'LEAD_BUTTON_USER', 'LEAD_BUTTON_PASS', 'manager', '(логин менеджеров)'
)
LEAD_BUTTON_ADMIN_USER, LEAD_BUTTON_ADMIN_PASS = _env_credentials(
    'LEAD_BUTTON_ADMIN_USER', 'LEAD_BUTTON_ADMIN_PASS', 'admin', '(логин настроек)'
)


def _make_auth_decorator(realm, valid_user, valid_pass):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.authorization
            ok = False
            if auth:
                ok = (
                    secrets.compare_digest(auth.username or '', valid_user) and
                    secrets.compare_digest(auth.password or '', valid_pass)
                )
            if not ok:
                return Response(
                    'Authentication required', 401,
                    {'WWW-Authenticate': f'Basic realm="{realm}"'}
                )
            return f(*args, **kwargs)
        return decorated
    return decorator


# Разные "realm" в заголовке — браузер держит для них отдельные сохранённые
# пароли и не путает менеджерский логин с админским, даже если оба открыты
# в одной вкладке один за другим.
requires_auth = _make_auth_decorator('Lead Button', LEAD_BUTTON_USER, LEAD_BUTTON_PASS)
requires_admin_auth = _make_auth_decorator('Lead Button Admin', LEAD_BUTTON_ADMIN_USER, LEAD_BUTTON_ADMIN_PASS)


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:"
    )
    return response


@app.route('/')
@requires_auth
def index():
    users = sorted(
        ({'id': u.get('id'), 'name': u.get('name')} for u in amocrm.fetch_users() if u.get('id')),
        key=lambda u: u['name'] or ''
    )
    return render_template('index.html', users=users)


@app.route('/settings')
@requires_admin_auth
def settings_page():
    return render_template('settings.html')


@app.route('/api/get-lead', methods=['POST'])
@requires_auth
def api_get_lead():
    body = request.get_json(silent=True) or {}
    user_id = body.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'missing_user_id'}), 400

    try:
        known_user_ids = {u.get('id') for u in amocrm.fetch_users()}
        if int(user_id) not in known_user_ids:
            return jsonify({'ok': False, 'error': 'unknown_user'}), 403
        result = lead_distribution.get_lead_for_manager(int(user_id))
    except lead_distribution.ConfigError as e:
        return jsonify({'ok': False, 'error': 'not_configured', 'detail': str(e)}), 503
    except amocrm.AmoCRMError as e:
        return jsonify({'ok': False, 'error': 'amocrm_error', 'detail': str(e)}), 502

    return jsonify(result)


@app.route('/api/status', methods=['POST'])
@requires_auth
def api_status():
    body = request.get_json(silent=True) or {}
    user_id = body.get('user_id')
    if not user_id:
        return jsonify({'ok': False, 'error': 'missing_user_id'}), 400

    try:
        known_user_ids = {u.get('id') for u in amocrm.fetch_users()}
        if int(user_id) not in known_user_ids:
            return jsonify({'ok': False, 'error': 'unknown_user'}), 403
        result = lead_distribution.get_status_for_manager(int(user_id))
    except lead_distribution.ConfigError as e:
        return jsonify({'ok': False, 'error': 'not_configured', 'detail': str(e)}), 503
    except amocrm.AmoCRMError as e:
        return jsonify({'ok': False, 'error': 'amocrm_error', 'detail': str(e)}), 502

    return jsonify(result)


@app.route('/api/settings', methods=['GET', 'POST'])
@requires_admin_auth
def api_settings():
    try:
        if request.method == 'GET':
            return jsonify({
                'ok': True,
                'groups': lead_distribution.list_groups(),
                'tags': [t.get('name') for t in amocrm.fetch_account_tags('leads') if t.get('name')],
                'users': [
                    {'id': u.get('id'), 'name': u.get('name')}
                    for u in amocrm.fetch_users() if u.get('id')
                ],
                'pipelines': amocrm.fetch_pipelines(),
                'funnel': lead_distribution.load_funnel_settings(),
            })
        payload = request.get_json(silent=True) or {}
        lead_distribution.save_groups(payload.get('groups', []))
        if payload.get('funnel'):
            lead_distribution.save_funnel_settings(payload['funnel'])
        return jsonify({'ok': True})
    except lead_distribution.ConfigError as e:
        return jsonify({'ok': False, 'error': 'not_configured', 'detail': str(e)}), 503
    except amocrm.AmoCRMError as e:
        return jsonify({'ok': False, 'error': 'amocrm_error', 'detail': str(e)}), 502


if __name__ == '__main__':
    debug = os.environ.get('FLASK_DEBUG') == '1'
    port = int(os.environ.get('PORT', '5060'))
    app.run(debug=debug, port=port, host='127.0.0.1')
