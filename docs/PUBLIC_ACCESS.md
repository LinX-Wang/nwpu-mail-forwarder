# HTTPS 公网面板

默认通过 SSH 隧道管理。如需手机及其他电脑直接访问，使用已有 Caddy HTTPS 站点的独立路径，例如 https://example.com/mail/。面板应用保持监听 127.0.0.1:18765，不直接开放此端口。

## 启用

先确认域名解析、可信 HTTPS 证书及该站点在云服务商入口可从外网正常访问。在源码目录运行：

```bash
sudo /opt/nwpu-mail-forwarder/current/.venv/bin/python deploy/expose-web.py --url https://example.com/mail/
```

脚本仅向已有域名的多行 Caddy 站点块添加面板路径，保留原站点路由；先备份配置、校验 Caddy 配置，再更新面板设置及服务。失败时尝试恢复原配置。支持重复执行，不重复添加路由。

配置文件中的 public_url 保存公网入口，默认空字符串。修改此项需与代理配置同步并重启服务，网页邮箱设置不会修改公网入口。配置及迁移备份不包含私钥或面板密码。

## 验证

登录页、CSS、登录后主页、设置、日志及退出均应位于面板前缀之下。公网 Cookie 使用 Secure、HttpOnly、SameSite=Strict，并限定面板路径。Caddy 覆盖转发头，应用只信任来自回环地址的同机代理。

登录默认同一客户端 IP 每 5 分钟最多尝试 5 次，单进程限速；重启面板会清除限速及已有会话。第一版使用一个 Gunicorn worker，不支持将多个面板进程直接组成集群。

**必须再从手机或其他电脑验证 HTTPS。** 服务器自测成功，仍可能被云入口或当前网络拦截。HTTP 403 “Non-compliance ICP Filing” 表示域名受到云服务商备案限制，调整 Flask 或关闭证书验证不能解决；应改用满足云平台要求的地址或部署环境。

公网 IP 的 HTTPS 访问需要受信任且包含 IP SAN 的证书，以及可靠自动续期；不能用自签名证书冒充浏览器可信 HTTPS。迁移导致 IP 改变时应重新申请相应证书。

## 使用公网 IPv4

适用于 Ubuntu / Debian、systemd、已有 Caddy 服务的服务器。公网 TCP 80、443 必须允许访问；80 用于 HTTP-01 证书验证，保持验证路径可访问才能续期。先安装独立的证书客户端：

```bash
sudo python3 -m venv /opt/nwpu-mail-forwarder/acme-venv
sudo /opt/nwpu-mail-forwarder/acme-venv/bin/python -m pip install certbot==5.10.0
sudo /opt/nwpu-mail-forwarder/current/.venv/bin/python deploy/expose-ip-https.py --ip YOUR_PUBLIC_IPV4 --prefix /mail
```

将 YOUR_PUBLIC_IPV4 换成服务器实际公网 IPv4。脚本使用 Let's Encrypt 的 shortlived 配置申请包含 IP SAN 的可信证书，保留其他网站路由；现有域名的面板路径会重定向到 IP 入口。邮件账号、凭据和 UID 基线不会重新初始化。Caddy 2.6 的 IP 访问需要 default_sni，脚本自动添加；已有不同 default_sni 时会拒绝覆盖。

这类证书有效期约 6 天，必须自动续期。本工具安装 nwpu-forwarder-ip-cert.timer，每天两次检查（附加最多一小时随机延迟），到期前由 Certbot 续期、更新 Caddy 专用证书文件并重载代理。可检查：

```bash
sudo systemctl list-timers nwpu-forwarder-ip-cert.timer
sudo systemctl start nwpu-forwarder-ip-cert.service
sudo systemctl show nwpu-forwarder-ip-cert.service -p Result -p ExecMainStatus
sudo journalctl -u nwpu-forwarder-ip-cert.service --no-pager -n 30
```

手动启动仅检查是否需要续期，不强制重复签发。证书与 ACME 账户保存在 /etc/letsencrypt-nwpu；Caddy 私钥仅 root 和 caddy 可读取。不要公开这些目录。公网 IP 为访问地址，不能替代云平台对服务部署的其他要求。

## 恢复私有访问

```bash
sudo /opt/nwpu-mail-forwarder/current/.venv/bin/python deploy/expose-web.py --disable
```

脚本移除本工具标记的 Caddy 路由并清空 public_url，恢复 SSH 隧道方式。卸载公网部署前也应先执行此命令，避免留下无效反向代理。

IP 模式也会移除本工具添加的 IP 站点、默认 SNI 和旧面板重定向，停止本工具的证书续期定时器；证书备份保留，其他网站和其他证书续期服务不受影响。

## 迁移

public_url 随非敏感配置备份迁移，Caddy 站点配置及证书需在新服务器单独准备。相同域名需更新 DNS；地址变化需重新配置公网入口。域名受限制或新入口尚未就绪时，可先恢复 SSH 模式完成邮件服务验证。

IP 模式迁移时在新服务器重新安装独立 Certbot 并运行 IP 部署脚本申请新 IP 的证书。邮件迁移包不会包含证书私钥、ACME 账户或续期配置；新服务器需单独验证定时器。
