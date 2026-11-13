# 启动器项目交接

维护日期：2026-11-12。新接手的 AI 必须先读本页及 [AGENTS.md](../AGENTS.md)，再检查当前代码、工作树、远端与 Release。本页是交接快照，不代表目标机器的当前部署状态。

## 范围与发布基线

- 仓库为 `PorkLittleHamburger/wechatbot-launcher`，只维护桌面启动器。旧对话中的共享仓库或 `WechatBot/launcher` 路径已不适合作为定位依据；先用 `git rev-parse --show-toplevel` 确认。
- 网关、微信桥接、原生 DLL 及对应测试位于独立 `PorkLittleHamburger/wechatbot` 仓库，通过 `WECHAT_RUNTIME_SOURCE` 参与构建，不复制到启动器源码库。
- 当前发布线：[v0.8.11](https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.8.11)，源码以该标签指向的提交为准；正式发布状态以 GitHub Latest 为准，后续文档提交不改写发布标签。
- [CI](../.github/workflows/desktop.yml) 固定 Bot v0.9.3 源码 `0af6c8098533e17898e0fcbd2824392e9738f20d`（恢复协议 v1）；功能基线为 `b908d4c`，后续仅同步发布说明。本地构建不会自动检出或核对提交，发布前须检查运行时源 HEAD 与工作树。v0.8.8 的历史运行时为 Bot v0.8.6 `23f46eb`。
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
| `operations.py`、`catalog.py`、`instance_storage.py`、`instance_config.py`、`restore_service.py`、`async_utils.py` | 操作路由、故障隔离目录读取、原子创建、Launcher 侧实例配置视图、恢复/回滚及取消安全线程执行 |
| `versions.py`、`self_update.py` | 分离的 Bot 与 Launcher 发布来源、下载及校验 |
| `scripts/`、`desktop/src-tauri/windows/` | 运行时合并、构建、清单、安装、更新和卸载 |

必须保留的行为：

- 浏览器管理入口已移除；实例 WebUI 子窗口仍存在且没有桌面原生权限。
- 安装和反复启动均不自动安装 Bot 版本。随包 Python/模块用于启动器后台；用户主动下载或导入后才能创建实例。
- 桌面创建模式固定 Helper；Mock 仅保留在后端及隔离测试中，不是安装或界面选项。
- 实例配置归所选 Bot 版本所有：Launcher 只通过 `instance_config.py` 读取 WebUI 地址、storage 隔离路径和 driver 对象，不按自带运行时版本解析 Bot 全量 schema。helper 实例允许 Bot 0.10.x 持久化的 `driver.type=sdk` 回退值，未知驱动类型仍拒绝；Launcher 不自动改写 `driver.type`，恢复备份时将其归一为实例 mode（0.8.12 源码，见下文）。
- Bot 列表读取 Bot 仓库 Atom 和便携 ZIP；启动器更新读取 Launcher 仓库。避开 REST 配额不保证免于所有 HTTP 限流。
- 默认关闭自启、隐藏自启、实例恢复和桥接恢复；关闭窗口默认隐藏到托盘。数据目录切换不会迁移数据，实例活动时禁止切换。
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
| 0.8.9 | 显式请求桥接恢复、等待登录与停止意图状态展示、旧 worker 能力提示；修复升级意图继承与并发状态倒序覆盖 |
| 0.8.10 | 实例 WebView2 独立数据目录、窗口串行创建/复用、取消安全门闩、端口变更重建及分阶段错误提示 |
| 0.8.11 | 备份删除确认、回滚保护、成功/取消反馈及普通通知 5 秒自动收起 |

## 0.8.8 修复与反馈

0.8.8 包含四项 CR 修复、并发进度、损坏条目诊断、设置草稿保留与目录/更新前置检查。新增模块见上表。网络下载不占版本库发布锁；创建、恢复、升级和删除通过版本库锁保护引用关系，实例启停使用各实例锁。还原配置的最终路径绑定在发布前完成。

复查补齐每次操作的关联 ID、目录切换与设置共用原生事务锁、缺失清单的故障诊断、ZIP 解码异常隔离及重复取消时的线程等待。关闭管理服务按拥有的进程句柄收尾，不依赖已损坏的磁盘清单。该版本源码验证为 150 项 Python、22 项前端、19 项 Rust 单元和 5 项进程集成测试通过，具体环境与边界见[验收指南](desktop-verification.md)。

## 验证证据与剩余事项

### 0.8.9：显式桥接恢复

v0.8.9 包含本节改动，v0.8.8 及更早版本不包含。根因是旧 worker 固定 `start_runtime=False`，恢复实例从未请求 Bot 恢复桥接。新增 `restore_bridges=false`，只有 Shell `--autostart`、`autostart`、`restore_instances` 和该开关同时满足时，对 `running.json` 中的实例传 `--resume-bridge`。普通启动、重启和升级检查均不请求。

