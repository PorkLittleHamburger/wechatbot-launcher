"""Filesystem paths exposed to child processes and gateway configuration."""
from __future__ import annotations

import os
from pathlib import Path, PurePath, PureWindowsPath
import re


def process_path(path: Path) -> Path:
    """Prefer an equivalent short DOS path; never reinterpret UNC or devices.

    This is an interoperability conversion, not a local-path security validator.
    Only an existing, provably identical file/directory may lose its prefix.
    """
    resolved = path.resolve()
    value = str(resolved)
    if os.name == 'nt' and re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
        plain = value[4:]
        if len(plain.encode('utf-16-le', errors='surrogatepass')) // 2 < 260:
            candidate = Path(plain)
            try:
                if candidate.samefile(resolved):
                    return candidate
            except (OSError, ValueError):
                pass
    return resolved


def identity_path(path: Path) -> PurePath:
    """Comparable resolved names, never passed to filesystem I/O.

    Windows extended and ordinary spellings can identify the same location even
    for missing descendants. Resolve links first; preserve component spelling.
    """
    resolved = path.resolve()
    if os.name != 'nt':
        return PurePath(resolved)
    value = str(resolved)
    if value.startswith('\\\\?\\UNC\\'):
        value = '\\\\' + value[8:]
    elif re.match(r'^\\\\\?\\[A-Za-z]:\\', value):
        value = value[4:]
    return PureWindowsPath(value)


def is_within(path: Path, parent: Path) -> bool:
    return identity_path(path).is_relative_to(identity_path(parent))


def relative_storage_path(path: Path, parent: Path) -> str:
    relative = identity_path(path).relative_to(identity_path(parent))
    # Portable backups can be restored under an ordinary DOS workspace. Names
    # requiring verbatim semantics cannot safely be persisted as relative paths.
    if os.name == 'nt':
        require_portable_relative(relative)
    candidate = parent / str(relative)
    if identity_path(candidate) != identity_path(path):
        raise ValueError('存储路径无法等价转换为相对路径')
    if path.exists():
        try:
            same = candidate.samefile(path)
        except OSError:
            same = False
        if not same:
            raise ValueError('相对存储路径指向了不同的文件或目录')
    return str(relative)


def require_portable_relative(path: PurePath) -> None:
    """Reject raw archive spellings before any Win32 lookup can alias them."""
    if any(
        part.endswith(('.', ' ')) or re.match(
            r'^(?:CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])(?:\.|$)',
            part, re.IGNORECASE,
        ) for part in path.parts
    ):
        raise ValueError('路径含有依赖 Windows 扩展语义的特殊名称，无法安全备份、归档或恢复')
