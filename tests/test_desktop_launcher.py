"""Offline desktop packages and lifecycle tests; never start a real helper."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import warnings
import zipfile
from pathlib import Path

import httpx
import pytest

from wechat_onebot.config import AppConfig, ConfigError


ROOT = Path(__file__).resolve().parents[1]
PROJECT = Path(os.environ["WECHAT_RUNTIME_SOURCE"]).resolve()
MANIFEST = "VERSION_MANIFEST.json"


def write_manifest(directory, version="0.8.0", **overrides):
    files = {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in directory.rglob("*") if p.is_file() and p.name != MANIFEST}
    manifest = {"package_schema": 1, "launcher_api": 1, "version": version,
                "architecture": "windows-x64", "files": files, **overrides}
    (directory / MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


@pytest.mark.asyncio
async def test_release_catalog_uses_atom_feed_without_api_rate_limit(store, monkeypatch):
    client_class = httpx.AsyncClient
    requests = []
    atom = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <updated>2026-11-03T02:19:46Z</updated>
        <link rel="alternate" href="https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.8.0"/>
        <title>WeChat OneBot Launcher 0.8.0</title>
      </entry>
    </feed>"""

    def catalog(request):
        requests.append((request.method, str(request.url)))
        if request.url.path.endswith("/releases.atom"):
            return httpx.Response(200, text=atom)
        if request.method == "HEAD" and request.url.path.endswith(
                "/wechat-onebot-runtime-0.8.0-windows-x64.zip"):
            return httpx.Response(200, headers={"Content-Length": "12345"})
        return httpx.Response(404)

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(catalog)
        return client_class(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    assert await store.releases() == {"releases": [{
        "version": "0.8.0",
        "name": "WeChat OneBot Launcher 0.8.0",
        "published_at": "2026-11-03T02:19:46Z",
        "compatible": True,
        "reason": "",
        "download_url": "https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/download/v0.8.0/wechat-onebot-runtime-0.8.0-windows-x64.zip",
        "size": 12345,
        "digest": None,
    }]}
    assert requests[0][0] == "GET"
    assert requests[0][1].endswith("/wechatbot-launcher/releases.atom")
    assert all("api.github.com" not in url for _, url in requests)


@pytest.fixture
def bundle_factory(tmp_path):
    def make(version="0.8.0", *, runnable=False):
        directory = tmp_path / f"bundle-{version}"
        directory.mkdir()
        if runnable:
            shutil.copytree(PROJECT / "src" / "wechat_onebot", directory / "src" / "wechat_onebot",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            shutil.copytree(ROOT / "python/wechat_onebot/launcher",
                            directory / "src/wechat_onebot/launcher", dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            init = directory / "src" / "wechat_onebot" / "__init__.py"
            init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{version}"',
                                   init.read_text(encoding="utf-8")), encoding="utf-8")
        else:
            for name, content in {
                "src/wechat_onebot/launcher/worker.py": b"# offline package fixture\n",
                "src/wechat_onebot/__init__.py": f'__version__ = "{version}"\n'.encode(),
            }.items():
                path = directory / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        for name, content in {
            "python/python.exe": b"offline placeholder; never execute this file",
            "webui/index.html": b"<html>offline package fixture</html>",
            "vendor/helper/VENDORED_COMMIT.txt": b"offline-test-fixture\n",
        }.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        write_manifest(directory, version)
        return directory
    return make


def zip_bundle(directory, target, *, extra=(), prefix=""):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)  # Deliberately test duplicate entries.
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in directory.rglob("*"):
                if path.is_file():
                    archive.write(path, prefix + path.relative_to(directory).as_posix())
            for name, content in extra:
                archive.writestr(name, content)
    return target


@pytest.fixture
def store(tmp_path):
    from wechat_onebot.launcher.versions import VersionStore
    return VersionStore(tmp_path / "store")


def tree_bytes(root):
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*") if p.is_file()}


@pytest.mark.asyncio
async def test_import_version_verifies_content_and_survives_new_store(store, bundle_factory, tmp_path):
    from wechat_onebot.launcher.versions import VersionStore
    bundle = bundle_factory()
    expected = tree_bytes(bundle)
    archive = zip_bundle(bundle, tmp_path / "runtime.zip")
    original = archive.read_bytes()
    row = await store.import_archive(archive)
    assert row["version"] == "0.8.0"
    assert [r["version"] for r in store.list()] == ["0.8.0"]
    installed = store.resolve("0.8.0")
    assert installed.is_dir()
    assert tree_bytes(installed) == expected
    assert archive.read_bytes() == original
    assert tree_bytes(bundle) == expected
    reopened = VersionStore(tmp_path / "store")
    assert reopened.resolve("0.8.0") == installed
    assert [r["version"] for r in reopened.list()] == ["0.8.0"]


@pytest.mark.asyncio
async def test_import_keeps_versions_independent_and_refuses_overwrite(store, bundle_factory, tmp_path):
    for version in ("0.8.0", "0.9.0"):
        await store.import_archive(zip_bundle(bundle_factory(version), tmp_path / f"{version}.zip"))
    first = store.resolve("0.8.0")
    second = store.resolve("0.9.0")
    assert first != second
    before = tree_bytes(first)
    assert (await store.import_archive(tmp_path / "0.8.0.zip"))["version"] == "0.8.0"
    replacement = tmp_path / "bundle-0.8.0"
    (replacement / "webui/index.html").write_bytes(b"different content under the same version")
    write_manifest(replacement)
    with pytest.raises(ConfigError):
        await store.import_archive(zip_bundle(replacement, tmp_path / "replacement.zip"))
    assert tree_bytes(first) == before
    assert {r["version"] for r in store.list()} == {"0.8.0", "0.9.0"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    {"package_schema": 2}, {"launcher_api": 999}, {"architecture": "linux-x64"},
    {"version": "../escape"}, {"version": "C:/escape"},
])
async def test_incompatible_or_unsafe_manifest_rejected(store, bundle_factory, tmp_path, change):
    bundle = bundle_factory()
    write_manifest(bundle, **change)
    with pytest.raises(ConfigError):
        await store.import_archive(zip_bundle(bundle, tmp_path / "invalid.zip"))
    assert store.list() == []
    assert not (tmp_path / "escape").exists()