Rust 将自启标志传给 supervisor，`DesktopManager.initialize` 选择意图；`worker.py` 按 Bot 的 `RESUME_CAPABILITY_VERSION=1` 启用 `create_webui_app(start_runtime=True)`。Bot 唯一拥有等待登录和停止取消；Launcher 不另建账号扫描或注入重试。认证健康端点提供 `bridgeResume`，`bridge_status.py` 校验一次协议，快照刷新后显示 `disabled/no_target/waiting_login/starting/stopping/running/stopped/failed/unsupported`。管理服务启动成功与桥接恢复结果分开；旧能力缺失保留管理入口并给升级建议，状态读取失败不能当作成功。

兼容性边界：实例使用所选版本内的 worker，更新 Launcher 不更新已安装的版本文件。需在新 Launcher 下载或导入 Bot v0.9.3 或支持同一协议的新便携版本并切换实例；不能仅追加一个会被旧 worker 忽略的参数就宣称已恢复。旧 Bot 支持管理服务模式。本轮按用户明确请求发布配套版本，只做隔离验证，不改真实配置、任务或运行实例，不执行微信注入或发送。

验证：新 Bot 源码 183 项 Python 通过；旧 Bot v0.8.6 为 178 通过、5 项新能力用例跳过；前端 33 项通过；Rust 主程序 21 项、进程目标 6 项通过，两项 opt-in 未执行。真实临时导入 ZIP 与嵌入 Python 验证三种恢复状态通过；独立 CR 的异常退出升级意图继承及旧轮询覆盖新状态两项已修复并复审通过。完整说明见[验收指南](desktop-verification.md)。

