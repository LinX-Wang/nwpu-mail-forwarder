# 2026-11-16 控制台改版验收

- Python 自动化测试：111 项通过。
- 浏览器自动化检查：36 项通过，控制台错误 0。
- 1920×1080、1440×900、1366×768、390×844、320×740：四页、日间与夜间主题均无横向溢出。
- 三种桌面尺寸：四页整页不滚动、页脚同位；较长内容内部滚动，保存和测试操作保留。
- 概览与日志统计卡片居中，概览两块列表等高，转发设置填满可用高度。
- 当前学号来自有效登录会话；退出在右上角；侧栏底部简化为服务、账号、LinX 出品。
- 官方教程默认折叠，展开后每份 4 个步骤；桌面展开不改变页脚位置，手机无横向溢出。
- 热启动页面加载：字体、CSS、脚本 transferSize = 0，正确内容指纹静态资源长期缓存。敏感页面/API 仍为 no-store。
- 既有启停、测试提交、配置保存、权限校验、日志搜索筛选复制与清空确认全部通过。
- 公网 HTTPS 登录、四页及版本化静态资源正常；学校 IMAP 已连接，监听运行，失败和待重试均为 0。
- 只重启 web 服务，worker PID 在部署前后保持不变。未发送真实测试邮件，未修改邮箱转发核心或数据库结构。

截图使用隔离 Flask 运行环境与合成邮件数据；公网验证只读取真实状态，不输出邮件内容或凭据。

## 修改文件

- src/nwpu_forwarder/web.py：有效会话账号展示，静态资源指纹与缓存；认证与业务 API 语义保留。
- templates/base.html、login.html、settings.html、logs.html、新增 credential_tutorial.html：品牌、统一框架、主题入口、右上角退出、侧栏与官方折叠教程。
- static/app.css、app.js、新增 theme.js、fonts.css、linx-lockup-white.svg：字号、居中、单屏布局、主题、日志统计、状态更新优化、带版本字体 URL。
- scripts/verify-ui.py、subset-ui-fonts.py：浏览器验证及构建后的字体指纹更新。
- tests/test_account_login.py、test_public_panel.py、新增 test_static_cache.py：账号展示、子路径资源、公开静态缓存与敏感数据不缓存。
- docs/CLIENT_CREDENTIALS.md、UI_THEME_AND_LAYOUT.md、UI_ACCEPTANCE_20261116.md：教程、改动说明与验收报告。

所有资源本地部署，无新增 CDN 或前端框架依赖；未向 GitHub 发布。
