#!/usr/bin/env bash
set -euo pipefail
if [[ $EUID -ne 0 ]]; then echo '请使用 sudo'; exit 1; fi
app_root=/opt/nwpu-mail-forwarder
previous="$(cat "$app_root/previous-release")"
resolved="$(realpath -- "$previous")"
case "$resolved" in "$app_root"/releases/*) ;; *) echo '回滚目标不在应用发布目录内'; exit 1;; esac
[[ -x "$resolved/.venv/bin/nwpu-forwarder" ]] || exit 1
systemctl stop nwpu-forwarder-web.service nwpu-forwarder-worker.service
"$resolved/.venv/bin/nwpu-forwarder" preflight
ln -s "$resolved" "$app_root/current.rollback"
mv -Tf "$app_root/current.rollback" "$app_root/current"
systemctl start nwpu-forwarder-worker.service nwpu-forwarder-web.service
echo '已回滚程序版本，保留最新邮件处理状态。'
