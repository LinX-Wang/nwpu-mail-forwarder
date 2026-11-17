"""Run installed services with synthetic encrypted credentials before onboarding."""
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

if os.geteuid() != 0:
    raise SystemExit('请在服务器使用 sudo python3 deploy/smoke-test.py')
config = Path('/etc/nwpu-mail-forwarder/settings/config.json')
if json.loads(config.read_text()).get('school_address'):
    raise SystemExit('此安装测试仅用于尚未配置真实账号的实例')
for service in ('nwpu-forwarder-worker','nwpu-forwarder-web'):
    if subprocess.run(['systemctl','is-active','--quiet',service],check=False).returncode == 0:
        raise SystemExit('请先停止正式服务')
base='/opt/nwpu-mail-forwarder/current/.venv/bin/'
with tempfile.TemporaryDirectory(prefix='nwpu-service-smoke-',dir='/run') as work:
    credentials=[]
    for name in ('panel_password','school_password','qq_password'):
        path=Path(work)/name
        subprocess.run(['systemd-creds','encrypt','--with-key=host','--name='+name,'-',str(path)],input=os.urandom(32).hex().encode(),check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        credentials.append('--property=LoadCredentialEncrypted='+name+':'+str(path))
    common=['--property=User=nwpu-forwarder','--property=Group=nwpu-forwarder','--property=UMask=0077','--property=NoNewPrivileges=true','--property=PrivateTmp=true','--property=ProtectSystem=strict','--property=ProtectHome=true','--property=ReadWritePaths=/var/lib/nwpu-mail-forwarder /var/log/nwpu-mail-forwarder /etc/nwpu-mail-forwarder/settings']
    try:
        subprocess.run(['systemd-run','--unit=nwpu-forwarder-smoke-worker','--collect',*common,*credentials[1:],base+'nwpu-forwarder','worker'],check=True)
        subprocess.run(['systemd-run','--unit=nwpu-forwarder-smoke-web','--collect',*common,credentials[0],base+'gunicorn','--bind','127.0.0.1:18765','--workers','1','--threads','4','--access-logfile','/dev/null','nwpu_forwarder.web:create_app()'],check=True)
        for _ in range(20):
            try:
                with urllib.request.urlopen('http://127.0.0.1:18765/login',timeout=2) as response:
                    assert response.status == 200 and '进入私有面板'.encode() in response.read()
                break
            except (OSError, AssertionError):
                time.sleep(0.5)
        else:
            raise RuntimeError('私有面板未就绪，请检查服务日志')
        subprocess.run(['systemctl','is-active','--quiet','nwpu-forwarder-smoke-worker','nwpu-forwarder-smoke-web'],check=True)
        print('NONROOT_HARDENED_WORKER_AND_PRIVATE_PANEL_PASS')
    finally:
        subprocess.run(['systemctl','stop','nwpu-forwarder-smoke-web','nwpu-forwarder-smoke-worker'],check=False)
