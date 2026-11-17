from dataclasses import replace
import time

import pytest

from nwpu_forwarder.config import ConfigManager
from nwpu_forwarder.web import create_app


@pytest.mark.parametrize(
    'path', ['/login', '/', '/settings', '/settings?section=forwarding', '/logs', '/api/status']
)
def test_expired_session_shows_login_form_without_status_polling(paths, config, path):
    ConfigManager(paths.config).save(config)
    client = create_app(paths, 'synthetic-owner-password').test_client()
    with client.session_transaction() as session:
        session.update(authenticated=True, login_time=time.time() - 3601, csrf='expired-session-csrf')
    response = client.get(path, follow_redirects=True)
    assert response.status_code == 200
    assert b'data-login-form' in response.data
    assert b'data-status-url' not in response.data
    assert b'class="app-shell"' not in response.data
    with client.session_transaction() as session:
        assert not session.get('authenticated')
        csrf = session['csrf']
    # Reauthentication works using the actual form's still-valid CSRF token.
    assert client.post('/login', data={
        'csrf': csrf, 'username': config.panel_username, 'password': 'synthetic-owner-password',
    }).status_code == 302
    assert client.get('/api/status').status_code == 200


def test_login_page_does_not_render_dashboard_for_active_session(paths, config):
    ConfigManager(paths.config).save(config)
    client = create_app(paths, 'synthetic-owner-password').test_client()
    with client.session_transaction() as session:
        session.update(authenticated=True, login_time=time.time(), csrf='active-csrf')
    response = client.get('/login')
    assert b'data-login-form' in response.data
    assert b'data-status-url' not in response.data


@pytest.mark.parametrize(
    "username,password,success",
    [
        ("2026000000", "synthetic-owner-password", True),
        ("2026000001", "synthetic-owner-password", False),
        ("2026000000", "wrong-password", False),
        ("", "synthetic-owner-password", False),
    ],
)
def test_login_requires_both_student_number_and_password(
    paths, config, username, password, success
):
    ConfigManager(paths.config).save(config)
    client = create_app(paths, "synthetic-owner-password").test_client()
    page = client.get("/login")
    assert b'autocomplete="username"' in page.data
    assert config.panel_username.encode() not in page.data
    with client.session_transaction() as session:
        csrf = session["csrf"]
    response = client.post(
        "/login", data={"csrf": csrf, "username": username, "password": password}
    )
    assert response.status_code == (302 if success else 200)
    if success:
        assert client.get("/api/status").status_code == 200
        dashboard = client.get("/")
        assert b'data-panel-account' in dashboard.data
        assert config.panel_username.encode() in dashboard.data
    else:
        assert "学号或登录密码不正确".encode() in response.data
        assert client.get("/api/status").status_code == 302


def test_unconfigured_account_cannot_fall_back_to_password_only(paths, config):
    ConfigManager(paths.config).save(replace(config, panel_username=""))
    client = create_app(paths, "synthetic-owner-password").test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    response = client.post(
        "/login", data={"csrf": csrf, "password": "synthetic-owner-password"}
    )
    assert response.status_code == 200
    assert client.get("/").status_code == 302


@pytest.mark.parametrize(
    "value",
    ["abc123", "12345", "123456789012345678901", "２０２６００００００", None, 123456],
)
def test_student_number_validation(config, value):
    with pytest.raises(ValueError):
        replace(config, panel_username=value).validate()
