"""Provision a trusted IPv4 HTTPS panel without changing mail credentials."""

import argparse
import ipaddress
import json
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

CERTBOT = Path("/opt/nwpu-mail-forwarder/acme-venv/bin/certbot")
CERTDIR = Path("/etc/letsencrypt-nwpu")
LINEAGE = CERTDIR / "live" / "nwpu-forwarder-ip"
WEBROOT = Path("/var/lib/nwpu-mail-forwarder-acme")
HOOK = Path("/usr/local/lib/nwpu-mail-forwarder/install-ip-certificate.sh")
METADATA = Path("/etc/nwpu-mail-forwarder/ip-https.json")
BEGIN = "# BEGIN NWPU MAIL FORWARDER"
END = "# END NWPU MAIL FORWARDER"


def remove_ip_site(text):
    return re.sub(
        r"(?ms)^# BEGIN NWPU IP HTTPS.*?# END NWPU IP HTTPS[^\n]*\n", "", text
    )


def default_ip_sni(text, ip):
    # Browsers omit TLS SNI for IP literals. Caddy 2.6 needs a default name
    # to select the manually loaded IP certificate for these connections.
    if re.search(r"(?m)^\s*default_sni\s+", text):
        if not re.search(r"(?m)^\s*default_sni\s+" + re.escape(ip) + r"\s*$", text):
            raise ValueError("已有 default_sni 设置，请先核对 Caddy 配置")
        return text
    match = re.match(r"(?:[ \t]*#[^\n]*\n|\s)*\{[ \t]*\n", text)
    if match:
        at = match.end()
        return (
            text[:at] + f"    # NWPU IP DEFAULT SNI\n    default_sni {ip}\n" + text[at:]
        )
    return (
        "# BEGIN NWPU IP GLOBAL\n{\n    default_sni "
        + ip
        + "\n}\n# END NWPU IP GLOBAL\n"
        + text
    )


def http_site(ip):
    return f"""http://{ip} {{
    handle /.well-known/acme-challenge/* {{
        root * {WEBROOT}
        file_server
    }}
    handle {{
        redir https://{ip}{{uri}} 308
    }}
}}
"""


def render_ip_site(ip, prefix, certificate=False):
    text = "# BEGIN NWPU IP HTTPS\n" + http_site(ip)
    if certificate:
        text += f"""https://{ip} {{
    tls /etc/caddy/nwpu-forwarder-ip/fullchain.pem /etc/caddy/nwpu-forwarder-ip/privkey.pem
    {BEGIN}
    redir {prefix} {prefix}/ 308
    handle_path {prefix}/* {{
        reverse_proxy 127.0.0.1:18765 {{
            header_up X-Forwarded-For {{remote_host}}
            header_up X-Forwarded-Proto {{scheme}}
            header_up X-Forwarded-Host {{host}}
            header_up X-Forwarded-Prefix {prefix}
        }}
    }}
    {END}
}}
"""
    return text + "# END NWPU IP HTTPS\n"


def legacy_redirect(text, legacy, ip):
    if not legacy:
        return text
    url = urlsplit(legacy)
    if url.hostname == ip:
        return text
    matches = []
    for match in re.finditer(r"(?m)^([^\n{}#]+)\{[ \t]*\n", text):
        names = match.group(1).replace(",", " ").split()
        if any(
            name in {url.hostname, "https://" + url.hostname, url.hostname + ":443"}
            for name in names
        ):
            matches.append(match)
    if len(matches) != 1:
        raise ValueError("旧域名站点不唯一，请先核对 Caddy 配置")
    prefix = url.path.rstrip("/")
    block = f"""    {BEGIN}
    @nwpu_mail_legacy path {prefix} {prefix}/*
    redir @nwpu_mail_legacy https://{ip}{{uri}} 308
    {END}
"""
    at = matches[0].end()
    return text[:at] + block + text[at:]


def run(*args):
    subprocess.run([str(x) for x in args], check=True)


def verify_panel(url):
    # systemd reports active before Gunicorn has finished binding its socket.
    deadline = time.monotonic() + 30
    while True:
        try:
            with urllib.request.urlopen(url + "/login", timeout=5) as response:
                if (
                    response.status == 200
                    and "进入私有面板".encode() in response.read()
                ):
                    return
                raise ValueError("IP HTTPS 页面验证失败")
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(1)


