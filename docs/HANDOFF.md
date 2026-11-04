# 启动器项目交接

维护日期：2026-11-04。新接手的 AI 必须先读本页及 [AGENTS.md](../AGENTS.md)，再检查当前代码、工作树、远端与 Release。本页是交接快照，不代表目标机器的当前部署状态。

## 范围与发布基线

- 仓库为 `PorkLittleHamburger/wechatbot-launcher`，只维护桌面启动器。旧对话中的共享仓库或 `WechatBot/launcher` 路径已不适合作为定位依据；先用 `git rev-parse --show-toplevel` 确认。
- 网关、微信桥接、原生 DLL 及对应测试位于独立 `PorkLittleHamburger/wechatbot` 仓库，通过 `WECHAT_RUNTIME_SOURCE` 参与构建，不复制到启动器源码库。
- 当前发布线：[v0.8.8](https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.8.8)，源码以该标签指向的提交为准；正式发布状态以 GitHub Latest 为准，后续文档提交不改写发布标签。
- 该发布使用 Bot v0.8.6 源码 `23f46eb2d2b4c30c6f4cdf7c3098fb5c915e583f`。[CI](../.github/workflows/desktop.yml) 固定检出它，本地构建不会自动检出或核对提交，发布前须检查运行时源 HEAD 与工作树。
- 源码 HEAD、正式发布和实际安装是三种状态，必须分别确认。更新验证后提交推送；只有用户明确要求时才发布新 Release。

## 接手顺序与文档分工

先运行 `git status --short`、`git remote -v`、`git log -5 --oneline`，再通过 `gh release view`、`gh run list` 核实发布与 CI。不要把旧对话的版本或本机路径当作当前事实。

| 文档 | 本次审查范围与持续职责 |
| --- | --- |
| [README](../README.md) | 远端首页、功能与安装入口，使用通用示例，避免旧升级建议 |
| [桌面使用](desktop-launcher.md) | 默认值、版本来源、数据目录、自启、更新、备份和故障排查 |
| [构建指南](building.md) | 工具链、外部运行时、环境覆盖、产物、CI 与人工发布 |
| [验收指南](desktop-verification.md) | 可重复步骤、隔离条件、历史证据和未验证项 |
| [原生壳说明](../desktop/src-tauri/README.md) | 生命周期、IPC、窗口权限、自启和安装器契约 |
| [AGENTS](../AGENTS.md) | 贡献规则、必读交接及文档维护要求 |

## 代码入口与关键约束

| 入口 | 职责 |
| --- | --- |
| `desktop/src/App.tsx`、`components/`、`useWorkspace.ts`、`useInstanceLogs.ts` | 页面协调、独立功能页、共享轮询和日志生命周期 |
| `api.ts`、`api-contract.ts`、`operation-feedback.ts` | RPC DTO 边界校验、并发反馈与终态确认 |
| `desktop/src-tauri/src/main.rs` | RPC 权限、托盘、设置事务、目录切换和更新交接 |
| `ipc.rs`、`windows.rs` | 隐藏管理进程、Job Object、JSON 行通信及退出 |
| `autostart.rs`、`startup_entry.rs`、`scheduler.rs`、`startup_helper.rs` | 自启事务、注册表快照、系统适配与普通权限入口 |
| `snapshot_guard.rs`、`paths.rs` | 分区诊断下的原生操作前置检查及路径身份 |
| `python/wechat_onebot/launcher/desktop_manager.py`、`manager.py` | 应用协调与实例生命周期、每实例锁 |
| `operations.py`、`catalog.py`、`instance_storage.py`、`restore_service.py`、`async_utils.py` | 操作路由、故障隔离目录读取、原子创建、恢复/回滚及取消安全线程执行 |
| `versions.py`、`self_update.py` | 分离的 Bot 与 Launcher 发布来源、下载及校验 |
| `scripts/`、`desktop/src-tauri/windows/` | 运行时合并、构建、清单、安装、更新和卸载 |

必须保留的行为：

