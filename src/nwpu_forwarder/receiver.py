from __future__ import annotations

import hashlib
import json
import math
import ssl
import time
from datetime import datetime

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientError

from .config import Config
from .mail import MailParser
from .state import StateManager


class MailReceiver:
    def __init__(
        self, config: Config, password: str, state: StateManager, factory=IMAPClient
    ):
        self.config = config
        self.password = password
        self.state = state
        self.factory = factory
        self.client = None
        self.validity = None
        self.idle_supported = False

    def connect(self) -> None:
        self.client = self.factory(
            self.config.imap_host,
            port=self.config.imap_port,
            ssl=True,
            ssl_context=ssl.create_default_context(),
            use_uid=True,
            timeout=30,
        )
        try:
            self.client.login(self.config.school_address, self.password)
            info = self.client.select_folder("INBOX", readonly=True)
            self.validity = int(info[b"UIDVALIDITY"])
            self.idle_supported = self.client.has_capability("IDLE")
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.shutdown()
            except Exception:
                pass
            self.client = None

    def scan(self) -> None:
        # Re-select also detects a generation change during a live connection.
        info = self.client.select_folder("INBOX", readonly=True)
        validity = int(info[b"UIDVALIDITY"])
        account = self.state.account(self.config.account_key)
        if account is None:
            # SEARCH avoids treating UIDNEXT-1 (possibly a reserved UID) as delivered mail.
            uids = self.client.search(["ALL"])
            self.state.baseline(self.config.account_key, validity, max(uids, default=0))
            self.state.set("last_check", time.time())
            return
        recovery = account["validity"] != validity or self.state.get(
            "recover_generation:" + self.config.account_key, False
        )
        if account["validity"] != validity:
            # Persist recovery intent BEFORE resetting the cursor.
            self.state.set("recover_generation:" + self.config.account_key, True)
            self.state.new_generation(self.config.account_key, validity)
            account = self.state.account(self.config.account_key)
            self.state.set("uid_reset", time.time())
        uids = sorted(self.client.search(["UID", f"{account['last_uid'] + 1}:*"]))
        # IMAP n:* can return the last existing UID when n is beyond the end.
        uids = [uid for uid in uids if uid > account["last_uid"]]
        for uid in uids:
            info = self.client.fetch([uid], ["RFC822.SIZE", "INTERNALDATE"]).get(uid)
            if info is None:  # Expunged between SEARCH and FETCH.
                self.state.advance(self.config.account_key, uid)
                continue
            date = info.get(b"INTERNALDATE")
            received = date.timestamp() if isinstance(date, datetime) else time.time()
            if (
                recovery
                and isinstance(date, datetime)
                and received < math.floor(account["baseline"])
            ):
                self.state.advance(self.config.account_key, uid)
                continue
            size = int(info[b"RFC822.SIZE"])
            truncated = size > self.config.max_message_mib * 1024 * 1024
            section = "BODY.PEEK[]<0.65536>" if truncated else "BODY.PEEK[]"
            data = self.client.fetch([uid], [section]).get(uid)
            if data is None:
                self.state.advance(self.config.account_key, uid)
                continue
            candidates = [v for k, v in data.items() if k.startswith(b"BODY[")]
            if not candidates or not isinstance(candidates[0], bytes):
                raise ValueError("IMAP 未返回邮件内容")
            raw = candidates[0]
            # Only complete-content hashes permit automatic generation deduplication.
            fingerprint = hashlib.sha256(
                raw + str(size).encode() + str(received).encode()
            ).hexdigest()
            parsed = MailParser.parse(raw)
            review = recovery and (
                not isinstance(date, datetime)
                or truncated
                or received < account["baseline"]
            )
            self.state.enqueue(
                account=self.config.account_key,
                validity=validity,
                uid=uid,
                fingerprint=fingerprint,
                message_id=str(parsed.get("Message-ID", "")),
                received=received,
                payload=raw,
                metadata={"truncated": truncated, "size": size},
                review=review,
                notice="size_limit_notification" if truncated else "",
            )
        self.state.set("recover_generation:" + self.config.account_key, False)
        self.state.set("last_check", time.time())

    def wait(self, stop, seconds: float, changed=None) -> None:
        if not self.idle_supported:
            # Small waits permit prompt pause/config reload without extra IMAP requests.
            end = time.monotonic() + seconds
            while not stop.is_set() and time.monotonic() < end:
                if changed and changed():
                    return
                stop.wait(min(1, max(0, end - time.monotonic())))
            return
        try:
            self.client.idle()
        except IMAPClientError:
            self.idle_supported = False
            self.state.set("listener_type", "poll")
            self.state.set("idle_fallback", True)
            return self.wait(stop, self.config.poll_seconds, changed)
        try:
            end = time.monotonic() + min(seconds, 1200)
            while not stop.is_set() and time.monotonic() < end:
                if changed and changed():
                    break
                if self.client.idle_check(timeout=1):
                    break
        finally:
            self.client.idle_done()

    def refresh_task_summaries(self, limit: int = 50) -> dict:
        """One-off read-only enrichment; never scan, enqueue, or advance UIDs."""
        result = {"updated": 0, "unavailable": 0}
        info = self.client.select_folder("INBOX", readonly=True)
        validity = int(info[b"UIDVALIDITY"])
        with self.state.connect() as db:
            tasks = [
                dict(row)
                for row in db.execute(
                    "SELECT id,uid,received,message_id,metadata FROM tasks "
                    "WHERE account=? AND validity=? ORDER BY detected DESC LIMIT ?",
                    (self.config.account_key, validity, max(1, min(limit, 50))),
                )
            ]
        for task in tasks:
            metadata = json.loads(task["metadata"])
            if all(
                key in metadata
                for key in ("subject", "preview", "sender", "attachments")
            ):
                continue
            info = self.client.fetch(
                [task["uid"]], ["RFC822.SIZE", "INTERNALDATE"]
            ).get(task["uid"])
            date = info.get(b"INTERNALDATE") if info else None
            if (
                not isinstance(date, datetime)
                or abs(date.timestamp() - task["received"]) > 1
                or info.get(b"RFC822.SIZE") != metadata.get("size")
            ):
                result["unavailable"] += 1
                continue
            data = self.client.fetch([task["uid"]], ["BODY.PEEK[]<0.65536>"]).get(
                task["uid"], {}
            )
            raw = next(
                (
                    value
                    for key, value in data.items()
                    if key.startswith(b"BODY[") and isinstance(value, bytes)
                ),
                None,
            )
            if raw is None or (
                task["message_id"]
                and str(MailParser.parse(raw).get("Message-ID", ""))
                != task["message_id"]
            ):
                result["unavailable"] += 1
                continue
            if self.state.set_mail_summary(task["id"], raw):
                result["updated"] += 1
        return result
