#!/usr/bin/env bash
# Compatibility check only; Docker is not the production deployment method.
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
sudo docker run --rm --name nwpu-forwarder-debian-check --memory=512m --cpus=1 --env "PIP_INDEX_URL=${NWPU_PACKAGE_INDEX:-https://pypi.org/simple}" --env "DEBIAN_MIRROR=${NWPU_DEBIAN_MIRROR:-http://deb.debian.org/debian}" --mount "type=bind,source=$source_dir,target=/input,readonly" debian:12-slim bash -c '
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
sed -i "s|http://deb.debian.org/debian-security|$DEBIAN_MIRROR-security|g; s|http://deb.debian.org/debian|$DEBIAN_MIRROR|g" /etc/apt/sources.list.d/debian.sources
apt-get update -qq
apt-get install -y -qq python3 python3-venv systemd ca-certificates
mkdir -p /work
tar -C /input --exclude=.venv --exclude=__pycache__ --exclude="*.egg-info" --exclude=.pytest_cache -cf - . | tar -C /work -xf -
cd /work
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[test]"
.venv/bin/python -m pytest -q
mkdir -p /opt/nwpu-mail-forwarder /var/lib/nwpu-mail-forwarder /var/log/nwpu-mail-forwarder /etc/nwpu-mail-forwarder/settings
ln -s /work /opt/nwpu-mail-forwarder/current
systemd-analyze verify deploy/nwpu-forwarder-worker.service deploy/nwpu-forwarder-web.service
printf "synthetic-check" | systemd-creds encrypt --with-key=host --name=check - /tmp/check.cred
test "$(systemd-creds decrypt --name=check /tmp/check.cred -)" = synthetic-check
echo DEBIAN_COMPATIBILITY_PASS
'
