# WeChat OneBot Launcher

Windows x64 桌面启动器，用于管理个人微信 OneBot v11 网关的多个独立实例。使用 Tauri 2、React/TypeScript 和 Python 管理服务，提供实例启停、运行版本下载/导入、升级回滚、备份恢复、日志、托盘、自启和数据目录切换。

本仓库仅维护启动器。网关、微信桥接、原生组件和对应依赖锁位于独立的 [wechatbot 仓库](https://github.com/PorkLittleHamburger/wechatbot)，通过外部运行时源参与构建。浏览器管理入口已移除；实例 WebUI 在桌面子窗口中打开。

## 安装与使用

从 [最新正式发布](https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/latest) 下载 `WeChat-OneBot-Launcher_<版本>_x64-setup.exe`。安装包包含后台 Python、依赖和离线前置组件，使用者无需安装开发工具。主要验收环境为 Windows 11 x64；其他 Windows 版本需单独验证。

1. 安装并启动，按系统提示授予管理员权限。
2. 新工作空间的版本列表为空；先在“版本管理”下载 Bot 运行版本或导入兼容 ZIP，再创建 Helper 实例。桌面不提供 Mock 模式选择。
3. 启动实例并打开 WebUI。微信构建兼容性、账号登录和桥接操作以所选 Bot 版本的说明为准。

默认数据目录为 `%LOCALAPPDATA%\WeChatOneBotLauncher`，与安装目录分离；可在设置中更改。切换目录不会自动迁移已有数据。关闭窗口默认隐藏到托盘，托盘“退出”会停止全部实例并退出。

## 更新与自启

“设置 → 启动器更新”更新桌面程序；“版本管理”独立读取 [Bot 发布页](https://github.com/PorkLittleHamburger/wechatbot/releases)，更新实例运行版本。两者分别操作；更新启动器不会自动安装 Bot 或切换现有实例版本。

更新启动器前停止全部实例，再选择“自动更新并重启”。0.8.5 起内置助手静默覆盖原安装目录并携带原数据目录重启；更早版本首次升级仍可能显示安装向导。直接双击安装器按普通安装流程运行。

自启默认关闭，启用后可在 Windows 任务管理器“启动应用”中管理。0.8.7 修复旧入口的 `\\?\` 路径兼容问题，并迁移匹配当前安装的旧命令；在 Windows 中禁用或删除入口的选择会保留。入口日志在 `%LOCALAPPDATA%\WeChatOneBotLauncherShell\startup.log`。注册成功不等于真实登录已验证，详细步骤见[桌面使用说明](docs/desktop-launcher.md)。

## 开发

从仓库根目录执行。示例假定网关检出在相邻 `wechatbot` 目录；其他布局请修改 `$runtime`。开发和完整构建需要 Python 3.13 x64，及[构建指南](docs/building.md)中的 Windows 工具链。

```powershell
$runtime = (Resolve-Path ../wechatbot).Path
if (Test-Path (Join-Path $runtime 'bot/requirements-runtime.lock')) {
    $runtime = Join-Path $runtime 'bot'
}
$env:WECHAT_RUNTIME_SOURCE = $runtime
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r (Join-Path $runtime 'requirements-runtime.lock')
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m pytest -q tests
pwsh -File scripts/build_desktop.ps1
```

构建会合并外部运行时、运行测试并生成 NSIS 安装器和运行包到 `dist/`。源码包含 `desktop/src/`（界面）、`desktop/src-tauri/`（原生壳）、`python/wechat_onebot/launcher/`（管理服务）、`scripts/`、`tests/` 和 `docs/`；生成资源与用户数据不提交。

## 文档导航

- [桌面使用与故障排查](docs/desktop-launcher.md)
- [工具链、构建与发布](docs/building.md)
- [验收步骤与历史验证记录](docs/desktop-verification.md)
- [原生壳架构和 IPC 契约](desktop/src-tauri/README.md)
- [项目交接、开发沿革与待验证事项](docs/HANDOFF.md)（接手开发必读）
- [贡献与 AI 工作要求](AGENTS.md)

发布记录描述对应版本的验证范围，不代表所有机器均已验证。安装器目前未配置代码签名；下载校验依赖 GitHub HTTPS 发布的 SHA-256 文件。
