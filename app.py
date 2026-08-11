"""Бэкенд + веб-страница кнопки «Получить лид» — отдельный сервис, не зависит
от дашборда.

Менеджеры входят через OAuth AmoCRM (жмут «Войти через AmoCRM», логинятся
на стороне самого AmoCRM — мы их пароль не видим) — так исключается риск
выбрать чужую фамилию из списка и нажимать за коллегу: сервер сам знает,
кто перед ним, из подтверждённой личности через OAuth, а не из выпадающего
списка на клиенте.

Настройки (/settings) — отдельный, более закрытый доступ: HTTP Basic Auth
с отдельным логином только для владельца/руководителей.

Роуты:
  GET  /                  — страница «Получить лид» (нужен вход через OAuth)
  GET  /login              — редирект на страницу логина AmoCRM
  GET  /oauth/callback     — обратный вызов от AmoCRM после входа
  GET  /logout             — выйти (очистить сессию)
  GET  /settings           — страница настроек (админский Basic Auth)
  POST /api/get-lead        — нажатие кнопки (user_id берётся из сессии, не от клиента)
  POST /api/status          — сколько доступно/осталось лимита (из сессии)
  GET/POST /api/settings    — чтение/сохранение настроек (админский Basic Auth)
"""
import os
import secrets
from functools import wraps

from dotenv import load_dotenv
from flask import Flask, jsonify, redirect, render_template, request, session, Response

load_dotenv()

import amocrm
import lead_distribution

app = Flask(__name__)

app.secret_key = os.environ.get('FLASK_SECRET_KEY')
if not app.secret_key:
    app.secret_key = secrets.token_hex(32)
    print('⚠️  FLASK_SECRET_KEY env var not set — используется случайный ключ на этот '
          'процесс (все сессии слетят при следующем деплое/рестарте). Задайте постоянный.')

app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_DEBUG') != '1'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = 60 * 60 * 24 * 30  # 30 дней

OAUTH_CLIENT_ID = os.environ.get('AMOCRM_OAUTH_CLIENT_ID', '').strip()
OAUTH_CLIENT_SECRET = os.environ.get('AMOCRM_OAUTH_CLIENT_SECRET', '').strip()
OAUTH_REDIRECT_URI = os.environ.get('AMOCRM_OAUTH_REDIRECT_URI', '').strip()
AMOCRM_SUBDOMAIN = os.environ.get('AMOCRM_SUBDOMAIN', '').strip()

LEAD_BUTTON_ADMIN_USER = os.environ.get('LEAD_BUTTON_ADMIN_USER', 'admin').strip()
LEAD_BUTTON_ADMIN_PASS = os.environ.get('LEAD_BUTTON_ADMIN_PASS')
if LEAD_BUTTON_ADMIN_PASS is not None:
    # Частая история со вставкой в веб-формы (Render и т.п.) — лишний перевод
    # строки/пробел на конце из буфера обмена молча ломает сравнение.
    LEAD_BUTTON_ADMIN_PASS = LEAD_BUTTON_ADMIN_PASS.strip()
if not LEAD_BUTTON_ADMIN_PASS:
    LEAD_BUTTON_ADMIN_PASS = 'changeme'
    print('⚠️  LEAD_BUTTON_ADMIN_PASS env var not set — using default "changeme". '
          'DO NOT use this in production.')


def requires_admin_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        ok = False
        if auth:
            ok = (
                secrets.compare_digest(auth.username or '', LEAD_BUTTON_ADMIN_USER) and
                secrets.compare_digest(auth.password or '', LEAD_BUTTON_ADMIN_PASS)
            )
        if not ok:
            return Response(
                'Authentication required', 401,
                {'WWW-Authenticate': 'Basic realm="Lead Button Admin"'}
            )
        return f(*args, **kwargs)
    return decorated


def requires_login(f):
    """Для страниц (GET) — редирект на /login. Для API (не-GET) — 401 JSON."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('user_id'):
            if request.method == 'GET':
                return redirect('/login')
            return jsonify({'ok': False, 'error': 'not_logged_in'}), 401
        return f(*args, **kwargs)
    return decorated


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


@app.route('/login')
def login():
    if not (OAUTH_CLIENT_ID and OAUTH_REDIRECT_URI):
        return 'OAuth-вход ещё не настроен администратором (нет AMOCRM_OAUTH_CLIENT_ID/REDIRECT_URI).', 503
    state = secrets.token_urlsafe(16)
    session['oauth_state'] = state
    auth_url = (
        'https://www.amocrm.ru/oauth'
        f'?client_id={OAUTH_CLIENT_ID}&state={state}&mode=post_message'
    )
    # NB: mode=post_message — формат для popup-сценария; при обычном
    # полностраничном переходе AmoCRM тем не менее должна сделать 302 на
    # redirect_uri с ?code=&state=. Если на практике придёт не так —
    # см. https://www.amocrm.ru/developers/content/oauth/step-by-step,
    # возможно потребуется убрать параметр mode вовсе.
    return redirect(auth_url)


@app.route('/oauth/callback')
def oauth_callback():
    code = request.args.get('code')
    state = request.args.get('state')
    if not code or not state or state != session.get('oauth_state'):
        return 'Не удалось войти (код/состояние не совпали). Начните заново с <a href="/login">/login</a>.', 400
    session.pop('oauth_state', None)

    try:
        token_data = amocrm.oauth_exchange_code(
            OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_REDIRECT_URI, code
        )
        candidate_user_id = amocrm.decode_jwt_user_id(token_data.get('access_token', ''))
        # fetch_active_users(), не fetch_users() — уволенный/деактивированный
        # сотрудник не должен получить доступ к кнопке, даже если ещё умеет
        # войти в сам AmoCRM.
        matched = next((u for u in amocrm.fetch_active_users() if u.get('id') == candidate_user_id), None)
    except amocrm.AmoCRMError as e:
        return f'Ошибка при обращении к AmoCRM во время входа: {e}', 502

    if not matched:
        return 'Доступ закрыт: пользователь не найден среди активных сотрудников AmoCRM.', 403

    session.clear()
    session.permanent = True
    session['user_id'] = matched['id']
    session['user_name'] = matched.get('name')
    return redirect('/')


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')


@app.route('/')
@requires_login
def index():
    return render_template('index.html', user_name=session.get('user_name'))


@app.route('/settings')
@requires_admin_auth
def settings_page():
    return render_template('settings.html')


@app.route('/api/get-lead', methods=['POST'])
@requires_login
def api_get_lead():
    user_id = session['user_id']
    try:
        result = lead_distribution.get_lead_for_manager(user_id)
    except lead_distribution.ConfigError as e:
        return jsonify({'ok': False, 'error': 'not_configured', 'detail': str(e)}), 503
    except amocrm.AmoCRMError as e:
        return jsonify({'ok': False, 'error': 'amocrm_error', 'detail': str(e)}), 502

    return jsonify(result)


@app.route('/api/status', methods=['POST'])
@requires_login
def api_status():
    user_id = session['user_id']
    try:
        result = lead_distribution.get_status_for_manager(user_id)
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
                    for u in amocrm.fetch_active_users() if u.get('id')
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
