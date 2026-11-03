# WeChat OneBot Launcher

Windows x64 桌面启动器，用于管理个人微信 OneBot v11 网关的多个独立实例。桌面应用采用 Tauri 2、React/TypeScript 与 Python 管理服务，提供实例启停、版本选择、升级、备份恢复、日志、托盘、开机自启和可切换的数据目录。

从 0.8.3 起支持“设置 → 启动器更新”：检查正式新版、查看说明、下载并校验 Windows 安装器，再手动选择“安装并退出”。安装前须停止所有实例；安装助手等待桌面退出后打开安装器，保留工作空间。本体更新与“版本管理”中的网关升级分别操作。

本仓库只包含启动器工程。OneBot 网关、微信桥接和原生组件位于独立仓库 [PorkLittleHamburger/wechatbot](https://github.com/PorkLittleHamburger/wechatbot)，不在此仓库中重复提交。

## 目录

| 路径 | 用途 |
| --- | --- |
| `desktop/src` | React 页面、样式与桌面 API 调用 |
| `desktop/src-tauri` | Rust 桌面壳、托盘、系统集成、进程管理和 NSIS 安装器 |
| `python/wechat_onebot/launcher` | Python 实例、版本、升级、备份恢复与通信实现 |
| `scripts` | 运行时组装、桌面构建、工具配置与隔离验收脚本 |
| `tests` | 桌面后端、版本、通信、升级恢复及打包测试 |
| `docs` | 使用、构建、设计和验收说明 |

## 开发环境

源码测试和安装器构建需要一个外部网关运行时源。将 `WECHAT_RUNTIME_SOURCE` 指向 `wechatbot` 仓库检出目录；脚本同时支持检出根目录和其中的 `bot/` 子目录。仓库不复制该运行时源。

```powershell
$runtime = 'D:\study\project\wechatbot'
if (Test-Path (Join-Path $runtime 'bot/requirements-runtime.lock')) {
    $runtime = Join-Path $runtime 'bot'
}
$env:WECHAT_RUNTIME_SOURCE = $runtime
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r (Join-Path $runtime 'requirements-runtime.lock')
.venv/Scripts/python.exe -m pip install -e '.[dev]'
.venv/Scripts/python.exe -m pytest -q tests
```

浏览器多实例管理入口已移除；实例管理全部由桌面应用提供，新建实例固定使用 Helper 微信桥接模式。桌面使用者应安装构建生成的 EXE，无需源码环境。版本发布只下载兼容运行时包，本仓库不包含网关源码或原生 DLL。

## 桌面工程

React 前端通过 Tauri 命令调用 Rust 桌面壳。Rust 管理 Python 服务进程，通过标准输入/输出 JSON 行协议发送请求并接收响应及进度事件。实例 WebUI 使用独立子窗口，不授予桌面原生接口权限。

前端依赖使用 `desktop/package-lock.json`，Rust 依赖使用 `desktop/src-tauri/Cargo.lock`。生成的前端资源、内置运行时和编译输出不纳入 Git。

桌面工作空间默认为 `%LOCALAPPDATA%\WeChatOneBotLauncher`，构建产物位于仓库根目录 `dist`。

## 0.8.4 更新与升级

首次启动不再自动安装任何 Bot 运行版本，新工作空间的版本列表为空。请先在“版本管理”中下载或导入 ZIP，再创建实例。安装包保留启动器后台所需的 Python 和模块，但不会将它们自动导入 Bot 版本库；更新启动器也不会自动补装版本。

本版修复本地扩展路径被 helper 误判为网络路径的问题：启动器交接已验证等价的普通盘符路径，Bot 运行包兼容文件/语音的扩展本地路径；界面中的版本路径、数据目录和页脚显示可读形式。旧绝对数据路径的备份、恢复与升级回滚兼容保留，特殊目录名不会被有损改写。

旧版用户先通过托盘退出，再覆盖安装 0.8.4 并保留数据目录；也可通过“设置 → 启动器更新”下载校验后手动触发安装。如需 Bot 修复，请单独下载或导入 0.8.4 运行包，停止实例后选择升级。不要手动覆盖旧版本目录；更新启动器不会自动切换实例版本或改写旧版本 ACL。

更多资料：[桌面使用](docs/desktop-launcher.md)、[构建说明](docs/building.md)、[桌面壳与 IPC](desktop/src-tauri/README.md)、[验收指南](docs/desktop-verification.md)。

当前源码的版本管理直接读取 [Bot 发布页](https://github.com/PorkLittleHamburger/wechatbot/releases)，启动器本体更新仍读取 Launcher 发布页。已发布的启动器 0.8.4 及更早版本无法自动发现仅在 Bot 仓库发布的新版本；可下载 `wechat-onebot-<版本>-windows-x64.zip` 后手动导入，再停止实例并升级。此发布源修复尚需后续启动器版本发布才会进入现有安装。
