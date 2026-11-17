from __future__ import annotations

import logging
import os
import signal
import threading
import time
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler

from .config import ConfigManager, Paths, credential
from .errors import backoff, error_code
from .mail import MailForwarder
from .receiver import MailReceiver
from .state import StateManager


@contextmanager
def instance_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if os.name == "nt":
            import msvcrt

            stream.write(b"0")
            stream.flush()
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError("已有监听服务运行") from None
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise RuntimeError("已有监听服务运行") from None
        try:
            yield
        finally:
            if os.name == "nt":
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


class Worker:
    def __init__(self, paths: Paths):
        self.paths = paths
        self.configs = ConfigManager(paths.config)
        self.state = StateManager(paths.database)
        self.stop = threading.Event()
        self.log = logging.getLogger("nwpu")

    def receive_loop(self):
        attempts = 0
        blocked = False
        while not self.stop.is_set():
            config = self.configs.load()
            if not self.state.get("running", False) or blocked:
                self.state.set("imap_connected", False)
                self.stop.wait(1)
                continue
            receiver = None
            try:
                config.validate(ready=True)
                receiver = MailReceiver(
                    config, credential("school_password"), self.state
                )
                receiver.connect()
                self.state.set("imap_connected", True)
                self.state.set("imap_error", None)
                self.state.set(
                    "listener_type", "IDLE" if receiver.idle_supported else "poll"
                )
                attempts = 0
                while (
                    not self.stop.is_set()
                    and self.state.get("running", False)
                    and self.configs.load() == config
                ):
                    receiver.scan()
                    receiver.wait(
                        self.stop,
                        1200 if receiver.idle_supported else config.poll_seconds,
                        changed=lambda config=config: (
                            not self.state.get("running", False)
                            or self.configs.load() != config
                        ),
                    )
            except Exception as exc:
                code = error_code(exc)
                self.state.set("imap_error", code)
                self.state.set("imap_connected", False)
                self.log.warning("IMAP %s", code)
                if code == "authentication_failed":
                    blocked = True
                attempts += 1
                self.stop.wait(backoff(attempts))
            finally:
                if receiver:
                    receiver.close()
                self.state.set("imap_connected", False)
        self.state.set("receiver_stopped", True)

    def control(self, command):
        action = command["action"]
        try:
            config = self.configs.load()
            config.validate(ready=action == "test_mail")
            if not config.school_address or not config.qq_address:
                raise ValueError("请先填写邮箱地址")
            if action == "test_imap":
                receiver = MailReceiver(
                    config, credential("school_password"), self.state
                )
                try:
                    receiver.connect()
                finally:
                    receiver.close()
            else:
                forwarder = MailForwarder(config, credential("qq_password"))
                if action == "test_mail":
                    forwarder.send_test(command["id"])
                else:
                    forwarder.test()
                self.state.set("last_smtp_ok", time.time())
            self.state.finish_command(command["id"], "success", True)
        except Exception as exc:
            self.state.finish_command(command["id"], error_code(exc), False)

    def send_loop(self):
        blocked = False
        while not self.stop.is_set():
            self.state.set("heartbeat", time.time())
            command = self.state.claim_command()
            if command:
                self.state.set("sending_active", True)
                try:
                    self.control(command)
                finally:
                    self.state.set("sending_active", False)
                continue
            if not self.state.get("running", False) or blocked:
                self.stop.wait(1)
                continue
            config = self.configs.load()
            try:
                config.validate(ready=True)
            except ValueError:
                self.state.set("smtp_error", "configuration_required")
                self.stop.wait(1)
                continue
            task = self.state.claim(config.account_key)
            if not task:
                self.stop.wait(1)
                continue
            self.state.set("sending_active", True)
            try:
                notice = MailForwarder(config, credential("qq_password")).send(task)
                self.state.sent(task["id"], notice)
                self.state.set("last_smtp_ok", time.time())
                self.state.set("smtp_error", None)
                self.log.info("task=%s sent", task["id"])
            except Exception as exc:
                code = error_code(exc)
                self.state.set("smtp_error", code)
                self.log.warning("task=%s %s", task["id"], code)
                permanent = code in {"smtp_permanent", "recipient_rejected"}
                self.state.failure(
                    task["id"], code, backoff(task["attempts"]), permanent
                )
                if code == "authentication_failed":
                    blocked = True
            finally:
                self.state.set("sending_active", False)

    def run(self):
        self.paths.logs.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            self.paths.logs / "runtime.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        self.log.addHandler(handler)
        self.log.setLevel(logging.INFO)
        with instance_lock(self.paths.data / "worker.lock"):
            self.state.recover()
            self.state.set("receiver_stopped", False)
            self.state.set("sending_active", False)
            self.state.set("imap_error", None)
            self.state.set("smtp_error", None)
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, lambda *_: self.stop.set())

            def guarded(target):
                try:
                    target()
                except BaseException:
                    # Fatal storage/config failures are supervised by systemd.
                    self.log.error("worker_fatal")
                    self.stop.set()
                    self.state.set("worker_fatal", True)

            receiver = threading.Thread(
                target=lambda: guarded(self.receive_loop), daemon=True
            )
            receiver.start()
            try:
                self.send_loop()
            finally:
                self.stop.set()
                receiver.join(timeout=40)
                self.state.set("imap_connected", False)
                self.state.set("heartbeat", 0)
                handler.close()
                self.log.removeHandler(handler)
            if self.state.get("worker_fatal", False):
                self.state.set("worker_fatal", False)
                raise RuntimeError("后台服务异常，将由 systemd 重启")
