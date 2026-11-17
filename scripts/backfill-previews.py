"""Enrich up to 50 recorded tasks using a systemd-provided school credential.

Does not send mail, mark it read, change task states, or move the UID cursor.
Only aggregate counts and sanitized errors are printed.
"""

import json

from nwpu_forwarder.config import ConfigManager, Paths, credential
from nwpu_forwarder.errors import error_code
from nwpu_forwarder.receiver import MailReceiver
from nwpu_forwarder.state import StateManager


def main():
    paths = Paths.environment()
    receiver = MailReceiver(
        ConfigManager(paths.config).load(),
        credential("school_password"),
        StateManager(paths.database),
    )
    try:
        receiver.connect()
        print(json.dumps(receiver.refresh_task_summaries()))
    except Exception as exc:
        print("摘要补取失败：" + error_code(exc))
        raise SystemExit(1) from None
    finally:
        receiver.close()


if __name__ == "__main__":
    main()
