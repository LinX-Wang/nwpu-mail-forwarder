"""Reproduce expired-cookie login and polling redirects using an isolated runtime."""
from pathlib import Path
import tempfile
import threading
import time
import logging

from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server
from flask import request, session

from nwpu_forwarder.config import Config, ConfigManager, Paths
from nwpu_forwarder.web import create_app

logging.getLogger('werkzeug').setLevel(logging.ERROR)
with tempfile.TemporaryDirectory(prefix='session-expiry-') as directory:
    root = Path(directory)
    paths = Paths(root / 'config.json', root / 'data', root / 'logs')
    ConfigManager(paths.config).save(Config(panel_username='2026000000'))
    app = create_app(paths, 'synthetic-session-password')
    expiry = {'on_status': False}
    def expire_during_poll():
        if expiry['on_status'] and request.path == '/api/status':
            session['login_time'] = time.time() - 3601
            expiry['on_status'] = False
    app.before_request_funcs[None].insert(0, expire_during_poll)
    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}'
    serializer = app.session_interface.get_signing_serializer(app)
    def cookie(expired):
        return serializer.dumps(dict(authenticated=True, login_time=time.time() - (3601 if expired else 0), csrf='synthetic-csrf'))
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path='C:/Program Files/Google/Chrome/Application/chrome.exe', headless=True)
            context = browser.new_context()
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda e: errors.append(str(e)))
            requests = []
            page.on('request', lambda r: requests.append(r.url) if '/api/status' in r.url else None)
            context.add_cookies([dict(name='nwpu_panel', value=cookie(True), url=url)])
            page.goto(url + '/')
            expect(page.locator('[data-login-form]')).to_be_visible()
            expect(page.locator('.app-shell')).to_have_count(0)
            page.wait_for_timeout(16000)
            assert not requests, requests
            assert page.url == url + '/login'
            context.add_cookies([dict(name='nwpu_panel', value=cookie(False), url=url)])
            expiry['on_status'] = True
            page.goto(url + '/')
            expect(page.locator('[data-login-form]')).to_be_visible()
            assert len(requests) == 1, requests
            page.wait_for_timeout(16000)
            assert len(requests) == 1, requests
            assert not errors, errors
            output = Path(__file__).resolve().parents[1] / 'runtime' / 'session-expiry-fixed.png'
            page.screenshot(path=str(output), full_page=True)
            browser.close()
            print('Expired cookie: login visible, zero polling requests for 16 seconds.')
            print('Expiry during polling: one redirect, no further requests for 16 seconds.')
            print('Browser JavaScript errors: 0.')
    finally:
        server.shutdown()
