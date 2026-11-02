# 启动器开发目录

WeChat OneBot Launcher 的全部开发文件集中在本目录。桌面应用采用 Tauri 2、React/TypeScript 和 Python 管理服务，通过标准输入/输出 JSON 消息通信，安装程序采用 NSIS。

| 路径 | 用途 |
| --- | --- |
| desktop/src | React 页面、样式和桌面接口 |
| desktop/src-tauri | Rust 桌面壳、托盘、进程管理、系统集成和安装器 |
| python/wechat_onebot/launcher | Python 多实例、版本、升级、备份恢复和通信实现 |
| webui/launcher.html | 源码开发及兼容使用的浏览器管理页 |
| scripts | 桌面构建、运行时组装、工具环境与隔离安装验收脚本 |
| tests | 启动器管理服务、通信、版本和打包测试 |
| docs | 设计、使用方法和验证记录 |
| launcher.cmd | 浏览器管理入口；桌面用户使用 EXE 安装程序 |

## 开发与验证

以下命令均从仓库根目录执行，复用根目录 `.venv`。机器人协议和原生桥接源码仍在 `bot`，启动器后端继续使用 `wechat_onebot.launcher` 模块名。`bot/src/wechat_onebot/launcher/__init__.py` 仅将源码导入转到本目录，后端实现以本目录为准。Python 安装配置会将这些模块打入同一个 `wechat-onebot` wheel。

```powershell
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"
.venv/Scripts/python.exe -m pytest -q bot/tests launcher/tests -c bot/pyproject.toml
```

只运行启动器测试可执行 `.venv/Scripts/python.exe -m pytest -q launcher`。浏览器管理入口可以使用 `launcher/launcher.cmd` 或 `bot/launcher.cmd`，两者使用同一个实现。公共区旧入口与缓存已清理；历史构建日志归入本目录的 `.development/legacy-public-area/data`，不纳入 Git。

## 构建桌面安装程序

```powershell
& 'D:/Tool/PowerShell-7.6.6-win-x64/pwsh.exe' -File launcher/scripts/build_desktop.ps1
```

构建脚本运行 Python 测试、组装内置 Python 和离线依赖、安装锁定的前端依赖、构建前端、运行 Rust 测试，最后生成 NSIS EXE 和 SHA-256。工具与缓存通过 `launcher/scripts/desktop_env.ps1` 按进程设置，优先使用 `D:\Tool`，复用既有 `D:\nodejs`，不修改全局环境。

构建产物仍输出到仓库根目录 `dist`：

- `WeChat-OneBot-Launcher_0.8.0_x64-setup.exe` 与相邻 `.sha256`。
- `wechat-onebot-runtime-0.8.0-windows-x64.zip` 兼容版本包，包含版本和文件校验清单。
- 已有桌面截图及隔离安装验收输出。

生成的 `desktop/src-tauri/resources/runtime`、前端依赖和编译输出不纳入 Git。打包时将机器人源码和本目录后端合并到原有平面布局 `src/wechat_onebot/launcher`，并将浏览器面板放到 `webui/launcher.html`；最终用户无需了解源码目录拆分。桌面工作空间继续位于 `%LOCALAPPDATA%\WeChatOneBotLauncher`，本机已有配置和数据不随开发目录移动。

详见 [桌面使用及构建说明](docs/desktop-launcher.md)、[多实例设计](docs/multi-instance-launcher.md) 与 [验证记录](docs/desktop-verification.md)。构建和推送代码不会自动发布 GitHub Release。
