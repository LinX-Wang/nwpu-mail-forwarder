# 字体与按钮 · 2026-11-16

参考 [LinX GPT Relay Price Monitor 设计说明](https://github.com/LinX-Wang/gpt-relay-price-monitor/blob/main/docs/DESIGN.md) 和其共享 theme.css、tokens.css。

英文界面使用 Inter，中文使用 Noto Sans SC，KPI、收件/检查时间及日志使用 JetBrains Mono（加载别名 LinX JetBrains Mono）。字体存放在 static/fonts/，fonts.css 以相对路径加载，无字体 CDN、无远程图片请求。保留西工大校徽、产品名称与原始 LinX 彩色矢量署名。

字体来自本机已安装、附带 SIL OFL 1.1 许可的对应字体资源，许可全文随字体打包。Inter 保留 2849 字符；Noto Sans SC 经过子集化，覆盖 7546 字符，包括 GB2312 常用中文和当前界面文字；JetBrains Mono 保留 976 字符。其他文字仍可由系统字体回退显示。开源字体许可不等同于 Logo 的使用许可。

维护者可安装 .[font-build]，执行 scripts/subset-ui-fonts.py --source-dir 字体源目录 重建字库。源目录包含三个对应 .woff2 及 *-OFL.txt。构建工具不属于生产运行依赖。

按钮共用统一字重、图标间距、8px 圆角和约 40px 高度。主操作使用青绿色，次要按钮使用轻边框和浅蓝悬停，Ghost 保持低调，危险按钮采用浅红背景与深红文字。禁用时使用明确灰色外观，不执行悬停变色；保留键盘焦点、按下及 Loading 状态。主按钮使用参考青绿色的较深色阶，保证白色文字对比度。

改动仅涉及 CSS、字体资源、模板中的图标/字体样式引用、打包声明及验证文档。未修改后端接口、邮箱连接、发送、去重、授权或数据库。

验证：109 项自动化测试通过；浏览器覆盖 1920×1080、1440×900、1366×768 与两种手机宽度，确认本地字体加载、按钮状态和原有操作，控制台错误为 0。截图使用隔离测试样本，线上读取真实状态。
