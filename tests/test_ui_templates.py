from dataclasses import asdict
from html.parser import HTMLParser
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest

from nwpu_forwarder.config import ConfigManager
from nwpu_forwarder.web import create_app


class SettingsFields(HTMLParser):
    """Read the actual settings form, including its inactive navigation view."""

    def __init__(self):
        super().__init__()
        self.active = False
        self.select = None
        self.values = {}

    def handle_starttag(self, tag, attrs):
        attr = dict(attrs)
        if tag == "form":
            self.active = attr.get("id") == "settings-form"
        if not self.active:
            return
        name = attr.get("name")
        if tag == "input" and name:
            if attr.get("type") == "checkbox":
                if "checked" in attr:
                    self.values[name] = "on"
            else:
                self.values[name] = attr.get("value", "")
        if tag == "select":
            self.select = name
        if tag == "option" and "selected" in attr:
            self.values[self.select] = attr["value"]

    def handle_endtag(self, tag):
        if tag == "form":
            self.active = False
        if tag == "select":
            self.select = None


@pytest.fixture
def ui_client(paths, config):
    ConfigManager(paths.config).save(config)
    app = create_app(paths, "synthetic-ui-password")
    app.config["TESTING"] = True
    client = app.test_client()
    client.get("/login")
    with client.session_transaction() as session:
        csrf = session["csrf"]
    client.post(
        "/login",
        data={
            "csrf": csrf,
            "username": "2026000000",
            "password": "synthetic-ui-password",
        },
    )
    return client


@pytest.mark.parametrize(
    "route,title",
    [
        ("/", "运行概览"),
        ("/settings", "邮箱配置"),
        ("/settings?section=forwarding", "转发设置"),
        ("/logs", "运行日志"),
    ],
)
def test_all_console_pages_use_shared_navigation(ui_client, route, title):
    response = ui_client.get(route)
    assert response.status_code == 200
    text = response.data.decode()
    assert "<h1>" + title + "</h1>" in text
    assert 'class="sidebar"' in text and 'id="main-content"' in text
    assert 'href="/settings?section=forwarding"' in text
    assert "filename=" not in text
    assert "/static/app.js" in text and "/static/nwpu-emblem.svg" in text
    assert "/static/linx-lockup.svg" in text
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]


@pytest.mark.parametrize("route", ["/settings", "/settings?section=forwarding"])
def test_split_settings_form_round_trip_preserves_hidden_fields(
    ui_client, config, paths, route
):
    parsed = SettingsFields()
    parsed.feed(ui_client.get(route).data.decode())
    expected = {
        "school_address",
        "qq_address",
        "imap_host",
        "smtp_host",
        "imap_port",
        "smtp_port",
        "mode",
        "poll_seconds",
        "max_message_mib",
        "consent",
        "attachments",
        "csrf",
    }
    assert expected <= parsed.values.keys()
    response = ui_client.post(route, data=parsed.values)
    assert "配置已保存".encode() in response.data
    assert asdict(ConfigManager(paths.config).load()) == asdict(config)


def test_dashboard_keeps_conditional_control_and_private_tasks(ui_client, state):
    state.set("running", True)
    html = ui_client.get("/").data.decode()
    assert 'value="stop"' in html and 'value="start"' not in html
    state.set("running", False)
    html = ui_client.get("/").data.decode()
    assert 'value="start"' in html and 'value="stop"' not in html
    assert "等待第一封新邮件" in html and "尚无活动记录" in html
    assert set(ui_client.get("/api/status").json) == {
        "state",
        "stats",
        "tasks",
        "commands",
    }


@pytest.mark.parametrize(
    "asset", ["nwpu-emblem.svg", "linx-lockup.svg", "linx-symbol.svg", "favicon.svg"]
)
def test_brand_vectors_are_local_paths_without_active_content(ui_client, asset):
    response = ui_client.get("/static/" + asset)
    assert response.status_code == 200
    root = ET.fromstring(response.data)
    allowed = {"svg", "g", "path", "title"}
    assert root.get("viewBox")
    for element in root.iter():
        assert element.tag.split("}")[-1] in allowed
        for key, value in element.attrib.items():
            assert not key.lower().startswith("on")
            assert "href" not in key.lower() and "url(" not in value


def test_browser_script_is_bundled_and_no_inline_scripts(ui_client):
    assert ui_client.get("/static/app.js").status_code == 200
    root = Path(__file__).resolve().parents[1]
    assert '"static/*.js"' in (root / "pyproject.toml").read_text(encoding="utf-8")
    html = ui_client.get("/logs").data.decode()
    assert "<script src=" in html
    assert "<script>" not in html
    assert "服务器日志" in html
