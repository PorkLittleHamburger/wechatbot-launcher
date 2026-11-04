"""Backup restoration and rollback transactions, separate from process ownership."""
from __future__ import annotations

import json
import tempfile
import time
import uuid
import zipfile
from pathlib import Path

from ..config import AppConfig, ConfigError, _atomic_write_json
from .async_utils import run_blocking
from .catalog import instance_record
from .instance_storage import bind_runtime
from .manager import LauncherManager
from .paths import identity_path, is_within, relative_storage_path, require_portable_relative
from .versions import VersionStore, extract_checked


class RestoreService:
    def __init__(self, lifecycle: LauncherManager, versions: VersionStore):
        self.lifecycle = lifecycle
        self.root = lifecycle.root
        self.versions = versions

    def backup_path(self, name: str) -> Path:
        if not isinstance(name, str) or Path(name).name != name or "\\" in name or ":" in name:
            raise ConfigError("备份名称无效")
        path = self.root / "backups" / name
        if not path.is_file() or not is_within(path, self.root / "backups"):
            raise ConfigError("备份不存在")
        return path

    def _read_restore(self, source: Path, stage: Path) -> dict:
        extract_checked(source, stage)
        try:
            row = instance_record(json.loads((stage / "instance.json").read_text(encoding="utf-8")))
            config = AppConfig.load(stage / "config.json")
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise ConfigError("备份缺少有效的实例配置") from error
        self.versions.resolve(row.get("version"))
        if row.get("mode") not in ("mock", "helper"):
            raise ConfigError("备份驱动无效")
        for path in (config.storage.data_dir, config.storage.media_dir):
            if not is_within(path, stage):
                raise ConfigError("备份数据目录必须位于实例目录内")
        return row

    async def restore(self, name: str | None = None, *, path: str | Path | None = None):
        source = Path(path) if path is not None else self.backup_path(name)
        async with self.versions.lock, self.lifecycle.guard:
            staging = self.root / ".staging"
            staging.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="restore-", dir=staging) as temporary:
                stage = Path(temporary)
                row = await run_blocking(self._read_restore, source, stage)
                instance_id = uuid.uuid4().hex
                directory = self.lifecycle.directory(instance_id)
                port = next((p for p in range(5100, 65000) if self.lifecycle.available(p)), None)
                self.lifecycle.validate_port(port)
                row.update(id=instance_id, name=self.lifecycle.validate_name(row["name"][:74] + " · 恢复"), port=port,
                           created_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
                config_path = stage / "config.json"
                config = json.loads(config_path.read_text(encoding="utf-8-sig"))
                config["webui"].update(host="127.0.0.1", port=port)
                config["driver"]["type"] = row["mode"]
                # Normalize paths to the new instance, never retain old absolutes.
                for key in ("data_dir", "media_dir"):
                    config["storage"][key] = relative_storage_path(stage / config["storage"][key], stage)
                _atomic_write_json(stage / "instance.json", row)
                bind_runtime(config, directory, self.versions.resolve(row["version"]))
                _atomic_write_json(config_path, config)
                (stage / "launcher.lock").unlink(missing_ok=True)
                (stage / "upgrade.json").unlink(missing_ok=True)
                stage.rename(directory)
                return row

    async def restore_archive(self, archive_name: str):
        if not isinstance(archive_name, str) or Path(archive_name).name != archive_name or "\\" in archive_name or ":" in archive_name:
            raise ConfigError("归档名称无效")
        source = self.root / "archive" / archive_name
        if not source.is_dir() or identity_path(source).parent != identity_path(self.root / "archive"):
            raise ConfigError("归档不存在")
        with tempfile.TemporaryDirectory(prefix="archive-", dir=self.root) as temporary:
            package = Path(temporary) / "restore.zip"
            def pack():
                with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as output:
                    for item in source.rglob("*"):
                        require_portable_relative(item.relative_to(source))
                        if item.is_file() and item != source / "launcher.lock":
                            if not is_within(item, source):
                                raise ConfigError("归档包含越界链接")
                            output.write(item, item.relative_to(source))
            await run_blocking(pack)
            return await self.restore(path=package)

    def rollback(self, instance_id: str, backup: Path):
        directory = self.lifecycle.directory(instance_id)
        with tempfile.TemporaryDirectory(prefix="rollback-", dir=self.root) as temporary:
            stage = Path(temporary) / "restored"
            stage.mkdir()
            self._read_restore(backup, stage)
            config_path = stage / "config.json"
            config = json.loads(config_path.read_text(encoding="utf-8-sig"))
            row = instance_record(json.loads((stage / "instance.json").read_text(encoding="utf-8")))
            bind_runtime(config, directory, self.versions.resolve(row["version"]))
            _atomic_write_json(config_path, config)
            damaged = Path(temporary) / "replaced"
            directory.rename(damaged)
            try:
                stage.rename(directory)
            except BaseException:
                damaged.rename(directory)
                raise

