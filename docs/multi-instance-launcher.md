# 浏览器多实例管理入口

浏览器管理入口用于源码开发和兼容部署。它使用当前源码与 Python 环境管理多个独立服务实例。需要版本管理、托盘、实例升级及恢复时，使用 [桌面应用](desktop-launcher.md)。以下命令从仓库根目录执行。

## 功能范围

实例配置、消息库、媒体、管理密码、OneBot 配置及日志相互独立；所有实例共享当前安装目录的代码和 Python。此兼容入口不提供按实例选择软件版本、托盘、启动器开机自启或 ZIP 自动导入恢复。升级共享代码前应停止全部实例并备份。

## 启动

双击 `launcher/launcher.cmd`，或在仓库根目录执行：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot launcher
```

默认在 `http://127.0.0.1:5088/` 监听，自动打开附带临时凭据的本地入口，进入后清除地址中的凭据。控制台也显示入口地址。浏览器关闭后服务继续运行；关闭启动器进程会停止其管理的实例。

Helper 模式需要管理员权限，可在管理员终端运行以上命令，或执行：

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File .\launcher\scripts\run_launcher.ps1 -Elevated
```

自定义启动器端口和数据目录：

```powershell
.\.venv\Scripts\python.exe -m wechat_onebot launcher --port 5087 --data-dir ./instances --no-browser
```

同一个数据目录只能有一个启动器，文件锁在退出或崩溃后由系统释放。不同启动器数据目录可以并行运行，端口需互不冲突。

## 使用步骤

1. 新建实例，填写名称、驱动和可选 WebUI 端口。不填端口则从 5100 起选择空闲端口，默认驱动为 Mock。
2. 点击启动。启动器只启动管理服务与已保存的 OneBot 端点，不恢复微信桥接。
3. 打开该实例的 WebUI，点击启动器的首次登录凭据按钮获取初始 Token，在 WebUI 中设置该实例的管理密码。
4. Helper 实例在自己的 WebUI 中扫描账号、绑定、配置 OneBot 节点并手动启动账号桥接。Mock 实例用于离线协议验证。
5. 停止实例后可修改名称/端口、生成备份或归档删除。删除会将整个目录移动到 `archive/`，保留配置和消息数据。

备份 ZIP 包含凭据与消息资料，保存于管理数据目录的 `backups/`。手动恢复前应关闭启动器并保留原有目录，检查实例 ID、配置和端口，避免将备份叠加到现有运行实例。需要受管理的恢复流程时使用桌面应用。

## 进程和账号分层

```text
浏览器启动器面板
  └─ Launcher FastAPI（127.0.0.1:5088）
       ├─ 子进程 A → 实例 A WebUI → Runtime → 微信 PID A
       └─ 子进程 B → 实例 B WebUI → Runtime → 微信 PID B
```

“运行中”表示实例服务完成启动并且子进程尚未退出；微信账号在线与 OneBot 节点连接情况应在实例 WebUI 查看。此入口不进行持续 HTTP 活性探测或自动崩溃重启。进程异常退出后会显示退出码。实例互不依赖，停止一个不会停止其他实例。

启动器不导入或覆盖 `bot/config.json`、`bot/data/accounts`、密码与自启配置；`bot/start.cmd` 管理单服务部署。启动器实例的自启由启动器负责，实例 WebUI 不修改单服务任务。

同一用户下的所有本项目 Helper 驱动在连接前竞争微信 PID 的系统文件锁。锁在原生后端成功清理之后释放，清理失败保留锁并允许重试；进程终止后由操作系统释放。该锁不会阻止未采用此约定的其他程序操作微信。Windows 的 TEMP 设置不同可能导致锁目录不同，部署时应保持同一用户的 TEMP 配置一致。

## 数据目录

```text
bot/data/launcher/
  launcher.lock
  instances/<32位随机ID>/
    instance.json
    config.json
    service.log
    service.previous.log       # 下次启动时轮换超过 5 MiB 的旧日志
    data/
      webui-auth.json
      accounts/<账号>/...
      media/...
  backups/<ID>-<时间>.zip
  archive/<ID>-<时间>/...
```

日志页只读取末尾 64 KiB；当前运行日志在下次启动时按大小轮换，持续运行时不强制截断。实例 ID 由系统生成，用户名称不用于路径。数据路径必须位于实例目录中，Helper 组件以相对路径引用安装目录。整体移动安装目录时请保持实例数据与组件的相对位置；自定义外置数据目录在移动安装目录后需修正各实例的 `driver.helper_root`。

## 启停和访问保护

正常状态转换为 `stopped → starting → running → stopping → stopped`，启动失败进入 `error`。同一实例的启停、编辑、备份和归档使用互斥锁。启动前检查端口，启动后以每次运行的随机标识探测实际子服务，避免将占用同端口的其他 HTTP 服务误判为成功。端口预检查存在操作系统层面的竞争窗口，最终由实际绑定和启动检查决定结果。

停止时关闭子进程标准输入管道，worker 请求 Uvicorn 正常退出，连接排空最多等待 3 秒，随后触发 Runtime、网络端点和桥接清理；等待 15 秒后强制终止持有的子进程并回收。启动器异常退出时，管道 EOF 同样触发子服务清理。worker 持有实例目录锁直至退出，重新打开启动器时仍在关闭的旧 worker 会阻止备份和归档。备份不包含系统锁文件，取消备份请求时仍等文件线程结束后才释放互斥锁。启动器不读取磁盘上的旧 PID 来结束进程。强制终止或断电仍可能中断正在进行的数据写入。

启动器只监听本机地址，使用每次启动随机凭据换取 HttpOnly/SameSite Cookie，验证 Host 与修改请求的来源及专用请求头。实例列表不包含管理 Token；首次登录凭据需主动读取，设置管理密码后停止提供初始 Token。启动器持有全部实例的管理权限，请仅从受信任的本机环境使用。

## 验证范围

自动化验证包含两个真实 Mock 服务子进程的独立启动与停止、数据与凭据隔离、端口冲突、停止后备份与归档、认证和请求来源校验、目录锁及跨进程 PID 锁。上述验证不会加载原生桥接，不证明多个真实微信账号在当前电脑上已经完成联调。

实例清单与进程生命周期的分层设计参考 [AstrBot Launcher](https://github.com/AstrBotDevs/astrbot-launcher)，本管理入口使用 Python/FastAPI 实现。
