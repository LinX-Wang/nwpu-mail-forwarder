from __future__ import annotations

import hmac
import hashlib
import secrets
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit

from flask import Flask, abort, redirect, render_template, request, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config, ConfigManager, Paths, credential
from .state import StateManager

LABELS = {
    "pending": "等待发送",
    "sending": "发送中",
    "retry": "等待重试",
    "failed": "失败",
    "sent": "成功",
    "duplicate": "已关联旧任务",
    "needs_review": "时间异常，待确认",
    "processing": "处理中",
    "done": "完成",
    "authentication_failed": "认证失败，请在终端更新凭据并重启服务",
    "configuration_required": "请完成配置和凭据设置",
    "network_timeout": "网络超时，将自动重试",
    "network_or_storage_failed": "网络或本地存储异常",
    "tls_failed": "TLS 证书或连接验证失败",
    "smtp_permanent": "SMTP 拒绝发送，请检查配置后重试",
    "smtp_temporary": "SMTP 暂时不可用，将自动重试",
    "recipient_rejected": "收件地址被拒绝",
    "delivery_uncertain": "发送结果不明，将使用同一任务 ID 重试",
    "size_limit_notification": "超过大小限制，已改为简短通知",
    "operation_failed": "操作失败，请查看脱敏日志",
    "interrupted": "测试被重启中断，请重新测试",
    "success": "成功",
    "test_imap": "学校连接测试",
    "test_smtp": "QQ SMTP 测试",
    "test_mail": "发送测试邮件",
}


def display_mail_text(value):
    """Presentation-only redaction; stored mail and notification content unchanged."""
    text = re.sub(
        r"(?:https?://|www\.)[^\s<>]+", "（链接已隐藏）", str(value or ""), flags=re.I
    )
    return re.sub(
        r"([A-Za-z0-9_.+%-])[A-Za-z0-9_.+%-]*@([A-Za-z0-9.-]+)", r"\1***@\2", text
    )


