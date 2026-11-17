import time
from email.message import EmailMessage
import pytest
from nwpu_forwarder.config import Config, Paths
from nwpu_forwarder.state import StateManager


@pytest.fixture
def paths(tmp_path):
    return Paths(
        tmp_path / "settings" / "config.json", tmp_path / "data", tmp_path / "logs"
    )


@pytest.fixture
def config():
    return Config(
        panel_username="2026000000",
        school_address="student@mail.example.invalid",
        qq_address="personal@example.invalid",
        consent=True,
    )


@pytest.fixture
def state(paths):
    return StateManager(paths.database)


@pytest.fixture
def raw_mail():
    msg = EmailMessage()
    msg["From"] = "教师 <teacher@example.invalid>"
    msg["To"] = "student@mail.example.invalid"
    msg["Subject"] = "中文通知：课程调整 ✓"
    msg["Message-ID"] = "<one@example.invalid>"
    msg.set_content("课程通知包含特殊符号 ©。")
    msg.add_alternative(
        "<html><body><p>课程<b>通知</b></p><script>secret()</script></body></html>",
        subtype="html",
    )
    msg.add_attachment(
        b"attachment-content",
        maintype="application",
        subtype="octet-stream",
        filename="中文附件 ✓.txt",
    )
    return msg.as_bytes()


@pytest.fixture
def task(raw_mail):
    now = time.time()
    return dict(
        id="a" * 32,
        account="source",
        validity=1,
        uid=11,
        message_id="<one@example.invalid>",
        fingerprint="content-hash",
        received=now,
        detected=now,
        payload=raw_mail,
        metadata={},
    )


def enqueue(state, task, **changes):
    args = {k: v for k, v in task.items() if k not in ("id", "detected")}
    args.update(changes)
    return state.enqueue(**args)