功能提交 `609390c` 的 [Windows CI 41819056040](https://github.com/PorkLittleHamburger/wechatbot-launcher/actions/runs/41819056040) 完整成功，含 183 项 Python、33 项前端、22 项 Rust 主程序和 7 项进程目标测试及离线安装器构建。0.8.9 发布元数据提交需另行通过完整 CI，并核对标签、附件摘要、重新下载与 Latest 状态；发布记录以 [Release](https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.8.9) 为准。

v0.8.7 的 [Windows CI 41754065459](https://github.com/PorkLittleHamburger/wechatbot-launcher/actions/runs/41754065459) 成功：123 项 Python、10 项 Rust 单元、5 项集成测试通过。另有管理员权限的两个隔离任务回归、普通权限入口请求独立高权限程序的验证。安装器解包通过，1,395 个运行时文件哈希匹配，新工作空间不自动装 Bot，手动导入通过；旧版 0.8.6 更新模块已从正式发布源发现、下载并校验新版。

这些是对应发布的历史证据，不包含真实微信操作，也不证明目标机器实际登录后的自启已恢复。仍需在升级后的隔离 Windows 环境验证注销/登录、隐藏托盘、实例恢复、真实覆盖安装和卸载。未执行项目标记“未验证”，Mock 不替代实际验收。

排查先采集安装版本、Run/StartupApproved、任务 XML 与 Shell-Core 登录事件，再检查 `<workspace>/autostart.log`、`%LOCALAPPDATA%\WeChatOneBotLauncherShell\startup.log`、`<workspace>/logs/supervisor.stderr.log` 和更新下载目录的 `install.log`。任务请求成功只代表接受请求；设置变更后要重新采样日志和状态。

构建脚本仍有原开发环境的目录探测，可选工具配置脚本也有默认代理；这是现存可移植性限制，不是通用前置要求。参照构建指南显式覆盖。不要依赖旧对话中的临时测试脚本或个人部署目录。

静态审查另发现容量边界：ZIP 提取/恢复限制为 100,000 个条目、累计未压缩大小 8 GiB，而备份创建未预检该上限。超限备份可能创建成功后无法恢复，尚未以超大归档实测。本次仅补齐文档，不改变备份实现；后续如处理此问题，应增加创建端预检或明确的可恢复性校验。

## 完成与交付要求

### 2026-11-13：Bot 0.10.x 驱动回退兼容（源码改动，未发布）

用户升级 Bot 0.10.1/0.10.2 后，启动实例报「实例驱动与清单不一致」，恢复备份报「备份缺少有效的实例配置」。根因分两层：

1. Bot 0.10.x 在 helper 配置下遇到不支持的微信版本（实测 4.2.0.52）会回退到 sdk，并把 `driver.type=sdk` 持久化进实例 `config.json`；Launcher 0.8.11 的 `manager.check_config` 要求 `driver.type` 等于 `instance.json` 的 `mode`（仍为 helper）。
2. Launcher 0.8.11 内置运行时是 Bot v0.9.3（CI 固定 `0af6c80`，其配置模型只接受 helper/mock）。`desktop_manager.check_config` 与 `restore_service._read_restore` 用该模型 `AppConfig.load` 解析实例配置，遇到 sdk 即失败：启动被第一层拦下，备份恢复则被包装成「备份缺少有效的实例配置」。

修复（源码，未提交发布）：

- 新增 Launcher 自有只读视图 `instance_config.py`：只校验并读取 `webui` 地址、`storage.data_dir`/`media_dir` 隔离路径与 `driver` 对象，不再用自带运行时解析 Bot 全量 schema。`desktop_manager` 的 OneBot 监听端口扫描与 `restore_service` 的备份校验改用该视图。
- `manager.check_config` 保留一致性检查但按模式限定已知集合：helper 接受 helper/sdk，mock 只接受 mock；未知驱动类型仍明确报错。Launcher 不自动改写 `driver.type`（配置归 Bot 所有）。
- 恢复备份时仍把 `driver.type` 归一为实例 `mode`，避免旧回退值带入新实例。
- 不新增 `mode=sdk`：sdk 是 Bot 内部回退而不是用户可选启动模式，UI 与创建流程仍固定 Helper，不扩展权限与产品契约。

验证（本机，运行时固定 Bot v0.9.3 源码，与 0.8.11 打包一致）：新增 3 项回归（helper+sdk 通过启动前校验、sdk 备份可恢复且归一为 helper、配置缺少 storage 时给出明确错误）；修复前 3 项均失败，修复后全量 190 通过、1 跳过（符号链接用例需权限）。未改动 Rust/前端/Windows 代码，未跑完整构建与安装器校验，未发布。

版本与迁移：计划 v0.8.12 补丁；无数据迁移。现有 sdk 配置由 Launcher 兼容读取，Bot 0.10.3 起不再持久化 sdk，两侧可独立发布。发布需用户明确请求，并按惯例完成完整 CI、解包哈希与附件校验；源码提交不等于已发布。

### 2026-11-12：0.8.11 备份删除与反馈修复

备份页原先只有恢复入口，没有删除 RPC。现新增 `backup_delete(name)`、确认对话框和严格结果校验；只删除工作空间 backups 中的直接 ZIP，允许损坏包，不删除实例。删除和还原/升级共用事务锁，未完成升级 journal 引用的备份受保护；保留明确的缺失、权限/占用错误，不返回伪成功。界面恢复与删除使用同一资源 key，防止并发点击。

`useWorkspace` 按 operationId 管理 5 秒反馈计时，仅在完整 RPC 结束的成功或取消状态触发；pending/error 不过期。卸载及手动关闭清理计时器，旧计时器不移除新批次操作。普通 notice 同样自动收起。v0.8.11 包含本节改动，v0.8.10 及更早版本不包含。用户已明确要求发布；发布前须完整构建并校验附件，不能把源码推送当作发布。验证只使用隔离数据，不操作用户真实备份。

验证：Python 全量 187 项通过；随后补充的目录符号链接回归在本机因权限跳过，删除专项为 4 通过、1 跳过。前端 45 项及 TypeScript/Vite 构建通过，覆盖真实 hook 的假时钟、RPC 未完成不收起、旧关闭回调隔离和删除确认契约。未执行实际桌面安装或用户备份删除。

### 2026-11-12：0.8.10 实例 WebView2 改进

用户报告 v0.8.9 打开 WebUI 时发生 `0x80010108`。Bot 侧已确认实例 HTTP 正常且 v0.9.4 下载、导入有效，不能因此认定 Bot 包损坏。`open_instance` 原本为 async 命令；Tauri 官方明确警告 Windows 同步命令创建窗口可能死锁，故不改为同步 UI 回调创建。

新增 `instance_window.rs`，每实例使用 `%LOCALAPPDATA%/<应用标识>/instance-webviews/<id>` 独立浏览器目录，不复用主窗口环境；创建与复用由原生窗口门闩串行控制，门闩随阻塞任务持有，调用方取消不能提前释放。同 URL 重开只还原/显示/聚焦，不刷新登录或配置页面。实例子窗口仍没有原生 capability，导航仍限制在校验后的本地服务 origin。首次使用独立目录可能需要重新登录 WebUI，不迁移或删除旧浏览器缓存。

验证：Rust 主程序 21 项、进程目标 6 项通过，两项 opt-in 未执行；真实 WebView2 隔离 smoke 覆盖主窗口存在时创建两实例、重复打开、端口变更后重建、销毁后重建。旧代码在本机同类 smoke 也通过，尚未精确重现用户 HRESULT，不能宣称根因已被唯一确定。验证没有启动 supervisor、微信或真实实例，也未更改系统自启。真实用户安装仍待升级后复测。用户随后明确要求发布，0.8.10 包含上述改动；不修改现有安装、实例或 Bot v0.9.4 发布。功能提交 `ec327a2` 的 [Windows CI 43154453227](https://github.com/PorkLittleHamburger/wechatbot-launcher/actions/runs/43154453227) 已成功；发布元数据提交仍须通过完整 CI 和附件校验。

最新的[防御性编程与模块边界 CR](CODE_REVIEW.md)记录了三项 P2 和一项 P3 的隔离复现，以及前端/后端拆分建议。四项问题已修复并纳入 0.8.8。原始发现与修复状态分开保留；新接手应核对实际 HEAD，不把 v0.8.7 安装包当成包含这些修复。

每次变更后核对所有受影响文档，尤其 README、使用说明及本页。同步版本、默认值、命令、模式和限制；首页保持通用，历史结果注明版本与来源。验证链接及命令语法后提交推送，再确认远端 README 与提交内容一致。推送成功不能代替 CI 或真实安装验证。

文档整理不应替换用户安装、停止实际实例或更改系统自启。真实微信桥接、注入或发送需按独立 Bot 项目要求另行明确授权。
