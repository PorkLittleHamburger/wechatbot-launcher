# 桌面安装器构建

以下命令均从仓库根目录执行。桌面使用者只需安装 EXE，无需安装本页列出的开发工具。

## 构建依赖

- Windows x64 与 PowerShell 7。
- Python 3.13 x64，用于测试及运行时组装。
- Node.js 24 与 npm。
- Rust stable 的 MSVC 工具链及 Cargo。
- MSVC C++ 编译工具和 Windows SDK，包含链接器、系统库及 `rc.exe`。
- 首次安装依赖、下载 Python、WebView2 及 Visual C++ 运行库需要网络。

## 配置工具目录

`launcher/scripts/desktop_env.ps1` 为构建进程配置工具目录和缓存，当前脚本带有预设目录。首次在其他构建机器使用时，先根据实际安装位置调整这些设置，不能假定示例机器的盘符存在：

| 设置 | 用途 |
| --- | --- |
| RUSTUP_HOME、CARGO_HOME | Rust 工具链及 Cargo 安装目录，应与现有工具链一致 |
| CARGO_TARGET_DIR | Rust 编译及 Tauri 打包输出 |
| WECHAT_BUILD_ROOT | 临时运行时组装目录，以及可选的 `msvc-env.ps1` |
| WECHAT_BUILD_CACHE | Python 运行时和构建组件下载缓存 |
| PIP_CACHE_DIR、npm_config_cache | pip 与 npm 缓存 |
| PATH | 可用的 Node.js、Cargo、MSVC 和 Windows SDK 工具 |

环境配置按进程生效。已有 Visual Studio C++ 开发环境可以复用，确保环境脚本没有覆盖它所需的路径。

`launcher/scripts/setup_portable_msvc.ps1` 是可选的微软工具负载提取脚本，支持 `InstallRoot`、`CacheRoot`、`BuildRoot`、`RustRoot` 和 `Proxy` 参数。使用前检查这些参数及下载配置；不需要代理时将 `Proxy` 设为空字符串。它生成的 `msvc-env.ps1` 应放在构建脚本实际使用的 `WECHAT_BUILD_ROOT` 下。

## 准备 Python 环境

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r bot/requirements-runtime.lock
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"
```

Python 运行依赖使用 `bot/requirements-runtime.lock`，前端使用 `launcher/desktop/package-lock.json`，Rust 使用 `launcher/desktop/src-tauri/Cargo.lock`。

## 生成 EXE

完成工具目录配置后运行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File launcher/scripts/build_desktop.ps1
```

脚本依次执行 Python 回归、离线运行时组装和校验、前端构建、Rust 测试及 Tauri/NSIS 打包。Rust 测试包含使用内置 Python 的实际管理服务通信检查。`-SkipTests` 和 `-SkipRuntime` 仅用于对应内容已验证且未变化的增量构建。

产物输出到仓库根目录 `dist`：

- `WeChat-OneBot-Launcher_<版本>_x64-setup.exe`。
- 相邻的 `.exe.sha256` 文件。
- `wechat-onebot-runtime-<版本>-windows-x64.zip`，可用于版本导入，包含兼容及文件校验清单。

安装器包含运行时、WebView2 离线安装组件和经签名及哈希校验的 Visual C++ x64 运行库。默认构建的应用安装包未做代码签名。

## 校验与 CI

使用 PowerShell 检查安装器摘要，并与相邻校验文件中的值比较：

```powershell
Get-ChildItem dist/*setup.exe | Get-FileHash -Algorithm SHA256
```

[Windows CI](../../.github/workflows/desktop.yml) 执行同一构建入口，成功后保存安装器、SHA-256 和运行时 ZIP 为 Actions 工件。构建流程不自动创建 GitHub Release。

构建成功仅说明编译及自动化检查通过。分发前还应按 [验收指南](desktop-verification.md) 在隔离 Windows 环境检查安装、覆盖升级、卸载、自启和数据保留。
