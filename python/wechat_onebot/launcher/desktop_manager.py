"""Desktop management operations; no HTTP server or browser bootstrap."""
from __future__ import annotations

import asyncio
import json
import os
import socket
from pathlib import Path

import httpx

from . import __version__
from ..config import ConfigError, _atomic_write_json
from .instance_config import read_instance_config
from .manager import LauncherManager, ProcessSlot
from .versions import VersionStore, VERSION_RE
from .restore_service import RestoreService
from .async_utils import run_blocking
from .catalog import CatalogReader, collect_snapshot
from .instance_storage import bind_runtime
from .self_update import LauncherUpdater
from .bridge_status import read_bridge_status
from .paths import identity_path

DEFAULT_SETTINGS = {"close_to_tray": True, "autostart": False,
                    "autostart_hidden": False, "restore_instances": False, "restore_bridges": False}


class DesktopManager(LauncherManager):
    def __init__(self, root: Path, project: Path):
        super().__init__(root, project)
        self.versions = VersionStore(self.root)
        self.updater = LauncherUpdater(self.root)
        self.catalog = CatalogReader(self.root)
        self.restorer = RestoreService(self, self.versions)

    def settings(self) -> dict:
        path = self.root / "settings.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(raw, dict) or any(type(raw[key]) is not bool for key in DEFAULT_SETTINGS if key in raw):
            raise ConfigError("设置文件格式错误，请修复 settings.json 后再保存设置")
        return {key: raw.get(key, default) for key, default in DEFAULT_SETTINGS.items()}

    def metadata(self, instance_id: str) -> dict:
        row = super().metadata(instance_id)
        if not isinstance(row.get("version"), str) or not VERSION_RE.fullmatch(row["version"]):
            raise ConfigError(f"实例 {instance_id} 的运行版本清单缺失或无效，请修复 instance.json")
        return row

    def save_settings(self, values: dict) -> dict:
        if set(values) - set(DEFAULT_SETTINGS) or any(type(v) is not bool for v in values.values()):
            raise ConfigError("设置只能包含受支持的布尔值")
        updated = self.settings() | values
        _atomic_write_json(self.root / "settings.json", updated)
        return updated

    async def initialize(self, *, autostart: bool = False):
        for row in self.rows():
            journal = self.directory(row["id"]) / "upgrade.json"
            if journal.exists():
                # EOF shutdown may still be releasing a worker from a previous session.
                for _ in range(100):
                    try:
                        self.require_stopped(row["id"])
                        break
                    except ConfigError:
                        await asyncio.sleep(0.2)
                else:
                    raise ConfigError("上次升级进程尚未退出，请稍后重启启动器")
                state = json.loads(journal.read_text(encoding="utf-8"))
                await run_blocking(self.restorer.rollback, row["id"], self.restorer.backup_path(state["backup"]))
        saved = self.root / "running.json"
        ids = json.loads(saved.read_text(encoding="utf-8")).get("instances", []) if saved.exists() else []
        settings = self.settings()
        if settings["restore_instances"]:
            for instance_id in ids:
                try:
                    await self.start(instance_id, resume_bridge=autostart and settings["autostart"] and settings["restore_bridges"])
                except Exception as error:
                    self.slots.setdefault(instance_id, ProcessSlot()).error = str(error)

    def default_version(self) -> str:
        installed = self.versions.list()
        if not installed:
            raise ConfigError("请先在版本管理中下载或导入运行版本，再创建实例")
        return installed[0]["version"]

    def _remember_running(self):
        if not self.closing:
            _atomic_write_json(self.root / "running.json", {
                "instances": [r["id"] for r in self.rows() if r["state"] == "running"]})

    async def start(self, instance_id: str, *, resume_bridge: bool = False):
        await super().start(instance_id, resume_bridge=resume_bridge)
        self._remember_running()

    async def stop(self, instance_id: str):
        await super().stop(instance_id)
        self._remember_running()

    async def restart(self, instance_id: str):
        try:
            await super().restart(instance_id)
        finally:
            self._remember_running()

    async def create(self, name: str, mode: str, port: int | None, version: str | None = None):
        async with self.versions.lock, self.guard:
            version = self.default_version() if version is None else version
            project = self.versions.resolve(version)
            return self._create_instance(name, mode, port, project, version)

    @staticmethod
    def _bind_version(directory: Path, project: Path):
        config_path = directory / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        bind_runtime(config, directory, project)
        _atomic_write_json(config_path, config)

    def _launch_parameters(self, instance_id: str):
        project = self.versions.resolve(self.metadata(instance_id)["version"])
        return str(project / "python" / "python.exe"), project

    async def _start_locked(self, instance_id: str, slot: ProcessSlot, *, resume_bridge: bool = False):
        await super()._start_locked(instance_id, slot, resume_bridge=resume_bridge)
        try:
            port = self.metadata(instance_id)["port"]
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                health = await client.get(f"http://127.0.0.1:{port}/healthz")
                health.raise_for_status()
                status = health.json()
                # Bridge recovery failure does not make the management UI unhealthy.
                if status.get("lastError") and slot.resume_requested:
                    current = await client.get(f"http://127.0.0.1:{port}/_launcher/health",
                                               headers={"X-Launcher-Probe": slot.probe})
                    current.raise_for_status()
                    current_health = current.json()
                    if not isinstance(current_health, dict) or current_health.get("identity") != slot.probe:
                        raise ConfigError("管理服务身份校验失败")
                    slot.bridge_resume = read_bridge_status(current_health, True)
                bridge_failed = (slot.bridge_resume is not None and slot.bridge_resume["state"] == "failed"
                                 and slot.bridge_resume["error"] == status.get("lastError"))
                if not status.get("ok") or status.get("lastError") and not bridge_failed:
                    raise ConfigError(f"管理服务启动检查失败: {status.get('lastError') or '服务未就绪'}")
        except BaseException as error:
            await self._stop(slot)
            slot.state, slot.error = "error", str(error)
            raise

    def check_config(self, instance_id: str):
        row = super().check_config(instance_id)
        self.versions.resolve(row["version"])
        directory = self.directory(instance_id)
        config = read_instance_config(directory / "config.json")
        listeners = [("127.0.0.1", row["port"])]
        # Endpoints are restored independently of the bridge. Reject all listener
        # conflicts before starting any part of the instance.
        for path in config.data_dir.glob("accounts/*/onebot.json"):
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            for key in ("httpServers", "wsServers"):
                for endpoint in raw.get(key, []):
                    if endpoint.get("enabled", True):
                        host, port = endpoint.get("host", "127.0.0.1"), endpoint.get("port")
                        if type(port) is not int or not 1 <= port <= 65535:
                            raise ConfigError(f"OneBot 监听端口无效: {path.name}")
                        listeners.append((host, port))
        bound = []
        try:
            for host, port in listeners:
                family = socket.AF_INET6 if ":" in host else socket.AF_INET
                sock = socket.socket(family)
                bound.append(sock)
                if os.name == "nt":
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
                try:
                    sock.bind((host, port))
                except OSError as error:
                    raise ConfigError(f"监听地址冲突或不可用: {host}:{port}") from error
        finally:
            for sock in bound:
                sock.close()
        return row

    def backups(self):
        return self.catalog.backups()

    async def delete_backup(self, name: str):
        # Restore/upgrade hold this same transaction lock while reading backups.
        async with self.versions.lock, self.guard:
            path = self.restorer.backup_path(name)
            folder = self.root / "backups"
            if (path.suffix.lower() != ".zip" or path.is_symlink()
                    or identity_path(folder) != identity_path(self.root) / "backups"):
                raise ConfigError("只能删除当前工作空间备份目录中的 ZIP 文件")
            for instance in self.instances.iterdir():
                journal = instance / "upgrade.json"
                if not journal.exists():
                    continue
                try:
                    state = json.loads(journal.read_text(encoding="utf-8"))
                    backup = state["backup"]
                    if not isinstance(backup, str):
                        raise ValueError("备份名称无效")
                except (OSError, ValueError, KeyError, TypeError) as error:
                    raise ConfigError("升级恢复记录无法读取，请先完成恢复后再删除备份") from error
                if identity_path(folder / backup) == identity_path(path):
                    raise ConfigError("该备份用于未完成升级的回滚，请先重启启动器完成恢复")
            try:
                path.unlink()
            except OSError as error:
                raise ConfigError(f"删除备份失败，请检查文件权限或占用：{error}") from error

    def archives(self):
        return self.catalog.archives()

    def referenced_versions(self) -> set[str]:
        rows = self.rows() + self.archives()
        if any(row.get("invalid") for row in rows):
            raise ConfigError("归档清单损坏，无法确认版本引用；请先修复归档后再替换或删除版本")
        return {row["version"] for row in rows}

    async def remove_version(self, version: str):
        async with self.versions.lock:
            await run_blocking(self.versions.remove, version, self.referenced_versions())

    async def restore(self, name: str | None = None, *, path: str | Path | None = None):
        return await self.restorer.restore(name, path=path)

    async def restore_archive(self, archive_name: str):
        return await self.restorer.restore_archive(archive_name)

    async def upgrade(self, instance_id: str, version: str):
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with self.versions.lock, slot.lock:
            self.require_stopped(instance_id)
            row = self.metadata(instance_id)
            project = self.versions.resolve(version)
            if row["version"] == version:
                raise ConfigError("实例已经使用该版本")
            backup = await run_blocking(self._backup, instance_id)
            directory = self.directory(instance_id)
            _atomic_write_json(directory / "upgrade.json", {"backup": backup, "old_version": row["version"]})
            try:
                row["version"] = version
                _atomic_write_json(directory / "instance.json", row)
                self._bind_version(directory, project)
                await self._start_locked(instance_id, slot)
                await self._stop(slot)
                (directory / "upgrade.json").unlink()
                return {"ok": True, "version": version, "backup": backup}
            except BaseException as error:
                await self._stop(slot)
                await run_blocking(self.restorer.rollback, instance_id, self.restorer.backup_path(backup))
                slot.state, slot.error = "stopped", f"升级失败，已回滚: {error}"
                raise ConfigError(slot.error) from error

    async def refresh_bridge_status(self):
        async def refresh(instance_id, slot):
            process, probe = slot.process, slot.probe
            slot.bridge_poll += 1
            poll = slot.bridge_poll
            try:
                port = self.metadata(instance_id)["port"]
                async with httpx.AsyncClient(trust_env=False, timeout=0.8) as client:
                    response = await client.get(f"http://127.0.0.1:{port}/_launcher/health",
                                                headers={"X-Launcher-Probe": probe})
                    response.raise_for_status()
                    health = response.json()
                    if not isinstance(health, dict) or health.get("identity") != probe:
                        raise ConfigError("管理服务身份校验失败")
                    status = read_bridge_status(health, slot.resume_requested)
            except (httpx.HTTPError, ValueError, OSError, ConfigError) as error:
                status = {**slot.bridge_resume, "state": "failed", "error": f"无法读取桥接状态：{error}"} if slot.bridge_resume else None
            if slot.process is process and slot.probe == probe and slot.state == "running" and slot.bridge_poll == poll:
                slot.bridge_resume = status
        await asyncio.gather(*(refresh(key, slot) for key, slot in self.slots.items()
                               if slot.state == "running" and slot.process is not None))

    def snapshot(self):
        sections, diagnostics = collect_snapshot({
            "instances": self.rows, "versions": self.versions.list,
            "backups": self.backups, "archives": self.archives, "settings": self.settings,
        })
        if sections["settings"] is None:
            sections["settings"] = dict(DEFAULT_SETTINGS)
        return {**sections, "diagnostics": diagnostics, "root": str(self.root), "version": __version__}

    async def close(self):
        try:
            _atomic_write_json(self.root / "running.json", {
                "instances": [key for key, slot in self.slots.items()
                              if slot.process is not None and slot.process.poll() is None]})
        finally:
            await super().close()