def create_app(paths=None, panel_password=None):
    paths = paths or Paths.environment()
    password = (
        panel_password if panel_password is not None else credential("panel_password")
    )
    configs = ConfigManager(paths.config)
    panel_username = configs.load().panel_username
    public_url = configs.load().public_url.rstrip("/")
    public = urlsplit(public_url)
    prefix = public.path if public_url else ""
    app = Flask(__name__)
    static_root = Path(app.static_folder)
    asset_versions = {
        path.relative_to(static_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        for path in static_root.rglob("*") if path.is_file()
    }

    def asset_url(filename):
        return url_for("static", filename=filename, v=asset_versions.get(filename, ""))

    app.jinja_env.globals["asset_url"] = asset_url
    app.jinja_env.filters["mailtext"] = display_mail_text
    app.config.update(
        SECRET_KEY=secrets.token_hex(32),
        MAX_CONTENT_LENGTH=16384,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=bool(public_url),
        SESSION_COOKIE_SAMESITE="Strict",
        SESSION_COOKIE_NAME="nwpu_panel",
        SESSION_COOKIE_PATH=prefix or "/",
        APPLICATION_ROOT=prefix or "/",
        TRUSTED_HOSTS=["127.0.0.1", "localhost"]
        + ([public.hostname] if public_url else []),
    )
    if public_url:
        original_wsgi = app.wsgi_app
        proxy_wsgi = ProxyFix(original_wsgi, x_for=1, x_proto=1, x_host=1, x_prefix=1)

        def trusted_proxy(environ, start_response):
            target = (
                proxy_wsgi
                if environ.get("REMOTE_ADDR") in {"127.0.0.1", "::1"}
                else original_wsgi
            )
            return target(environ, start_response)

        app.wsgi_app = trusted_proxy
    state = StateManager(paths.database)
    attempts = {}
    attempts_lock = threading.Lock()

    @app.template_filter("localtime")
    def localtime(value):
        return (
            datetime.fromtimestamp(value, ZoneInfo("Asia/Shanghai")).strftime(
                "%m-%d %H:%M:%S"
            )
            if value
            else "—"
        )

    @app.template_filter("label")
    def label(value):
        return LABELS.get(value, value or "—")

    @app.before_request
    def protect():
        if public_url and (
            not request.is_secure
            or request.host.split(":")[0] != public.hostname
            or request.script_root != prefix
        ):
            if request.method not in {"GET", "HEAD"}:
                abort(400)
            return redirect(public_url + request.path, code=308)
        if request.endpoint == "static":
            return None
        if session.get("authenticated") and time.time() - session.get("login_time", 0) > 3600:
            # Preserve CSRF so an already-open login form can still be submitted.
            session.pop("authenticated", None)
            session.pop("login_time", None)
        if "csrf" not in session:
            session["csrf"] = secrets.token_hex(32)
        if request.method == "POST":
            token = request.form.get("csrf", "")
            if not hmac.compare_digest(token, session["csrf"]):
                abort(400)

    @app.after_request
    def headers(response):
        filename = (request.view_args or {}).get("filename")
        versioned_asset = (
            request.endpoint == "static" and response.status_code in {200, 304}
            and filename in asset_versions
            and request.args.get("v") == asset_versions[filename]
        )
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if versioned_asset else "no-store"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'"
        )
        if public_url and request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.context_processor
    def access_mode():
        active_session = session.get("authenticated") and time.time() - session.get("login_time", 0) <= 3600
        return {"public_access": bool(public_url), "panel_account": panel_username if active_session else "",
                "panel_authenticated": bool(active_session and request.endpoint != "login")}

    def authenticated(fn):
        @wraps(fn)
        def inner(*args, **kwargs):
            if (
                not session.get("authenticated")
                or time.time() - session.get("login_time", 0) > 3600
            ):
                return redirect(url_for("login"))
            return fn(*args, **kwargs)

        return inner

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            peer = hashlib.sha256(
                (request.remote_addr or "unknown").encode()
            ).hexdigest()
            with attempts_lock:
                cutoff = time.time() - 300
                expired = [
                    key
                    for key, values in attempts.items()
                    if not values or values[-1] < cutoff
                ]
                for key in expired:
                    del attempts[key]
                if peer not in attempts and len(attempts) >= 2048:
                    return render_template(
                        "login.html", error="登录请求过多，请稍后重试"
                    ), 429
                recent = [t for t in attempts.get(peer, []) if time.time() - t < 300]
                if len(recent) >= 5:
                    return render_template(
                        "login.html", error="尝试次数过多，请 5 分钟后重试"
                    ), 429
                attempts[peer] = recent + [time.time()]
            username_ok = hmac.compare_digest(
                request.form.get("username", "").strip().encode(),
                panel_username.encode(),
            )
            password_ok = hmac.compare_digest(
                request.form.get("password", "").encode(), password.encode()
            )
            if panel_username and username_ok and password_ok:
                session.clear()
                session.update(
                    authenticated=True,
                    login_time=time.time(),
                    csrf=secrets.token_hex(32),
                )
                return redirect(url_for("dashboard"))
            error = "学号或登录密码不正确"
        return render_template("login.html", error=error)

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @authenticated
    def dashboard():
        snapshot = state.snapshot()
        snapshot["alive"] = time.time() - snapshot["state"].get("heartbeat", 0) < 90
        return render_template("dashboard.html", **snapshot)

    @app.route("/settings", methods=["GET", "POST"])
    @authenticated
    def settings():
        config = configs.load()
        message = None
        if request.method == "POST":
            try:
                fields = asdict(config)
                for key in (
                    "school_address",
                    "imap_host",
                    "qq_address",
                    "smtp_host",
                    "mode",
                ):
                    fields[key] = request.form.get(key, "").strip()
                for key in (
                    "imap_port",
                    "smtp_port",
                    "poll_seconds",
                    "max_message_mib",
                ):
                    fields[key] = int(request.form.get(key, ""))
                for key in ("consent", "full_consent", "attachments"):
                    fields[key] = request.form.get(key) == "on"
                config = Config(**fields)
                config.validate()
                # Require pause before changing routing, preventing in-flight ambiguity.
                if state.get("running", False) or state.get("sending_active", False):
                    raise ValueError("请先停止监听并等待当前发送完成，再修改设置")
                configs.save(config)
                message = "配置已保存；需要时在主页启动监听"
            except (ValueError, TypeError):
                message = "保存失败：请确认邮箱、端口、间隔、转发许可有效，并先停止监听"
        return render_template("settings.html", config=config, message=message)

    @app.post("/action")
    @authenticated
    def action():
        value = request.form.get("action")
        if value == "start":
            try:
                configs.load().validate(ready=True)
            except ValueError:
                return redirect(url_for("settings"))
            state.set("running", True)
        elif value == "stop":
            state.set("running", False)
        elif value == "retry":
            state.retry(request.form.get("task_id", ""))
        elif value in {"test_imap", "test_smtp", "test_mail"}:
            state.command(value)
        else:
            abort(400)
        return redirect(url_for("dashboard"))

    @app.get("/logs")
    @authenticated
    def logs():
        path = paths.logs / "runtime.log"
        text = "暂无日志"
        if path.exists():
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 32768))
                text = stream.read().decode("utf-8", "replace")
        return render_template("logs.html", text=text)

    @app.get("/api/status")
    @authenticated
    def api_status():
        return state.snapshot()

    @app.errorhandler(500)
    def internal_error(_):
        return "本地服务暂时不可用，请检查磁盘空间及服务状态。", 500

    return app
