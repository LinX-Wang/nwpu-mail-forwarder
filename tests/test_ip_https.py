import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest


@pytest.fixture
def builders():
    source = Path(__file__).resolve().parents[1] / "deploy" / "expose-ip-https.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    names = {
        "remove_ip_site",
        "http_site",
        "render_ip_site",
        "legacy_redirect",
        "default_ip_sni",
    }
    functions = [
        n for n in module.body if isinstance(n, ast.FunctionDef) and n.name in names
    ]
    namespace = {
        "re": re,
        "urlsplit": urlsplit,
        "WEBROOT": Path("/acme"),
        "BEGIN": "# BEGIN NWPU MAIL FORWARDER",
        "END": "# END NWPU MAIL FORWARDER",
    }
    exec(  # noqa: S102
        compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace


def test_challenge_route_and_secure_proxy(builders):
    initial = builders["render_ip_site"]("192.0.2.10", "/mail")
    assert "handle /.well-known/acme-challenge/*" in initial
    assert "https://192.0.2.10 {" not in initial
    final = builders["render_ip_site"]("192.0.2.10", "/mail", True)
    assert "tls /etc/caddy/nwpu-forwarder-ip/fullchain.pem" in final
    assert "header_up X-Forwarded-Prefix /mail" in final
    assert "header_up X-Forwarded-For {remote_host}" in final


def test_replace_ip_site_preserves_other_sites(builders):
    original = "example.invalid {\n    reverse_proxy localhost:9000\n}\n"
    final = original + builders["render_ip_site"]("192.0.2.10", "/mail", True)
    assert builders["remove_ip_site"](final) == original
    assert builders["remove_ip_site"](original) == original


def test_legacy_redirect_preserves_website(builders):
    original = "example.invalid {\n    handle /other/* {\n        reverse_proxy localhost:9000\n    }\n}\n"
    updated = builders["legacy_redirect"](
        original, "https://example.invalid/mail", "192.0.2.10"
    )
    assert original.split("\n", 1)[1] in updated
    assert "@nwpu_mail_legacy path /mail /mail/*" in updated
    assert "https://192.0.2.10{uri} 308" in updated
    assert builders["legacy_redirect"](original, "", "192.0.2.10") == original
    with pytest.raises(ValueError):
        builders["legacy_redirect"](
            original, "https://missing.invalid/mail", "192.0.2.10"
        )


def test_default_ip_sni_preserves_global_settings(builders):
    builder = builders["default_ip_sni"]
    original = (
        "# comment\n{\n    email admin@example.invalid\n}\nexample.invalid {\n}\n"
    )
    updated = builder(original, "192.0.2.10")
    assert updated.count("default_sni 192.0.2.10") == 1
    assert "email admin@example.invalid" in updated
    assert builder(updated, "192.0.2.10") == updated
    plain = builder("example.invalid {\n}\n", "192.0.2.10")
    assert plain.startswith("# BEGIN NWPU IP GLOBAL")
    with pytest.raises(ValueError):
        builder(updated, "192.0.2.11")


def test_disable_removes_ip_and_legacy_routes_only(builders):
    source = Path(__file__).resolve().parents[1] / "deploy" / "expose-web.py"
    module = ast.parse(source.read_text(encoding="utf-8"))
    function = next(
        n
        for n in module.body
        if isinstance(n, ast.FunctionDef) and n.name == "remove_public_routes"
    )
    namespace = {"re": re}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )  # noqa: S102
    original = "example.invalid {\n    reverse_proxy localhost:9000\n}\n"
    panel = builders["legacy_redirect"](
        original, "https://example.invalid/mail", "192.0.2.10"
    )
    panel += builders["render_ip_site"]("192.0.2.10", "/mail", True)
    panel = builders["default_ip_sni"](panel, "192.0.2.10")
    assert namespace["remove_public_routes"](panel) == original
    global_original = "{\n    email admin@example.invalid\n}\n" + original
    panel = builders["default_ip_sni"](global_original, "192.0.2.10")
    assert namespace["remove_public_routes"](panel) == global_original
