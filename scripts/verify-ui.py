"""Exercise the actual Flask UI in an isolated, synthetic runtime; never send mail.

Install .[ui-test] then run this script. Chrome may be supplied with --browser.
Screenshots contain synthetic data only and are stored under ignored runtime/.
"""

import argparse
import json
import logging
import tempfile
import threading
import time
from email.message import EmailMessage
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from nwpu_forwarder.config import Config, ConfigManager, Paths
from nwpu_forwarder.state import StateManager
from nwpu_forwarder.web import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", help="Optional Chrome/Chromium executable")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "runtime" / "ui-screenshots"
    output.mkdir(parents=True, exist_ok=True)
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    with tempfile.TemporaryDirectory(
        prefix="ui-check-", dir=root / "runtime"
    ) as directory:
        path = Path(directory)
        paths = Paths(path / "config.json", path / "data", path / "logs")
        config = Config(
            panel_username="2026000000",
            school_address="student@mail.example.invalid",
            qq_address="personal@example.invalid",
            consent=True,
        )
        ConfigManager(paths.config).save(config)
        state = StateManager(paths.database)
        now = time.time()
        for key, value in {
            "running": True,
            "imap_connected": True,
            "listener_type": "poll",
            "last_check": now,
            "last_forward": now - 30,
            "last_smtp_ok": now - 30,
        }.items():
            state.set(key, value)
        for i, status in enumerate(["sent", "retry", "failed"]):
            mail = EmailMessage()
            mail["Subject"] = ["课程安排更新通知", "学术报告邀请", "校园服务通知"][i]
            mail["From"] = "教务通知 <teacher@example.invalid>"
            mail.set_content(
                "这是一封用于界面验证的模拟邮件。请查看最新安排，按通知要求完成相关事项。 https://example.invalid/verify?token=synthetic-secret"
            )
            mail.add_attachment(
                b"synthetic attachment",
                maintype="application",
                subtype="octet-stream",
                filename="课程安排.pdf",
            )
            task_id = state.enqueue(
                account=config.account_key,
                validity=1,
                uid=101 + i,
                fingerprint=str(i),
                message_id=f"<{i}@example.invalid>",
                received=now,
                payload=mail.as_bytes(),
                metadata={},
            )
            with state.connect() as db:
                db.execute(
                    "UPDATE tasks SET status=?,attempts=?,completed=?,error=?,next_attempt=? WHERE id=?",
                    (
                        status,
                        1 + i,
                        now - 30 if status == "sent" else None,
                        "network_timeout"
                        if status == "retry"
                        else "recipient_rejected"
                        if status == "failed"
                        else None,
                        now + 90,
                        task_id,
                    ),
                )
        for action in ["test_imap", "test_smtp"]:
            command_id = state.command(action)
            state.finish_command(command_id, "success", True)
            with state.connect() as db:
                db.execute(
                    "UPDATE commands SET created=? WHERE id=?", (now - 300, command_id)
                )
        paths.logs.mkdir(parents=True)
        log_path = paths.logs / "runtime.log"
        log_path.write_text(
            "2026-11-16 15:00:00,123 INFO task=synthetic-check sent\n2026-11-16 15:01:00,456 WARNING IMAP network_timeout\n2026-11-16 15:02:00,789 ERROR task=synthetic-failure recipient_rejected\n",
            encoding="utf-8",
        )
        app = create_app(paths, "local-ui-test-password")
        server = make_server("127.0.0.1", 0, app, threaded=True)
        base = f"http://127.0.0.1:{server.server_port}"
        stopped = threading.Event()

        def heartbeat():
            while not stopped.is_set():
                state.set("heartbeat", time.time())
                stopped.wait(1)

        thread = threading.Thread(target=server.serve_forever, daemon=True)
        beat = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        beat.start()
        errors, checks = [], []
        try:
            with sync_playwright() as playwright:
                options = {"headless": True}
                if args.browser:
                    options["executable_path"] = args.browser
                browser = playwright.chromium.launch(**options)
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    permissions=["clipboard-read", "clipboard-write"],
                )
                page = context.new_page()
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.on(
                    "console",
                    lambda message: (
                        errors.append(message.text) if message.type == "error" else None
                    ),
                )
                page.goto(base + "/login")
                page.evaluate("window.NWPUTheme.setMode('light')")
                page.evaluate("document.fonts.ready")
                assert page.locator(".auth-layout").bounding_box()["width"] > 1200
                page.screenshot(path=str(output / "login-desktop.png"), full_page=True)
                page.get_by_label("账号（学号）", exact=True).fill("2026000000")
                page.get_by_label("登录密码", exact=True).fill("local-ui-test-password")
                page.get_by_role("button", name="进入私有面板").click()
                expect(
                    page.get_by_role("heading", name="运行概览", exact=True)
                ).to_be_visible()
                expect(page.locator("[data-service-badge]")).to_have_text("服务在线")
                expect(page.locator(".task-preview").first).to_contain_text(
                    "用于界面验证的模拟邮件"
                )
                assert "<script>alert" not in page.content()
                page.evaluate("document.fonts.ready")
                for font in ["Inter", "Noto Sans SC", "LinX JetBrains Mono"]:
                    assert page.evaluate(
                        "name => [...document.fonts].some(f => f.family.replaceAll(\"'\", '').replaceAll('\"', '') === name && f.status === 'loaded')",
                        font,
                    ), font
                checks.append(
                    "Inter, Noto Sans SC and JetBrains Mono actually load from local resources"
                )
                assert "synthetic-secret" not in page.content()
                expect(page.locator(".task-preview").first).not_to_be_visible()
                page.locator(".task-trigger").first.click()
                expect(page.locator(".task-preview").first).to_be_visible()
                expect(page.locator(".task-attachments").first).to_contain_text(
                    "课程安排.pdf"
                )
                with state.connect() as db:
                    db.execute("UPDATE tasks SET attempts=attempts+1")
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(page.locator(".task-row").first).to_have_attribute("open", "")
                page.locator(".task-trigger").first.click()
                checks.append(
                    "private task details, hidden links, attachment names and refresh preservation"
                )
                page.reload()
                expect(page.locator('body')).not_to_have_class('is-loading')
                page.evaluate('document.fonts.ready')
                entries=page.evaluate(r"performance.getEntriesByType('resource').filter(e=>/static\/(app\.css|tokens\.css|components\.css|fonts\.css|theme\.js|app\.js|fonts\/.*\.woff2)/.test(e.name)).map(e=>({name:e.name,bytes:e.transferSize}))")
                assert len(entries)>=4 and all(e['bytes']==0 for e in entries),entries
                checks.append('warm page navigation reuses fingerprinted CSS, scripts and fonts without network transfer')
                checks.append("login and real snapshot render")
                for width, height, name in [
                    (1920, 1080, "desktop"),
                    (1440, 900, "medium-desktop"),
                    (1366, 768, "laptop"),
                    (390, 844, "mobile"),
                    (320, 740, "small-mobile"),
                ]:
                    page.set_viewport_size({"width": width, "height": height})
                    for route, stem, title in [
                        ("/", "overview", "运行概览"),
                        ("/settings", "mailboxes", "邮箱配置"),
                        ("/settings?section=forwarding", "forwarding", "转发设置"),
                        ("/logs", "logs", "运行日志"),
                    ]:
                        page.goto(base + route)
                        expect(
                            page.get_by_role("heading", name=title, exact=True)
                        ).to_be_visible()
                        expect(page.locator("body")).not_to_have_class("is-loading")
                        page.evaluate("document.fonts.ready")
                        assert page.locator(
                            ".field-help, .task-meta, .log-description, .button"
                        ).evaluate_all(
                            "els => els.every(el => parseFloat(getComputedStyle(el).fontSize) >= 12)"
                        ), (name, route, "small type")
                        assert page.locator(
                            ".panel input:not([type=checkbox]), .panel select"
                        ).evaluate_all(
                            "els => els.filter(el => el.getBoundingClientRect().width).every(el => el.getBoundingClientRect().right <= el.closest('.panel').getBoundingClientRect().right)"
                        ), (name, route, "input outside panel")
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        ), (name, route, "horizontal overflow")
                        if width > 760:
                            expect(page.locator(".sidebar")).to_be_visible()
                            assert (
                                abs(
                                    page.locator(".content").bounding_box()["width"]
                                    - (width - 220)
                                )
                                < 2
                            )
                            if width >= 1100:
                                assert page.evaluate(
                                    "document.documentElement.scrollHeight <= window.innerHeight"
                                ), (name, route, "page must fit viewport")
                                assert (
                                    page.locator(".page-footer").bounding_box()["y"]
                                    + page.locator(".page-footer").bounding_box()[
                                        "height"
                                    ]
                                    <= height + 1
                                )
                                if route.startswith("/settings"):
                                    expect(
                                        page.get_by_role("button", name="保存转发设置" if "forwarding" in route else "保存邮箱配置")
                                    ).to_be_visible()
                        else:
                            page.get_by_role("button", name="打开导航").click()
                            expect(page.locator("body")).to_have_class("nav-open")
                            page.keyboard.press("Escape")
                            expect(page.locator("body")).not_to_have_class("nav-open")
                        page.screenshot(
                            path=str(output / f"{stem}-{name}.png"), full_page=True
                        )
                        checks.append(f"{name} {route}: responsive and navigation")
                for width,height in [(1920,1080),(1440,900),(1366,768)]:
                    page.set_viewport_size({'width':width,'height':height})
                    positions=[]
                    for route in ['/','/settings','/settings?section=forwarding','/logs']:
                        page.goto(base+route)
                        expect(page.locator('body')).not_to_have_class('is-loading')
                        footer=page.locator('.page-footer').bounding_box();positions.append(footer['y'])
                        assert abs(footer['y']+footer['height']-height)<2
                        assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
                    assert max(positions)-min(positions)<2
                page.goto(base+'/settings')
                for guide in ['school-password-tutorial','qq-code-tutorial']:
                    expect(page.locator('#'+guide)).not_to_have_attribute('open','')
                    page.locator('#'+guide+' > summary').click()
                    expect(page.locator('#'+guide+' .guide-steps li')).to_have_count(4)
                    assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
                    page.locator('#'+guide).evaluate("el=>el.closest('.form-body').scrollTop=el.offsetTop")
                page.screenshot(path=str(output/'mailbox-tutorials-light.png'),full_page=True)
                page.evaluate("window.NWPUTheme.setMode('dark')")
                page.wait_for_timeout(220)
                page.screenshot(path=str(output/'mailbox-tutorials-dark.png'),full_page=True)
                page.evaluate("window.NWPUTheme.setMode('light')")
                page.set_viewport_size({'width':390,'height':844})
                page.goto(base+'/settings')
                page.locator('#school-password-tutorial > summary').click()
                page.locator('#qq-code-tutorial > summary').click()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path=str(output/'mailbox-tutorials-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1920,'height':1080})
                page.goto(base+'/settings')
                page.locator('#school-password-tutorial > summary').click()
                page.locator('#qq-code-tutorial > summary').click()
                for guide in ['school-password-tutorial','qq-code-tutorial']:
                    page.locator('#'+guide).evaluate("el=>{const b=el.closest('.form-body');b.scrollTop=el.getBoundingClientRect().top-b.getBoundingClientRect().top+b.scrollTop}")
                page.screenshot(path=str(output/'mailbox-tutorials-light.png'),full_page=True)
                page.evaluate("window.NWPUTheme.setMode('dark')")
                page.wait_for_timeout(220)
                page.screenshot(path=str(output/'mailbox-tutorials-dark.png'),full_page=True)
                page.evaluate("window.NWPUTheme.setMode('light')")
                checks.append('four fixed pages share footer baseline; official tutorials expand without outer scrolling')
                expect(page.locator('[data-panel-account]')).to_have_text('2026000000')
                for theme in ['dark', 'light']:
                    for width, height, size in [(1920,1080,'desktop'),(1440,900,'medium-desktop'),(1366,768,'laptop'),(390,844,'mobile'),(320,740,'small-mobile')]:
                        page.set_viewport_size({'width':width,'height':height})
                        for route, stem in [('/','overview'),('/settings','mailboxes'),('/settings?section=forwarding','forwarding'),('/logs','logs')]:
                            page.goto(base+route)
                            page.evaluate("mode => window.NWPUTheme.setMode(mode)",theme)
                            page.wait_for_timeout(220)
                            expect(page.locator('html')).to_have_attribute('data-theme',theme)
                            expect(page.locator('body')).not_to_have_class('is-loading')
                            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),(theme,size,route)
                            if route=='/settings?section=forwarding' and width>=1440:
                                layout=page.locator('.forwarding-layout').bounding_box()
                                bar=page.locator('.save-bar').bounding_box()
                                assert abs(layout['y']+layout['height']+12-bar['y'])<2
                                a=page.locator('.forwarding-content').bounding_box();b=page.locator('.forwarding-recovery').bounding_box()
                                assert abs(a['height']-b['height'])<2
                            selection=page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--selection').trim()")
                            assert page.locator('.nav-item.active').evaluate("(el,color)=>{const probe=document.createElement('span');probe.style.color=color;document.body.append(probe);const same=getComputedStyle(el).color===getComputedStyle(probe).color;probe.remove();return same}",selection)
                            if route=='/settings':
                                field=page.locator('.settings-grid input[type=email]').first
                                assert field.evaluate("el=>getComputedStyle(el).backgroundColor!==getComputedStyle(el.closest('.panel')).backgroundColor")
                            if route=='/logs':
                                assert page.locator('.log-stat').evaluate_all("els=>els.every(el=>{const s=getComputedStyle(el);return s.textAlign==='center' && s.alignItems==='center' && s.justifyContent==='center'})")
                            if route=='/':
                                assert page.locator('.kpi-card').evaluate_all("els=>els.every(el=>{const s=getComputedStyle(el);return s.textAlign==='center' && s.alignItems==='center' && s.justifyContent==='center'})")
                                if width>=1100:
                                    a=page.locator('.activity-panel').bounding_box();b=page.locator('.tasks-panel').bounding_box()
                                    assert abs(a['y']-b['y'])<2 and abs(a['height']-b['height'])<2
                            if width>=1100 and route in ['/','/settings','/settings?section=forwarding','/logs']:
                                assert page.evaluate('document.documentElement.scrollHeight <= innerHeight')
                            if width==1920:
                                page.screenshot(path=str(output/f'{stem}-{theme}.png'),full_page=True)
                    page.reload()
                    expect(page.locator('html')).to_have_attribute('data-theme',theme)
                    checks.append(f'{theme}: all pages in five sizes; persistent theme, centered KPI and matched panels')
                page.get_by_role('button',name='跟随时间',exact=True).click()
                assert page.evaluate("window.NWPUTheme.getMode()")=='auto'
                assert page.evaluate("window.NWPUTheme.resolve('auto',6)")=='dark'
                assert page.evaluate("window.NWPUTheme.resolve('auto',12)")=='light'
                page.evaluate("window.NWPUTheme.setMode('light')")
                checks.append('account display and day/night/time theme controls')
                page.set_viewport_size({"width": 1366, "height": 768})
                page.goto(base + "/")
                pending_action=[]
                page.route('**/action',lambda route:pending_action.append(route) if route.request.method=='POST' else route.continue_())
                control=page.locator('[data-listener-control] button')
                before=control.bounding_box()
                control.click()
                expect(control).to_have_attribute('aria-busy','true')
                during=control.bounding_box()
                assert abs(before['width']-during['width'])<1 and abs(before['height']-during['height'])<1
                assert pending_action
                pending_action.pop().continue_()
                page.unroute('**/action')
                expect(control).not_to_have_attribute('aria-busy','true')
                expect(page.get_by_role('button',name='启动监听',exact=True)).to_be_visible()
                page.get_by_role('button',name='启动监听',exact=True).click()
                expect(page.get_by_role('button',name='停止监听',exact=True)).to_be_visible()
                checks.append('operation loading preserves button width and height without duplicate submission')
                page.get_by_role("button", name="停止监听", exact=True).click()
                expect(
                    page.get_by_role("button", name="启动监听", exact=True)
                ).to_be_visible()
                assert not state.get("running")
                primary = page.get_by_role("button", name="启动监听", exact=True)
                page.mouse.move(0, 0)
                page.wait_for_timeout(220)
                assert (
                    primary.evaluate("el => getComputedStyle(el).backgroundColor")
                    == "rgb(8, 127, 115)"
                )
                primary.hover()
                page.wait_for_timeout(220)
                assert (
                    primary.evaluate("el => getComputedStyle(el).backgroundColor")
                    == "rgb(6, 105, 95)"
                )
                page.mouse.move(0, 0)
                checks.append(
                    "teal primary button hover and disabled/save loading states"
                )
                page.get_by_role("button", name="启动监听", exact=True).click()
                expect(
                    page.get_by_role("button", name="停止监听", exact=True)
                ).to_be_visible()
                assert state.get("running")
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/action")
                ):
                    page.get_by_role("button", name="测试学校连接", exact=True).click()
                assert any(
                    c["action"] == "test_imap" and c["status"] == "pending"
                    for c in state.snapshot()["commands"]
                )
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/action")
                ):
                    page.get_by_role("button", name="测试 QQ SMTP", exact=True).click()
                assert any(
                    c["action"] == "test_smtp" and c["status"] == "pending"
                    for c in state.snapshot()["commands"]
                )
                with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/action")
                ):
                    page.get_by_role("button", name="发送测试邮件", exact=True).click()
                assert any(
                    c["action"] == "test_mail" and c["status"] == "pending"
                    for c in state.snapshot()["commands"]
                )
                checks.append("start/stop and IMAP/SMTP/test-mail command submission")
                page.get_by_role("button", name="停止监听", exact=True).click()
                expect(
                    page.get_by_role("button", name="启动监听", exact=True)
                ).to_be_visible()
                page.goto(base + "/settings")
                expect(page.get_by_role("button", name="保存邮箱配置")).to_be_enabled()
                page.get_by_label("IMAP 服务器", exact=True).fill(
                    "imap.example.invalid"
                )
                expect(page.locator("[data-save-state]")).to_have_text("未保存更改")
                pending = []
                page.route(
                    "**/settings",
                    lambda route: (
                        pending.append(route)
                        if route.request.method == "POST"
                        else route.continue_()
                    ),
                )
                save_button=page.locator("#settings-form button[type=submit]")
                before_save=save_button.bounding_box()
                save_button.click()
                expect(page.locator("[data-save-state]")).to_have_text("正在保存")
                during_save=save_button.bounding_box()
                assert abs(before_save["width"]-during_save["width"])<1 and abs(before_save["height"]-during_save["height"])<1
                expect(
                    page.get_by_role("button", name="保存中", exact=True)
                ).to_be_disabled()
                assert pending
                pending.pop().fulfill(
                    status=200,
                    content_type="text/html",
                    body='<div data-save-result="failed">保存失败：模拟验证错误</div>',
                )
                expect(page.locator("[data-save-state]")).to_have_text(
                    "保存失败，更改已保留"
                )
                expect(page.get_by_label("IMAP 服务器", exact=True)).to_have_value(
                    "imap.example.invalid"
                )
                page.unroute("**/settings")
                page.get_by_role("button", name="保存邮箱配置").click()
                expect(
                    page.get_by_text(
                        "配置已保存；需要时在主页启动监听", exact=True
                    ).first
                ).to_be_visible()
                saved = ConfigManager(paths.config).load()
                assert (
                    saved.imap_host == "imap.example.invalid"
                    and saved.poll_seconds == config.poll_seconds
                    and saved.attachments == config.attachments
                    and saved.consent == config.consent
                )
                page.goto(base + "/settings?section=forwarding")
                page.get_by_label("轮询间隔", exact=True).fill("120")
                page.get_by_role("button", name="保存转发设置").click()
                expect(
                    page.get_by_text(
                        "配置已保存；需要时在主页启动监听", exact=True
                    ).first
                ).to_be_visible()
                saved = ConfigManager(paths.config).load()
                assert (
                    saved.poll_seconds == 120
                    and saved.imap_host == "imap.example.invalid"
                    and saved.qq_address == config.qq_address
                )
                checks.append("split views preserve all settings fields across POST")
                checks.append(
                    "dirty state, save loading and validation failure retain edits"
                )
                page.set_viewport_size({"width": 1920, "height": 1080})
                for route, stem in [
                    ("/settings", "mailboxes"),
                    ("/settings?section=forwarding", "forwarding"),
                ]:
                    page.goto(base + route)
                    expect(page.locator("body")).not_to_have_class("is-loading")
                    page.evaluate("document.fonts.ready")
                    expect(
                        page.locator("#settings-form button[type=submit]")
                    ).to_be_enabled()
                    page.screenshot(
                        path=str(output / f"{stem}-desktop.png"), full_page=True
                    )
                page.set_viewport_size({"width": 1366, "height": 768})
                page.goto(base + "/logs")
                expect(page.locator(".log-entry")).to_have_count(3)
                assert page.locator(".log-entries").bounding_box()["height"] < 768 * 0.6
                page.locator(".log-entry > summary").first.click()
                expect(page.locator(".log-full").first).to_be_visible()
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(page.locator(".log-entry").first).to_have_attribute("open", "")
                checks.append("single-screen log viewport and expandable row survives refresh")
                original_logs = log_path.read_text(encoding="utf-8")
                log_path.write_text(
                    "".join(
                        f"2026-11-16 15:00:00,123 INFO task=synthetic-{i} sent\n"
                        for i in range(80)
                    ),
                    encoding="utf-8",
                )
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(page.locator(".log-entry")).to_have_count(80)
                assert page.locator(".log-entries").evaluate(
                    "el => el.scrollHeight > el.clientHeight"
                )
                assert (
                    page.locator(".log-entries").bounding_box()["height"]
                    <= 768 * 0.6 + 2
                )
                log_path.write_text(original_logs, encoding="utf-8")
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(page.locator(".log-entry")).to_have_count(3)
                checks.append("long log list is bounded without expanding page width")
                page.get_by_label("搜索日志").fill("network_timeout")
                expect(page.locator(".log-entry")).to_have_count(1)
                page.get_by_role("button", name="复制日志").click()
                assert "network_timeout" in page.evaluate(
                    "navigator.clipboard.readText()"
                )
                page.get_by_label("搜索日志").fill("")
                page.get_by_label("筛选日志级别").select_option("ERROR")
                expect(page.locator(".log-entry")).to_have_count(1)
                page.get_by_role("button", name="清空视图").click()
                expect(page.locator("#confirm-dialog")).to_be_visible()
                page.get_by_role("button", name="取消", exact=True).click()
                expect(page.locator(".log-entry")).to_have_count(1)
                page.get_by_role("button", name="清空视图").click()
                page.get_by_role("button", name="确认", exact=True).click()
                expect(page.locator(".log-entry")).to_have_count(0)
                assert not page.get_by_label("自动刷新").is_checked()
                assert "network_timeout" in log_path.read_text(encoding="utf-8")
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(page.locator(".log-entry")).to_have_count(1)
                checks.append(
                    "log search, level filter, clipboard, confirmation and refresh preserve server logs"
                )
                page.goto(base + "/")
                with state.connect() as db:
                    db.execute("DELETE FROM tasks")
                    db.execute("DELETE FROM commands")
                page.get_by_role("button", name="刷新状态", exact=True).click()
                expect(
                    page.get_by_role("heading", name="等待第一封新邮件")
                ).to_be_visible()
                expect(page.get_by_role("heading", name="尚无活动记录")).to_be_visible()
                page.screenshot(path=str(output / "overview-empty.png"), full_page=True)
                checks.append("empty state renders without mock fallback data")
                assert not errors, errors
                browser.close()
        finally:
            stopped.set()
            server.shutdown()
            thread.join(timeout=5)
            beat.join(timeout=5)
        result = {
            "checks": checks,
            "console_errors": errors,
            "screenshots": str(output),
            "mail_sent": False,
        }
        (output / "verification.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