def write_caddy(path, text):
    info = path.stat()
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".nwpu-ip-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        subprocess.run(
            ["caddy", "validate", "--config", temp, "--adapter", "caddyfile"],
            check=True,
            capture_output=True,
        )
        os.chmod(temp, info.st_mode & 0o777)
        os.chown(temp, info.st_uid, info.st_gid)
        os.replace(temp, path)
        run("systemctl", "reload", "caddy")
    finally:
        Path(temp).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description="配置受信任公网 IPv4 证书及面板")
    parser.add_argument("--ip", required=True)
    parser.add_argument("--prefix", default="/mail")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise ValueError("请使用 sudo")
    address = ipaddress.ip_address(args.ip)
    if address.version != 4 or not address.is_global:
        raise ValueError("请使用本服务器的公网 IPv4 地址")
    ip = str(address)
    prefix = args.prefix.rstrip("/")
    paths = Paths()
    manager = ConfigManager(paths.config)
    old = manager.load()
    updated = replace(old, public_url=f"https://{ip}{prefix}")
    updated.validate()
    if not CERTBOT.exists():
        raise ValueError("请先安装 Certbot 5.10.0 到独立 acme-venv")
    caddy = Path("/etc/caddy/Caddyfile")
    old_text = caddy.read_text(encoding="utf-8")
    backup = Path("/var/backups/nwpu-mail-forwarder") / (
        "ip-https-" + time.strftime("%Y%m%dT%H%M%S")
    )
    backup.mkdir(parents=True, mode=0o700)
    shutil.copy2(caddy, backup / "Caddyfile")
    shutil.copy2(paths.config, backup / "config.json")
    for path in backup.iterdir():
        path.chmod(0o600)
    metadata = json.loads(METADATA.read_text()) if METADATA.exists() else {}
    legacy = metadata.get("legacy_url", "")
    if old.public_url and urlsplit(old.public_url).hostname != ip:
        legacy = old.public_url
    if legacy:
        replace(old, public_url=legacy).validate()
    source = Path(__file__).resolve().parent
    HOOK.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "install-ip-certificate.sh", HOOK)
    HOOK.chmod(0o700)
    WEBROOT.mkdir(parents=True, exist_ok=True, mode=0o755)
    WEBROOT.chmod(0o755)
    for directory in (
        CERTDIR,
        Path("/var/lib/nwpu-mail-forwarder-certbot"),
        Path("/var/log/nwpu-mail-forwarder-certbot"),
    ):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        directory.chmod(0o700)
    owner = pwd.getpwnam("nwpu-forwarder")
    try:
        base = remove_ip_site(old_text)
        write_caddy(caddy, base + "\n" + render_ip_site(ip, prefix, False))
        run(
            CERTBOT,
            "certonly",
            "--non-interactive",
            "--agree-tos",
            "--register-unsafely-without-email",
            "--webroot",
            "--webroot-path",
            WEBROOT,
            "--ip-address",
            ip,
            "--cert-name",
            "nwpu-forwarder-ip",
            "--required-profile",
            "shortlived",
            "--keep-until-expiring",
            "--config-dir",
            CERTDIR,
            "--work-dir",
            "/var/lib/nwpu-mail-forwarder-certbot",
            "--logs-dir",
            "/var/log/nwpu-mail-forwarder-certbot",
            "--deploy-hook",
            HOOK,
        )
        run(
            "openssl",
            "x509",
            "-in",
            LINEAGE / "fullchain.pem",
            "-noout",
            "-checkip",
            ip,
        )
        subprocess.run(
            [str(HOOK)], env={**os.environ, "RENEWED_LINEAGE": str(LINEAGE)}, check=True
        )
        base = re.sub(
            r"(?ms)^[ \t]*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"[^\n]*\n",
            "",
            base,
        )
        final = (
            legacy_redirect(base, legacy, ip) + "\n" + render_ip_site(ip, prefix, True)
        )
        final = default_ip_sni(final, ip)
        manager.save(updated)
        os.chown(paths.config, owner.pw_uid, owner.pw_gid)
        run("systemctl", "restart", "nwpu-forwarder-worker", "nwpu-forwarder-web")
        write_caddy(caddy, final)
        verify_panel(updated.public_url)
        for name in ("nwpu-forwarder-ip-cert.service", "nwpu-forwarder-ip-cert.timer"):
            shutil.copy2(source / name, Path("/etc/systemd/system") / name)
        run("systemctl", "daemon-reload")
        run("systemctl", "enable", "--now", "nwpu-forwarder-ip-cert.timer")
        METADATA.write_text(
            json.dumps(
                {"ip": ip, "prefix": prefix, "legacy_url": legacy},
                ensure_ascii=False,
                indent=2,
            )
        )
        METADATA.chmod(0o600)
        print("PUBLIC_IP_HTTPS_READY", updated.public_url + "/")
        print("IP_CERTIFICATE_RENEWAL_TIMER_ENABLED")
    except BaseException:
        manager.save(old)
        os.chown(paths.config, owner.pw_uid, owner.pw_gid)
        write_caddy(caddy, old_text)
        run("systemctl", "restart", "nwpu-forwarder-worker", "nwpu-forwarder-web")
        raise


if __name__ == "__main__":
    main()
