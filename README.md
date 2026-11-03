# WeChat OneBot Launcher

Windows x64 桌面启动器，用于管理个人微信 OneBot v11 网关的多个独立实例。桌面应用采用 Tauri 2、React/TypeScript 与 Python 管理服务，提供实例启停、版本选择、升级、备份恢复、日志、托盘、开机自启和可切换的数据目录。

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

## 0.8.2 更新与升级

本版内置 Bot 0.8.2，修复嵌入式 Python 下微信桥接退出清理守护无法导入 `run_selftest` 的问题。运行时组装会实际执行包内 Python 的 watchdog `--help` 检查，包含使用缓存 Python 的构建。守护启动失败时保留标准错误和退出码，并分别提示提前退出或就绪超时及日志绝对路径。

旧版用户先通过托盘正常退出启动器，再覆盖安装 0.8.2，保留原数据目录。覆盖安装会新增 0.8.2 运行版本，已有实例仍使用原版本；停止实例后，在实例升级操作中选择 0.8.2，启动器会备份并执行升级检查。确认成功后重新启动实例；微信桥接仍需手动启动。不要手动覆盖正在被实例引用的 0.8.1 版本目录。

更多资料：[桌面使用](docs/desktop-launcher.md)、[构建说明](docs/building.md)、[桌面壳与 IPC](desktop/src-tauri/README.md)、[验收指南](docs/desktop-verification.md)。
