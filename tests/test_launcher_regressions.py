"""Launcher lifecycle regressions; subprocesses use only the Mock driver."""
from __future__ import annotations

import asyncio
import os
import socket
import threading
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from wechat_onebot.config import AppConfig, ConfigError
from wechat_onebot.launcher.locking import DirectoryLease
from wechat_onebot.launcher.manager import LauncherManager


PROJECT = Path(__file__).resolve().parents[2] / "bot"


@pytest.fixture
def manager(tmp_path):
    return LauncherManager(tmp_path / "launcher", PROJECT)


async def create_mock(manager):
    with socket.socket() as sock:
        if os.name == "nt":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return await manager.create("Regression Mock", "mock", port)


@asynccontextmanager
async def open_event_stream(manager, row):
    config = AppConfig.load(manager.directory(row["id"]) / "config.json")
    assert config.driver.type == "mock"
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{row['port']}", trust_env=False, timeout=5,
    ) as client:
        login = await client.post("/api/login", json={"token": config.webui.access_token})
        assert login.status_code == 200
        setup = await client.post("/api/setup-password", json={
            "password": "offline regression password",
        })
        assert setup.status_code == 200
        async with client.stream("GET", "/api/events") as response:
            assert response.status_code == 200
            # Retain the iterator throughout shutdown: finalizing it early
            # closes the connection and would hide the original regression.
            lines = response.aiter_lines()
            assert (await asyncio.wait_for(anext(lines), timeout=5)).startswith("data: ")
            yield response


@pytest.mark.asyncio
async def test_open_sse_does_not_force_kill_worker_on_stop(manager):
    row = await create_mock(manager)
    try:
        await manager.start(row["id"])
        process = manager.slots[row["id"]].process
        assert process is not None
        async with open_event_stream(manager, row):
            # The three-second Uvicorn drain must finish before the launcher's
            # fifteen-second kill fallback, even while this client stays open.
            await asyncio.wait_for(manager.stop(row["id"]), timeout=10)
            assert process.poll() == 0
            assert manager.slots[row["id"]].process is None
            assert manager.rows()[0]["state"] == "stopped"
            assert "Application shutdown complete." in manager.logs(row["id"])
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_fresh_manager_blocks_backup_and_archive_of_unowned_worker(manager):
    row = await create_mock(manager)
    instance_id = row["id"]
    directory = manager.directory(instance_id)
    replacement = LauncherManager(manager.root, PROJECT)
    try:
        await manager.start(instance_id)
        process = manager.slots[instance_id].process
        assert process is not None and process.poll() is None

        status, = replacement.rows()
        assert status["state"] == "stopping"
        assert status["pid"] is None
        assert replacement.slots[instance_id].process is None
        with pytest.raises(ConfigError, match="停止"):
            await replacement.backup(instance_id)
        with pytest.raises(ConfigError, match="停止"):
            await replacement.archive(instance_id)
        assert directory.is_dir()
        assert not list((manager.root / "backups").glob("*.zip"))
        assert not (manager.root / "archive").exists()
        assert process.poll() is None

        await manager.stop(instance_id)
        assert replacement.rows()[0]["state"] == "stopped"
        # The worker's lease must also be released after its actual exit.
        name = await replacement.backup(instance_id)
        with zipfile.ZipFile(manager.root / "backups" / name) as archive:
            assert "config.json" in archive.namelist()
            assert archive.testzip() is None
        await replacement.archive(instance_id)
        assert not directory.exists()
        assert replacement.rows() == []
    finally:
        await manager.close()


@pytest.mark.asyncio
async def test_cancelled_backup_blocks_archive_until_thread_finishes(manager, monkeypatch):
    row = await create_mock(manager)
    instance_id = row["id"]
    directory = manager.directory(instance_id)
    payload = b"\x00cancelled-backup-regression\xff"
    (directory / "payload.bin").write_bytes(payload)
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    archive_requested = asyncio.Event()
    backup_names = []
    original_backup = manager._backup

    def paused_backup(instance_id):
        entered.set()
        try:
            if not release.wait(timeout=15):
                raise TimeoutError("Test did not release the backup thread")
            name = original_backup(instance_id)
            backup_names.append(name)
            return name
        finally:
            finished.set()

    async def request_archive():
        archive_requested.set()
        await manager.archive(instance_id)
        assert finished.is_set(), "Archive completed before the backup thread"

    monkeypatch.setattr(manager, "_backup", paused_backup)
    backup_task = asyncio.create_task(manager.backup(instance_id))
    archive_task = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        backup_task.cancel()
        await asyncio.sleep(0)  # Deliver cancellation to the awaiting coroutine.
        archive_task = asyncio.create_task(request_archive())
        await asyncio.wait_for(archive_requested.wait(), timeout=5)

        assert not backup_task.done(), "Cancellation must wait for the filesystem thread"
        assert not archive_task.done(), "Archive must wait for the backup's instance lock"
        assert manager.slots[instance_id].lock.locked()
        assert directory.is_dir()
        assert not finished.is_set()
        with pytest.raises(ConfigError):
            lease = DirectoryLease(directory, create=False)
            lease.close()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(backup_task, timeout=5)
        await asyncio.wait_for(archive_task, timeout=5)
        assert finished.is_set()
        assert not directory.exists()
        assert len(backup_names) == 1
        with zipfile.ZipFile(manager.root / "backups" / backup_names[0]) as archive:
            assert archive.testzip() is None
            assert archive.read("payload.bin") == payload
            assert {"config.json", "instance.json"} <= set(archive.namelist())
        archived, = (manager.root / "archive").iterdir()
        assert (archived / "payload.bin").read_bytes() == payload
    finally:
        release.set()
        tasks = [task for task in (backup_task, archive_task) if task is not None]
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=10)
        await manager.close()
