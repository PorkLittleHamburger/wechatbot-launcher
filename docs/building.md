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

`scripts/desktop_env.ps1` 为构建进程配置工具目录和缓存。脚本仍有开发机目录探测：存在预设的 D 盘工具目录时可能选用它们，否则构建/缓存目录回退到仓库 `build/`。这些目录不是项目要求。其他机器应预先显式配置下列变量，并检查 `Get-Command python,node,npm.cmd,cargo,link.exe,rc.exe` 的解析结果；脚本还会优先加入存在的 `D:\nodejs`，存在该目录时需检查 Node 是否符合预期。

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

该脚本的默认目录和代理来自原开发环境，不能作为通用配置。请传入自己的目录；无需代理时明确传入 `-Proxy ''`。常规 Visual Studio Build Tools 用户可在已加载 MSVC 环境的 PowerShell 中构建，无需运行该可选脚本。

例如在仓库根目录显式指定输出位置（工具链路径另按本机安装配置）：

```powershell
$env:WECHAT_BUILD_ROOT = Join-Path $PWD 'build'
$env:WECHAT_BUILD_CACHE = Join-Path $PWD 'build/cache'
$env:CARGO_TARGET_DIR = Join-Path $PWD 'build/cargo-target'
```

## 准备 Python 环境

示例假定 Bot 仓库检出在相邻 `wechatbot` 目录；其他布局请替换 `$runtime` 为实际路径。

```powershell
$runtime = (Resolve-Path ../wechatbot).Path
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
$env:WECHAT_RUNTIME_SOURCE = (Resolve-Path ../wechatbot).Path
pwsh -NoProfile -ExecutionPolicy Bypass -File scripts/build_desktop.ps1
```

脚本依次执行启动器 Python 回归、将启动器模块合并到外部运行时、前端构建、Rust 测试及 Tauri/NSIS 打包。`-SkipTests` 和 `-SkipRuntime` 仅用于对应内容已验证且未变化的增量构建。可用 `-RuntimeSource` 和 `-Python` 显式指定路径。

0.8.7 使用 Bot `v0.8.6` 提交 `23f46eb2d2b4c30c6f4cdf7c3098fb5c915e583f`，CI 固定检出该提交，保留路径和临时快照权限修复，并包含用户资料缓存与补全修复。集成 Bot 修复后必须完整组装运行时，不能用旧缓存替代新源码；只允许复用锁定版本的 Python 解释器与依赖。

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

## 人工发布检查

仅在用户明确要求发布时执行；普通代码或文档推送不创建 Release。

1. 对齐 `pyproject.toml`、两个 Python `__init__.py`、前端 package/lock、Cargo package/lock、Tauri 配置、Windows 清单和构建/验收脚本中的版本与文件名；核对 README、使用说明及 [交接文档](HANDOFF.md)。
2. 核实外部 Bot 源码 HEAD、工作树及 CI 固定提交。`WECHAT_PYTHON_RUNTIME` 可指向与外部依赖锁完全匹配的解释器及依赖目录，只复用 Python，不跳过源码重新组装。完整发布不得使用 `-SkipTests` 或 `-SkipRuntime`。
3. 完成完整构建和必要的隔离验收。发布前另为运行包 ZIP 生成 `.zip.sha256`；当前构建脚本只自动写安装器 `.exe.sha256`。校验文件内容为 `<小写 SHA-256>  <文件名>`。
4. 将验证后的源码推送，确认该提交的 Windows CI 成功；记录尚未完成的手动验证。创建指向该提交的版本标签，上传安装器、运行包及两份校验文件，核对服务端大小/哈希。
5. 正式发布后核对标签提交、非草稿/非预发布和 Latest 状态，重新下载校验附件，并以旧版更新模块检查能否发现新版。不要以 Git 推送或本地构建成功代替远端验证。

`wechat-onebot-runtime-<启动器版本>-windows-x64.zip` 是启动器组装的兼容运行包，不表示独立 Bot 已发布同号版本。Bot 官方便携包名为 `wechat-onebot-<Bot版本>-windows-x64.zip`，由 Bot 仓库发布。
