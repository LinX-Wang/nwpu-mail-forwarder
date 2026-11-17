#!/usr/bin/env bash
set -euo pipefail
umask 077
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo bash deploy/install.sh'; exit 1; fi
source /etc/os-release
case "$ID:$VERSION_ID" in ubuntu:24.04|debian:12) ;; *) echo '第一版仅支持 Ubuntu 24.04 和 Debian 12'; exit 1;; esac
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
app_root=/opt/nwpu-mail-forwarder
release_dir="$app_root/releases/$(date -u +%Y%m%dT%H%M%S)-0.1.0"
apt-get update -qq
apt-get install -y python3 python3-venv ca-certificates
getent passwd nwpu-forwarder >/dev/null || useradd --system --home-dir /var/lib/nwpu-mail-forwarder --shell /usr/sbin/nologin nwpu-forwarder
install -d -m 755 "$app_root" "$app_root/releases"
install -d -m 750 -o nwpu-forwarder -g nwpu-forwarder /var/lib/nwpu-mail-forwarder /var/log/nwpu-mail-forwarder
install -d -m 755 /etc/nwpu-mail-forwarder
install -d -m 750 -o nwpu-forwarder -g nwpu-forwarder /etc/nwpu-mail-forwarder/settings
install -d -m 700 /etc/nwpu-mail-forwarder/credentials /var/backups/nwpu-mail-forwarder
install -d -m 755 "$release_dir"
cp -a "$source_dir/src" "$release_dir/src"
install -m 644 "$source_dir/pyproject.toml" "$source_dir/requirements.txt" "$source_dir/LICENSE" "$source_dir/README.md" "$release_dir/"
python3 -m venv "$release_dir/.venv"
package_index="${NWPU_PACKAGE_INDEX:-https://pypi.org/simple}"
case "$package_index" in https://*) ;; *) echo 'Python 包索引必须使用 HTTPS'; exit 1;; esac
"$release_dir/.venv/bin/python" -m pip install --index-url "$package_index" --disable-pip-version-check "$release_dir"
# Runtime code is readable/executable by the service user, writable only by root.
chown -R root:root "$release_dir"
chmod -R a+rX "$release_dir"
had_credentials=false
[[ -f /etc/nwpu-mail-forwarder/credentials/school_password && -f /etc/nwpu-mail-forwarder/credentials/qq_password && -f /etc/nwpu-mail-forwarder/credentials/panel_password ]] && had_credentials=true
systemctl stop nwpu-forwarder-web.service nwpu-forwarder-worker.service 2>/dev/null || true
if [[ -x "$app_root/current/.venv/bin/nwpu-forwarder" ]]; then
    "$app_root/current/.venv/bin/nwpu-forwarder" backup --output "/var/backups/nwpu-mail-forwarder/$(date -u +%Y%m%dT%H%M%S).backup.tar.gz"
    readlink -f "$app_root/current" > "$app_root/previous-release"
fi
ln -s "$release_dir" "$app_root/current.new"
mv -Tf "$app_root/current.new" "$app_root/current"
install -m 644 "$source_dir/deploy/nwpu-forwarder-worker.service" "$source_dir/deploy/nwpu-forwarder-web.service" /etc/systemd/system/
cat > /usr/local/bin/nwpu-forwarder <<'SH'
#!/usr/bin/env bash
exec /opt/nwpu-mail-forwarder/current/.venv/bin/nwpu-forwarder "$@"
SH
chmod 755 /usr/local/bin/nwpu-forwarder
if [[ ! -f /etc/nwpu-mail-forwarder/settings/config.json ]]; then
    "$release_dir/.venv/bin/python" -c 'from nwpu_forwarder.config import ConfigManager, Config, Paths; ConfigManager(Paths().config).save(Config())'
fi
"$release_dir/.venv/bin/nwpu-forwarder" preflight
chown -R nwpu-forwarder:nwpu-forwarder /etc/nwpu-mail-forwarder/settings /var/lib/nwpu-mail-forwarder /var/log/nwpu-mail-forwarder
runuser -u nwpu-forwarder -- "$release_dir/.venv/bin/nwpu-forwarder" preflight
systemctl daemon-reload
if $had_credentials; then
    systemctl enable nwpu-forwarder-worker.service nwpu-forwarder-web.service
    systemctl start nwpu-forwarder-worker.service nwpu-forwarder-web.service
fi
echo '安装完成。首次配置请在交互式 SSH 终端运行：sudo nwpu-forwarder credentials'
echo '面板只绑定 127.0.0.1:18765；默认 SSH 隧道访问，也可配置 Caddy HTTPS 公网入口。'
