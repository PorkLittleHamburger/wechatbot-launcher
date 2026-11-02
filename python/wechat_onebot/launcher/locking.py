"""Kernel-owned directory leases, released automatically on process exit."""
from __future__ import annotations

import os
from pathlib import Path

from ..config import ConfigError


class DirectoryLease:
    def __init__(self, root: Path, *, create: bool = True):
        if create:
            root.mkdir(parents=True, exist_ok=True)
        self.stream = (root / "launcher.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self.stream.close()
            raise ConfigError("该数据目录已有启动器或实例进程在运行") from error

    def close(self):
        self.stream.close()
