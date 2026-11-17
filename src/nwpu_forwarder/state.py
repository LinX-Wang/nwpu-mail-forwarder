from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .mail import MailParser

SCHEMA_VERSION = 1


class StateManager:
    def __init__(self, path: Path):
        self.path = path.resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError("数据库版本不兼容，请先安装对应程序版本")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts(
                    account TEXT PRIMARY KEY, validity INTEGER NOT NULL,
                    last_uid INTEGER NOT NULL, baseline REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, validity INTEGER NOT NULL,
                    uid INTEGER NOT NULL, message_id TEXT, fingerprint TEXT NOT NULL,
                    received REAL NOT NULL, detected REAL NOT NULL, completed REAL,
                    status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, error TEXT, notice TEXT,
                    payload BLOB, metadata TEXT NOT NULL,
                    UNIQUE(account, validity, uid));
                CREATE INDEX IF NOT EXISTS task_due ON tasks(status,next_attempt);
                CREATE INDEX IF NOT EXISTS task_fingerprint ON tasks(account,fingerprint);
                CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS commands(
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, status TEXT NOT NULL,
                    created REAL NOT NULL, result TEXT);
                PRAGMA user_version=1;
            """)
        self.path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, key: str, default=None):
        with self.connect() as db:
            row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
            return json.loads(row[0]) if row else default

    def set(self, key: str, value) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO kv VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )

    def account(self, key: str):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM accounts WHERE account=?", (key,)
            ).fetchone()
            return dict(row) if row else None

    def baseline(self, account: str, validity: int, uid: int) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO accounts VALUES(?,?,?,?)",
                (account, validity, uid, time.time()),
            )

    def new_generation(self, account: str, validity: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE accounts SET validity=?,last_uid=0 WHERE account=?",
                (validity, account),
            )

    def advance(self, account: str, uid: int) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE accounts SET last_uid=max(last_uid,?) WHERE account=?",
                (uid, account),
            )

    def enqueue(
        self,
        *,
        account: str,
        validity: int,
        uid: int,
        fingerprint: str,
        message_id: str,
        received: float,
        payload: bytes,
        metadata: dict,
        review: bool = False,
        notice: str = "",
    ) -> str:
        task_id = uuid.uuid4().hex
        metadata = {**metadata, **MailParser.summary(payload)}
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM tasks WHERE account=? AND validity=? AND uid=?",
                (account, validity, uid),
            ).fetchone()
            if existing:
                return existing[0]
            previous = (
                None
                if metadata.get("truncated")
                else db.execute(
                    "SELECT id FROM tasks WHERE account=? AND validity<>? AND fingerprint=?",
                    (account, validity, fingerprint),
                ).fetchone()
            )
            status = (
                "duplicate" if previous else ("needs_review" if review else "pending")
            )
            db.execute(
                """INSERT INTO tasks(id,account,validity,uid,message_id,fingerprint,received,
                       detected,status,payload,metadata,notice) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    account,
                    validity,
                    uid,
                    message_id,
                    fingerprint,
                    received,
                    time.time(),
                    status,
                    None if previous else payload,
                    json.dumps(metadata),
                    notice,
                ),
            )
            db.execute(
                "UPDATE accounts SET last_uid=max(last_uid,?) WHERE account=?",
                (uid, account),
            )
        return task_id

    def recover(self) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status='retry',error='delivery_uncertain',next_attempt=? WHERE status='sending'",
                (time.time() + 30,),
            )
            db.execute(
                "UPDATE commands SET status='failed',result='interrupted' WHERE status='processing'"
            )

    def set_mail_summary(self, task_id: str, raw: bytes) -> bool:
        """Enrich an existing task without requeueing or retaining its body."""
        summary = MailParser.summary(raw)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT metadata FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
            if not row:
                return False
            metadata = {**json.loads(row[0]), **summary}
            db.execute(
                "UPDATE tasks SET metadata=? WHERE id=?",
                (json.dumps(metadata), task_id),
            )
        return True

    def claim(self, account: str):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM tasks WHERE account=? AND status IN ('pending','retry') AND next_attempt<=? ORDER BY detected,uid LIMIT 1",
                (account, time.time()),
            ).fetchone()
            if not row:
                return None
            db.execute(
                "UPDATE tasks SET status='sending',attempts=attempts+1 WHERE id=?",
                (row["id"],),
            )
            value = dict(row)
            value["attempts"] += 1
            value["metadata"] = json.loads(value["metadata"])
            return value

    def sent(self, task_id: str, notice: str = "") -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status='sent',completed=?,payload=NULL,error=NULL,notice=? WHERE id=?",
                (time.time(), notice, task_id),
            )
            db.execute(
                "INSERT INTO kv VALUES('last_forward',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps(time.time()),),
            )

    def failure(
        self, task_id: str, code: str, delay: float, permanent: bool = False
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE tasks SET status=?,error=?,next_attempt=? WHERE id=? AND status IN ('pending','sending','retry')",
                (
                    "failed" if permanent else "retry",
                    code,
                    time.time() + delay,
                    task_id,
                ),
            )

    def retry(self, task_id: str) -> bool:
        with self.connect() as db:
            result = db.execute(
                "UPDATE tasks SET status='pending',next_attempt=0,error=NULL WHERE id=? AND status IN ('failed','retry','needs_review')",
                (task_id,),
            )
            return bool(result.rowcount)

    def command(self, action: str) -> str:
        if action not in {"test_imap", "test_smtp", "test_mail"}:
            raise ValueError("无效操作")
        with self.connect() as db:
            # Bound the queue to avoid accidental repeated test messages.
            row = db.execute(
                "SELECT id FROM commands WHERE action=? AND (status IN ('pending','processing') OR created>?) ORDER BY created DESC LIMIT 1",
                (action, time.time() - 30),
            ).fetchone()
            if row:
                return row[0]
            command_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO commands VALUES(?,?, 'pending',?,NULL)",
                (command_id, action, time.time()),
            )
            return command_id

    def claim_command(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM commands WHERE status='pending' ORDER BY created LIMIT 1"
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE commands SET status='processing' WHERE id=?", (row["id"],)
                )
                return dict(row)

    def finish_command(self, command_id: str, result: str, success: bool) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE commands SET status=?,result=? WHERE id=?",
                ("done" if success else "failed", result, command_id),
            )

    def snapshot(self) -> dict:
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        start = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        end = (
            now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        ).timestamp()
        with self.connect() as db:
            stats = dict(
                db.execute(
                    """SELECT
                sum(detected>=? AND detected<? AND status<>'duplicate') AS today_detected,
                sum(completed>=? AND completed<? AND status='sent') AS today_sent,
                sum(status IN ('failed','retry')) AS failures,
                sum(status<>'duplicate') AS total,
                sum(status='sent') AS sent,
                sum(status IN ('pending','sending','retry')) AS pending
                FROM tasks""",
                    (start, end, start, end),
                ).fetchone()
            )
            tasks = [
                dict(r)
                for r in db.execute(
                    "SELECT id,uid,status,attempts,error,notice,detected,completed,next_attempt,received,metadata FROM tasks ORDER BY detected DESC LIMIT 50"
                )
            ]
            for task in tasks:
                metadata = json.loads(task.pop("metadata"))
                task["subject"] = str(metadata.get("subject", ""))[:200]
                task["preview"] = str(metadata.get("preview", ""))[:300]
                task["sender"] = str(metadata.get("sender", "发件人未留存"))[:100]
                task["attachments"] = metadata.get("attachments", [])[:20]
            commands = [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM commands ORDER BY created DESC LIMIT 10"
                )
            ]
            settings = {r[0]: json.loads(r[1]) for r in db.execute("SELECT * FROM kv")}
        return {
            "stats": {k: v or 0 for k, v in stats.items()},
            "tasks": tasks,
            "commands": commands,
            "state": settings,
        }

    def backup_to(self, target: Path) -> None:
        with self.connect() as db, closing(sqlite3.connect(target)) as dest:
            db.backup(dest)
