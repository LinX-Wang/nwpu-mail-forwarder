# 运维说明

## 常用命令

```bash
sudo nwpu-forwarder status
sudo nwpu-forwarder start
sudo nwpu-forwarder stop
sudo nwpu-forwarder test imap
sudo nwpu-forwarder test smtp
sudo nwpu-forwarder test mail
sudo systemctl status nwpu-forwarder-worker nwpu-forwarder-web
```

start/stop 保存期望运行状态，不负责启动 systemd 本身。test 命令交给后台执行，结果在面板显示；连续点击会合并相同测试，避免短时间反复发测试邮件。发送测试邮件需先确认通知许可。

面板退出不停止监听；SSH 隧道关闭只影响管理访问。停止监听允许当前发送完成。认证失败更新凭据后重启；网络及临时 SMTP 错误自动重试。失败任务可手动重试。

## 更新与回滚

下载新版本后再次运行 `sudo bash deploy/install.sh`。先构建新虚拟环境，再暂停服务并备份状态，原发布目录保留。配置和凭据不覆盖。备份位于 `/var/backups/nwpu-mail-forwarder`。

默认 Python 包索引为 HTTPS PyPI。网络较慢时可用 `sudo env NWPU_PACKAGE_INDEX=https://mirrors.aliyun.com/pypi/simple/ bash deploy/install.sh`；安装脚本拒绝 HTTP Python 包索引。

`sudo bash deploy/rollback.sh` 切回前一个程序版本，保留最新数据库。当前只有 schema 1；未来版本必须增加显式迁移与回滚策略，不支持直接用旧程序打开未知新版本数据库。

## 删除和卸载

```bash
sudo nwpu-forwarder stop-services
sudo nwpu-forwarder delete-credentials
# 若也需要删除基线、去重与待处理邮件：
sudo nwpu-forwarder clear-data
# 最后卸载程序，默认保留其余用户数据：
sudo bash deploy/uninstall.sh
```

删除命令要求交互输入 DELETE；卸载要求 UNINSTALL。备份不自动删除。删除凭据后服务无法认证；清除状态后新启动会跳过当时已有的历史邮件。

## 日志与故障

日志位于 `/var/log/nwpu-mail-forwarder/runtime.log`，每个 2 MiB，最多保留 5 个轮转文件。面板提供最近 32 KiB。错误仅显示类别，避免服务器回复泄漏敏感内容。

遇到 network_or_storage_failed 时检查 DNS、出站规则和磁盘空间；tls_failed 检查系统时间与证书；authentication_failed 确认使用客户端密码或授权码。不要为排错关闭证书验证，也不要粘贴密码到 issue。

服务由 systemd 自动重启，并保留运行状态。部署不主动重启整台服务器。长时间睡眠、断网后重建连接并补处理；SMTP 最终收件成功及手机推送仍取决于 QQ 邮箱服务。

## IP HTTPS 证书维护

IP 公网入口使用短期可信证书，依靠 nwpu-forwarder-ip-cert.timer 每天两次检查续期。运行 `systemctl list-timers nwpu-forwarder-ip-cert.timer` 查看下次检查时间，`journalctl -u nwpu-forwarder-ip-cert.service` 查看结果。保持公网 80 的 ACME 验证路径可达。完整部署与回退步骤见 [公网访问](PUBLIC_ACCESS.md)。
