import concurrent.futures
import json
import smtplib
import ssl
from dataclasses import replace
from datetime import datetime, timezone
from email.message import EmailMessage
import pytest
from imapclient.exceptions import LoginError
from nwpu_forwarder.config import ConfigManager, credential
from nwpu_forwarder.errors import backoff, error_code
from nwpu_forwarder.mail import MailForwarder, MailParser
from nwpu_forwarder.receiver import MailReceiver
from nwpu_forwarder.worker import instance_lock
from conftest import enqueue


@pytest.mark.parametrize(
    "changes",
    [
        dict(imap_port=0),
        dict(smtp_port=65536),
        dict(poll_seconds=1),
        dict(max_message_mib=0),
        dict(mode="full"),
        dict(imap_host="host\nsecret"),
        dict(school_address="bad"),
        dict(consent="yes"),
        dict(version=2),
    ],
)
def test_invalid_config(config, changes):
    with pytest.raises(ValueError):
        replace(config, **changes).validate()


def test_config_has_no_credentials(paths, config):
    manager = ConfigManager(paths.config)
    manager.save(config)
    assert manager.load() == config
    assert not any("password" in key for key in json.loads(paths.config.read_text()))


def test_credentials_only_from_systemd(tmp_path, monkeypatch):
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(RuntimeError):
        credential("school_password")
    (tmp_path / "school_password").write_text("synthetic-password")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(tmp_path))
    assert credential("school_password") == "synthetic-password"


def test_notification(config, task):
    msg = MailForwarder(config, "not-real").build(task)
    assert msg["From"] == config.qq_address
    assert msg["Subject"].startswith("[西工大] 中文")
    assert "教师" in msg.get_content() and task["id"] in msg.get_content()
    assert not list(msg.iter_attachments())
    assert "attachment-content" not in msg.get_content()
    assert (
        msg["Message-ID"] == MailForwarder(config, "not-real").build(task)["Message-ID"]
    )


def test_full_mime_and_chinese_filename(config, task):
    msg = MailForwarder(
        replace(config, mode="full", full_consent=True), "not-real"
    ).build(task)
    reparsed = MailParser.parse(msg.as_bytes())
    attachments = [p for p in reparsed.walk() if p.get_filename()]
    assert attachments[0].get_filename() == "中文附件 ✓.txt"
    assert attachments[0].get_payload(decode=True) == b"attachment-content"
    assert any(p.get_content_type() == "text/html" for p in reparsed.walk())
    assert reparsed["From"] == config.qq_address


def test_disable_attachments(config, task):
    msg = MailForwarder(
        replace(config, mode="full", full_consent=True, attachments=False), "x"
    ).build(task)
    assert not any(p.get_filename() for p in msg.walk())


def test_html_preview_does_not_include_script():
    msg = EmailMessage()
    msg.set_content(
        "<p>你好</p><script>secret()</script><style>hidden</style>", subtype="html"
    )
    assert MailParser.preview(msg.as_bytes()) == "你好"


def test_unknown_charset_and_preview_limit():
    raw = b"Content-Type: text/plain; charset=unknown-xyz\r\n\r\n" + b"a" * 1000
    assert len(MailParser.preview(raw)) == 300


def test_same_uid_and_duplicate_message_id(state, task):
    state.baseline("source", 1, 10)
    first = enqueue(state, task)
    assert enqueue(state, task) == first
    second = enqueue(state, task, uid=12, fingerprint="different-content")
    assert first != second and state.snapshot()["stats"]["total"] == 2


def test_generation_content_dedup(state, task):
    state.baseline("source", 1, 10)
    original = enqueue(state, task)
    state.sent(original)
    state.new_generation("source", 2)
    enqueue(state, task, validity=2, uid=1)
    assert state.snapshot()["tasks"][0]["status"] == "duplicate"
    assert state.snapshot()["stats"]["sent"] == 1


def test_single_atomic_claim(state, task):
    state.baseline("source", 1, 10)
    enqueue(state, task)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: state.claim("source"), range(8)))
    assert sum(r is not None for r in results) == 1


def test_crash_recovery_retains_task_id(state, task):
    state.baseline("source", 1, 10)
    task_id = enqueue(state, task)
    assert state.claim("source")["id"] == task_id
    state.recover()
    assert state.snapshot()["tasks"][0]["status"] == "retry"
    assert state.snapshot()["tasks"][0]["error"] == "delivery_uncertain"
    state.retry(task_id)
    assert state.claim("source")["id"] == task_id
    state.sent(task_id)
    assert state.claim("source") is None
    with state.connect() as db:
        assert db.execute("SELECT payload FROM tasks").fetchone()[0] is None


def test_retry_schedule(state, task):
    state.baseline("source", 1, 10)
    task_id = enqueue(state, task)
    state.failure(task_id, "network_timeout", 1800)
    assert state.claim("source") is None
    assert state.retry(task_id) and state.claim("source")
    state.failure(task_id, "smtp_permanent", 0, True)
    assert state.claim("source") is None


def test_instance_lock(paths):
    with instance_lock(paths.data / "worker.lock"):
        with pytest.raises(RuntimeError):
            with instance_lock(paths.data / "worker.lock"):
                pass


