"""Verified, immutable runtime packages for desktop instances."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath

import httpx

from ..config import ConfigError

MANIFEST = "VERSION_MANIFEST.json"
VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?")
PACKAGE_SCHEMA = 1
LAUNCHER_API = 1
RELEASE_REPO = "PorkLittleHamburger/wechatbot-launcher"
RELEASES_ATOM = f"https://github.com/{RELEASE_REPO}/releases.atom"
LAUNCHER_PACKAGE = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def runtime_asset_name(version: str) -> str:
    return f"wechat-onebot-runtime-{version}-windows-x64.zip"


def runtime_asset_url(tag: str, version: str) -> str:
    return f"https://github.com/{RELEASE_REPO}/releases/download/{tag}/{runtime_asset_name(version)}"


def prepare_legacy_runtime(directory: Path) -> None:
    required = {
        "python/python.exe",
        "src/wechat_onebot/__init__.py",
        "webui/index.html",
    }
    if any(not (directory / name).is_file() for name in required):
        raise ConfigError("ZIP 缺少兼容清单，且不是可识别的便携运行时包")
    init = directory / "src" / "wechat_onebot" / "__init__.py"
    match = re.search(r"(?m)^__version__\s*=\s*['\"]([^'\"]+)['\"]", init.read_text(encoding="utf-8"))
    if match is None or not VERSION_RE.fullmatch(match.group(1)):
        raise ConfigError("ZIP 中无法识别网关版本号")
    for temporary in directory.rglob("__pycache__"):
        if temporary.is_dir():
            shutil.rmtree(temporary)
    for compiled in directory.rglob("*.pyc"):
        compiled.unlink(missing_ok=True)
    launcher = directory / "src" / "wechat_onebot" / "launcher"
    if launcher.exists():
        shutil.rmtree(launcher)
    shutil.copytree(
        LAUNCHER_PACKAGE,
        launcher,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    files = {}
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.name != MANIFEST:
            files[path.relative_to(directory).as_posix()] = digest(path)
    manifest = {
        "package_schema": PACKAGE_SCHEMA,
        "launcher_api": LAUNCHER_API,
        "version": match.group(1),
        "architecture": "windows-x64",
        "files": files,
    }
    (directory / MANIFEST).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    seen: set[str] = set()
    result = []
    total = 0
    for item in archive.infolist():
        name = item.filename
        path = PurePosixPath(name)
        mode = item.external_attr >> 16
        if ("\\" in name or ":" in name or path.is_absolute() or ".." in path.parts
                or not path.parts or stat.S_ISLNK(mode)):
            raise ConfigError("归档包含无效路径或符号链接")
        key = name.rstrip("/").casefold()
        if key in seen:
            raise ConfigError("归档包含重复路径")
        seen.add(key)
        total += item.file_size
        if len(seen) > 100_000 or total > 8 * 1024**3:
            raise ConfigError("归档超过大小限制")
        result.append(item)
    return result


def extract_checked(source: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(source) as archive:
            members = safe_members(archive)
            for item in members:
                target = destination.joinpath(*PurePosixPath(item.filename).parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise ConfigError("归档路径越界")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(item) as incoming, target.open("xb") as output:
                        shutil.copyfileobj(incoming, output)
    except (zipfile.BadZipFile, RuntimeError) as error:
        raise ConfigError(f"归档无法读取: {error}") from error


def validate_manifest(directory: Path, *, verify: bool = True) -> dict:
    try:
        raw = json.loads((directory / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ConfigError("版本包缺少有效兼容清单，不兼容当前启动器") from error
    if (raw.get("package_schema") != PACKAGE_SCHEMA or raw.get("launcher_api") != LAUNCHER_API
            or raw.get("architecture") != "windows-x64"
            or not isinstance(raw.get("version"), str) or not VERSION_RE.fullmatch(raw["version"])):
        raise ConfigError("版本包格式、平台或启动器协议不兼容")
    files = raw.get("files")
    required = {"python/python.exe", "src/wechat_onebot/launcher/worker.py",
                "src/wechat_onebot/__init__.py", "webui/index.html"}
    if not isinstance(files, dict) or not required.issubset(files):
        raise ConfigError("版本包缺少 Python、启动入口或 WebUI")
    seen = set()
    for name, expected in files.items():
        path = PurePosixPath(name)
        if (not name or "\\" in name or ":" in name or path.is_absolute()
                or ".." in path.parts or name.casefold() in seen
                or not isinstance(expected, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", expected)):
            raise ConfigError("版本校验清单无效")
        seen.add(name.casefold())
        target = directory.joinpath(*path.parts)
        if target.is_symlink() or not target.resolve().is_relative_to(directory.resolve()):
            raise ConfigError("版本文件路径越界")
        if verify and (not target.is_file() or digest(target) != expected.lower()):
            raise ConfigError(f"版本文件校验失败: {name}")
    if verify:
        actual = {p.relative_to(directory).as_posix() for p in directory.rglob("*") if p.is_file()}
        if actual != set(files) | {MANIFEST}:
            raise ConfigError("版本包包含未校验文件")
    return raw


class VersionStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.directory = self.root / "versions"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = asyncio.Lock()

    def resolve(self, version: str) -> Path:
        if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
            raise ConfigError("版本号无效")
        path = self.directory / version
        if path.resolve().parent != self.directory or not (path / MANIFEST).is_file():
            raise ConfigError(f"版本尚未安装: {version}")
        validate_manifest(path, verify=False)
        return path

    def list(self) -> list[dict]:
        rows = []
        for path in sorted(self.directory.iterdir()):
            if path.is_dir() and VERSION_RE.fullmatch(path.name):
                raw = validate_manifest(path, verify=False)
                rows.append({"version": raw["version"], "path": str(path),
                             "architecture": raw["architecture"], "compatible": True})
        return rows

    def _install(self, source: Path, *, archive: bool, expected_version: str | None = None, replace: bool = False, referenced_versions: set[str] | None = None) -> dict:
        staging = self.root / ".staging"
        staging.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="version-", dir=staging) as temporary:
            stage = Path(temporary) / "payload"
            stage.mkdir()
            if archive:
                extract_checked(source, stage)
            else:
                shutil.copytree(source, stage, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            if not (stage / MANIFEST).is_file():
                children = list(stage.iterdir())
                if (len(children) == 1 and children[0].is_dir()
                        and ((children[0] / MANIFEST).is_file()
                             or children[0].name not in {"python", "src", "webui", "vendor", "assets"})):
                    stage = children[0]
            if not (stage / MANIFEST).is_file():
                prepare_legacy_runtime(stage)
            raw = validate_manifest(stage)
            if expected_version is not None and raw["version"] != expected_version:
                raise ConfigError("下载包版本号与所选版本不一致")
            target = self.directory / raw["version"]
            if target.exists():
                installed = validate_manifest(target)
                if installed != raw:
                    if not replace:
                        raise ConfigError("相同版本号已安装不同内容，请使用不同版本号")
                    if referenced_versions and raw["version"] in referenced_versions:
                        raise ConfigError("该版本仍被实例或归档引用，不能替换")
                    previous = Path(temporary) / "previous"
                    target.rename(previous)
                    try:
                        stage.rename(target)
                    except BaseException:
                        previous.rename(target)
                        raise
            else:
                stage.rename(target)
            return {"version": raw["version"], "path": str(target), "compatible": True}

    async def import_archive(self, path: Path, *, replace: bool = False,
                            referenced_versions: set[str] | None = None) -> dict:
        async with self.lock:
            return await self._thread(self._install, Path(path), archive=True,
                                        replace=replace, referenced_versions=referenced_versions)

    async def import_directory(self, path: Path) -> dict:
        async with self.lock:
            return await self._thread(self._install, Path(path), archive=False)

    @staticmethod
    async def _thread(function, *args, **kwargs):
        task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    def remove(self, version: str, referenced_versions: set[str]):
        if version in referenced_versions:
            raise ConfigError("该版本仍被实例或归档引用，不能删除")
        path = self.resolve(version)
        shutil.rmtree(path)

    async def releases(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(RELEASES_ATOM, headers={"Accept": "application/atom+xml"})
                response.raise_for_status()
                try:
                    feed = ET.fromstring(response.text)
                except ET.ParseError as error:
                    raise ConfigError("版本列表响应格式无效") from error
                namespace = "{http://www.w3.org/2005/Atom}"
                rows = []
                for entry in feed.findall(f"{namespace}entry"):
                    link = entry.find(f"{namespace}link")
                    href = link.get("href", "") if link is not None else ""
                    match = re.search(r"/releases/tag/([^/?#]+)$", href)
                    if match is None:
                        continue
                    tag = match.group(1)
                    if not tag.startswith("v"):
                        continue
                    version = tag[1:]
                    if not VERSION_RE.fullmatch(version):
                        continue
                    asset = runtime_asset_url(tag, version)
                    try:
                        probe = await client.head(asset)
                    except httpx.HTTPError:
                        compatible, size = False, 0
                    else:
                        compatible = probe.status_code == 200
                        try:
                            size = int(probe.headers.get("Content-Length", "0")) if compatible else 0
                        except ValueError:
                            size = 0
                    rows.append({
                        "version": version,
                        "name": entry.findtext(f"{namespace}title") or tag,
                        "published_at": entry.findtext(f"{namespace}updated"),
                        "compatible": compatible,
                        "reason": "" if compatible else "没有兼容的桌面运行时包",
                        "download_url": asset if compatible else None,
                        "size": size,
                        "digest": None,
                    })
                return {"releases": rows}
        except httpx.HTTPError as error:
            raise ConfigError(f"获取版本列表失败: {error}") from error

    async def download(self, version: str, progress) -> dict:
        releases = (await self.releases())["releases"]
        release = next((r for r in releases if r["version"] == version and r["compatible"]), None)
        if release is None:
            raise ConfigError("没有可下载的兼容版本")
        downloads = self.root / "downloads"
        downloads.mkdir(exist_ok=True)
        path = downloads / f"{version}.zip.partial"
        async with self.lock:
            try:
                async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
                    async with client.stream("GET", release["download_url"]) as response:
                        response.raise_for_status()
                        received = 0
                        with path.open("wb") as output:
                            async for chunk in response.aiter_bytes(1024 * 1024):
                                received += len(chunk)
                                if received > 2 * 1024**3:
                                    raise ConfigError("下载超过大小限制")
                                output.write(chunk)
                                progress(f"下载 {version}", min(90, int(received * 90 / max(1, release["size"]))))
                expected = release.get("digest")
                if expected and expected.startswith("sha256:") and digest(path) != expected[7:]:
                    raise ConfigError("下载文件 SHA-256 不匹配")
                result = await self._thread(self._install, path, archive=True, expected_version=version)
                return result
            finally:
                path.unlink(missing_ok=True)
