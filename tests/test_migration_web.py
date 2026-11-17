import io
import json
import tarfile
from dataclasses import asdict
import pytest
from nwpu_forwarder.config import ConfigManager, Paths
from nwpu_forwarder.portability import export_backup, import_backup
from nwpu_forwarder.state import StateManager
from nwpu_forwarder.web import create_app
from nwpu_forwarder.worker import instance_lock
from conftest import enqueue


def test_migration_restores_baseline_pending_and_success(
    paths, config, state, task, tmp_path
):
    ConfigManager(paths.config).save(config)
    state.baseline("source", 1, 10)
    sent = enqueue(state, task)
    state.sent(sent)
    pending = enqueue(state, task, uid=12, fingerprint="second")
    state.set("running", True)
    archive = tmp_path / "migration.backup.tar.gz"
    export_backup(paths, archive)
    with tarfile.open(archive) as tar:
        assert set(tar.getnames()) == {"manifest.json", "config.json", "state.sqlite3"}
    target = Paths(
        tmp_path / "target/config.json",
        tmp_path / "target/data",
        tmp_path / "target/logs",
    )
    import_backup(target, archive)
    restored = StateManager(target.database)
    assert (
        restored.account("source")["last_uid"] == 12
        and restored.claim("source")["id"] == pending
    )
    assert (
        restored.snapshot()["stats"]["sent"] == 1 and restored.get("running") is False
    )
    assert (
        restored.get("migrated_resume_running") is True
        and ConfigManager(target.config).load() == config
    )


def test_backup_rejects_active_worker(paths, config, tmp_path):
    ConfigManager(paths.config).save(config)
    with instance_lock(paths.data / "worker.lock"):
        with pytest.raises(RuntimeError):
            export_backup(paths, tmp_path / "a.backup.tar.gz")


def test_restore_does_not_overwrite_existing_state(
    paths, config, state, task, tmp_path
):
    ConfigManager(paths.config).save(config)
    state.baseline("source", 1, 10)
    enqueue(state, task)
    archive = tmp_path / "a.backup.tar.gz"
    export_backup(paths, archive)
    with pytest.raises(ValueError):
        import_backup(paths, archive)
    assert state.snapshot()["stats"]["total"] == 1


@pytest.mark.parametrize("kind", ["corrupt", "version", "traversal", "symlink"])
def test_invalid_backup_rejected_before_target_change(
    paths, config, state, tmp_path, kind
):
    ConfigManager(paths.config).save(config)
    source = tmp_path / "valid.backup.tar.gz"
    export_backup(paths, source)
    with tarfile.open(source) as tar:
        files = {m.name: tar.extractfile(m).read() for m in tar.getmembers()}
    if kind == "corrupt":
        files["config.json"] += b"bad"
    if kind == "version":
        manifest = json.loads(files["manifest.json"])
        manifest["schema_version"] = 999
        files["manifest.json"] = json.dumps(manifest).encode()
    dest = tmp_path / "invalid.backup.tar.gz"
    with tarfile.open(dest, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(
                "../config.json"
                if kind == "traversal" and name == "config.json"
                else name
            )
            if kind == "symlink" and name == "config.json":
                info.type = tarfile.SYMTYPE
                info.linkname = "/etc/passwd"
            else:
                info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    before = paths.config.read_bytes()
    with pytest.raises(ValueError):
        import_backup(paths, dest)
    assert paths.config.read_bytes() == before


@pytest.fixture
def web(paths, config):
    ConfigManager(paths.config).save(config)
    app = create_app(paths, "synthetic-panel-password")
    app.config["TESTING"] = True
    return app.test_client()


def login(client):
    client.get("/login")
    with client.session_transaction() as session:
        token = session["csrf"]
    assert (
        client.post(
            "/login",
            data={
                "csrf": token,
                "username": "2026000000",
                "password": "synthetic-panel-password",
            },
        ).status_code
        == 302
    )
    with client.session_transaction() as session:
        return session["csrf"]


def test_panel_requires_authentication(web):
    assert web.get("/").status_code == 302 and web.get("/api/status").status_code == 302
    assert web.post("/action", data={"action": "start"}).status_code == 400


def test_panel_csrf_and_bad_host(web):
    token = login(web)
    assert (
        web.post("/action", data={"action": "start", "csrf": "bad"}).status_code == 400
    )
    assert web.get("/", headers={"Host": "attacker.invalid"}).status_code == 400
    assert (
        web.post("/action", data={"action": "start", "csrf": token}).status_code == 302
    )


def test_panel_controls_stats_and_safe_render(web, state, task):
    token = login(web)
    enqueue(state, task)
    assert (
        web.get("/").status_code == 200
        and b"attachment-content" not in web.get("/").data
    )
    assert (
        web.get("/settings").status_code == 200 and web.get("/logs").status_code == 200
    )
    web.post("/action", data={"action": "start", "csrf": token})
    assert state.get("running")
    web.post("/action", data={"action": "stop", "csrf": token})
    assert not state.get("running")


def test_login_rate_limit(web):
    web.get("/login")
    with web.session_transaction() as session:
        token = session["csrf"]
    for _ in range(5):
        web.post(
            "/login",
            data={"csrf": token, "username": "2026000000", "password": "incorrect"},
        )
    assert (
        web.post(
            "/login",
            data={"csrf": token, "username": "2026000000", "password": "incorrect"},
        ).status_code
        == 429
    )


def test_test_mail_queue_is_deduplicated(state):
    assert state.command("test_mail") == state.command("test_mail")


def test_config_change_requires_pause(web, state, config, paths):
    token = login(web)
    state.set("running", True)
    form = {k: str(v) for k, v in asdict(config).items()}
    form.update(csrf=token, consent="on", imap_host="another.example.invalid")
    assert "保存失败".encode() in web.post("/settings", data=form).data
    assert ConfigManager(paths.config).load().imap_host == config.imap_host