- 浏览器管理入口已移除；实例 WebUI 子窗口仍存在且没有桌面原生权限。
- 安装和反复启动均不自动安装 Bot 版本。随包 Python/模块用于启动器后台；用户主动下载或导入后才能创建实例。
- 桌面创建模式固定 Helper；Mock 仅保留在后端及隔离测试中，不是安装或界面选项。
- Bot 列表读取 Bot 仓库 Atom 和便携 ZIP；启动器更新读取 Launcher 仓库。避开 REST 配额不保证免于所有 HTTP 限流。
- 默认关闭自启、隐藏自启和实例恢复；默认关闭窗口隐藏到托盘。数据目录切换不会迁移数据，实例活动时禁止切换。
- 自启为 HKCU Run → `asInvoker` 入口 → 预先授权的按需高权限任务 → 主程序。任务无登录触发器，不能绕过任务管理器禁用。Run 使用普通本地路径；仅迁移精确匹配当前安装的旧命令。
- 文件身份与显示格式分开；不能盲目删除所有 `\\?\` 前缀，或有损改写长路径、UNC、设备路径及特殊名称。
- 自动更新要求停止实例、等待桌面退出、再次校验，再以 `/S /UPDATE /D=<原安装目录>` 安装并携带原工作空间重启。更新启动器不切换实例运行版本。

## 开发沿革

按当前 Git 历史整理；只用于解释决策，不替代当前使用说明。

| 阶段 | 已落实的变化 |
| --- | --- |
| 独立仓库至 0.8.1 | 拆分启动器、清理无关文件及浏览器管理，修复 ZIP 导入/同版本替换，增加数据目录控制 |
| 0.8.2 | 集成外部 watchdog 修复和构建检查；发布运行版本时继承工作空间 ACL |
| 0.8.3 | 启动器更新模块；新工作空间不再自动导入随包 Bot |
| 0.8.4 | 扩展路径交接和界面显示；备份恢复拒绝可能产生别名误读的特殊名称 |
| 0.8.5 | Bot/Launcher 发布源分离，静默覆盖更新并重启 |
| 0.8.6 | 自启修复、任务管理器入口；通过 COM/UTF-8 读取任务 XML，避免中文路径损坏 |
| 0.8.7 | Run 路径修复、旧命令迁移、保留禁用选择、入口日志；测试兼容 8.3 临时目录别名 |
| 0.8.8 | 资源锁与原子创建修复、目录故障隔离、模块拆分、操作批次反馈与自启事务协调 |

## 0.8.8 修复与反馈

0.8.8 包含四项 CR 修复、并发进度、损坏条目诊断、设置草稿保留与目录/更新前置检查。新增模块见上表。网络下载不占版本库发布锁；创建、恢复、升级和删除通过版本库锁保护引用关系，实例启停使用各实例锁。还原配置的最终路径绑定在发布前完成。

复查补齐每次操作的关联 ID、目录切换与设置共用原生事务锁、缺失清单的故障诊断、ZIP 解码异常隔离及重复取消时的线程等待。关闭管理服务按拥有的进程句柄收尾，不依赖已损坏的磁盘清单。该版本源码验证为 150 项 Python、22 项前端、19 项 Rust 单元和 5 项进程集成测试通过，具体环境与边界见[验收指南](desktop-verification.md)。

## 验证证据与剩余事项

v0.8.7 的 [Windows CI 41754065459](https://github.com/PorkLittleHamburger/wechatbot-launcher/actions/runs/41754065459) 成功：123 项 Python、10 项 Rust 单元、5 项集成测试通过。另有管理员权限的两个隔离任务回归、普通权限入口请求独立高权限程序的验证。安装器解包通过，1,395 个运行时文件哈希匹配，新工作空间不自动装 Bot，手动导入通过；旧版 0.8.6 更新模块已从正式发布源发现、下载并校验新版。

这些是对应发布的历史证据，不包含真实微信操作，也不证明目标机器实际登录后的自启已恢复。仍需在升级后的隔离 Windows 环境验证注销/登录、隐藏托盘、实例恢复、真实覆盖安装和卸载。未执行项目标记“未验证”，Mock 不替代实际验收。

排查先采集安装版本、Run/StartupApproved、任务 XML 与 Shell-Core 登录事件，再检查 `<workspace>/autostart.log`、`%LOCALAPPDATA%\WeChatOneBotLauncherShell\startup.log`、`<workspace>/logs/supervisor.stderr.log` 和更新下载目录的 `install.log`。任务请求成功只代表接受请求；设置变更后要重新采样日志和状态。

构建脚本仍有原开发环境的目录探测，可选工具配置脚本也有默认代理；这是现存可移植性限制，不是通用前置要求。参照构建指南显式覆盖。不要依赖旧对话中的临时测试脚本或个人部署目录。

静态审查另发现容量边界：ZIP 提取/恢复限制为 100,000 个条目、累计未压缩大小 8 GiB，而备份创建未预检该上限。超限备份可能创建成功后无法恢复，尚未以超大归档实测。本次仅补齐文档，不改变备份实现；后续如处理此问题，应增加创建端预检或明确的可恢复性校验。

## 完成与交付要求

最新的[防御性编程与模块边界 CR](CODE_REVIEW.md)记录了三项 P2 和一项 P3 的隔离复现，以及前端/后端拆分建议。四项问题已修复并纳入 0.8.8。原始发现与修复状态分开保留；新接手应核对实际 HEAD，不把 v0.8.7 安装包当成包含这些修复。

每次变更后核对所有受影响文档，尤其 README、使用说明及本页。同步版本、默认值、命令、模式和限制；首页保持通用，历史结果注明版本与来源。验证链接及命令语法后提交推送，再确认远端 README 与提交内容一致。推送成功不能代替 CI 或真实安装验证。

文档整理不应替换用户安装、停止实际实例或更改系统自启。真实微信桥接、注入或发送需按独立 Bot 项目要求另行明确授权。
