"""User-initiated desktop installer updates, separate from gateway versions."""
from __future__ import annotations

import asyncio
import hashlib
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET

import httpx

from ..config import ConfigError
from . import __version__
from .versions import digest

RELEASE_REPO = "PorkLittleHamburger/wechatbot-launcher"
RELEASES_ATOM = f"https://github.com/{RELEASE_REPO}/releases.atom"

MAX_INSTALLER = 1024 ** 3
CHECK_TIMEOUT = 60
DOWNLOAD_TIMEOUT = 600
STABLE = re.compile(r"\d+\.\d+\.\d+")


class PlainNotes(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


class LauncherUpdater:
    def __init__(self, root: Path):
        self.directory = root / "launcher-updates"
        self.lock = asyncio.Lock()
        self.ready: dict | None = None

    @staticmethod
    def asset(version: str) -> str:
        if not isinstance(version, str) or not STABLE.fullmatch(version):
            raise ConfigError("启动器版本号无效")
        return f"WeChat-OneBot-Launcher_{version}_x64-setup.exe"

    @classmethod
    def url(cls, version: str) -> str:
        return f"https://github.com/{RELEASE_REPO}/releases/download/v{version}/{cls.asset(version)}"

    async def check(self) -> dict:
        try:
            return await asyncio.wait_for(self._check(), CHECK_TIMEOUT)
        except asyncio.TimeoutError as error:
            raise ConfigError("检查启动器更新超时，请重试") from error

    @staticmethod
    async def limited_get(client, url, limit):
        content = bytearray()
        async with client.stream('GET', url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(65536):
                content.extend(chunk)
                if len(content) > limit:
                    raise ConfigError("更新元数据超过大小限制")
        return bytes(content)

    async def _check(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                feed = ET.fromstring(await self.limited_get(client, RELEASES_ATOM, 2 * 1024 ** 2))
                latest = await client.head(f"https://github.com/{RELEASE_REPO}/releases/latest")
                latest.raise_for_status()
                latest_tag = re.search(r'/tag/v(\d+\.\d+\.\d+)$', str(latest.url))
                newer = latest_tag and tuple(map(int, latest_tag[1].split('.'))) > tuple(map(int, __version__.split('.')))
                ns = "{http://www.w3.org/2005/Atom}"
                candidates = []
                for entry in feed.findall(f"{ns}entry")[:20]:
                    link = entry.find(f"{ns}link")
                    href = link.get("href", "") if link is not None else ""
                    match = re.fullmatch(
                        rf"https://github.com/{re.escape(RELEASE_REPO)}/releases/tag/v(\d+\.\d+\.\d+)", href,
                    )
                    if (match and href == str(latest.url)
                            and tuple(map(int, match[1].split('.'))) > tuple(map(int, __version__.split('.')))):
                        notes = PlainNotes()
                        notes.feed(entry.findtext(f"{ns}content") or "")
                        candidates.append({"version": match[1], "name": entry.findtext(f"{ns}title") or match[1],
                                           "notes": "\n".join(notes.parts)[:30000], "url": href})
                for row in sorted(candidates, key=lambda r: tuple(map(int, r['version'].split('.'))), reverse=True):
                    installer = await client.head(self.url(row['version']))
                    checksum = await client.head(self.url(row['version']) + '.sha256')
                    if installer.status_code == 200 and checksum.status_code == 200:
                        return {"current_version": __version__, "release": row}
                    raise ConfigError("最新正式发布暂无完整安装器和校验文件，请稍后重试")
                if newer:
                    raise ConfigError("最新正式版的发布列表尚在同步，请稍后重试")
                return {"current_version": __version__, "release": None}
        except (httpx.HTTPError, ET.ParseError) as error:
            raise ConfigError(f"检查启动器更新失败，可重试：{error}") from error

    async def download(self, version: str, progress=lambda message, value: None) -> dict:
        try:
            return await asyncio.wait_for(self._download(version, progress), DOWNLOAD_TIMEOUT)
        except asyncio.TimeoutError as error:
            raise ConfigError("启动器下载总时间超过 10 分钟，已取消并清理，请重试") from error

    async def _download(self, version: str, progress) -> dict:
        async with self.lock:
            self.ready = None
            available = (await self.check())["release"]
            if available is None or available['version'] != version:
                raise ConfigError("请选择最新可用的启动器版本并重新检查更新")
            self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.directory.is_symlink() or getattr(self.directory.lstat(), 'st_file_attributes', 0) & 0x400:
                raise ConfigError("更新目录无效")
            self.prune_cache()
            download_dir = Path(tempfile.mkdtemp(prefix='download-', dir=self.directory))
            target = download_dir / self.asset(version)
            partial = target.with_suffix('.exe.partial')
            if target.is_symlink() or partial.is_symlink():
                raise ConfigError("更新文件不能是符号链接")
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                    checksum = await self.limited_get(client, self.url(version) + '.sha256', 4096)
                    match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?" + re.escape(target.name) + r"\s*", checksum.decode('utf-8'))
                    if match is None:
                        raise ConfigError("安装器 SHA-256 校验文件无效")
                    expected = match[1].lower()
                    digest = hashlib.sha256()
                    received = 0
                    async with client.stream('GET', self.url(version)) as response:
                        response.raise_for_status()
                        total = int(response.headers.get('Content-Length', '0'))
                        with partial.open('xb') as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                received += len(chunk)
                                if received > MAX_INSTALLER:
                                    raise ConfigError("启动器安装器超过大小限制")
                                output.write(chunk)
                                digest.update(chunk)
                                progress('下载启动器安装器', min(95, received * 95 // max(1, total)))
                    if received == 0 or (total and total != received) or digest.hexdigest() != expected:
                        raise ConfigError("安装器不完整或 SHA-256 不匹配，请重新下载")
                    partial.replace(target)
                    self.ready = {"version": version, "path": str(target.resolve()), "sha256": expected, "size": received}
                    progress('启动器安装器已校验', 100)
                    return dict(self.ready)
            except (httpx.HTTPError, OSError, ValueError) as error:
                raise ConfigError(f"下载启动器更新失败，可重试：{error}") from error
            finally:
                partial.unlink(missing_ok=True)
                if self.ready is None and download_dir.resolve().parent == self.directory.resolve():
                    shutil.rmtree(download_dir)

    def prune_cache(self):
        # Keep two completed downloads before creating the next one. Installation
        # handoffs are leased; retain their diagnostic logs after completion.
        candidates = []
        for path in self.directory.glob('download-*'):
            if (not path.is_dir() or path.is_symlink()
                    or getattr(path.lstat(), 'st_file_attributes', 0) & 0x400
                    or path.resolve().parent != self.directory.resolve()
                    or (path / '.handoff').exists()):
                continue
            if (path / 'install.log').exists():
                for installer in path.glob('*.exe'):
                    installer.unlink(missing_ok=True)
            else:
                candidates.append(path)
        for path in sorted(candidates, key=lambda p: p.stat().st_mtime_ns, reverse=True)[2:]:
            shutil.rmtree(path)

    async def prepare_install(self) -> dict:
        async with self.lock:
            if self.ready is None:
                raise ConfigError("请先下载并校验启动器安装器")
            path = Path(self.ready['path'])
            if path.is_symlink() or not path.resolve().is_relative_to(self.directory.resolve()):
                raise ConfigError("启动器安装器路径无效")
            try:
                actual = await asyncio.to_thread(digest, path)
            except OSError as error:
                raise ConfigError("安装器不可读，请重新下载") from error
            if actual != self.ready['sha256']:
                self.ready = None
                raise ConfigError("安装器已改变，请重新下载")
            return dict(self.ready)
