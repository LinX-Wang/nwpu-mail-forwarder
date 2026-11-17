import json
from email.message import EmailMessage

from conftest import enqueue
from nwpu_forwarder.mail import MailParser
from nwpu_forwarder.receiver import MailReceiver
from nwpu_forwarder.web import display_mail_text
from test_core import FakeIMAP
from test_ui_templates import ui_client  # noqa: F401


def test_summary_survives_payload_cleanup(state, task):
    task_id = enqueue(state, task)
    state.sent(task_id)
    item = state.snapshot()["tasks"][0]
    assert item["subject"] == "中文通知：课程调整 ✓"
    assert item["preview"] == "课程通知包含特殊符号 ©。"
    assert item["sender"] == "教师"
    assert item["attachments"] == ["中文附件 ✓.txt"]
    assert "attachment-content" not in json.dumps(item)
    assert "payload" not in item and "metadata" not in item
    with state.connect() as db:
        assert db.execute("SELECT payload FROM tasks").fetchone()[0] is None


def test_summary_html_and_bounds():
    msg = EmailMessage()
    msg["Subject"] = "中文" * 200
    msg.set_content(
        "<p>你好 &amp; 欢迎</p><script>secret()</script><style>hidden</style>"
        + "文" * 500,
        subtype="html",
    )
    summary = MailParser.summary(msg.as_bytes())
    assert len(summary["subject"]) == 200
    assert len(summary["preview"]) == 300
    assert summary["preview"].startswith("你好 & 欢迎")
    assert "secret" not in summary["preview"] and "hidden" not in summary["preview"]


def test_presentation_redacts_links_and_addresses_without_altering_mail():
    text = "联系 personal@example.invalid https://example.invalid/verify?token=private www.example.invalid/reset/secret"
    shown = display_mail_text(text)
    assert "personal@" not in shown and "private" not in shown and "secret" not in shown
    assert "p***@example.invalid" in shown
    assert shown.count("（链接已隐藏）") == 2
    assert "token=private" in text


def test_sender_without_name_is_masked():
    msg = EmailMessage()
    msg["From"] = "personal@example.invalid"
    msg.set_content("正文")
    assert MailParser.summary(msg.as_bytes())["sender"] == "p***@example.invalid"


def test_authenticated_summary_is_escaped_and_not_cached(ui_client, state, task):
    msg = EmailMessage()
    msg["Subject"] = "<img src=x onerror=alert(1)>"
    msg.set_content("<script>alert(1)</script>")
    enqueue(state, task, payload=msg.as_bytes())
    response = ui_client.get("/")
    html = response.data.decode()
    assert "&lt;img src=x onerror=alert(1)&gt;" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<img src=x" not in html and "<script>alert" not in html
    assert response.headers["Cache-Control"] == "no-store"
    with ui_client.session_transaction() as session:
        session.clear()
    assert ui_client.get("/api/status").status_code == 302


def test_old_record_enrichment_changes_only_metadata(state, config, task, raw_mail):
    task_id = enqueue(
        state, task, account=config.account_key, metadata={"size": len(raw_mail)}
    )
    state.sent(task_id)
    with state.connect() as db:
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps({"size": len(raw_mail)}), task_id),
        )
        before = dict(db.execute("SELECT * FROM tasks").fetchone())
    assert state.set_mail_summary(task_id, raw_mail)
    with state.connect() as db:
        after = dict(db.execute("SELECT * FROM tasks").fetchone())
    assert {k: v for k, v in before.items() if k != "metadata"} == {
        k: v for k, v in after.items() if k != "metadata"
    }
    assert json.loads(after["metadata"])["size"] == len(raw_mail)
    assert not state.set_mail_summary("missing", raw_mail)


def test_backfill_reads_only_existing_tasks_and_checks_identity(
    state, config, raw_mail
):
    from datetime import datetime, timezone

    server = FakeIMAP()
    rx = MailReceiver(config, "synthetic", state)
    rx.client = server
    rx.scan()
    date = datetime.now(timezone.utc)
    server.mails = {1: (raw_mail, date)}
    rx.scan()
    task_id = state.snapshot()["tasks"][0]["id"]
    state.sent(task_id)
    with state.connect() as db:
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps({"size": len(raw_mail)}), task_id),
        )
    before = state.account(config.account_key)
    assert rx.refresh_task_summaries() == {"updated": 1, "unavailable": 0}
    assert state.account(config.account_key) == before
    assert state.snapshot()["stats"]["sent"] == 1
    assert state.snapshot()["tasks"][0]["preview"] == "课程通知包含特殊符号 ©。"
    assert ["BODY.PEEK[]<0.65536>"] in server.commands
    with state.connect() as db:
        db.execute(
            "UPDATE tasks SET metadata=? WHERE id=?",
            (json.dumps({"size": len(raw_mail)}), task_id),
        )
        db.execute("UPDATE tasks SET message_id='wrong' WHERE id=?", (task_id,))
    assert rx.refresh_task_summaries() == {"updated": 0, "unavailable": 1}
    server.validity += 1
    assert rx.refresh_task_summaries() == {"updated": 0, "unavailable": 0}
