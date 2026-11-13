"""Launcher-owned read-only view of a gateway instance configuration.

The full gateway schema belongs to the Bot version selected for the instance,
which may be newer than the runtime bundled with the launcher. The launcher only
depends on the management WebUI address, the storage roots it keeps isolated
inside the instance directory, and the driver object it rewrites on restore.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..config import ConfigError

STORAGE_KEYS = ("data_dir", "media_dir")


@dataclass(frozen=True)
class InstanceConfig:
    raw: dict
    webui_host: str
    webui_port: int
    data_dir: Path
    media_dir: Path


def read_instance_config(path: str | Path) -> InstanceConfig:
    """Validate the launcher-relevant subset of config.json.

    The selected Bot runtime remains the only owner of its schema. Unknown
    driver types and fields added by newer Bot versions must not block the
    launcher, but values it relies on are rejected explicitly.
    """
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ConfigError(f"无法读取实例配置：{error}") from error
    if not isinstance(raw, dict):
        raise ConfigError("实例配置根节点必须是 JSON 对象")
    webui, driver, storage = raw.get("webui"), raw.get("driver"), raw.get("storage")
    if not isinstance(webui, dict) or not isinstance(driver, dict) or not isinstance(storage, dict):
        raise ConfigError("实例配置缺少 webui、driver 或 storage 对象")
    host, port = webui.get("host"), webui.get("port")
    if not isinstance(host, str) or not host.strip():
        raise ConfigError("webui.host 必须是非空字符串")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ConfigError("webui.port 必须是 1–65535 的整数")
    roots = []
    for key in STORAGE_KEYS:
        value = storage.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"storage.{key} 必须是非空路径字符串")
        roots.append((target.parent / value).resolve())
    return InstanceConfig(raw=raw, webui_host=host.strip(), webui_port=port,
                          data_dir=roots[0], media_dir=roots[1])