@pytest.mark.asyncio
async def test_same_version_replacement_requires_no_references(store, bundle_factory, tmp_path):
    await store.import_archive(zip_bundle(bundle_factory(), tmp_path / "original.zip"))
    replacement = tmp_path / "replacement"
    shutil.copytree(store.resolve("0.8.0"), replacement)
    (replacement / "webui/index.html").write_text("<html>replacement</html>", encoding="utf-8")
    write_manifest(replacement)
    archive = zip_bundle(replacement, tmp_path / "replacement.zip")
    with pytest.raises(ConfigError, match="不同内容"):
        await store.import_archive(archive)
    with pytest.raises(ConfigError, match="引用"):
        await store.import_archive(archive, replace=True, referenced_versions={"0.8.0"})
    await store.import_archive(archive, replace=True, referenced_versions=set())
    assert (store.resolve("0.8.0") / "webui/index.html").read_text() == "<html>replacement</html>"


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["hash", "missing", "unlisted"])
async def test_manifest_integrity_rejected_atomically(store, bundle_factory, tmp_path, corruption):
    bundle = bundle_factory()
    worker = bundle / "src/wechat_onebot/launcher/worker.py"
    if corruption == "hash":
        worker.write_bytes(b"tampered worker")
    elif corruption == "missing":
        worker.unlink()
    else:
        (bundle / "unlisted.py").write_bytes(b"unlisted payload")
    with pytest.raises(ConfigError):
        await store.import_archive(zip_bundle(bundle, tmp_path / "corrupt.zip"))
    assert store.list() == []
    with pytest.raises(ConfigError):
        store.resolve("0.8.0")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [
    "../escape.txt", "../../escape.txt", "..\\escape.txt", "/escape.txt",
    "C:/escape.txt", "C:escape.txt", "\\\\server\\share\\escape.txt",
    "src/../../escape.txt", "src/wechat_onebot/launcher/worker.py",
    "SRC/WECHAT_ONEBOT/LAUNCHER/WORKER.PY",
    "data/name./payload.bin", "data/name /payload.bin", "data/CON.txt",
    "data/LPT¹.txt", "data/aux/payload.bin",
])
async def test_unsafe_or_duplicate_zip_paths_rejected(store, bundle_factory, tmp_path, entry):
    bundle = bundle_factory()
    archive = zip_bundle(bundle, tmp_path / "unsafe.zip", extra=[(entry, b"unsafe")])
    with pytest.raises(ConfigError):
        await store.import_archive(archive)
    assert store.list() == []
    assert not (tmp_path / "escape.txt").exists()


