"""Launcher development namespace; gateway modules are loaded from an external runtime."""
from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.8.2"
APP_NAME = "wechatbot"


def extend_runtime_path() -> None:
    raw = os.environ.get("WECHAT_RUNTIME_SOURCE")
    if not raw:
        return
    root = Path(raw).resolve()
    for candidate in (root / "src" / "wechat_onebot", root / "bot" / "src" / "wechat_onebot"):
        if (candidate / "__init__.py").is_file() and str(candidate) not in __path__:
            __path__.append(str(candidate))
            return


extend_runtime_path()
