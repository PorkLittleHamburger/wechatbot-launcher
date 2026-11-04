from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from ..config import ConfigError, _atomic_write_json, default_config
from .locking import DirectoryLease
from .instance_storage import bind_runtime, publish_instance
from .async_utils import run_blocking
from .paths import identity_path, is_within, process_path, relative_storage_path, require_portable_relative


@dataclass
class ProcessSlot:
    state: str = "stopped"
    process: subprocess.Popen | None = None
    error: str = ""
    probe: str = ""
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class LauncherManager:
    """Own child handles; persistent manifests never contain trusted PIDs."""

    def __init__(self, root: Path, project: Path):
        root.mkdir(parents=True, exist_ok=True)
        self.root = process_path(root)
        self.project = process_path(project)
        self.instances = self.root / "instances"
        self.instances.mkdir(parents=True, exist_ok=True)
        self.slots: dict[str, ProcessSlot] = {}
        self.guard = asyncio.Lock()
        self.closing = False

    def directory(self, instance_id: str) -> Path:
        if not isinstance(instance_id, str) or not re.fullmatch(r"[a-f0-9]{32}", instance_id):
            raise ConfigError("实例 ID 无效")
        path = self.instances / instance_id
        # Refuse junction/symlink escapes too.
        if identity_path(path).parent != identity_path(self.instances):
            raise ConfigError("实例目录超出数据目录")
        return path

    def metadata(self, instance_id: str) -> dict:
        path = self.directory(instance_id) / "instance.json"
        if not path.is_file():
            raise ConfigError("实例不存在")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict) or value.get("id") != instance_id or value.get("mode") not in ("mock", "helper"):
            raise ConfigError("实例清单损坏")
        self.validate_port(value.get("port"))
        return value

    def rows(self) -> list[dict]:
        result = []
        for path in sorted(self.instances.iterdir()):
            if not (path / "instance.json").is_file():
                if path.is_dir() and re.fullmatch(r"[a-f0-9]{32}", path.name):
                    raise ConfigError(f"实例清单缺失：{path.name}/instance.json；无法确认实例状态")
                continue
            row = self.metadata(path.name)
            slot = self.slots.setdefault(path.name, ProcessSlot())
            if slot.process is not None and slot.process.poll() is not None:
                code = slot.process.returncode
                if slot.process.stdin:
                    slot.process.stdin.close()
                slot.process = None
                slot.state = "error" if code else "stopped"
                slot.error = f"进程退出，退出码 {code}" if code else ""
            if slot.process is None:
                try:
                    lease = DirectoryLease(path, create=False)
                except ConfigError:
                    slot.state, slot.error = "stopping", "上次实例进程仍在关闭，请稍后刷新"
                else:
                    lease.close()
                    if slot.state == "stopping":
                        slot.state, slot.error = "stopped", ""
            result.append({**row, "state": slot.state,
                           "pid": slot.process.pid if slot.process else None,
                           "error": slot.error, "url": f"http://127.0.0.1:{row['port']}/"})
        seen = {row["id"] for row in result}
        if any(slot.process is not None and slot.process.poll() is None and instance_id not in seen
               for instance_id, slot in self.slots.items()):
            raise ConfigError("运行中实例目录或清单缺失，无法确认实例状态")
        return result

    def validate_port(self, port):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ConfigError("端口须为 1024–65535")

    def available(self, port: int, exclude: str = "") -> bool:
        if any(row["port"] == port and row["id"] != exclude for row in self.rows()):
            return False
        try:
            with socket.socket() as sock:
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False

    async def create(self, name: str, mode: str, port: int | None) -> dict:
        async with self.guard:
            return self._create_instance(name, mode, port, self.project)

    def _create_instance(self, name: str, mode: str, port: int | None,
                         project: Path, version: str | None = None) -> dict:
        if self.closing:
            raise ConfigError("启动器正在关闭")
        name = self.validate_name(name)
        if mode not in ("mock", "helper"):
            raise ConfigError("驱动仅支持 mock 或 helper")
        if port is None:
            port = next((p for p in range(5100, 65000) if self.available(p)), None)
        self.validate_port(port)
        if not self.available(port):
            raise ConfigError("端口已被实例或其他程序占用")
        instance_id = uuid.uuid4().hex
        directory = self.directory(instance_id)
        row = {"id": instance_id, "name": name, "mode": mode, "port": port,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        if version is not None:
            row["version"] = version
        config = default_config()
        config["webui"]["port"] = port
        config["driver"]["type"] = mode
        bind_runtime(config, directory, project)
        publish_instance(directory, row, config)
        return row

    @staticmethod
    def validate_name(name):
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
            raise ConfigError("实例名称须为 1–80 个字符")
        return name.strip()

    def require_stopped(self, instance_id: str) -> ProcessSlot:
        self.rows()
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        if slot.process is not None or slot.state in ("starting", "stopping"):
            raise ConfigError("请先停止实例")
        return slot

    async def update(self, instance_id: str, name: str, port: int):
        async with self.guard, self.slots.setdefault(instance_id, ProcessSlot()).lock:
            self.require_stopped(instance_id)
            row = self.metadata(instance_id)
            self.validate_port(port)
            if not self.available(port, instance_id):
                raise ConfigError("端口已被占用")
            directory = self.directory(instance_id)
            config = json.loads((directory / "config.json").read_text(encoding="utf-8-sig"))
            row.update(name=self.validate_name(name), port=port)
            config["webui"]["port"] = port
            _atomic_write_json(directory / "config.json", config)
            _atomic_write_json(directory / "instance.json", row)

    def check_config(self, instance_id: str):
        row = self.metadata(instance_id)
        directory = self.directory(instance_id)
        raw = json.loads((directory / "config.json").read_text(encoding="utf-8-sig"))
        # Data isolation remains an invariant even after manual config changes.
        if raw["webui"]["host"] != "127.0.0.1" or raw["webui"]["port"] != row["port"]:
            raise ConfigError("实例 WebUI 地址与清单不一致，请在启动器编辑端口")
        if raw["driver"]["type"] != row["mode"]:
            raise ConfigError("实例驱动与清单不一致")
        for key in ("data_dir", "media_dir"):
            target = (directory / raw["storage"][key]).resolve()
            if not is_within(target, directory):
                raise ConfigError("实例数据路径必须位于本实例目录内")
        if row["mode"] == "helper":
            import ctypes
            if os.name != "nt" or not ctypes.windll.shell32.IsUserAnAdmin():
                raise ConfigError("Helper 实例需以管理员身份运行启动器")
        return row

    async def start(self, instance_id: str):
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with slot.lock:
            await self._start_locked(instance_id, slot)

    def _launch_parameters(self, instance_id: str) -> tuple[str, Path]:
        return sys.executable, self.project

    async def _start_locked(self, instance_id: str, slot: ProcessSlot):
        if self.closing:
            raise ConfigError("启动器正在关闭")
        self.require_stopped(instance_id)
        row = self.check_config(instance_id)
        if not self.available(row["port"], instance_id):
            raise ConfigError("端口已被其他程序占用")
        directory = self.directory(instance_id)
        python, project = self._launch_parameters(instance_id)
        slot.state, slot.error, slot.probe = "starting", "", secrets.token_urlsafe(32)
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1", WECHAT_LAUNCHER_PROBE=slot.probe)
        # Source development keeps launcher modules ahead of the external runtime.
        sources = [str(project / "src")]
        launcher_source = os.environ.get("WECHAT_LAUNCHER_SOURCE")
        if launcher_source and not (project / "src/wechat_onebot/launcher/worker.py").is_file():
            sources.insert(0, str(Path(launcher_source).resolve()))
        env["PYTHONPATH"] = os.pathsep.join(sources + [env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
        log = directory / "service.log"
        try:
            if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
                os.replace(log, directory / "service.previous.log")
            with log.open("ab", buffering=0) as output:
                slot.process = subprocess.Popen(
                    [str(python), "-B", "-u", "-m", "wechat_onebot.launcher.worker", str(directory / "config.json")],
                    cwd=project, env=env, stdin=subprocess.PIPE, stdout=output,
                    stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            deadline = time.monotonic() + 30
            async with httpx.AsyncClient(trust_env=False, timeout=0.8) as client:
                while time.monotonic() < deadline:
                    if slot.process.poll() is not None:
                        raise ConfigError("服务进程提前退出，请查看实例日志")
                    try:
                        response = await client.get(f"http://127.0.0.1:{row['port']}/_launcher/health",
                                                    headers={"X-Launcher-Probe": slot.probe})
                        if response.status_code == 200 and response.json().get("identity") == slot.probe:
                            slot.state = "running"
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                    await asyncio.sleep(0.2)
            raise ConfigError("启动超时（30 秒），请查看实例日志")
        except BaseException as error:
            await self._stop(slot)
            slot.state, slot.error = "error", str(error)
            raise

    async def _stop(self, slot: ProcessSlot):
        process = slot.process
        if process is not None:
            slot.state = "stopping"
            if process.stdin:
                process.stdin.close()
            try:
                await asyncio.to_thread(process.wait, timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            slot.process = None
        slot.state = "stopped"

    async def stop(self, instance_id: str):
        self.metadata(instance_id)
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with slot.lock:
            await self._stop(slot)
            slot.error = ""

    async def restart(self, instance_id: str):
        self.metadata(instance_id)
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with slot.lock:
            await self._stop(slot)
            await self._start_locked(instance_id, slot)

    async def backup(self, instance_id: str) -> str:
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with slot.lock:
            self.metadata(instance_id)
            self.require_stopped(instance_id)
            lease = DirectoryLease(self.directory(instance_id), create=False)
            try:
                return await run_blocking(self._backup, instance_id)
            finally:
                lease.close()

    def _backup(self, instance_id: str) -> str:
        directory = self.directory(instance_id)
        backups = self.root / "backups"
        backups.mkdir(exist_ok=True)
        target = backups / f"{instance_id}-{time.time_ns()}.zip"
        temporary = target.with_suffix(".zip.partial")
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in directory.rglob("*"):
                    require_portable_relative(path.relative_to(directory))
                    if path.is_file() and path != directory / "launcher.lock":
                        if not is_within(path, directory):
                            raise ConfigError("备份包含实例目录外的链接")
                        if path == directory / "config.json":
                            raw = json.loads(path.read_text(encoding="utf-8-sig"))
                            changed = False
                            for key in ("data_dir", "media_dir"):
                                value = raw["storage"][key]
                                resolved = (directory / value).resolve()
                                if not is_within(resolved, directory):
                                    raise ConfigError("备份数据路径超出实例目录")
                                if Path(value).is_absolute():
                                    raw["storage"][key] = relative_storage_path(resolved, directory)
                                    changed = True
                            if changed:
                                archive.writestr("config.json", json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
                                continue
                        archive.write(path, path.relative_to(directory))
            os.replace(temporary, target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return target.name

    async def archive(self, instance_id: str):
        async with self.guard, self.slots.setdefault(instance_id, ProcessSlot()).lock:
            self.metadata(instance_id)
            self.require_stopped(instance_id)
            directory = self.directory(instance_id)
            for item in directory.rglob('*'):
                require_portable_relative(item.relative_to(directory))
            config_path = directory / "config.json"
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
            changed = False
            for key in ("data_dir", "media_dir"):
                value = raw["storage"][key]
                resolved = (directory / value).resolve()
                if not is_within(resolved, directory):
                    raise ConfigError("归档数据路径超出实例目录")
                if Path(value).is_absolute():
                    raw["storage"][key] = relative_storage_path(resolved, directory)
                    changed = True
            if changed:
                _atomic_write_json(config_path, raw)
            trash = self.root / "archive"
            trash.mkdir(exist_ok=True)
            self.directory(instance_id).rename(trash / f"{instance_id}-{time.time_ns()}")
            self.slots.pop(instance_id, None)

    def logs(self, instance_id: str) -> str:
        self.metadata(instance_id)
        path = self.directory(instance_id) / "service.log"
        if not path.exists():
            return "尚无运行日志"
        with path.open("rb") as stream:
            stream.seek(max(0, path.stat().st_size - 64 * 1024))
            return stream.read().decode("utf-8", errors="replace")

    async def close(self):
        self.closing = True
        async def stop_owned(slot):
            async with slot.lock:
                await self._stop(slot)
        await asyncio.gather(*(stop_owned(slot) for slot in self.slots.values()))
