"""Offline launcher coverage; real subprocesses always use the Mock driver."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import zipfile
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from wechat_onebot.config import AppConfig, ConfigError
from wechat_onebot.launcher import manager as manager_module
from wechat_onebot.launcher.locking import DirectoryLease
from wechat_onebot.launcher.manager import LauncherManager


PROJECT = Path(os.environ["WECHAT_RUNTIME_SOURCE"]).resolve()


@pytest.fixture
def manager(tmp_path):
    # Workers need the actual source tree, while every writable path is temporary.
    return LauncherManager(tmp_path / "launcher", PROJECT)


@contextmanager
def reserved_port():
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock.getsockname()[1]


def free_port():
    with reserved_port() as port:
        return port


async def create_mock(manager, name="Offline Mock"):
    return await manager.create(name, "mock", free_port())


@pytest.mark.asyncio
async def test_two_real_mock_workers_are_healthy_and_isolated(manager):
    first = await create_mock(manager, "First Mock")
    second = await create_mock(manager, "Second Mock")
    configs = [AppConfig.load(manager.directory(row["id"]) / "config.json")
               for row in (first, second)]
    assert all(config.driver.type == "mock" for config in configs)
    assert configs[0].webui.access_token != configs[1].webui.access_token
    assert configs[0].onebot.access_token != configs[1].onebot.access_token
    for row, config in zip((first, second), configs):
        directory = manager.directory(row["id"])
        assert config.storage.data_dir.is_relative_to(directory)
        assert config.storage.media_dir.is_relative_to(directory)
    assert configs[0].storage.data_dir != configs[1].storage.data_dir
    assert configs[0].storage.media_dir != configs[1].storage.media_dir

    processes = []
    try:
        await asyncio.gather(manager.start(first["id"]), manager.start(second["id"]))
        slots = [manager.slots[row["id"]] for row in (first, second)]
        processes = [slot.process for slot in slots]
        assert all(process is not None and process.poll() is None for process in processes)
        assert len({process.pid for process in processes} | {os.getpid()}) == 3
        assert all(row["state"] == "running" for row in manager.rows())
        assert slots[0].probe != slots[1].probe

        async with (httpx.AsyncClient(base_url=f"http://127.0.0.1:{first['port']}",
                                     trust_env=False, timeout=5) as a,
                    httpx.AsyncClient(base_url=f"http://127.0.0.1:{second['port']}",
                                     trust_env=False, timeout=5) as b):
            for child, slot, other_slot in ((a, slots[0], slots[1]), (b, slots[1], slots[0])):
                health = await child.get("/_launcher/health", headers={"X-Launcher-Probe": slot.probe})
                assert health.status_code == 200
                assert health.json() == {"identity": slot.probe}
                assert (await child.get("/healthz")).json()["ok"] is True
                assert (await child.get("/_launcher/health")).status_code == 404
                assert (await child.get("/_launcher/health", headers={
                    "X-Launcher-Probe": other_slot.probe})).status_code == 404

            for index, config in enumerate(configs):
                config.storage.media_dir.mkdir(parents=True, exist_ok=True)
                (config.storage.media_dir / "isolation.txt").write_text(str(index), encoding="utf-8")
            assert [(c.storage.media_dir / "isolation.txt").read_text(encoding="utf-8")
                    for c in configs] == ["0", "1"]

            with pytest.raises(ConfigError, match="停止"):
                await manager.backup(first["id"])
            with pytest.raises(ConfigError, match="停止"):
                await manager.archive(first["id"])
            await manager.stop(first["id"])
            assert processes[0].poll() is not None
            assert processes[1].poll() is None
            rows = {row["id"]: row for row in manager.rows()}
            assert rows[first["id"]]["state"] == "stopped"
            assert rows[first["id"]]["pid"] is None
            assert rows[second["id"]]["pid"] == processes[1].pid
            assert (await b.get("/healthz")).json()["ok"] is True
    finally:
        await manager.close()
    assert all(process.poll() is not None for process in processes)


@pytest.mark.asyncio
async def test_occupied_port_rejected_on_create_and_start(manager):
    with reserved_port() as port:
        with pytest.raises(ConfigError, match="占用"):
            await manager.create("Occupied", "mock", port)
        assert list(manager.instances.iterdir()) == []
    row = await create_mock(manager)
    with socket.socket() as occupied:
        occupied.bind(("127.0.0.1", row["port"]))
        occupied.listen()
        with pytest.raises(ConfigError, match="占用"):
            await manager.start(row["id"])
        assert manager.slots[row["id"]].process is None
        assert manager.rows()[0]["state"] == "stopped"


@pytest.mark.asyncio
async def test_spawn_failure_sets_error_without_process(manager, monkeypatch):
    row = await create_mock(manager)

    def fail_spawn(*args, **kwargs):
        raise OSError("offline spawn failure")

    monkeypatch.setattr(manager_module.subprocess, "Popen", fail_spawn)
    with pytest.raises(OSError, match="offline spawn failure"):
        await manager.start(row["id"])
    slot = manager.slots[row["id"]]
    assert slot.state == "error"
    assert slot.process is None
    assert slot.error == "offline spawn failure"
    await manager.close()


@pytest.mark.asyncio
async def test_startup_failure_reaps_real_child_and_closes_parent_pipe(manager, monkeypatch):
    row = await create_mock(manager)
    processes = []
    popen = subprocess.Popen

    def record_spawn(*args, **kwargs):
        child = popen(*args, **kwargs)
        processes.append(child)
        return child

    def fail_probe_client(*args, **kwargs):
        raise RuntimeError("offline readiness failure")

    monkeypatch.setattr(manager_module.subprocess, "Popen", record_spawn)
    monkeypatch.setattr(manager_module.httpx, "AsyncClient", fail_probe_client)
    try:
        with pytest.raises(RuntimeError, match="offline readiness failure"):
            await manager.start(row["id"])
        assert len(processes) == 1
        assert processes[0].poll() is not None
        assert processes[0].stdin.closed
        slot = manager.slots[row["id"]]
        assert slot.process is None
        assert slot.state == "error"
        assert slot.error == "offline readiness failure"
    finally:
        await manager.close()
        # Even a backend regression must not leave an offline test worker alive.
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.asyncio
async def test_stopped_backup_and_archive_preserve_all_files(manager):
    row = await create_mock(manager)
    directory = manager.directory(row["id"])
    data = directory / "data" / "nested"
    data.mkdir(parents=True)
    (data / "payload.bin").write_bytes(b"\x00offline\xff")
    (directory / "service.log").write_text("offline log", encoding="utf-8")
    expected = {p.relative_to(directory).as_posix(): p.read_bytes()
                for p in directory.rglob("*") if p.is_file()}
    name = await manager.backup(row["id"])
    backup = manager.root / "backups" / name
    with zipfile.ZipFile(backup) as archive:
        assert set(archive.namelist()) == set(expected)
        assert archive.testzip() is None
        assert {n: archive.read(n) for n in archive.namelist()} == expected
    assert {p.relative_to(directory).as_posix(): p.read_bytes()
                for p in directory.rglob("*") if p.is_file() and p.name != "launcher.lock"} == expected
    backup_bytes = backup.read_bytes()
    await manager.archive(row["id"])
    assert manager.rows() == []
    assert not directory.exists()
    archived, = (manager.root / "archive").iterdir()
    assert archived.name.startswith(row["id"] + "-")
    assert {p.relative_to(archived).as_posix(): p.read_bytes()
            for p in archived.rglob("*") if p.is_file() and p != archived / "launcher.lock"} == expected
    assert backup.read_bytes() == backup_bytes


@pytest.mark.parametrize("instance_id", ["..", "../outside", "..\\outside", "/tmp/outside",
                                        "a" * 31, "a" * 33, "A" * 32, "g" * 32])
def test_instance_path_traversal_rejected(manager, instance_id):
    with pytest.raises(ConfigError, match="ID"):
        manager.directory(instance_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["data_dir", "media_dir"])
async def test_storage_path_escape_rejected_before_spawn(manager, tmp_path, monkeypatch, key):
    row = await create_mock(manager)
    path = manager.directory(row["id"]) / "config.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["storage"][key] = str(tmp_path / "outside")
    path.write_text(json.dumps(raw), encoding="utf-8")

    def unexpected_spawn(*args, **kwargs):
        pytest.fail("An escaping storage path must be rejected before spawning")

    monkeypatch.setattr(manager_module.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(ConfigError, match="数据路径"):
        await manager.start(row["id"])
    assert manager.slots[row["id"]].process is None
    assert not (tmp_path / "outside").exists()


def test_directory_lease_rejects_duplicate_and_can_be_reacquired(tmp_path):
    root = tmp_path / "leased"
    first = DirectoryLease(root)
    try:
        with pytest.raises(ConfigError, match="已有启动器"):
            DirectoryLease(root)
        independent = DirectoryLease(tmp_path / "independent")
        independent.close()
    finally:
        first.close()
    replacement = DirectoryLease(root)
    replacement.close()
