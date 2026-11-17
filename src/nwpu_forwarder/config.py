from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    version: int = 1
    school_address: str = ""
    imap_host: str = "imap.nwpu.edu.cn"
    imap_port: int = 993
    qq_address: str = ""
    smtp_host: str = "smtp.qq.com"
    smtp_port: int = 465
    mode: str = "notification"
    consent: bool = False
    full_consent: bool = False
    attachments: bool = True
    poll_seconds: int = 90
    max_message_mib: int = 20
    public_url: str = ""
    panel_username: str = ""

    def validate(self, ready: bool = False) -> None:
        if not isinstance(self.panel_username, str) or (
            self.panel_username
            and not re.fullmatch(r"[0-9]{6,20}", self.panel_username)
        ):
            raise ValueError("登录账号应为 6–20 位数字学号")
        if self.version != 1 or self.mode not in {"notification", "full"}:
            raise ValueError("配置版本或转发模式无效")
        for address in (self.school_address, self.qq_address):
            if (ready or address) and not re.fullmatch(
                r"[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+", address
            ):
                raise ValueError("请填写完整邮箱地址")
        for host in (self.imap_host, self.smtp_host):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,252}", host):
                raise ValueError("服务器地址无效")
        for port in (self.imap_port, self.smtp_port):
            if type(port) is not int or not 1 <= port <= 65535:
                raise ValueError("端口应在 1–65535 之间")
        for flag in (self.consent, self.full_consent, self.attachments):
            if type(flag) is not bool:
                raise ValueError("确认选项无效")
        if type(self.poll_seconds) is not int or not 60 <= self.poll_seconds <= 3600:
            raise ValueError("轮询间隔应为 60–3600 秒")
        if (
            type(self.max_message_mib) is not int
            or not 1 <= self.max_message_mib <= 100
        ):
            raise ValueError("大小限制应为 1–100 MiB")
        if self.mode == "full" and not self.full_consent:
            raise ValueError("完整转发需要先确认学校规定允许")
        if ready and not self.consent:
            raise ValueError("请先确认允许向个人邮箱发送通知")
        if not isinstance(self.public_url, str):
            raise ValueError("公网地址应为字符串")
        if self.public_url:
            url = urlsplit(self.public_url)
            if (
                url.scheme != "https"
                or not url.hostname
                or not re.fullmatch(r"[a-z0-9.-]+", url.hostname)
                or url.username is not None
                or url.password is not None
                or url.port not in (None, 443)
                or url.query
                or url.fragment
                or not re.fullmatch(r"/[A-Za-z0-9_-]+/?", url.path)
            ):
                raise ValueError("公网地址应为 https://域名/面板路径，不包含查询参数")

    @property
    def account_key(self) -> str:
        # A changed account/server gets an independent first-run baseline.
        return f"{self.imap_host.lower()}:{self.imap_port}/{self.school_address.lower()}/INBOX"


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path

    def load(self) -> Config:
        if not self.path.exists():
            return Config()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        config = Config(**value)
        config.validate()
        return config

    def save(self, config: Config) -> None:
        config.validate()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=self.path.parent, prefix=".config-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(asdict(config), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)


@dataclass(frozen=True)
class Paths:
    config: Path = Path("/etc/nwpu-mail-forwarder/settings/config.json")
    data: Path = Path("/var/lib/nwpu-mail-forwarder")
    logs: Path = Path("/var/log/nwpu-mail-forwarder")

    @property
    def database(self) -> Path:
        return self.data / "state.sqlite3"

    @classmethod
    def environment(cls) -> "Paths":
        root = os.environ.get("NWPU_RUNTIME_DIR")
        if root:
            p = Path(root).resolve()
            return cls(p / "settings" / "config.json", p / "data", p / "logs")
        return cls()


def credential(name: str) -> str:
    directory = os.environ.get("CREDENTIALS_DIRECTORY")
    if not directory:
        raise RuntimeError("凭据尚未配置：请运行服务器终端凭据向导")
    value = (Path(directory) / name).read_text(encoding="utf-8")
    if not value:
        raise RuntimeError("凭据为空")
    return value