@pytest.mark.asyncio
async def test_zip_symlink_rejected(store, bundle_factory, tmp_path):
    bundle = bundle_factory()
    archive = zip_bundle(bundle, tmp_path / "symlink.zip")
    link = zipfile.ZipInfo("vendor/helper/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "a") as output:
        output.writestr(link, "../../../escape")
    with pytest.raises(ConfigError):
        await store.import_archive(archive)
    assert store.list() == []


@pytest.mark.asyncio
async def test_referenced_version_cannot_be_removed(store, bundle_factory, tmp_path):
    await store.import_archive(zip_bundle(bundle_factory(), tmp_path / "runtime.zip"))
    before = tree_bytes(store.resolve("0.8.0"))
    with pytest.raises(ConfigError):
        store.remove("0.8.0", referenced_versions={"0.8.0"})
    assert tree_bytes(store.resolve("0.8.0")) == before
    store.remove("0.8.0", referenced_versions=set())
    assert store.list() == []
    with pytest.raises(ConfigError):
        store.resolve("0.8.0")


@pytest.mark.asyncio
async def test_single_wrapped_bundle_imports_and_failed_import_leaves_no_staging(store, bundle_factory, tmp_path):
    bundle = bundle_factory()
    await store.import_archive(zip_bundle(bundle, tmp_path / "wrapped.zip", prefix="runtime/"))
    before = tree_bytes(store.resolve("0.8.0"))
    malformed = tmp_path / "not-a-zip.zip"
    malformed.write_bytes(b"not a ZIP file")
    with pytest.raises(ConfigError):
        await store.import_archive(malformed)
    assert tree_bytes(store.resolve("0.8.0")) == before
    assert list((store.root / ".staging").iterdir()) == []


@pytest.mark.asyncio
async def test_legacy_portable_zip_gets_manifest_and_launcher_runtime(store, tmp_path):
    bundle = tmp_path / "legacy-runtime"
    for name, content in {
        "python/python.exe": b"offline placeholder",
        "src/wechat_onebot/__init__.py": b'__version__ = "0.8.1"\n',
        "src/wechat_onebot/service.py": b"# gateway fixture\n",
        "webui/index.html": b"<html>gateway</html>",
        "vendor/helper/VENDORED_COMMIT.txt": b"fixture\n",
    }.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    archive = zip_bundle(bundle, tmp_path / "legacy.zip", prefix="wechat-onebot-0.8.1-windows-x64/")
    row = await store.import_archive(archive)
    assert row["version"] == "0.8.1"
    installed = store.resolve("0.8.1")
    manifest = json.loads((installed / "VERSION_MANIFEST.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "0.8.1"
    assert manifest["launcher_api"] == 1
    assert (installed / "src/wechat_onebot/launcher/worker.py").is_file()
    assert (installed / "webui/index.html").is_file()
    assert store.list()[0]["compatible"] is True


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL inheritance")
@pytest.mark.parametrize("archive", [False, True])
@pytest.mark.asyncio
async def test_published_runtime_inherits_workspace_reader_acl(store, bundle_factory, tmp_path, archive):
    staging = store.root / ".staging"
    staging.mkdir(parents=True)
    # Only an isolated test directory is changed; no installed runtime or user data.
    subprocess.run(
        ["icacls.exe", str(staging), "/grant", "*S-1-5-32-545:(OI)(CI)(RX)"],
        check=True, capture_output=True,
    )
    bundle = bundle_factory()
    if archive:
        await store.import_archive(zip_bundle(bundle, tmp_path / "runtime.zip"))
    else:
        await store.import_directory(bundle)
    for path in (store.resolve("0.8.0"), store.resolve("0.8.0") / "python/python.exe"):
        env = os.environ.copy()
        env["WECHAT_TEST_ACL"] = str(path)
        env.pop("PSModulePath", None)
        preferred = Path("D:/Tool/PowerShell-7.6.6-win-x64/pwsh.exe")
        shell = str(preferred) if preferred.is_file() else (shutil.which("pwsh") or "powershell.exe")
        result = subprocess.run(
            [shell, "-NoProfile", "-NonInteractive", "-Command",
             "$ErrorActionPreference='Stop'; @((Get-Acl -LiteralPath $env:WECHAT_TEST_ACL).Access | Where-Object { "
             "$_.IsInherited -and $_.IdentityReference.Translate("
             "[Security.Principal.SecurityIdentifier]).Value -eq 'S-1-5-32-545' }).Count"],
            env=env, capture_output=True, text=True, check=True, timeout=30,
        )
        assert int(result.stdout.strip()) > 0, f"Workspace reader ACL lost on {path}"
    assert list(staging.iterdir()) == []


@pytest.mark.parametrize("failure", ["copy", "rename"])
@pytest.mark.asyncio
async def test_publication_failure_preserves_previous_runtime(store, bundle_factory, tmp_path, monkeypatch, failure):
    await store.import_directory(bundle_factory())
    installed = store.resolve("0.8.0")
    before = tree_bytes(installed)
    replacement = tmp_path / "replacement"
    shutil.copytree(installed, replacement)
    (replacement / "webui/index.html").write_text("replacement", encoding="utf-8")
    write_manifest(replacement)
    original_copy = shutil.copytree
    original_rename = Path.rename

    def copy(source, destination, *args, **kwargs):
        if Path(destination).name.startswith("publish-"):
            Path(destination).mkdir()
            raise OSError("publication copy failed")
        return original_copy(source, destination, *args, **kwargs)

    def rename(source, destination):
        if source.name.startswith("publish-"):
            raise OSError("publication rename failed")
        return original_rename(source, destination)

    if failure == "copy":
        monkeypatch.setattr(shutil, "copytree", copy)
    else:
        monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError, match="publication"):
        await store.import_archive(
            zip_bundle(replacement, tmp_path / "replacement.zip"), replace=True,
        )
    assert tree_bytes(installed) == before
    assert list((store.root / ".staging").iterdir()) == []


def free_port():
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
async def desktop(tmp_path, bundle_factory, monkeypatch):
    from wechat_onebot.launcher.desktop_manager import DesktopManager
    bundled = bundle_factory("0.8.0", runnable=True)
    manager = DesktopManager(tmp_path / "desktop", bundled)
    await manager.initialize()
    await manager.versions.import_directory(bundled)
    launch_parameters = manager._launch_parameters
    launches = []

    def offline_python(instance_id):
        python, project = launch_parameters(instance_id)
        # Verify real version selection, replacing only the dummy embedded interpreter.
        assert Path(python) == project / "python" / "python.exe"
        assert Path(python).is_file()
        launches.append((instance_id, Path(python), project))
        return sys.executable, project

    monkeypatch.setattr(manager, "_launch_parameters", offline_python)
    manager._test_launches = launches
    try:
        yield manager
    finally:
        await manager.close()


async def add_version(manager, bundle_factory, tmp_path, version="0.9.0"):
    bundle = bundle_factory(version, runnable=True)
    return await manager.versions.import_archive(zip_bundle(bundle, tmp_path / f"{version}.zip"))


@pytest.mark.asyncio
async def test_desktop_preserves_explicitly_imported_version_on_reopen(desktop):
    versions = desktop.versions.list()
    assert [row["version"] for row in versions] == ["0.8.0"]
    await desktop.initialize()
    assert desktop.versions.list() == versions
    row = await desktop.create("Bundled Mock", "mock", free_port())
    assert row["version"] == "0.8.0"
    assert desktop.metadata(row["id"])["version"] == "0.8.0"
    assert desktop.rows()[0]["state"] == "stopped"


@pytest.mark.asyncio
async def test_new_workspace_never_installs_bundled_gateway(tmp_path, bundle_factory):
    from wechat_onebot.launcher.desktop_manager import DesktopManager
    manager = DesktopManager(tmp_path / 'empty-workspace', bundle_factory())
    try:
        await manager.initialize()
        await manager.initialize()
        assert manager.snapshot()['versions'] == []
        assert list(manager.versions.directory.iterdir()) == []
        with pytest.raises(ConfigError, match='下载或导入'):
            await manager.create('No implicit version', 'helper', free_port())
        assert manager.rows() == []
        await manager.versions.import_directory(manager.project)
        row = await manager.create('Explicit import', 'mock', free_port())
        before = {name: (manager.directory(row['id']) / name).read_bytes()
                  for name in ('config.json', 'instance.json')}
        await manager.initialize()
        assert {name: (manager.directory(row['id']) / name).read_bytes() for name in before} == before
        assert [v['version'] for v in manager.versions.list()] == ['0.8.0']
    finally:
        await manager.close()


@pytest.mark.skipif(os.name != 'nt', reason='Windows verbatim paths')
@pytest.mark.asyncio
async def test_legacy_verbatim_workspace_uses_plain_gateway_paths(tmp_path, bundle_factory):
    from wechat_onebot.launcher.desktop_manager import DesktopManager
    from wechat_onebot.launcher.paths import process_path
    root = tmp_path / '工作空间 with spaces'
    root.mkdir()
    project = bundle_factory()
    prefix = chr(92) * 2 + '?' + chr(92)
    manager = DesktopManager(Path(prefix + str(root)), Path(prefix + str(project)))
    try:
        assert manager.root == root.resolve()
        assert manager.project == project.resolve()
        await manager.initialize()
        await manager.versions.import_directory(project)
        row = await manager.create('Plain process paths', 'helper', free_port())
        config = AppConfig.load(manager.directory(row['id']) / 'config.json')
        for path in (config.driver.helper_root, config.storage.data_dir, config.storage.media_dir):
            assert not str(path).startswith(prefix)
        # A distinct NTFS trailing-dot directory must never be reinterpreted.
        ordinary = root / 'name'
        ordinary.mkdir()
        dotted = Path(prefix + str(root / 'name.'))
        dotted.mkdir()
        assert process_path(dotted) == dotted.resolve()
        dotted.rmdir()
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_two_real_desktop_mock_workers_use_separate_version_projects(desktop, bundle_factory, tmp_path):
    await add_version(desktop, bundle_factory, tmp_path)
    first = await desktop.create("First version", "mock", free_port(), version="0.8.0")
    second = await desktop.create("Second version", "mock", free_port(), version="0.9.0")
    configs = [AppConfig.load(desktop.directory(row["id"]) / "config.json") for row in (first, second)]
    assert all(config.driver.type == "mock" for config in configs)
    assert configs[0].webui.access_token != configs[1].webui.access_token
    assert configs[0].storage.data_dir != configs[1].storage.data_dir
    processes = []
    try:
        await asyncio.gather(*(desktop.start(row["id"]) for row in (first, second)))
        slots = [desktop.slots[row["id"]] for row in (first, second)]
        processes = [slot.process for slot in slots]
        assert all(p is not None and p.poll() is None for p in processes)
        assert len({p.pid for p in processes} | {os.getpid()}) == 3
        launches = {instance_id: (python, project) for instance_id, python, project in desktop._test_launches}
        assert launches[first["id"]][0] != launches[second["id"]][0]
        assert launches[first["id"]][1] == desktop.versions.resolve("0.8.0")
        assert launches[second["id"]][1] == desktop.versions.resolve("0.9.0")
        async with httpx.AsyncClient(trust_env=False, timeout=5) as browser:
            for row, slot, config in zip((first, second), slots, configs):
                url = f"http://127.0.0.1:{row['port']}"
                health = await browser.get(f"{url}/_launcher/health", headers={"X-Launcher-Probe": slot.probe})
                assert health.status_code == 200
                assert health.json() == {"identity": slot.probe}
                assert (await browser.post(f"{url}/api/login", json={"token": config.webui.access_token})).status_code == 200
                assert (await browser.post(f"{url}/api/setup-password", json={"password": "offline version password"})).status_code == 200
                status = await browser.get(f"{url}/api/status")
                assert status.status_code == 200
                assert status.json()["driver"] == "mock"
                assert status.json()["version"] == row["version"]
            assert (await browser.get(f"http://127.0.0.1:{first['port']}/api/status")).status_code == 200
            await desktop.stop(first["id"])
            assert processes[0].poll() is not None
            assert processes[1].poll() is None
            assert (await browser.get(f"http://127.0.0.1:{second['port']}/api/status")).status_code == 200
    finally:
        await desktop.close()
    assert all(p.poll() is not None for p in processes)


def seed_instance(manager, row):
    from wechat_onebot.webui.auth import PasswordStore
    config = AppConfig.load(manager.directory(row["id"]) / "config.json")
    PasswordStore(config.storage.data_dir / "webui-auth.json").set("preserved offline password")
    config.storage.media_dir.mkdir(parents=True, exist_ok=True)
    (config.storage.media_dir / "payload.bin").write_bytes(b"\x00preserve media\xff")
    return config


def assert_preserved(manager, row, original):
    config = AppConfig.load(manager.directory(row["id"]) / "config.json")
    assert config.webui.access_token == original.webui.access_token
    assert config.onebot.access_token == original.onebot.access_token
    assert config.webui.port == row["port"]
    assert config.driver.type == "mock"
    assert config.driver.helper_root == manager.versions.resolve(row["version"]) / "vendor/helper"
    assert config.storage.data_dir.is_relative_to(manager.directory(row["id"]))
    assert config.storage.media_dir.is_relative_to(manager.directory(row["id"]))
    assert (config.storage.media_dir / "payload.bin").read_bytes() == b"\x00preserve media\xff"
    assert (config.storage.data_dir / "webui-auth.json").read_bytes() == (
        original.storage.data_dir / "webui-auth.json").read_bytes()


@pytest.mark.asyncio
async def test_upgrade_prebacks_up_healthchecks_new_version_and_leaves_stopped(desktop, bundle_factory, tmp_path):
    await add_version(desktop, bundle_factory, tmp_path)
    row = await desktop.create("Upgrade Mock", "mock", free_port(), version="0.8.0")
    original = seed_instance(desktop, row)
    original_auth = (original.storage.data_dir / "webui-auth.json").read_bytes()
    result = await desktop.upgrade(row["id"], "0.9.0")
    assert result["ok"] is True
    assert result["version"] == "0.9.0"
    upgraded = desktop.metadata(row["id"])
    assert upgraded["version"] == "0.9.0"
    assert upgraded["port"] == row["port"]
    assert_preserved(desktop, upgraded, original)
    assert (original.storage.data_dir / "webui-auth.json").read_bytes() == original_auth
    assert desktop.slots[row["id"]].process is None
    assert desktop.rows()[0]["state"] == "stopped"
    assert not (desktop.directory(row["id"]) / "upgrade.json").exists()
    assert desktop._test_launches[-1][2] == desktop.versions.resolve("0.9.0")
    with zipfile.ZipFile(desktop.root / "backups" / result["backup"]) as backup:
        assert json.loads(backup.read("instance.json"))["version"] == "0.8.0"
        old_config = json.loads(backup.read("config.json"))
        assert old_config["webui"]["access_token"] == original.webui.access_token
        assert backup.read("data/media/payload.bin") == b"\x00preserve media\xff"
        assert backup.read("data/webui-auth.json") == original_auth
        assert "upgrade.json" not in backup.namelist()
    # The healthchecked replacement remains usable with the preserved password.
    await desktop.start(row["id"])
    async with httpx.AsyncClient(trust_env=False, timeout=5) as browser:
        url = f"http://127.0.0.1:{row['port']}"
        assert (await browser.post(f"{url}/api/login", json={"password": "preserved offline password"})).status_code == 200
        assert (await browser.get(f"{url}/api/status")).json()["version"] == "0.9.0"


@pytest.mark.asyncio
async def test_failed_upgrade_reaps_child_and_restores_prior_version_and_data(desktop, bundle_factory, tmp_path):
    bad = bundle_factory("0.9.0", runnable=True)
    (bad / "src/wechat_onebot/launcher/worker.py").write_text("raise SystemExit(19)\n", encoding="utf-8")
    write_manifest(bad, "0.9.0")
    await desktop.versions.import_archive(zip_bundle(bad, tmp_path / "broken-runtime.zip"))
    row = await desktop.create("Rollback Mock", "mock", free_port(), version="0.8.0")
    original = seed_instance(desktop, row)
    auth_bytes = (original.storage.data_dir / "webui-auth.json").read_bytes()
    with pytest.raises(ConfigError, match="回滚"):
        await desktop.upgrade(row["id"], "0.9.0")
    restored = desktop.metadata(row["id"])
    assert restored["version"] == "0.8.0"
    assert restored["port"] == row["port"]
    assert_preserved(desktop, restored, original)
    assert (original.storage.data_dir / "webui-auth.json").read_bytes() == auth_bytes
    assert desktop.slots[row["id"]].process is None
    assert desktop.slots[row["id"]].state == "stopped"
    assert not (desktop.directory(row["id"]) / "upgrade.json").exists()
    assert len(desktop.backups()) == 1
    await desktop.start(row["id"])
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        health = await client.get(f"http://127.0.0.1:{row['port']}/healthz")
        assert health.status_code == 200 and health.json()["ok"] is True


@pytest.mark.asyncio
async def test_upgrade_requires_stopped_instance_without_interrupting_worker(desktop, bundle_factory, tmp_path):
    await add_version(desktop, bundle_factory, tmp_path)
    row = await desktop.create("Running Mock", "mock", free_port(), version="0.8.0")
    await desktop.start(row["id"])
    process = desktop.slots[row["id"]].process
    with pytest.raises(ConfigError, match="停止"):
        await desktop.upgrade(row["id"], "0.9.0")
    assert process.poll() is None
    assert desktop.metadata(row["id"])["version"] == "0.8.0"
    assert desktop.backups() == []
    assert not (desktop.directory(row["id"]) / "upgrade.json").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("external", [False, True])
async def test_restore_backup_creates_stopped_new_id_and_port_preserving_source(desktop, tmp_path, external):
    row = await desktop.create("Backup Mock", "mock", free_port(), version="0.8.0")
    original = seed_instance(desktop, row)
    name = await desktop.backup(row["id"])
    source = desktop.root / "backups" / name
    original_archive = source.read_bytes()
    original_files = tree_bytes(desktop.directory(row["id"]))
    if external:
        source = tmp_path / "imported-backup.zip"
        source.write_bytes(original_archive)
        restored = await desktop.restore(path=source)
    else:
        restored = await desktop.restore(name)
    assert restored["id"] != row["id"]
    assert restored["port"] != row["port"]
    assert restored["version"] == row["version"]
    assert_preserved(desktop, restored, original)
    assert source.read_bytes() == original_archive
    assert tree_bytes(desktop.directory(row["id"])) == original_files
    assert {r["state"] for r in desktop.rows()} == {"stopped"}
    assert desktop._test_launches == []
    await desktop.start(restored["id"])
    async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
        url = f"http://127.0.0.1:{restored['port']}"
        assert (await client.post(f"{url}/api/login", json={"password": "preserved offline password"})).status_code == 200
        assert (await client.get(f"{url}/api/status")).json()["version"] == "0.8.0"


@pytest.mark.asyncio
async def test_restore_archive_preserves_archived_copy_and_credentials(desktop):
    row = await desktop.create("Archived Mock", "mock", free_port(), version="0.8.0")
    original = seed_instance(desktop, row)
    token = original.webui.access_token
    auth = (original.storage.data_dir / "webui-auth.json").read_bytes()
    await desktop.archive(row["id"])
    archived, = desktop.archives()
    directory = desktop.root / "archive" / archived["archive_name"]
    before = tree_bytes(directory)
    restored = await desktop.restore_archive(archived["archive_name"])
    assert restored["id"] != row["id"]
    assert restored["version"] == row["version"]
    config = AppConfig.load(desktop.directory(restored["id"]) / "config.json")
    assert config.webui.access_token == token
    assert (config.storage.data_dir / "webui-auth.json").read_bytes() == auth
    assert (config.storage.media_dir / "payload.bin").read_bytes() == b"\x00preserve media\xff"
    assert tree_bytes(directory) == before
    assert desktop.rows()[0]["state"] == "stopped"
    assert desktop.archives() == [archived]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["../outside.zip", "..\\outside.zip", "/outside.zip", "C:/outside.zip"])
async def test_restore_names_cannot_escape_managed_directories(desktop, name):
    with pytest.raises(ConfigError):
        await desktop.restore(name)
    with pytest.raises(ConfigError):
        await desktop.restore_archive(name)
    assert desktop.rows() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["traversal", "storage_escape", "missing_version"])
