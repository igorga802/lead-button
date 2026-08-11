"""Бэкенд кнопки «Получить лид» — отдельный сервис, не зависит от дашборда.

Два роута:
  POST /api/widget/get-lead  — менеджер нажал кнопку в AmoCRM
  GET/POST /api/widget/settings — экран настроек виджета (группы, лимиты, воронка)

Оба закрыты общим секретом (WIDGET_SHARED_SECRET), зашитым в widget/script.js,
и CORS, ограниченным на конкретный поддомен AmoCRM. Никакого HTTP Basic Auth
здесь нет — это не веб-страница для человека, а API для JS-виджета.
"""
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

import amocrm
import lead_distribution

app = Flask(__name__)

WIDGET_SHARED_SECRET = os.environ.get('WIDGET_SHARED_SECRET', '')
AMOCRM_SUBDOMAIN = os.environ.get('AMOCRM_SUBDOMAIN', '').strip()


@app.after_request
def add_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    if AMOCRM_SUBDOMAIN:
        response.headers['Access-Control-Allow-Origin'] = f'https://{AMOCRM_SUBDOMAIN}.amocrm.ru'
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Widget-Secret'
    return response


def requires_widget_secret(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'OPTIONS':
            return f(*args, **kwargs)  # CORS preflight — без кастомных заголовков
        if not WIDGET_SHARED_SECRET:
            return jsonify({'ok': False, 'error': 'widget_not_configured'}), 503
        provided = request.headers.get('X-Widget-Secret', '')
        if not secrets.compare_digest(provided, WIDGET_SHARED_SECRET):
            return jsonify({'ok': False, 'error': 'forbidden'}), 403
        return f(*args, **kwargs)
    return decorated


@app.route('/')
def index():
    return jsonify({'service': 'lead-button', 'status': 'ok'})


@app.route('/api/widget/get-lead', methods=['POST', 'OPTIONS'])
@requires_widget_secret
def widget_get_lead():
    if request.method == 'OPTIONS':
        return '', 204

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


@app.route('/api/widget/settings', methods=['GET', 'POST', 'OPTIONS'])
@requires_widget_secret
def widget_settings():
    if request.method == 'OPTIONS':
        return '', 204

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
