# 启动器开发说明

本目录包含 WeChat OneBot Launcher 的桌面工程和管理服务。使用桌面应用请阅读 [使用说明](docs/desktop-launcher.md)，构建安装器请阅读 [构建说明](docs/building.md)。

## 目录

| 路径 | 用途 |
| --- | --- |
| desktop/src | React 页面、样式与桌面 API 调用 |
| desktop/src-tauri | Rust 桌面壳、托盘、系统集成、进程管理和 NSIS 安装器 |
| python/wechat_onebot/launcher | Python 实例、版本、升级、备份恢复与通信实现 |
| webui/launcher.html | 浏览器多实例管理页 |
| scripts | 构建、运行时组装、工具配置与隔离安装验收脚本 |
| tests | 管理服务、版本、通信、升级恢复及打包测试 |
| docs | 使用、构建、设计和验收说明 |
| launcher.cmd | 浏览器管理入口 |

## Python 开发

以下命令均从仓库根目录执行。Python 管理服务依赖相邻的 `bot` 网关代码；保留完整仓库目录结构后安装：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r bot/requirements-runtime.lock
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"
.venv/Scripts/python.exe -m pytest -q bot/tests launcher/tests -c bot/pyproject.toml
```

仅运行启动器测试可执行 `.venv/Scripts/python.exe -m pytest -q launcher`。浏览器管理入口为 `launcher/launcher.cmd`，`bot/launcher.cmd` 转发到同一入口。

后端模块名为 `wechat_onebot.launcher`。源码安装通过 `bot/src/wechat_onebot/launcher/__init__.py` 定位本目录实现；wheel 和离线运行时直接包含管理模块。组装运行时将 `bot` 和本目录代码合并到 `src/wechat_onebot/launcher`，浏览器面板位于 `webui/launcher.html`。

## 桌面工程

React 前端通过 Tauri 命令调用 Rust 桌面壳。Rust 管理 Python 服务进程，通过标准输入/输出 JSON 行协议发送请求并接收响应及进度事件。实例 WebUI 使用独立子窗口，不授予桌面原生接口权限。

前端依赖使用 `desktop/package-lock.json`，Rust 依赖使用 `desktop/src-tauri/Cargo.lock`，运行时 Python 依赖使用 `bot/requirements-runtime.lock`。生成的前端资源、内置运行时和编译输出不纳入 Git。

桌面工作空间默认为 `%LOCALAPPDATA%\WeChatOneBotLauncher`，构建产物位于仓库根目录 `dist`。

更多资料：[桌面壳与 IPC](desktop/src-tauri/README.md)、[浏览器管理入口](docs/multi-instance-launcher.md)、[验收指南](docs/desktop-verification.md)。