async def test_invalid_backup_restore_leaves_no_instance(desktop, tmp_path, attack):
    row = await desktop.create("Invalid restore source", "mock", free_port(), version="0.8.0")
    name = await desktop.backup(row["id"])
    source = desktop.root / "backups" / name
    with zipfile.ZipFile(source) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if attack == "traversal":
        entries["../escape.txt"] = b"outside"
    elif attack == "storage_escape":
        config = json.loads(entries["config.json"])
        config["storage"]["data_dir"] = str(tmp_path / "outside-data")
        entries["config.json"] = json.dumps(config).encode()
    else:
        metadata = json.loads(entries["instance.json"])
        metadata["version"] = "9.9.9"
        entries["instance.json"] = json.dumps(metadata).encode()
    corrupt = tmp_path / "invalid-backup.zip"
    with zipfile.ZipFile(corrupt, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    before = tree_bytes(desktop.directory(row["id"]))
    with pytest.raises(ConfigError):
        await desktop.restore(path=corrupt)
    assert [r["id"] for r in desktop.rows()] == [row["id"]]
    assert tree_bytes(desktop.directory(row["id"])) == before
    assert not (tmp_path / "outside-data").exists()
    assert not (desktop.root / "escape.txt").exists()


@pytest.mark.asyncio
async def test_desktop_snapshot_and_settings_persist_with_strict_validation(desktop):
    before = desktop.snapshot()
    assert before["instances"] == []
    assert before["backups"] == []
    assert before["archives"] == []
    assert before["root"] == str(desktop.root)
    assert [r["version"] for r in before["versions"]] == ["0.8.0"]
    updated = desktop.save_settings({"close_to_tray": False, "restore_instances": True})
    assert updated["close_to_tray"] is False and updated["restore_instances"] is True
    from wechat_onebot.launcher.desktop_manager import DesktopManager
    reopened = DesktopManager(desktop.root, desktop.project)
    assert reopened.settings() == updated
    for invalid in ({"unknown": True}, {"autostart": "true"}, {"autostart": 1}):
        with pytest.raises(ConfigError):
            desktop.save_settings(invalid)
        assert desktop.settings() == updated


@pytest.fixture
async def metadata_desktop(tmp_path, bundle_factory):
    from wechat_onebot.launcher.desktop_manager import DesktopManager
    manager = DesktopManager(tmp_path / "metadata-desktop", bundle_factory())
    await manager.initialize()
    await manager.versions.import_directory(manager.project)
    try:
        yield manager
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_dispatcher_roundtrip_and_progress_without_launching_workers(metadata_desktop):
    from wechat_onebot.launcher.supervisor import Dispatcher
    events = []
    dispatcher = Dispatcher(metadata_desktop, events.append)
    row = await dispatcher.dispatch("create", {"name": "RPC Mock", "mode": "mock", "port": free_port()})
    assert row["version"] == "0.8.0"
    assert [event["data"]["progress"] for event in events] == [0, 100]
    assert all(event["event"] == "operation-progress" and event["data"]["operation"] == "create"
               for event in events)
    snapshot = await dispatcher.dispatch("snapshot")
    assert snapshot["instances"][0]["id"] == row["id"]
    assert snapshot["instances"][0]["state"] == "stopped"
    config = AppConfig.load(metadata_desktop.directory(row["id"]) / "config.json")
    assert await dispatcher.dispatch("credentials", {"id": row["id"]}) == {"token": config.webui.access_token}
    assert "text" in await dispatcher.dispatch("logs", {"id": row["id"]})
    with pytest.raises(ConfigError, match="启动"):
        await dispatcher.dispatch("instance_info", {"id": row["id"]})
    assert (await dispatcher.dispatch("settings_set", {"close_to_tray": False}))["close_to_tray"] is False
    assert (await dispatcher.dispatch("settings_get"))["close_to_tray"] is False
    backup = await dispatcher.dispatch("backup", {"id": row["id"]})
    restored = await dispatcher.dispatch("restore", backup)
    assert restored["id"] != row["id"]
    with pytest.raises(ConfigError, match="引用"):
        await dispatcher.dispatch("version_remove", {"version": "0.8.0"})
    assert await dispatcher.dispatch("archive", {"id": row["id"]}) == {"ok": True}
    archived = (await dispatcher.dispatch("snapshot"))["archives"][0]
    assert (await dispatcher.dispatch("restore_archive", {
        "archive_name": archived["archive_name"]}))["id"] not in {row["id"], restored["id"]}
    seed_instance(metadata_desktop, restored)
    with pytest.raises(ConfigError, match="密码"):
        await dispatcher.dispatch("credentials", {"id": restored["id"]})


@pytest.mark.asyncio
async def test_dispatcher_imports_and_removes_only_unreferenced_version(metadata_desktop, bundle_factory, tmp_path):
    from wechat_onebot.launcher.supervisor import Dispatcher
    dispatcher = Dispatcher(metadata_desktop)
    source = zip_bundle(bundle_factory("0.9.0"), tmp_path / "rpc-runtime.zip")
    imported = await dispatcher.dispatch("version_import", {"path": str(source)})
    assert imported["version"] == "0.9.0"
    assert {r["version"] for r in await dispatcher.dispatch("versions_list")} == {"0.8.0", "0.9.0"}
    assert await dispatcher.dispatch("version_remove", {"version": "0.9.0"}) == {"ok": True}
    assert [r["version"] for r in await dispatcher.dispatch("versions_list")] == ["0.8.0"]


@pytest.mark.asyncio
@pytest.mark.parametrize("method,params", [("unknown-method", {}), ("snapshot", ["invalid"]),
                                         ("settings_set", {"autostart": 1})])
async def test_dispatcher_rejects_invalid_calls_without_changing_instances(metadata_desktop, method, params):
    from wechat_onebot.launcher.supervisor import Dispatcher
    dispatcher = Dispatcher(metadata_desktop)
    before = metadata_desktop.snapshot()
    with pytest.raises(ConfigError):
        await dispatcher.dispatch(method, params)
    assert metadata_desktop.snapshot() == before


class SupervisorProtocol:
    def __init__(self, process, root, stderr):
        self.process = process
        self.root = root
        self.stderr = stderr
        self.events = []
        self.sequence = 0

    async def frame(self):
        line = await asyncio.wait_for(self.process.stdout.readline(), timeout=40)
        assert line, f"Supervisor closed stdout: {self.stderr.read_text(encoding='utf-8', errors='replace')}"
        value = json.loads(line)
        assert isinstance(value, dict), value
        return value

    async def send(self, payload, request_id):
        self.process.stdin.write(payload + b"\n")
        await self.process.stdin.drain()
        while True:
            frame = await self.frame()
            if "event" in frame:
                self.events.append(frame)
                assert frame["event"] != "fatal", frame
            else:
                assert frame.get("id") == request_id, frame
                assert ("result" in frame) != ("error" in frame), frame
                return frame

    async def call(self, method, params=None):
        self.sequence += 1
        request = {"id": self.sequence, "method": method, "params": params or {}}
        response = await self.send(json.dumps(request).encode(), self.sequence)
        assert "error" not in response, response
        return response["result"]

    async def eof(self):
        self.process.stdin.close()
        assert await asyncio.wait_for(self.process.wait(), timeout=25) == 0


@pytest.fixture
async def supervisor(tmp_path, bundle_factory):
    # Use a real embedded runtime when available; no venv or DLL tree is copied.
    portable = next((p for p in sorted((ROOT / "dist").glob("wechat-onebot-portable-*"), reverse=True)
                     if (p / "python/python.exe").is_file()
                     and (p / "src/wechat_onebot/launcher/worker.py").is_file()), None)
    python = portable / "python/python.exe" if portable else Path(sys.executable)
    worker_project = portable or PROJECT
    bundled = bundle_factory()
    root = tmp_path / "supervisor-data"
    from wechat_onebot.launcher.versions import VersionStore
    await VersionStore(root).import_directory(bundled)
    # Load current supervisor source with the installed runtime's dependencies.
    # Only launch parameters are substituted: worker lifecycle and IPC stay real.
    script = (
        f"import sys; sys.path.insert(0, {str(ROOT / 'python')!r}); "
        "from wechat_onebot.launcher.desktop_manager import DesktopManager; "
        f"DesktopManager._launch_parameters = lambda self, instance_id: ({str(python)!r}, "
        f"__import__('pathlib').Path({str(worker_project)!r})); "
        "from wechat_onebot.launcher.supervisor import main; main()"
    )
    stderr = tmp_path / "supervisor-stderr.log"
    with stderr.open("wb") as output:
        process = await asyncio.create_subprocess_exec(
            str(python), "-B", "-u", "-c", script, "--root", str(root), "--project", str(bundled),
            cwd=PROJECT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=output,
            env=dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1"),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        protocol = SupervisorProtocol(process, root, stderr)
        try:
            ready = await protocol.frame()
            assert ready == {"event": "ready", "data": {"root": str(root), "protocol": 1}}
            yield protocol
        finally:
            if process.returncode is None:
                process.stdin.close()
                try:
                    await asyncio.wait_for(process.wait(), timeout=25)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()


@pytest.mark.asyncio
async def test_supervisor_json_lines_correlates_errors_and_remains_usable(supervisor):
    snapshot = await supervisor.call("snapshot")
    assert snapshot["instances"] == []
    assert [r["version"] for r in snapshot["versions"]] == ["0.8.0"]
    for payload, request_id in ((b"not json", None), (b"[]", None),
                                (b'{"id":true,"method":"snapshot"}', True),
                                (b'{"id":"bad-call","method":"unknown"}', "bad-call")):
        assert "error" in await supervisor.send(payload, request_id)
    row = await supervisor.call("create", {"name": "Protocol Mock", "mode": "mock", "port": free_port()})
    assert row["mode"] == "mock"
    assert any(event["event"] == "operation-progress" and event["data"]["progress"] == 100
               for event in supervisor.events)
    assert (await supervisor.call("snapshot"))["instances"][0]["id"] == row["id"]
    assert await supervisor.call("shutdown") == {"ok": True}
    assert await asyncio.wait_for(supervisor.process.wait(), timeout=10) == 0


@pytest.mark.asyncio
async def test_supervisor_stdin_eof_stops_real_mock_child_and_releases_leases(supervisor):
    from wechat_onebot.launcher.locking import DirectoryLease
    row = await supervisor.call("create", {"name": "EOF Mock", "mode": "mock", "port": free_port()})
    assert await supervisor.call("start", {"id": row["id"]}) == {"ok": True}
    running = await supervisor.call("instance_info", {"id": row["id"]})
    assert running["state"] == "running"
    assert running["pid"] not in (None, os.getpid(), supervisor.process.pid)
    process_handle = None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        process_handle = kernel.OpenProcess(0x100000, False, running["pid"])
        assert process_handle, ctypes.get_last_error()
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            assert (await client.get(f"http://127.0.0.1:{row['port']}/healthz")).json()["ok"] is True
        await supervisor.eof()
        if process_handle:
            assert kernel.WaitForSingleObject(process_handle, 0) == 0
        for directory in (supervisor.root, supervisor.root / "instances" / row["id"]):
            lease = DirectoryLease(directory)
            lease.close()
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", row["port"])) != 0
    finally:
        if process_handle:
            kernel.CloseHandle(process_handle)


def seed_absolute_storage(manager, row, *, verbatim=False):
    directory = manager.directory(row["id"])
    path = directory / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["storage"].update(data_dir=str(directory / "workspace data"),
                          media_dir=str(directory / "media files"))
    if verbatim:
        prefix = chr(92) * 2 + '?' + chr(92)
        for key in ('data_dir', 'media_dir'):
            raw['storage'][key] = prefix + raw['storage'][key]
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    config = seed_instance(manager, row)
    (config.storage.data_dir / "persisted.bin").write_bytes(b"\x00absolute storage data\xff")
    manager.check_config(row["id"])
    return config


@pytest.mark.skipif(os.name != 'nt', reason='Windows verbatim special names')
@pytest.mark.parametrize('suffix', ['.', ' '])
@pytest.mark.asyncio
async def test_verbatim_special_storage_refuses_ambiguous_backup_and_archive(desktop, suffix):
    row = await desktop.create('Special storage', 'mock', free_port(), version='0.8.0')
    directory = desktop.directory(row['id'])
    ordinary = directory / 'name'
    ordinary.mkdir()
    (ordinary / 'different.bin').write_bytes(b'ordinary directory')
    prefix = chr(92)*2 + '?' + chr(92)
    special = Path(prefix + str(directory / ('name' + suffix)))
    special.mkdir()
    (special / 'persisted.bin').write_bytes(b'verbatim directory')
    config = directory / 'config.json'
    raw = json.loads(config.read_text(encoding='utf-8'))
    raw['storage']['data_dir'] = str(special)
    config.write_text(json.dumps(raw), encoding='utf-8')
    desktop.check_config(row['id'])
    before = config.read_bytes()
    for operation in (desktop.backup, desktop.archive):
        with pytest.raises(ValueError, match='特殊名称'):
            await operation(row['id'])
        assert config.read_bytes() == before
        assert (ordinary / 'different.bin').read_bytes() == b'ordinary directory'
        assert (special / 'persisted.bin').read_bytes() == b'verbatim directory'
    assert list((desktop.root / 'backups').iterdir()) == []


@pytest.mark.skipif(os.name != 'nt', reason='Windows verbatim special names')
@pytest.mark.parametrize('suffix', ['.', ' '])
@pytest.mark.asyncio
async def test_nested_special_names_refuse_backup_archive_and_restore(desktop, suffix):
    row = await desktop.create('Nested special names', 'mock', free_port(), version='0.8.0')
    config = seed_instance(desktop, row)
    ordinary = config.storage.data_dir / 'name'
    ordinary.mkdir()
    (ordinary / 'payload.bin').write_bytes(b'ordinary directory')
    prefix = chr(92)*2 + '?' + chr(92)
    special = Path(prefix + str(config.storage.data_dir / ('name' + suffix)))
    special.mkdir()
    (special / 'payload.bin').write_bytes(b'special directory')
    directory = desktop.directory(row['id'])
    before = (directory / 'config.json').read_bytes()
    for operation in (desktop.backup, desktop.archive):
        with pytest.raises(ValueError, match='特殊名称'):
            await operation(row['id'])
        assert (directory / 'config.json').read_bytes() == before
        assert (ordinary / 'payload.bin').read_bytes() == b'ordinary directory'
        assert (special / 'payload.bin').read_bytes() == b'special directory'
    assert list((desktop.root / 'backups').iterdir()) == []
    # Also cover old archives created before the preflight guard existed.
    archived = desktop.root / 'archive' / 'legacy-special-names'
    archived.parent.mkdir(exist_ok=True)
    directory.rename(archived)
    with pytest.raises(ValueError, match='特殊名称'):
        await desktop.restore_archive(archived.name)
    assert not directory.exists()
    assert list((desktop.root / 'instances').iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('verbatim', [False, pytest.param(True, marks=pytest.mark.skipif(os.name != 'nt', reason='Windows paths'))])
async def test_absolute_local_storage_backup_restore_keeps_live_config_unchanged(desktop, verbatim):
    row = await desktop.create("Absolute backup", "mock", free_port(), version="0.8.0")
    original = seed_absolute_storage(desktop, row, verbatim=verbatim)
    directory = desktop.directory(row["id"])
    live_config = (directory / "config.json").read_bytes()
    original_data = tree_bytes(original.storage.data_dir)
    original_media = tree_bytes(original.storage.media_dir)
    name = await desktop.backup(row["id"])
    backup = desktop.root / "backups" / name
    backup_bytes = backup.read_bytes()
    assert (directory / "config.json").read_bytes() == live_config
    with zipfile.ZipFile(backup) as archive:
        saved = json.loads(archive.read("config.json"))
        assert saved["storage"]["data_dir"] == "workspace data"
        assert saved["storage"]["media_dir"] == "media files"
        assert not any(Path(saved["storage"][key]).is_absolute() for key in ("data_dir", "media_dir"))
    restored = await desktop.restore(name)
    assert restored["id"] != row["id"]
    assert restored["port"] != row["port"]
    assert restored["version"] == "0.8.0"
    assert_preserved(desktop, restored, original)
    config = AppConfig.load(desktop.directory(restored["id"]) / "config.json")
    assert tree_bytes(config.storage.data_dir) == original_data
    assert tree_bytes(config.storage.media_dir) == original_media
    assert (directory / "config.json").read_bytes() == live_config
    assert backup.read_bytes() == backup_bytes
    assert {r["state"] for r in desktop.rows()} == {"stopped"}


@pytest.mark.asyncio
@pytest.mark.parametrize('verbatim', [False, pytest.param(True, marks=pytest.mark.skipif(os.name != 'nt', reason='Windows paths'))])
async def test_absolute_local_storage_failed_upgrade_rolls_back_without_journal(
    desktop, bundle_factory, tmp_path, monkeypatch, verbatim,
):
    bad = bundle_factory("0.9.0", runnable=True)
    (bad / "src/wechat_onebot/launcher/worker.py").write_text("raise SystemExit(19)\n", encoding="utf-8")
    write_manifest(bad, "0.9.0")
    await desktop.versions.import_archive(zip_bundle(bad, tmp_path / "absolute-broken-runtime.zip"))
    row = await desktop.create("Absolute rollback", "mock", free_port(), version="0.8.0")
    original = seed_absolute_storage(desktop, row, verbatim=verbatim)
    original_data = tree_bytes(original.storage.data_dir)
    original_media = tree_bytes(original.storage.media_dir)
    processes = []
    popen = subprocess.Popen

    def record_spawn(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr("wechat_onebot.launcher.manager.subprocess.Popen", record_spawn)
    with pytest.raises(ConfigError, match="回滚"):
        await desktop.upgrade(row["id"], "0.9.0")
    restored = desktop.metadata(row["id"])
    assert restored["version"] == "0.8.0"
    assert restored["port"] == row["port"]
    assert_preserved(desktop, restored, original)
    config = AppConfig.load(desktop.directory(row["id"]) / "config.json")
    assert tree_bytes(config.storage.data_dir) == original_data
    assert tree_bytes(config.storage.media_dir) == original_media
    assert not (desktop.directory(row["id"]) / "upgrade.json").exists()
    assert desktop.slots[row["id"]].process is None
    assert desktop.rows()[0]["state"] == "stopped"
    assert len(processes) == 1
    assert processes[0].poll() is not None and processes[0].stdin.closed
    assert len(desktop.backups()) == 1
    desktop.check_config(row["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize('verbatim', [False, pytest.param(True, marks=pytest.mark.skipif(os.name != 'nt', reason='Windows paths'))])
async def test_absolute_local_storage_archive_restore_preserves_archived_data(desktop, verbatim):
    row = await desktop.create("Absolute archive", "mock", free_port(), version="0.8.0")
    original = seed_absolute_storage(desktop, row, verbatim=verbatim)
    original_data = tree_bytes(original.storage.data_dir)
    original_media = tree_bytes(original.storage.media_dir)
    await desktop.archive(row["id"])
    archived, = desktop.archives()
    directory = desktop.root / "archive" / archived["archive_name"]
    assert not desktop.directory(row["id"]).exists()
    raw = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    assert raw["storage"]["data_dir"] == "workspace data"
    assert raw["storage"]["media_dir"] == "media files"
    archived_config = AppConfig.load(directory / "config.json")
    assert archived_config.storage.data_dir.is_relative_to(directory)
    assert archived_config.storage.media_dir.is_relative_to(directory)
    assert tree_bytes(archived_config.storage.data_dir) == original_data
    assert tree_bytes(archived_config.storage.media_dir) == original_media
    archived_files = tree_bytes(directory)
    restored = await desktop.restore_archive(archived["archive_name"])
    config = AppConfig.load(desktop.directory(restored["id"]) / "config.json")
    assert restored["id"] != row["id"]
    assert restored["version"] == "0.8.0"
    assert config.webui.access_token == original.webui.access_token
    assert config.onebot.access_token == original.onebot.access_token
    assert config.storage.data_dir.is_relative_to(desktop.directory(restored["id"]))
    assert config.storage.media_dir.is_relative_to(desktop.directory(restored["id"]))
    assert tree_bytes(config.storage.data_dir) == original_data
    assert tree_bytes(config.storage.media_dir) == original_media
    assert tree_bytes(directory) == archived_files
    assert desktop.archives() == [archived]
    assert desktop.rows()[0]["state"] == "stopped"
