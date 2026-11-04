# 桌面安装器构建

以下命令均从启动器仓库根目录执行。桌面使用者只需安装 EXE，无需安装本页列出的开发工具。

## 构建依赖

- Windows x64 与 PowerShell 7。
- Python 3.13 x64，用于测试及运行时组装。
- Node.js 24 与 npm。
- Rust stable 的 MSVC 工具链及 Cargo。
- MSVC C++ 编译工具和 Windows SDK，包含链接器、系统库及 `rc.exe`。
- 外部网关运行时源：[PorkLittleHamburger/wechatbot](https://github.com/PorkLittleHamburger/wechatbot)。
- 首次安装依赖、下载 Python、WebView2 及 Visual C++ 运行库需要网络。

本仓库不提交网关源码、原生 DLL 或运行时依赖锁文件。构建前检出 `wechatbot`，并将 `WECHAT_RUNTIME_SOURCE` 指向该检出目录。脚本同时识别检出根目录和其中的 `bot/` 子目录。

## 配置工具目录

`scripts/desktop_env.ps1` 为构建进程配置工具目录和缓存，当前脚本带有预设目录。首次在其他构建机器使用时，先根据实际安装位置调整这些设置：

| 设置 | 用途 |
| --- | --- |
| RUSTUP_HOME、CARGO_HOME | Rust 工具链及 Cargo 安装目录 |
| CARGO_TARGET_DIR | Rust 编译及 Tauri 打包输出 |
| WECHAT_BUILD_ROOT | 临时运行时组装目录及可选的 `msvc-env.ps1` |
| WECHAT_BUILD_CACHE | Python 运行时和构建组件下载缓存 |
| PIP_CACHE_DIR、npm_config_cache | pip 与 npm 缓存 |
| WECHAT_RUNTIME_SOURCE | `wechatbot` 网关检出目录 |
| PATH | 可用的 Node.js、Cargo、MSVC 和 Windows SDK 工具 |

`scripts/setup_portable_msvc.ps1` 是可选的微软工具负载提取脚本，支持 `InstallRoot`、`CacheRoot`、`BuildRoot`、`RustRoot` 和 `Proxy` 参数。它生成的 `msvc-env.ps1` 应放在构建脚本实际使用的 `WECHAT_BUILD_ROOT` 下。

## 准备 Python 环境

```powershell
$runtime = 'D:\study\project\wechatbot'
if (Test-Path (Join-Path $runtime 'bot/requirements-runtime.lock')) {
    $runtime = Join-Path $runtime 'bot'
}
$env:WECHAT_RUNTIME_SOURCE = $runtime
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r (Join-Path $runtime 'requirements-runtime.lock')
.venv/Scripts/python.exe -m pip install -e '.[dev]'
```

启动器依赖使用本仓库 `pyproject.toml`，网关运行依赖使用外部运行时的 `requirements-runtime.lock`，前端使用 `desktop/package-lock.json`，Rust 使用 `desktop/src-tauri/Cargo.lock`。

## 生成 EXE

```powershell
$env:WECHAT_RUNTIME_SOURCE = 'D:\study\project\wechatbot'
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
```

脚本依次执行启动器 Python 回归、将启动器模块合并到外部运行时、前端构建、Rust 测试及 Tauri/NSIS 打包。`-SkipTests` 和 `-SkipRuntime` 仅用于对应内容已验证且未变化的增量构建。可用 `-RuntimeSource` 和 `-Python` 显式指定路径。

0.8.5 使用 Bot `v0.8.5` 提交 `9ca025f66d21093f20c7968765f4a7fa464e6103`，CI 固定检出该提交，保留路径和临时快照权限修复，并包含用户资料缓存与补全修复。集成 Bot 修复后必须完整组装运行时，不能用旧缓存替代新源码；只允许复用锁定版本的 Python 解释器与依赖。

无论新下载还是复用缓存 Python，组装都会执行隔离解释器的依赖导入、`vendor/helper/native/bridge_watchdog.py --help` 和 FileStore 扩展本地路径初始化检查；失败时输出退出码及标准错误并阻止产物发布。这些检查不启动微信桥接。组装结果用于启动器后台和单独发布的运行时 ZIP，不在首次启动时自动安装到版本库。

产物输出到仓库根目录 `dist`：

- `WeChat-OneBot-Launcher_<版本>_x64-setup.exe`
- 相邻的 `.exe.sha256` 文件
- `wechat-onebot-runtime-<版本>-windows-x64.zip`

安装器包含网关运行时、WebView2 离线组件和经签名及哈希校验的 Visual C++ x64 运行库。默认安装包未做代码签名。

Cargo 的 `build.rs` 使用同一 Rust 工具链编译 `src/startup_helper.rs`，生成具有显式 `asInvoker` 清单的无控制台程序；Tauri 将其作为 `startup/wechat-onebot-startup.exe` 打包。该生成文件与运行时资源一样不提交。变更自启入口时应校验程序清单与最终安装器中的资源路径。

## 校验与 CI

```powershell
Get-ChildItem dist/*setup.exe | Get-FileHash -Algorithm SHA256
```

[Windows CI](../.github/workflows/desktop.yml) 会检出启动器和 `wechatbot` 运行时源，执行同一构建入口并保存安装器、SHA-256 与运行时 ZIP。构建流程不自动创建 GitHub Release。

构建成功仅说明编译及自动化检查通过。分发前还应按 [验收指南](desktop-verification.md) 在隔离 Windows 环境检查安装、覆盖升级、卸载、自启和数据保留。
