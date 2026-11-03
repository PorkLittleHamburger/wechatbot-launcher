"""Desktop management operations; no HTTP server or browser bootstrap."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

import httpx

from . import __version__
from ..config import AppConfig, ConfigError, _atomic_write_json
from .locking import DirectoryLease
from .manager import LauncherManager, ProcessSlot
from .versions import VersionStore, extract_checked
from .self_update import LauncherUpdater

DEFAULT_SETTINGS = {"close_to_tray": True, "autostart": False,
                    "autostart_hidden": False, "restore_instances": False}


class DesktopManager(LauncherManager):
    def __init__(self, root: Path, project: Path):
        super().__init__(root, project)
        self.versions = VersionStore(self.root)
        self.updater = LauncherUpdater(self.root)
        self.version_lock = asyncio.Lock()

    def settings(self) -> dict:
        path = self.root / "settings.json"
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        return {key: raw.get(key, default) for key, default in DEFAULT_SETTINGS.items()}

    def save_settings(self, values: dict) -> dict:
        if set(values) - set(DEFAULT_SETTINGS) or any(type(v) is not bool for v in values.values()):
            raise ConfigError("设置只能包含受支持的布尔值")
        updated = self.settings() | values
        _atomic_write_json(self.root / "settings.json", updated)
        return updated

    async def initialize(self):
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
                await asyncio.to_thread(self._rollback, row["id"], self._backup_path(state["backup"]))
        saved = self.root / "running.json"
        ids = json.loads(saved.read_text(encoding="utf-8")).get("instances", []) if saved.exists() else []
        if self.settings()["restore_instances"]:
            for instance_id in ids:
                try:
                    await self.start(instance_id)
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

    async def start(self, instance_id: str):
        await super().start(instance_id)
        self._remember_running()

    async def stop(self, instance_id: str):
        await super().stop(instance_id)
        self._remember_running()

    async def create(self, name: str, mode: str, port: int | None, version: str | None = None):
        version = version or self.default_version()
        project = self.versions.resolve(version)
        row = await super().create(name, mode, port)
        row["version"] = version
        directory = self.directory(row["id"])
        self._bind_version(directory, project)
        _atomic_write_json(directory / "instance.json", row)
        return row

    @staticmethod
    def _bind_version(directory: Path, project: Path):
        config_path = directory / "config.json"
        config = json.loads(config_path.read_text(encoding="utf-8-sig"))
        try:
            helper = os.path.relpath(project / "vendor" / "helper", directory)
        except ValueError:
            helper = str(project / "vendor" / "helper")
        config["driver"]["helper_root"] = helper
        _atomic_write_json(config_path, config)

    def _launch_parameters(self, instance_id: str):
        project = self.versions.resolve(self.metadata(instance_id)["version"])
        return str(project / "python" / "python.exe"), project

    async def _start_locked(self, instance_id: str, slot: ProcessSlot):
        await super()._start_locked(instance_id, slot)
        try:
            port = self.metadata(instance_id)["port"]
            async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
                health = await client.get(f"http://127.0.0.1:{port}/healthz")
                health.raise_for_status()
                status = health.json()
                if not status.get("ok") or status.get("lastError"):
                    raise ConfigError(f"管理服务启动检查失败: {status.get('lastError') or '服务未就绪'}")
        except BaseException as error:
            await self._stop(slot)
            slot.state, slot.error = "error", str(error)
            raise

    def check_config(self, instance_id: str):
        row = super().check_config(instance_id)
        self.versions.resolve(row["version"])
        directory = self.directory(instance_id)
        config = AppConfig.load(directory / "config.json")
        listeners = [("127.0.0.1", row["port"])]
        # Endpoints are restored independently of the bridge. Reject all listener
        # conflicts before starting any part of the instance.
        for path in config.storage.data_dir.glob("accounts/*/onebot.json"):
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
        rows = []
        for path in sorted((self.root / "backups").glob("*.zip"), reverse=True):
            try:
                with zipfile.ZipFile(path) as archive:
                    row = json.loads(archive.read("instance.json"))
                rows.append({"name": path.name, "size": path.stat().st_size,
                             "instance_name": row["name"], "version": row.get("version", "未知")})
            except (ValueError, KeyError, zipfile.BadZipFile):
                rows.append({"name": path.name, "size": path.stat().st_size,
                             "instance_name": "损坏的备份", "version": "未知", "invalid": True})
        return rows

    def archives(self):
        rows = []
        for path in sorted((self.root / "archive").glob("*/instance.json")):
            row = json.loads(path.read_text(encoding="utf-8"))
            rows.append({**row, "archive_name": path.parent.name})
        return rows

    def _backup_path(self, name: str) -> Path:
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or ":" in name:
            raise ConfigError("备份名称无效")
        path = self.root / "backups" / name
        if not path.is_file() or not path.resolve().is_relative_to((self.root / "backups").resolve()):
            raise ConfigError("备份不存在")
        return path

    def _read_restore(self, source: Path, stage: Path) -> dict:
        extract_checked(source, stage)
        try:
            row = json.loads((stage / "instance.json").read_text(encoding="utf-8"))
            config = AppConfig.load(stage / "config.json")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ConfigError("备份缺少有效的实例配置") from error
        self.versions.resolve(row.get("version"))
        if row.get("mode") not in ("mock", "helper"):
            raise ConfigError("备份驱动无效")
        for path in (config.storage.data_dir, config.storage.media_dir):
            if not path.resolve().is_relative_to(stage.resolve()):
                raise ConfigError("备份数据目录必须位于实例目录内")
        return row

    async def restore(self, name: str | None = None, *, path: str | Path | None = None):
        source = Path(path) if path is not None else self._backup_path(name)
        async with self.guard, self.version_lock:
            staging = self.root / ".staging"
            staging.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="restore-", dir=staging) as temporary:
                stage = Path(temporary)
                row = await VersionStore._thread(self._read_restore, source, stage)
                instance_id = uuid.uuid4().hex
                directory = self.directory(instance_id)
                port = next((p for p in range(5100, 65000) if self.available(p)), None)
                self.validate_port(port)
                row.update(id=instance_id, name=self.validate_name(row["name"][:74] + " · 恢复"), port=port,
                           created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                config_path = stage / "config.json"
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                config["webui"].update(host="127.0.0.1", port=port)
                config["driver"]["type"] = row["mode"]
                # Normalize paths to the new instance, never retain old absolutes.
                for key in ("data_dir", "media_dir"):
                    config["storage"][key] = os.path.relpath((stage / config["storage"][key]).resolve(), stage)
                _atomic_write_json(config_path, config)
                _atomic_write_json(stage / "instance.json", row)
                self._bind_version(stage, self.versions.resolve(row["version"]))
                # Rebind again at final location because staging depth differs.
                (stage / "launcher.lock").unlink(missing_ok=True)
                (stage / "upgrade.json").unlink(missing_ok=True)
                stage.rename(directory)
                self._bind_version(directory, self.versions.resolve(row["version"]))
                return row

    async def restore_archive(self, archive_name: str):
        if Path(archive_name).name != archive_name or "\\" in archive_name or ":" in archive_name:
            raise ConfigError("归档名称无效")
        source = self.root / "archive" / archive_name
        if not source.is_dir() or source.resolve().parent != (self.root / "archive").resolve():
            raise ConfigError("归档不存在")
        with tempfile.TemporaryDirectory(prefix="archive-", dir=self.root) as temporary:
            package = Path(temporary) / "restore.zip"
            def pack():
                with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as output:
                    for item in source.rglob("*"):
                        if item.is_file() and item != source / "launcher.lock":
                            if not item.resolve().is_relative_to(source.resolve()):
                                raise ConfigError("归档包含越界链接")
                            output.write(item, item.relative_to(source))
            await VersionStore._thread(pack)
            return await self.restore(path=package)

    def _rollback(self, instance_id: str, backup: Path):
        directory = self.directory(instance_id)
        with tempfile.TemporaryDirectory(prefix="rollback-", dir=self.root) as temporary:
            stage = Path(temporary) / "restored"
            stage.mkdir()
            self._read_restore(backup, stage)
            damaged = Path(temporary) / "replaced"
            directory.rename(damaged)
            try:
                stage.rename(directory)
            except BaseException:
                damaged.rename(directory)
                raise
        self._bind_version(directory, self.versions.resolve(self.metadata(instance_id)["version"]))

    async def upgrade(self, instance_id: str, version: str):
        slot = self.slots.setdefault(instance_id, ProcessSlot())
        async with self.version_lock, slot.lock:
            self.require_stopped(instance_id)
            row = self.metadata(instance_id)
            project = self.versions.resolve(version)
            if row["version"] == version:
                raise ConfigError("实例已经使用该版本")
            backup = await VersionStore._thread(self._backup, instance_id)
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
                await VersionStore._thread(self._rollback, instance_id, self._backup_path(backup))
                slot.state, slot.error = "stopped", f"升级失败，已回滚: {error}"
                raise ConfigError(slot.error) from error

    def snapshot(self):
        return {"instances": self.rows(), "versions": self.versions.list(), "backups": self.backups(),
                "archives": self.archives(), "settings": self.settings(),
                "root": str(self.root), "version": __version__}

    async def close(self):
        _atomic_write_json(self.root / "running.json", {
            "instances": [r["id"] for r in self.rows() if r["state"] == "running"]})
        await super().close()
