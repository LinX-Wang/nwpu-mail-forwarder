from dataclasses import replace
from pathlib import Path

import pytest

from nwpu_forwarder.config import ConfigManager
from nwpu_forwarder.web import create_app


@pytest.fixture
def public_web(paths, config):
    ConfigManager(paths.config).save(
        replace(config, public_url="https://panel.example.invalid/mail")
    )
    app = create_app(paths, "synthetic-panel-password")
    app.config["TESTING"] = True
    app.config["SERVER_NAME"] = "panel.example.invalid"
    client = app.test_client()
    client.environ_base["SCRIPT_NAME"] = "/mail"
    return client


def proxy_headers(peer="198.51.100.10"):
    return {
        "X-Forwarded-Host": "panel.example.invalid",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Prefix": "/mail",
        "X-Forwarded-For": peer,
    }


def public_login(client):
    headers = proxy_headers()
    response = client.get("/login", headers=headers)
    assert response.status_code == 200
    with client.session_transaction() as session:
        csrf = session["csrf"]
    result = client.post(
        "/login",
        headers=headers,
        data={
            "csrf": csrf,
            "username": "2026000000",
            "password": "synthetic-panel-password",
        },
    )
    assert result.status_code == 302 and result.headers["Location"] == "/mail/"
    return headers


def test_public_secure_cookies_and_redirects(public_web):
    response = public_web.get("/login", headers=proxy_headers())
    cookie = response.headers["Set-Cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "Path=/mail" in cookie
    assert "SameSite=Strict" in cookie
    assert response.headers["Strict-Transport-Security"] == "max-age=31536000"
    assert (
        public_web.get("/").headers["Location"] == "https://panel.example.invalid/mail/"
    )
    assert public_web.post("/login").status_code == 400


def test_public_links_static_and_authenticated_routes(public_web):
    headers = public_login(public_web)
    for path in ("/", "/settings", "/logs", "/api/status"):
        response = public_web.get(path, headers=headers)
        assert response.status_code == 200
    html = public_web.get("/", headers=headers).data.decode()
    assert 'href="/mail/settings"' in html and 'action="/mail/action"' in html
    assert 'href="/mail/static/app.css?v=' in html
    assert 'action="/mail/logout"' in html


def test_proxy_headers_from_nonlocal_peer_are_not_trusted(public_web):
    response = public_web.get(
        "/login",
        headers=proxy_headers(),
        environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert response.status_code == 308
    assert (
        public_web.post(
            "/login",
            headers=proxy_headers(),
            environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
        ).status_code
        == 400
    )


def test_bad_host_and_prefix(public_web):
    headers = proxy_headers()
    headers["X-Forwarded-Host"] = "attacker.invalid"
    assert public_web.get("/login", headers=headers).status_code == 400
    headers = proxy_headers()
    headers["X-Forwarded-Prefix"] = "/evil"
    assert (
        public_web.get("/login", headers=headers).headers["Location"]
        == "https://panel.example.invalid/mail/login"
    )


def test_login_limits_use_forwarded_client_ip(public_web):
    headers = proxy_headers()
    public_web.get("/login", headers=headers)
    with public_web.session_transaction() as session:
        csrf = session["csrf"]
    for _ in range(5):
        public_web.post(
            "/login",
            headers=headers,
            data={"csrf": csrf, "username": "2026000000", "password": "wrong"},
        )
    assert (
        public_web.post(
            "/login",
            headers=headers,
            data={"csrf": csrf, "username": "2026000000", "password": "wrong"},
        ).status_code
        == 429
    )
    assert (
        public_web.post(
            "/login",
            headers=proxy_headers("198.51.100.11"),
            data={"csrf": csrf, "username": "2026000000", "password": "wrong"},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.invalid/mail",
        "https://example.invalid/",
        "https://user:secret@example.invalid/mail",
        "https://example.invalid/../mail",
        "https://example.invalid/mail?query=x",
        "https://example.invalid:8443/mail",
    ],
)
def test_invalid_public_url(config, url):
    with pytest.raises(ValueError):
        replace(config, public_url=url).validate()


def test_caddy_route_preserves_existing_site_and_is_idempotent():
    source = Path(__file__).resolve().parents[1] / "deploy" / "expose-web.py"
    # Deployment script uses Linux pwd; only its pure route builder is compiled here.
    import ast

    module = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        node
        for node in module.body
        if isinstance(node, ast.FunctionDef) and node.name == "add_panel_route"
    )
    namespace = {"re": __import__("re")}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    original = "example.invalid {\n    reverse_proxy 127.0.0.1:9000\n}\n"
    updated = namespace["add_panel_route"](original, "example.invalid", "/mail")
    assert "reverse_proxy 127.0.0.1:9000" in updated
    assert "handle_path /mail/*" in updated
    assert "header_up X-Forwarded-Prefix /mail" in updated
    assert namespace["add_panel_route"](updated, "example.invalid", "/mail") == updated
