# v0.1.0 · 首次公开发布

学校来信，及时知晓。将学校新邮件发送至常用 QQ 邮箱，结合 QQ 邮箱已有的客户端通知能力，减少反复登录学校邮箱的麻烦。

## 本版内容

- 自托管 IMAP 监听与 QQ SMTP 通知，默认仅通知，可明确授权完整邮件及附件转发。
- 中文响应式管理面板：概览、邮箱配置、转发设置、运行日志。
- 日间 / 夜间 / 跟随时间主题，官方邮箱快捷入口，折叠式凭据获取教程。
- 自动重连、失败重试、持久化去重、可迁移状态、版本回滚。
- systemd 常驻部署，SSH 私有访问与可选 HTTPS 公网访问。
- 修复会话过期时反复跳转、重复状态请求与无法显示登录表单的问题。

## 安装与校验

源码包提供 ZIP 与 TAR.GZ，各附 SHA-256 校验文件。Linux 下载到同一目录后可运行：

```bash
sha256sum -c nwpu-mail-forwarder-0.1.0-source.tar.gz.sha256
tar -xzf nwpu-mail-forwarder-0.1.0-source.tar.gz
cd nwpu-mail-forwarder-0.1.0
sudo bash deploy/install.sh
sudo nwpu-forwarder credentials
```

安装面向 Ubuntu 24.04 / Debian 12，Python 3.11+，systemd 252+。首次配置、通知链路和可选公网管理见 [README](../README.md)。

## 发布验证

2026-11-17 在 Windows / Python 3.12 完成 120 项自动化测试。隔离浏览器检查覆盖四页、五种尺寸、日夜主题、表单保存、日志操作与加载状态；另有会话过期回归脚本。未发现 JavaScript 错误，截图使用合成数据，不发送真实邮件。

此前 Linux 安装与协议验证的范围见 [测试报告](TEST_REPORT.md)；本次完整 120 项测试未重新在全部 Linux 发行版运行。QQ / 微信端是否实际提醒，需要部署者按自己的账号与手机设置验收。

## 已知边界

- 首次监听跳过已有历史邮件；仅处理 INBOX。
- 轮询与客户端通知存在延迟，不保证绝对实时。
- SMTP 发送结果不明时重试，极少数情况可能重复。
- 完整转发需要符合学校规定并单独授权，默认大小限制 20 MiB。
- 面板使用单个 Gunicorn worker；不支持直接组成多进程集群。
- 本项目为个人工具，不是学校或腾讯官方产品。

代码 MIT 许可，学校及个人标识的权利另见 [标识说明](BRAND_ASSETS.md)。
