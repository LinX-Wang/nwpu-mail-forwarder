"""Expose the panel beneath an existing Caddy HTTPS site; keep Gunicorn private."""

import argparse
import os
import pwd
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlsplit

from nwpu_forwarder.config import ConfigManager, Paths


def remove_public_routes(text):
    text = re.sub(
        r"(?ms)^# BEGIN NWPU IP HTTPS.*?# END NWPU IP HTTPS[^\n]*\n", "", text
    )
    text = re.sub(
        r"(?ms)^# BEGIN NWPU IP GLOBAL.*?# END NWPU IP GLOBAL[^\n]*\n", "", text
    )
    text = re.sub(
        r"(?m)^[ \t]*# NWPU IP DEFAULT SNI\n[ \t]*default_sni[^\n]*\n", "", text
    )
    return re.sub(
        r"(?ms)^[ \t]*# BEGIN NWPU MAIL FORWARDER.*?# END NWPU MAIL FORWARDER[^\n]*\n",
        "",
        text,
    )


def add_panel_route(text, host, prefix):
    begin = "# BEGIN NWPU MAIL FORWARDER"
    end = "# END NWPU MAIL FORWARDER"
    text = re.sub(
        r"(?ms)^[ \t]*" + re.escape(begin) + r".*?" + re.escape(end) + r"[^\n]*\n",
        "",
        text,
    )
    matches = []
    for match in re.finditer(r"(?m)^([^\n{}#]+)\{[ \t]*\n", text):
        sites = match.group(1).replace(",", " ").split()
        if any(site in {host, "https://" + host, host + ":443"} for site in sites):
            matches.append(match)
    if len(matches) != 1:
        raise ValueError("需要一个已有 HTTPS 域名的独立多行 Caddy 站点块")
    if prefix + "/*" in text:
        raise ValueError("所选路径已被其他站点配置使用")
    block = (
        "    " + begin + "\n"
        f"    redir {prefix} {prefix}/ 308\n"
        f"    handle_path {prefix}/* {{\n"
        "        reverse_proxy 127.0.0.1:18765 {\n"
        "            header_up X-Forwarded-For {remote_host}\n"
        "            header_up X-Forwarded-Proto {scheme}\n"
        "            header_up X-Forwarded-Host {host}\n"
        f"            header_up X-Forwarded-Prefix {prefix}\n"
        "        }\n"
        "    }\n"
        "    " + end + "\n"
    )
    position = matches[0].end()
    return text[:position] + block + text[position:]


def run(*args):
    subprocess.run(args, check=True, capture_output=True)


def main():
    parser = argparse.ArgumentParser(
        description="将面板部署到已有 Caddy HTTPS 域名的子路径"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--url", help="例如 https://example.com/mail/")
    mode.add_argument(
        "--disable", action="store_true", help="移除公网入口并恢复 SSH 隧道访问"
    )
    parser.add_argument("--caddyfile", type=Path, default=Path("/etc/caddy/Caddyfile"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("请使用 sudo 和应用虚拟环境中的 Python")
    paths = Paths()
    manager = ConfigManager(paths.config)
    old_config = manager.load()
    public_url = "" if args.disable else args.url.rstrip("/")
    updated = replace(old_config, public_url=public_url)
    updated.validate()
    url = urlsplit(public_url)
    old_text = args.caddyfile.read_text(encoding="utf-8")
    old_stat = args.caddyfile.stat()
    if args.disable:
        candidate = remove_public_routes(old_text)
    else:
        candidate = add_panel_route(
            remove_public_routes(old_text), url.hostname, url.path
        )
    backup = Path("/var/backups/nwpu-mail-forwarder") / (
        "public-web-" + time.strftime("%Y%m%dT%H%M%S")
    )
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(args.caddyfile, backup / "Caddyfile")
    shutil.copy2(paths.config, backup / "config.json")
    for file in backup.iterdir():
        file.chmod(0o600)
    owner = pwd.getpwnam("nwpu-forwarder")
    fd, temp = tempfile.mkstemp(dir=args.caddyfile.parent, prefix=".nwpu-web-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(candidate)
        run("caddy", "validate", "--config", temp, "--adapter", "caddyfile")
        manager.save(updated)
        os.chown(paths.config, owner.pw_uid, owner.pw_gid)
        run("systemctl", "restart", "nwpu-forwarder-worker", "nwpu-forwarder-web")
        os.chmod(temp, old_stat.st_mode & 0o777)
        os.chown(temp, old_stat.st_uid, old_stat.st_gid)
        os.replace(temp, args.caddyfile)
        run("systemctl", "reload", "caddy")
        check_url = (
            "http://127.0.0.1:18765/login" if args.disable else public_url + "/login"
        )
        with urllib.request.urlopen(check_url, timeout=15) as response:
            if response.status != 200 or "进入私有面板".encode() not in response.read():
                raise ValueError("HTTPS 面板验证失败")
        print(
            "SSH_PANEL_RESTORED" if args.disable else "PUBLIC_HTTPS_PANEL_READY",
            public_url + "/" if public_url else "",
        )
        if not args.disable:
            print(
                "请再用手机或其他电脑确认外部 HTTPS 访问；服务器自测不能排除云入口限制。"
            )
        print("配置备份：", backup)
        if "# BEGIN NWPU IP HTTPS" in old_text:
            run("systemctl", "disable", "--now", "nwpu-forwarder-ip-cert.timer")
            Path("/etc/nwpu-mail-forwarder/ip-https.json").unlink(missing_ok=True)
    except BaseException:
        manager.save(old_config)
        os.chown(paths.config, owner.pw_uid, owner.pw_gid)
        args.caddyfile.write_text(old_text, encoding="utf-8")
        os.chmod(args.caddyfile, old_stat.st_mode & 0o777)
        run("systemctl", "restart", "nwpu-forwarder-worker", "nwpu-forwarder-web")
        run("systemctl", "reload", "caddy")
        raise
    finally:
        Path(temp).unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        raise SystemExit(
            "公网配置未完成，已尝试恢复原配置；请检查域名、服务状态及 Caddy 配置。"
        ) from None
