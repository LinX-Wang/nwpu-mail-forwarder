#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo'; exit 1; fi
echo '将卸载程序及 systemd 服务；配置、凭据、数据库、日志和备份保留。'
read -r -p '输入 UNINSTALL 确认：' confirmation
[[ "$confirmation" == UNINSTALL ]] || exit 1
systemctl disable --now nwpu-forwarder-web.service nwpu-forwarder-worker.service || true
if [[ -f /etc/systemd/system/nwpu-forwarder-ip-cert.timer ]]; then
    systemctl disable --now nwpu-forwarder-ip-cert.timer || true
    systemctl stop nwpu-forwarder-ip-cert.service || true
    rm -f -- /etc/systemd/system/nwpu-forwarder-ip-cert.timer /etc/systemd/system/nwpu-forwarder-ip-cert.service
fi
rm -f -- /etc/systemd/system/nwpu-forwarder-web.service /etc/systemd/system/nwpu-forwarder-worker.service /usr/local/bin/nwpu-forwarder
systemctl daemon-reload
rm -rf -- /opt/nwpu-mail-forwarder
echo '程序已卸载，用户数据保留；需要清除数据时请在卸载前使用 CLI 删除命令。'
