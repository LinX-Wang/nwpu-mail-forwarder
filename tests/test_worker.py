import logging
import time
from dataclasses import replace
from imapclient.exceptions import IMAPClientError
from nwpu_forwarder.config import ConfigManager
from nwpu_forwarder.worker import Worker
from nwpu_forwarder.receiver import MailReceiver
from conftest import enqueue


def test_success_cannot_be_demoted_to_retry(state, task):
    task_id = enqueue(state, task)
    state.sent(task_id)
    state.failure(task_id, "network_or_storage_failed", 30)
    assert state.snapshot()["tasks"][0]["status"] == "sent"


def test_sender_processes_task_and_pause(paths, config, task, monkeypatch):
    ConfigManager(paths.config).save(config)
    worker = Worker(paths)
    worker.state.set("running", True)
    enqueue(worker.state, task, account=config.account_key)
    monkeypatch.setattr("nwpu_forwarder.worker.credential", lambda _: "synthetic")

    def send(self, value):
        worker.stop.set()
        return ""

    monkeypatch.setattr("nwpu_forwarder.worker.MailForwarder.send", send)
    worker.send_loop()
    assert worker.state.snapshot()["stats"]["sent"] == 1
    assert not worker.state.get("sending_active")


def test_sender_disconnect_schedules_retry_and_safe_log(
    paths, config, task, monkeypatch, caplog
):
    ConfigManager(paths.config).save(config)
    worker = Worker(paths)
    worker.state.set("running", True)
    enqueue(worker.state, task, account=config.account_key)
    monkeypatch.setattr(
        "nwpu_forwarder.worker.credential", lambda _: "synthetic-password"
    )

    def send(self, value):
        worker.stop.set()
        raise ConnectionError(
            "synthetic-password " + value["payload"].decode(errors="replace")
        )

    monkeypatch.setattr("nwpu_forwarder.worker.MailForwarder.send", send)
    with caplog.at_level(logging.WARNING, logger="nwpu"):
        worker.send_loop()
    current = worker.state.snapshot()["tasks"][0]
    assert current["status"] == "retry" and current["next_attempt"] > time.time()
    assert (
        "synthetic-password" not in caplog.text
        and "attachment-content" not in caplog.text
    )


def test_receiver_reconnects_after_disconnect(paths, config, monkeypatch):
    ConfigManager(paths.config).save(config)
    worker = Worker(paths)
    worker.state.set("running", True)
    connections = []

    class Receiver:
        idle_supported = False

        def __init__(self, *args):
            pass

        def connect(self):
            connections.append(1)
            if len(connections) == 1:
                raise ConnectionError("synthetic")

        def scan(self):
            worker.stop.set()

        def wait(self, *args, **kwargs):
            pass

        def close(self):
            pass

    monkeypatch.setattr("nwpu_forwarder.worker.MailReceiver", Receiver)
    monkeypatch.setattr("nwpu_forwarder.worker.credential", lambda _: "synthetic")
    monkeypatch.setattr("nwpu_forwarder.worker.backoff", lambda _: 0)
    worker.receive_loop()
    assert len(connections) == 2


def test_no_consent_does_not_send(paths, config, task, monkeypatch):
    ConfigManager(paths.config).save(replace(config, consent=False))
    worker = Worker(paths)
    worker.state.set("running", True)
    enqueue(worker.state, task, account=config.account_key)

    def wait(_):
        worker.stop.set()

    monkeypatch.setattr(worker.stop, "wait", wait)
    worker.send_loop()
    assert worker.state.snapshot()["tasks"][0]["status"] == "pending"


def test_idle_rejected_falls_back_without_busy_poll(state, config, monkeypatch):
    import threading

    receiver = MailReceiver(config, "synthetic", state)
    receiver.idle_supported = True

    class Client:
        def idle(self):
            raise IMAPClientError("IDLE unsupported")

    receiver.client = Client()
    stop = threading.Event()
    stop.set()
    receiver.wait(stop, 1200)
    assert not receiver.idle_supported and state.get("listener_type") == "poll"
