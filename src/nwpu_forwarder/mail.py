from __future__ import annotations

import copy
import smtplib
import ssl
import time
from datetime import datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parseaddr
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from .config import Config


def safe_header(value) -> str:
    return " ".join(str(value or "（无）").splitlines())[:1000]


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden += 1
        if tag in {"p", "br", "div", "li"}:
            self.text.append(" ")

    def handle_endtag(self, tag):
        if tag in {"script", "style"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.text.append(data)


class MailParser:
    @classmethod
    def summary(cls, raw: bytes) -> dict:
        """Bounded plain text for the authenticated dashboard, never for logs."""
        message = cls.parse(raw)
        name, address = parseaddr(str(message.get("From", "")))
        # Presentation metadata only; never expose a full address in the overview.
        sender = " ".join(name.split())[:80] if name and "@" not in name else ""
        if not sender and "@" in address:
            local, domain = address.rsplit("@", 1)
            sender = local[:1] + "***@" + domain[:80]
        return {
            "subject": " ".join(str(message.get("Subject", "")).split())[:200]
            or "（无主题）",
            "preview": cls.preview(raw),
            "sender": sender or "发件人未留存",
            "attachments": [
                " ".join(str(part.get_filename()).split())[:120]
                for part in message.walk()
                if part.get_filename()
            ][:20],
        }

    @staticmethod
    def parse(raw: bytes):
        return BytesParser(policy=policy.default).parsebytes(raw)

    @classmethod
    def preview(cls, raw: bytes) -> str:
        message = cls.parse(raw)
        body = message.get_body(preferencelist=("plain", "html"))
        if body is None:
            return "（没有可预览的文本正文）"
        try:
            text = body.get_content()
        except (LookupError, UnicodeError, ValueError):
            text = (body.get_payload(decode=True) or b"").decode(
                "utf-8", errors="replace"
            )
        if not isinstance(text, str):
            return "（没有可预览的文本正文）"
        if body.get_content_type() == "text/html":
            parser = TextExtractor()
            parser.feed(text)
            text = "".join(parser.text)
        return " ".join(text.split())[:300] or "（空正文）"


def remove_attachments(message):
    if not message.is_multipart():
        return
    retained = []
    for part in message.iter_parts():
        if part.get_content_disposition() == "attachment":
            continue
        if part.get_filename() and part.get_content_disposition() != "inline":
            continue
        remove_attachments(part)
        retained.append(part)
    message.set_payload(retained)


class MailForwarder:
    def __init__(self, config: Config, password: str, smtp_factory=smtplib.SMTP_SSL):
        self.config = config
        self.password = password
        self.smtp_factory = smtp_factory

    def connect(self):
        client = self.smtp_factory(
            self.config.smtp_host,
            self.config.smtp_port,
            timeout=30,
            context=ssl.create_default_context(),
        )
        try:
            client.login(self.config.qq_address, self.password)
        except BaseException:
            client.close()
            raise
        return client

    def test(self) -> None:
        client = self.connect()
        # A QUIT failure after successful auth is not a failed connection test.
        try:
            client.quit()
        except (OSError, smtplib.SMTPException):
            client.close()

    def build(self, task: dict, force_notification: bool = False) -> EmailMessage:
        original = MailParser.parse(task["payload"] or b"")
        metadata = task["metadata"]
        subject = safe_header(original.get("Subject"))
        sender = safe_header(original.get("From"))
        received = datetime.fromtimestamp(
            task["received"], ZoneInfo("Asia/Shanghai")
        ).isoformat()
        full = (
            self.config.mode == "full"
            and not force_notification
            and not metadata.get("truncated")
        )
        notice = (
            "原邮件超过大小限制，以下仅为通知。\n"
            if metadata.get("truncated") or force_notification
            else ""
        )
        text = (
            f"{notice}原发件人：{sender}\n原主题：{subject}\n学校邮箱收件时间：{received}\n"
            f"转发任务 ID：{task['id']}\n\n"
        )
        result = EmailMessage(policy=policy.SMTP)
        result["From"] = self.config.qq_address
        result["To"] = self.config.qq_address
        result["Subject"] = f"[西工大] {subject}"
        # Stable identity and date across retries.
        result["Date"] = format_datetime(
            datetime.fromtimestamp(task["detected"], ZoneInfo("Asia/Shanghai"))
        )
        result["Message-ID"] = (
            f"<nwpu-{task['id']}@{self.config.qq_address.split('@')[1]}>"
        )
        result["X-NWPU-Task-ID"] = task["id"]
        if not full:
            result.set_content(
                text + "正文预览：\n" + MailParser.preview(task["payload"] or b"")
            )
        else:
            result.set_content(text)
            body = copy.deepcopy(original)
            for name in list(body.keys()):
                if (
                    not name.lower().startswith("content-")
                    and name.lower() != "mime-version"
                ):
                    del body[name]
            if not self.config.attachments:
                if body.get_content_disposition() == "attachment" or (
                    body.get_filename() and body.get_content_disposition() != "inline"
                ):
                    body = EmailMessage()
                    body.set_content("（附件转发已关闭）")
                else:
                    remove_attachments(body)
            result.make_mixed()
            result.attach(body)
        return result

    def send(self, task: dict) -> str:
        message = self.build(task)
        limit = self.config.max_message_mib * 1024 * 1024
        notice = task.get("notice") or ""
        if len(message.as_bytes()) > limit:
            message = self.build(task, force_notification=True)
            notice = "size_limit_notification"
        client = self.connect()
        try:
            # SMTP SIZE may advertise a lower limit than our own setting.
            client.ehlo_or_helo_if_needed()
            advertised = client.esmtp_features.get("size", "").split()
            if (
                advertised
                and advertised[0].isdigit()
                and len(message.as_bytes()) > int(advertised[0])
            ):
                message = self.build(task, force_notification=True)
                notice = "size_limit_notification"
            refused = client.send_message(
                message,
                from_addr=self.config.qq_address,
                to_addrs=[self.config.qq_address],
            )
            if refused:
                raise smtplib.SMTPRecipientsRefused(refused)
        finally:
            # Never let a failing QUIT mask DATA acceptance.
            try:
                client.quit()
            except (OSError, smtplib.SMTPException):
                client.close()
        return notice

    def send_test(self, task_id: str) -> None:
        now = time.time()
        original = EmailMessage(policy=policy.SMTP)
        original["From"] = "NWPU Mail Forwarder <test@example.invalid>"
        original["Subject"] = "连接测试成功"
        original.set_content("连接测试成功。这是一封测试通知。")
        raw = original.as_bytes()
        self.send(
            {
                "id": task_id,
                "payload": raw,
                "metadata": {},
                "received": now,
                "detected": now,
            }
        )
