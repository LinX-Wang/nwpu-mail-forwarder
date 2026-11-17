from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

from . import __version__
from .config import ConfigManager, Paths
from .state import SCHEMA_VERSION, StateManager
from .worker import instance_lock


def checksum(path: Path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def export_backup(paths: Paths, output: Path) -> None:
    output = output.resolve()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("备份目标必须是现有目录中的新文件")
    config = ConfigManager(paths.config).load()
    config.validate()
    with (
        instance_lock(paths.data / "worker.lock"),
        tempfile.TemporaryDirectory(prefix="nwpu-backup-", dir=paths.data) as work,
    ):
        stage = Path(work)
        os.chmod(stage, 0o700)
        state = StateManager(paths.database)
        state.backup_to(stage / "state.sqlite3")
        ConfigManager(stage / "config.json").save(config)
        manifest = {
            "format": 1,
            "program_version": __version__,
            "schema_version": SCHEMA_VERSION,
            "resume_running": state.get("running", False),
            "sha256": {
                name: checksum(stage / name)
                for name in ("config.json", "state.sqlite3")
            },
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with (
                os.fdopen(fd, "wb") as stream,
                tarfile.open(fileobj=stream, mode="w:gz") as archive,
            ):
                for name in ("manifest.json", "config.json", "state.sqlite3"):
                    info = archive.gettarinfo(str(stage / name), arcname=name)
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mode = 0o600
                    with (stage / name).open("rb") as source:
                        archive.addfile(info, source)
        except BaseException:
            output.unlink(missing_ok=True)
            raise


def import_backup(paths: Paths, archive_path: Path) -> dict:
    # Never extract arbitrary paths or links supplied by an archive.
    with (
        instance_lock(paths.data / "worker.lock"),
        tempfile.TemporaryDirectory(prefix="nwpu-import-", dir=paths.data) as work,
    ):
        stage = Path(work)
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            expected = {"manifest.json", "config.json", "state.sqlite3"}
            if len(members) != 3 or {m.name for m in members} != expected:
                raise ValueError("备份文件清单无效")
            for member in members:
                cap = 10 * 1024**3 if member.name == "state.sqlite3" else 16384
                if not member.isfile() or member.size > cap:
                    raise ValueError("备份文件类型或大小无效")
                if shutil.disk_usage(stage).free < member.size + 128 * 1024**2:
                    raise ValueError("磁盘空间不足，目标数据未修改")
                with (
                    archive.extractfile(member) as source,
                    (stage / member.name).open("wb") as dest,
                ):
                    shutil.copyfileobj(source, dest, 1024 * 1024)
        manifest = json.loads((stage / "manifest.json").read_text(encoding="utf-8"))
        if (
            manifest.get("format") != 1
            or manifest.get("schema_version") != SCHEMA_VERSION
            or manifest.get("program_version") != __version__
        ):
            raise ValueError("请使用与备份相同版本的程序恢复")
        for name in ("config.json", "state.sqlite3"):
            if checksum(stage / name) != manifest.get("sha256", {}).get(name):
                raise ValueError("备份校验失败，目标数据未修改")
        ConfigManager(stage / "config.json").load().validate()
        with closing(
            sqlite3.connect((stage / "state.sqlite3").as_uri() + "?mode=ro", uri=True)
        ) as db:
            if db.execute(
                "SELECT count(*) FROM sqlite_master WHERE type IN ('trigger','view')"
            ).fetchone()[0]:
                raise ValueError("备份包含不支持的数据库对象")
            if (
                db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION
                or db.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            ):
                raise ValueError("备份数据库无效")
            if not {"tasks", "accounts", "kv", "commands"}.issubset(
                {
                    r[0]
                    for r in db.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            ):
                raise ValueError("备份数据库结构无效")
        if paths.database.exists():
            with closing(
                sqlite3.connect(
                    paths.database.resolve().as_uri() + "?mode=ro", uri=True
                )
            ) as db:
                if (
                    db.execute("SELECT count(*) FROM tasks").fetchone()[0]
                    or db.execute("SELECT count(*) FROM accounts").fetchone()[0]
                ):
                    raise ValueError("目标已有处理状态，请先备份并明确清除，避免覆盖")
        state = StateManager(stage / "state.sqlite3")
        state.recover()
        for key, value in {
            "running": False,
            "heartbeat": 0,
            "imap_connected": False,
            "sending_active": False,
            "imap_error": None,
            "smtp_error": None,
            "last_smtp_ok": 0,
            "migrated_resume_running": manifest.get("resume_running", False),
        }.items():
            state.set(key, value)
        # Checkpoint so no WAL sidecar is left behind when moving the database.
        with state.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        paths.config.parent.mkdir(parents=True, exist_ok=True)
        for suffix in ("-wal", "-shm"):
            Path(str(paths.database) + suffix).unlink(missing_ok=True)
        os.replace(stage / "state.sqlite3", paths.database)
        os.replace(stage / "config.json", paths.config)
        paths.database.chmod(0o600)
        paths.config.chmod(0o600)
        return manifest
