"""Бэкенд + веб-страница кнопки «Получить лид» — отдельный сервис, не зависит
от дашборда.

Обычное веб-приложение за HTTP Basic Auth (один общий логин/пароль на всех
менеджеров, как в дашборде Dash_Bot_Fork) — не виджет AmoCRM, открывается
отдельной вкладкой/ссылкой. Менеджер сам выбирает себя из списка на странице.

Роуты:
  GET  /                — страница «Получить лид» (дропдаун + кнопка)
  GET  /settings         — страница настроек (группы, лимиты, воронка)
  POST /api/get-lead      — нажатие кнопки
  GET/POST /api/settings  — чтение/сохранение настроек
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

LEAD_BUTTON_USER = os.environ.get('LEAD_BUTTON_USER', 'admin')
LEAD_BUTTON_PASS = os.environ.get('LEAD_BUTTON_PASS')
if not LEAD_BUTTON_PASS:
    LEAD_BUTTON_PASS = 'changeme'
    print('⚠️  LEAD_BUTTON_PASS env var not set — using default "changeme". '
          'DO NOT use this in production.')


def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        ok = False
        if auth:
            ok = (
                secrets.compare_digest(auth.username or '', LEAD_BUTTON_USER) and
                secrets.compare_digest(auth.password or '', LEAD_BUTTON_PASS)
            )
        if not ok:
            return Response(
                'Authentication required', 401,
                {'WWW-Authenticate': 'Basic realm="Lead Button"'}
            )
        return f(*args, **kwargs)
    return decorated


@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
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
@requires_auth
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


@app.route('/api/settings', methods=['GET', 'POST'])
@requires_auth
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