@pytest.mark.parametrize(
    "exc,code",
    [
        (LoginError("synthetic-secret"), "authentication_failed"),
        (
            smtplib.SMTPAuthenticationError(535, b"synthetic-secret"),
            "authentication_failed",
        ),
        (TimeoutError("synthetic-secret"), "network_timeout"),
        (ssl.SSLError("synthetic-secret"), "tls_failed"),
        (smtplib.SMTPDataError(451, b"synthetic-secret"), "smtp_temporary"),
        (smtplib.SMTPDataError(550, b"synthetic-secret"), "smtp_permanent"),
    ],
)
def test_errors_never_echo_server_reply(exc, code):
    assert error_code(exc) == code and "synthetic-secret" not in error_code(exc)


def test_exponential_backoff():
    assert 24 <= backoff(1) <= 36 and 48 <= backoff(2) <= 72 and backoff(100) <= 1800


class FakeIMAP:
    validity = 1

    def __init__(self, mails=None):
        self.mails = mails or {}
        self.commands = []

    def select_folder(self, folder, readonly):
        assert folder == "INBOX" and readonly is True
        return {b"UIDVALIDITY": self.validity}

    def search(self, criteria):
        self.commands.append(criteria)
        if criteria == ["ALL"]:
            return list(self.mails)
        lower = int(criteria[1].split(":")[0])
        return [k for k in self.mails if k >= lower]

    def fetch(self, uids, fields):
        self.commands.append(fields)
        uid = uids[0]
        raw, date = self.mails[uid]
        if "RFC822.SIZE" in fields:
            return {uid: {b"RFC822.SIZE": len(raw), b"INTERNALDATE": date}}
        assert fields[0].startswith("BODY.PEEK")
        return {uid: {b"BODY[]": raw[:65536] if "<0.65536>" in fields[0] else raw}}


def test_first_run_then_read_mail_and_multiple(state, config, raw_mail):
    server = FakeIMAP({1: (raw_mail, datetime.now(timezone.utc))})
    rx = MailReceiver(config, "x", state)
    rx.client = server
    rx.scan()
    assert state.snapshot()["stats"]["total"] == 0
    server.mails.update(
        {
            2: (raw_mail, datetime.now(timezone.utc)),
            3: (raw_mail, datetime.now(timezone.utc)),
        }
    )
    rx.scan()
    rx.scan()
    assert (
        state.snapshot()["stats"]["total"] == 2
        and state.account(config.account_key)["last_uid"] == 3
    )


def test_uid_reset_does_not_replay_success(state, config, raw_mail):
    server = FakeIMAP()
    rx = MailReceiver(config, "x", state)
    rx.client = server
    rx.scan()
    date = datetime.now(timezone.utc)
    server.mails = {20: (raw_mail, date)}
    rx.scan()
    state.sent(state.snapshot()["tasks"][0]["id"])
    server.validity = 2
    server.mails = {1: (raw_mail, date)}
    rx.scan()
    assert state.snapshot()["tasks"][0]["status"] == "duplicate"


def test_enqueue_failure_does_not_advance(state, config, raw_mail, monkeypatch):
    server = FakeIMAP()
    rx = MailReceiver(config, "x", state)
    rx.client = server
    rx.scan()
    server.mails = {1: (raw_mail, datetime.now(timezone.utc))}

    def fail(**kwargs):
        raise OSError("disk full")

    original = state.enqueue
    monkeypatch.setattr(state, "enqueue", fail)
    with pytest.raises(OSError):
        rx.scan()
    assert state.account(config.account_key)["last_uid"] == 0
    monkeypatch.setattr(state, "enqueue", original)
    rx.scan()
    assert state.snapshot()["stats"]["total"] == 1


def test_oversize_uses_partial_peek(state, config):
    server = FakeIMAP()
    rx = MailReceiver(replace(config, max_message_mib=1), "x", state)
    rx.client = server
    rx.scan()
    server.mails = {
        1: (b"Subject: huge\r\n\r\n" + b"x" * 1100000, datetime.now(timezone.utc))
    }
    rx.scan()
    task = state.claim(config.account_key)
    assert task["metadata"]["truncated"] and len(task["payload"]) == 65536
    assert ["BODY.PEEK[]<0.65536>"] in server.commands


class FakeSMTP:
    def __init__(self, *args, **kwargs):
        assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
        self.esmtp_features = {}
        self.messages = []

    def login(self, *args):
        pass

    def ehlo_or_helo_if_needed(self):
        pass

    def send_message(self, msg, **kwargs):
        self.messages.append(msg)
        return {}

    def quit(self):
        raise smtplib.SMTPServerDisconnected("lost after DATA accepted")

    def close(self):
        pass


def test_quit_failure_is_not_send_failure(config, task):
    smtp = FakeSMTP(context=ssl.create_default_context())
    f = MailForwarder(config, "synthetic", smtp_factory=lambda *a, **k: smtp)
    assert f.send(task) == "" and len(smtp.messages) == 1


def test_final_encoded_size_falls_back_to_notification(config, task):
    raw = EmailMessage()
    raw["Subject"] = "huge"
    raw.set_content("body")
    raw.add_attachment(
        b"x" * 1100000,
        maintype="application",
        subtype="octet-stream",
        filename="large.bin",
    )
    task["payload"] = raw.as_bytes()
    smtp = FakeSMTP(context=ssl.create_default_context())
    f = MailForwarder(
        replace(config, mode="full", full_consent=True, max_message_mib=1),
        "x",
        smtp_factory=lambda *a, **k: smtp,
    )
    assert (
        f.send(task) == "size_limit_notification"
        and not smtp.messages[0].is_multipart()
    )
