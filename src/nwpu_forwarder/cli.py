from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

from .config import ConfigManager, Paths
from .errors import error_code
from .portability import export_backup, import_backup
from .state import StateManager
from .worker import Worker, instance_lock

SERVICES = ["nwpu-forwarder-web.service", "nwpu-forwarder-worker.service"]
CREDS = Path("/etc/nwpu-mail-forwarder/credentials")


def systemctl(*args):
    subprocess.run(["systemctl", *args], check=True)


def stopped_services():
    if os.name != "nt" and not os.environ.get("NWPU_RUNTIME_DIR"):
        for service in SERVICES:
            if (
                subprocess.run(
                    ["systemctl", "is-active", "--quiet", service], check=False
                ).returncode
                == 0
            ):
                raise ValueError(
                    "请先执行 sudo nwpu-forwarder stop-services，确保旧实例停止"
                )


def root_required():
    if os.name == "nt" or os.geteuid() != 0:
        raise ValueError("此操作需要在 Linux 服务器上使用 sudo")


def ownership(paths):
    if os.name != "nt" and os.geteuid() == 0 and not os.environ.get("NWPU_RUNTIME_DIR"):
        import pwd

        owner = pwd.getpwnam("nwpu-forwarder")
        for base in (paths.data, paths.config.parent, paths.logs):
            for path in [base, *base.rglob("*")]:
                if path.exists():
                    os.chown(path, owner.pw_uid, owner.pw_gid)


def configure_credentials():
    root_required()
    if not sys.stdin.isatty():
        raise ValueError("请在交互式 SSH 终端运行，密码不得通过聊天或脚本参数提供")
    paths = Paths.environment()
    manager = ConfigManager(paths.config)
    config = manager.load()
    username = (
        input("登录账号（学号；留空保留已有账号）：").strip() or config.panel_username
    )
    if not username:
        raise ValueError("首次配置必须填写登录学号")
    updated = replace(config, panel_username=username)
    updated.validate()
    CREDS.mkdir(parents=True, exist_ok=True, mode=0o700)
    CREDS.chmod(0o700)
    subprocess.run(["systemd-creds", "setup"], check=True, stdout=subprocess.DEVNULL)
    print("输入不会回显。留空保留已有凭据；首次配置必须填写三个凭据。")
    labels = {
        "school_password": "学校客户端专用密码",
        "qq_password": "QQ SMTP 授权码",
        "panel_password": "登录密码（自己设置，至少 10 个字符）",
    }
    staged = []
    try:
        for name, label in labels.items():
            value = getpass.getpass(label + "：")
            if not value:
                if not (CREDS / name).exists():
                    raise ValueError("首次配置不能留空")
                continue
            if name == "panel_password":
                if len(value) < 10 or value != getpass.getpass("再次输入登录密码："):
                    raise ValueError("登录密码长度不足或两次输入不一致")
            temp = CREDS / (".new-" + name)
            subprocess.run(
                [
                    "systemd-creds",
                    "encrypt",
                    "--with-key=host",
                    "--name=" + name,
                    "-",
                    str(temp),
                ],
                input=value.encode(),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            temp.chmod(0o600)
            staged.append((temp, CREDS / name))
            del value
        for temp, dest in staged:
            os.replace(temp, dest)
        manager.save(updated)
        ownership(paths)
        systemctl("enable", *SERVICES)
        systemctl("restart", *SERVICES)
        print("加密凭据已保存，服务已启动。首次监听仍需在面板完成配置并点击启动。")
    finally:
        for temp in CREDS.glob(".new-*"):
            temp.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="西工大邮件通知服务")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in (
        "worker",
        "status",
        "start",
        "stop",
        "credentials",
        "stop-services",
        "preflight",
        "delete-credentials",
        "clear-data",
    ):
        commands.add_parser(name)
    backup = commands.add_parser("backup")
    backup.add_argument("--output", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    test = commands.add_parser("test")
    test.add_argument("kind", choices=["imap", "smtp", "mail"])
    args = parser.parse_args()
    paths = Paths.environment()
    try:
        if args.command == "worker":
            Worker(paths).run()
        elif args.command == "credentials":
            configure_credentials()
        elif args.command == "stop-services":
            root_required()
            systemctl("stop", *SERVICES)
            print("面板和监听服务已停止。迁移期间请保持旧服务器停止。")
        elif args.command in {"backup", "restore"}:
            stopped_services()
            if args.command == "backup":
                export_backup(paths, args.output)
                print("备份已生成（可能包含待发送邮件内容），请通过 SSH 安全传输。")
            else:
                import_backup(paths, args.archive.resolve())
                ownership(paths)
                print(
                    "恢复成功，监听保持停止。请在新主机配置凭据、测试连接后手动启动。"
                )
        elif args.command in {"delete-credentials", "clear-data"}:
            root_required()
            stopped_services()
            target = CREDS if args.command == "delete-credentials" else paths.data
            print(
                f"将删除 {target} 下的{'加密凭据' if args.command == 'delete-credentials' else '数据库、去重记录及待发送邮件'}。已有备份不会删除。"
            )
            if not sys.stdin.isatty() or input("输入 DELETE 确认：") != "DELETE":
                raise ValueError("未确认，数据保持不变")
            with instance_lock(paths.data / "worker.lock"):
                for item in target.iterdir():
                    if item.name == "worker.lock":
                        continue
                    if item.is_dir() and not item.is_symlink():
                        shutil.rmtree(item)
                    else:
                        item.unlink()
            print("删除完成。清除状态后再次启动会建立新的 UID 基线。")
        elif args.command == "preflight":
            config = ConfigManager(paths.config).load()
            config.validate()
            state = StateManager(paths.database)
            # No IMAP search and no SMTP transmission.
            print(
                json.dumps(
                    {
                        "config_valid": True,
                        "schema": 1,
                        "account_initialized": state.account(config.account_key)
                        is not None,
                        "stats": state.snapshot()["stats"],
                        "running": state.get("running", False),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            state = StateManager(paths.database)
            if args.command == "status":
                print(json.dumps(state.snapshot(), ensure_ascii=False, indent=2))
            elif args.command in {"start", "stop"}:
                if args.command == "start":
                    ConfigManager(paths.config).load().validate(ready=True)
                state.set("running", args.command == "start")
                print(
                    "启动请求已保存"
                    if args.command == "start"
                    else "停止请求已保存，当前发送完成后暂停"
                )
            elif args.command == "test":
                task = state.command(
                    "test_"
                    + {"imap": "imap", "smtp": "smtp", "mail": "mail"}[args.kind]
                )
                print(f"测试已排队，任务 ID：{task}；请在面板查看结果。")
        return 0
    except Exception as exc:
        # Validation messages are locally controlled, not remote server replies.
        message = str(exc) if isinstance(exc, ValueError) else error_code(exc)
        print(
            "操作失败：" + message + "。请检查配置、服务状态和文件权限后重试。",
            file=sys.stderr,
        )
        return 1
    finally:
        if args.command not in {"worker", "credentials"}:
            ownership(paths)


if __name__ == "__main__":
    raise SystemExit(main())
