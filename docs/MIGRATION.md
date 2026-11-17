# 服务器迁移

适用于受支持的 Ubuntu 24.04 / Debian 12 主机。同一邮箱只启用一个活动实例，避免两台服务器独立发送相同邮件。

## 旧服务器

先停止两个服务，等待 systemd 完成正在发送的任务。不要用 kill -9。

```bash
sudo nwpu-forwarder stop-services
sudo nwpu-forwarder backup --output /var/backups/nwpu-mail-forwarder/migration.backup.tar.gz
```

备份包含程序版本、配置、校验清单及一致性数据库快照，包括首次基线、成功记录、固定任务 ID 和待发送内容。**不包含密码、主机密钥、面板口令、会话或日志。** 备份可能含邮件内容，权限为 0600，使用 SSH/SFTP 安全传输，不上传 GitHub。

保持旧服务停止；最终迁移期间若服务器可能重启，再执行：

```bash
sudo systemctl disable nwpu-forwarder-worker.service nwpu-forwarder-web.service
```

## 新服务器

安装与备份相同版本的源码，先不输入凭据。传输备份至服务器受保护目录后：

```bash
sudo bash deploy/install.sh
sudo nwpu-forwarder restore /path/to/migration.backup.tar.gz
sudo nwpu-forwarder preflight
sudo nwpu-forwarder credentials
```

恢复工具检查文件清单、路径、类型、大小、SHA-256、版本和 SQLite 完整性，拒绝覆盖已有账号或任务状态。只导入可信来源的自己生成的备份。

preflight 不查询邮件、不发送通知。导入后监听强制保持停止，密码需在新主机重新输入。通过 SSH 隧道登录面板，测试连接，检查任务和统计，再启动监听；切换期间的新邮件会补处理。

## 验证与回滚

- 已成功任务仍为成功，不应重发；待发送任务继续使用原任务 ID。
- 首次基线保持不变，不将迁移当作首次启动。
- 确认旧实例未运行后启动新实例。
- 回滚先停新实例并备份其最新状态，再导回旧服务器。旧服务器若已有状态，先备份并明确清除后恢复，不能直接覆盖。
- 清除数据和恢复备份不删除已有加密凭据；按需在终端向导更换。

迁移包校验用于发现损坏，不提供数字签名认证，也不是加密包。服务器到期前留出迁移和验收时间。

公网面板需单独迁移入口：在新服务器按 [公网访问说明](PUBLIC_ACCESS.md) 配置代理与证书。公网 IP 改变时重新申请包含新 IP 的证书并启用续期；证书私钥和 ACME 账户不在邮件迁移包中。
