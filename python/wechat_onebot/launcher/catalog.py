"""Read backup/archive catalogs without hiding damaged entries."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ..config import ConfigError


def instance_record(value: object) -> dict:
    if not isinstance(value, dict) or any(
        not isinstance(value.get(key), str) or not value[key]
        for key in ("id", "name", "version")
    ):
        raise ConfigError("实例清单缺少有效的 ID、名称或版本")
    return value


class CatalogReader:
    def __init__(self, root: Path):
        self.root = root

    def _entries(self, name: str) -> list[Path]:
        try:
            return sorted((self.root / name).iterdir())
        except FileNotFoundError:
            return []  # A catalog that has never been created is empty.

    def backups(self) -> list[dict]:
        rows = []
        for path in reversed(self._entries("backups")):
            if path.suffix.lower() != ".zip":
                continue
            size = 0
            try:
                size = path.stat().st_size
                with zipfile.ZipFile(path) as archive:
                    row = instance_record(json.loads(archive.read("instance.json")))
                rows.append({"name": path.name, "size": size,
                             "instance_name": row["name"], "version": row["version"]})
            except (OSError, ValueError, KeyError, zipfile.BadZipFile, RuntimeError, NotImplementedError, ConfigError) as error:
                rows.append({"name": path.name, "size": size, "instance_name": "无法读取的备份",
                             "version": "未知", "invalid": True, "error": str(error)})
        return rows

    def archives(self) -> list[dict]:
        rows = []
        for folder in self._entries("archive"):
            if not folder.is_dir():
                continue
            try:
                row = instance_record(json.loads((folder / "instance.json").read_text(encoding="utf-8")))
                rows.append({**row, "archive_name": folder.name})
            except (OSError, ValueError, ConfigError) as error:
                rows.append({"archive_name": folder.name, "id": "", "name": "无法读取的归档",
                             "version": "未知", "invalid": True, "error": str(error)})
        return rows


def collect_snapshot(providers: dict) -> tuple[dict, list[dict]]:
    sections, diagnostics = {}, []
    for name, read in providers.items():
        try:
            sections[name] = read()
        except (OSError, ValueError, ConfigError) as error:
            sections[name] = None if name == "settings" else []
            diagnostics.append({"section": name, "message": str(error)})
    return sections, diagnostics
