# WeChat OneBot Launcher 桌面版

桌面版使用 Tauri 2、React/TypeScript 和独立 Python 管理服务。NSIS EXE 安装包内置完整 Python、依赖、一个经过校验的运行时版本、WebView2 离线安装组件及微软签名的 Visual C++ x64 离线运行库。安装后无需系统 Python、PowerShell 操作或浏览器。Visual C++ 组件以固定版本、下载地址和 SHA-256 校验；只在用户运行安装器时安装，不在构建机器上安装。

## 安装和运行

运行 `WeChat-OneBot-Launcher_0.8.0_x64-setup.exe`，选择安装目录和快捷方式。默认程序目录为 Program Files。手动启动时统一申请管理员权限，以支持 Helper 桥接；本轮验收使用 Mock，不加载真实微信桥接。

工作空间默认为 `%LOCALAPPDATA%\WeChatOneBotLauncher`，独立于安装目录。首次运行创建新空间，不扫描或导入旧项目与便携包。实例在自己的 WebUI 中完成首次 Token 验证、管理密码设置、账号选择和 OneBot 配置；“打开 WebUI”在桌面应用内打开独立子窗口。

默认关闭主窗口会隐藏到托盘。托盘“显示窗口”恢复主界面，“退出”正常停止所有实例。设置中可以关闭“关闭到托盘”。重复运行 EXE 会唤起已有窗口。

开机自启、启动时隐藏到托盘、恢复上次运行实例均默认关闭。启用开机自启使用当前用户登录触发且以最高权限运行的独立计划任务。恢复实例仅恢复管理服务和配置的网络端点，微信桥接仍手动启动。桌面版不修改旧单服务或 AstrBot Launcher 的自启设置。

卸载默认保留工作空间。只有明确勾选“同时删除运行数据”才清理实例、版本、消息和备份；卸载会移除本产品的自启入口。

## 实例、版本和备份

- 创建实例时选择已安装版本、Mock/Helper 模式和 WebUI 端口。实例数据、管理密码、Cookie、媒体、日志和网络配置相互独立。
- 版本管理读取本项目 GitHub Releases。旧便携包没有桌面兼容清单时显示不兼容。可以下载兼容运行时 ZIP，或通过文件选择器导入本地版本包。
- 版本包携带 `VERSION_MANIFEST.json`，声明格式版本、启动器协议、Windows x64 平台、程序版本和所有文件的 SHA-256。先完整校验再安装；相同版本号不同内容不能覆盖。
- 每个版本拥有独立 Python、代码、依赖和 helper。同版本实例共享程序文件；启动时禁止写入程序目录。被实例或归档引用的版本不能卸载。
- 升级要求实例停止；先生成备份，再切换版本并启动管理服务检查。检查后保持停止；失败恢复原版本及数据。升级日志文件允许记录失败原因。升级中断后下次启动根据事务记录恢复。
- 备份只允许停止的实例，完成前为临时文件；取消请求仍等待文件操作结束才释放互斥锁。
- 恢复创建新实例，保留密码及账号数据，重新分配 WebUI 端口。所需版本未安装时先安装版本。恢复完成后保持停止，启动前复查 WebUI 与 OneBot 监听地址冲突。
- 归档保留完整实例，可从备份恢复页创建恢复实例，不覆盖原有实例。

版本管理只下载已发布版本，不发布 Release、不自动切换实例版本，也不自动更新桌面程序。

## 管理通信与进程归属

桌面主窗口通过受限的 Tauri 命令调用 Rust 外壳，Rust 与 Python 管理服务之间采用标准输入/输出 JSON 行协议。每个请求带 ID，响应包含 `result` 或 `error`，进度通过独立事件传递。标准输出只用于协议帧，诊断写入单独日志。

实例 WebUI 子窗口只加载启动器确认的本机实例地址，不拥有桌面原生接口权限。实例登录 Cookie 按实例 ID 区分。主窗口关闭到托盘时保持进程；明确退出先请求管理服务关闭。Windows Job 对象覆盖管理服务及其子进程，桌面异常结束时回收其进程树，不把已有微信进程纳入 Job。

实例数据目录锁覆盖整个服务进程生命周期。Helper 另持有按微信 PID 的排他锁，避免两个本项目实例同时桥接同一 PID。启动器不使用磁盘旧 PID 结束不属于自己的进程。

## 构建环境

推荐在 PowerShell 7 中运行 `launcher/scripts/build_desktop.ps1`。脚本按进程设置环境变量，不改动全局用户或系统环境。

| 内容 | 位置 |
| --- | --- |
| PowerShell | `D:\Tool\PowerShell-7.6.6-win-x64\pwsh.exe` |
| Rustup | `D:\Tool\Rust\rustup` |
| Cargo 与 Rust 缓存 | `D:\Tool\Rust\cargo` |
| Microsoft C++ 编译器 | `D:\Tool\MicrosoftVisualStudio\BuildTools\VC\Tools\MSVC` |
| Windows SDK | `D:\Tool\MicrosoftVisualStudio\BuildTools\Windows Kits\10` |
| 复用 Node.js | 本机现有 `D:\nodejs`，CI 使用其已配置的 Node |
| 下载、npm、pip 缓存 | `D:\Tool\Cache\WeChatOneBotLauncher` |
| Cargo 和临时打包输出 | `D:\Tool\Build\WeChatOneBotLauncher` |
| 最终安装器、运行时 ZIP、SHA-256 | 项目 `dist` |

`launcher/scripts/setup_portable_msvc.ps1` 仅下载并校验微软官方工具负载，解压到上述目录，不执行系统安装或写注册表。生成的 `msvc-env.ps1` 由 `launcher/scripts/desktop_env.ps1` 加载。已有 Visual Studio 构建环境也可复用。

Python 使用 `bot/requirements-runtime.lock`，前端使用 `package-lock.json`，Rust 使用 `Cargo.lock` 固定依赖。构建脚本先运行 Python 测试、生成并校验离线运行时，再执行前端和 Tauri/NSIS 构建。`-SkipTests` 和 `-SkipRuntime` 仅用于已验证内容未变化时的本地增量编译。

GitHub Windows CI 构建安装器并保存 Actions 工件，不创建或发布 GitHub Release。默认安装包未签名；发布动作仍需用户明确确认。

本次实际执行的检查及尚未执行的隔离安装验收见 [验证记录](desktop-verification.md)。运行 `launcher/scripts/test_installer_sandbox.ps1` 可生成断网 Sandbox 验收配置；该脚本默认只生成配置，不启动 Sandbox，也不在当前电脑安装或卸载软件。
