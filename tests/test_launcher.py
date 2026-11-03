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
from fastapi.testclient import TestClient

from wechat_onebot.config import AppConfig, ConfigError
from wechat_onebot.launcher import manager as manager_module
from wechat_onebot.launcher.entry import DirectoryLease
from wechat_onebot.launcher.manager import LauncherManager
from wechat_onebot.launcher.server import create_launcher_app


PROJECT = Path(os.environ["WECHAT_RUNTIME_SOURCE"]).resolve()
BASE_URL = "http://127.0.0.1:5088"
BOOTSTRAP_TOKEN = "offline-launcher-bootstrap"
LAUNCHER_COOKIE_NAME = "launcher_session_5088"
MUTATION_HEADERS = {"X-Launcher-Request": "1"}


@pytest.fixture
def manager(tmp_path):
    # Workers need the actual source tree, while every writable path is temporary.
    return LauncherManager(tmp_path / "launcher", PROJECT, launcher_port=5088)


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


def bootstrap(client):
    response = client.get(f"/?token={BOOTSTRAP_TOKEN}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert client.cookies.get(LAUNCHER_COOKIE_NAME)
    assert "launcher_session" not in client.cookies
    return response


@pytest.fixture
def client(manager):
    with TestClient(create_launcher_app(manager, BOOTSTRAP_TOKEN), base_url=BASE_URL) as client:
        yield client


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

            assert (await a.post("/api/login", json={"token": configs[1].webui.access_token})).status_code == 401
            assert (await b.post("/api/login", json={"token": configs[0].webui.access_token})).status_code == 401
            assert (await a.post("/api/login", json={"token": configs[0].webui.access_token})).status_code == 200
            assert (await a.post("/api/setup-password", json={"password": "first offline password"})).status_code == 200
            first_cookie_name = f"wechat_onebot_webui_{first['id']}"
            second_cookie_name = f"wechat_onebot_webui_{second['id']}"
            first_cookie = a.cookies.get(first_cookie_name)
            assert first_cookie
            assert "wechat_onebot_webui" not in a.cookies
            assert (await b.get("/api/status", headers={
                "Cookie": f"{first_cookie_name}={first_cookie}"})).status_code == 401
            assert (await b.get("/api/status", headers={
                "Cookie": f"{second_cookie_name}={first_cookie}"})).status_code == 401
            assert (await b.get("/api/auth-state")).json() == {"setupRequired": True}
            assert (configs[0].storage.data_dir / "webui-auth.json").is_file()
            assert not (configs[1].storage.data_dir / "webui-auth.json").exists()
            assert (await b.post("/api/login", json={"token": configs[1].webui.access_token})).status_code == 200
            assert (await b.post("/api/setup-password", json={"password": "second offline password"})).status_code == 200
            assert b.cookies.get(second_cookie_name)
            assert "wechat_onebot_webui" not in b.cookies

            # Browser cookies share a host across ports: use one real cookie jar.
            async with httpx.AsyncClient(trust_env=False, timeout=5) as browser:
                first_url = f"http://127.0.0.1:{first['port']}"
                second_url = f"http://127.0.0.1:{second['port']}"
                assert (await browser.post(f"{first_url}/api/login", json={
                    "password": "first offline password"})).status_code == 200
                first_session = browser.cookies.get(first_cookie_name)
                assert first_session
                assert (await browser.get(f"{first_url}/api/status")).status_code == 200
                assert (await browser.post(f"{second_url}/api/login", json={
                    "password": "second offline password"})).status_code == 200
                assert browser.cookies.get(second_cookie_name)
                assert browser.cookies.get(first_cookie_name) == first_session
                assert set(browser.cookies) == {first_cookie_name, second_cookie_name}
                assert (await browser.get(f"{second_url}/api/status")).status_code == 200
                revisited = await browser.get(f"{first_url}/api/status")
                assert revisited.status_code == 200
                # Both port-independent cookies really travel on the revisit.
                assert f"{first_cookie_name}=" in revisited.request.headers["cookie"]
                assert f"{second_cookie_name}=" in revisited.request.headers["cookie"]
                assert (await browser.post(f"{second_url}/api/logout")).status_code == 200
                assert second_cookie_name not in browser.cookies
                assert browser.cookies.get(first_cookie_name) == first_session
                assert (await browser.get(f"{second_url}/api/status")).status_code == 401
                assert (await browser.get(f"{first_url}/api/status")).status_code == 200
            for child in (a, b):
                status = await child.get("/api/status")
                assert status.status_code == 200
                assert status.json()["driver"] == "mock"
                # Managed workers must never query or change the old single-service task.
                assert (await child.get("/api/autostart")).status_code == 409
                for enabled in (True, False):
                    assert (await child.post("/api/autostart", json={"enabled": enabled})).status_code == 409
            for index, config in enumerate(configs):
                config.storage.media_dir.mkdir(parents=True, exist_ok=True)
                (config.storage.media_dir / "isolation.txt").write_text(str(index), encoding="utf-8")
            assert [(c.storage.media_dir / "isolation.txt").read_text(encoding="utf-8")
                    for c in configs] == ["0", "1"]

            # Running instances cannot be snapshotted or moved underneath the worker.
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
            assert (await b.get("/api/status")).status_code == 200
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


def test_token_bootstrap_sets_private_cookie_and_redirects_without_token(client):
    assert client.get("/api/status").status_code == 401
    assert client.post("/api/instances", headers=MUTATION_HEADERS,
                       json={"name": "Unauthenticated", "mode": "mock"}).status_code == 401
    assert client.get("/api/status", headers={
        "Authorization": f"Bearer {BOOTSTRAP_TOKEN}"}).status_code == 401
    assert client.get("/?token=wrong", follow_redirects=False).status_code == 401
    assert client.get(f"/api/status?token={BOOTSTRAP_TOKEN}").status_code == 401
    response = bootstrap(client)
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith(f"{LAUNCHER_COOKIE_NAME}=")
    assert "httponly" in cookie and "samesite=strict" in cookie and "path=/" in cookie
    assert BOOTSTRAP_TOKEN not in cookie
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert client.get("/api/status").status_code == 200
    client.cookies.clear()
    assert client.get("/api/status", headers={
        "Cookie": f"{LAUNCHER_COOKIE_NAME}=forged"}).status_code == 401


@pytest.mark.parametrize("host", ["evil.example:5088", "127.0.0.1:9999", "127.0.0.1",
                                 "127.0.0.1:5088.evil.example"])
def test_host_rejected_even_with_cookie_or_bootstrap_token(client, host):
    bootstrap(client)
    assert client.get("/api/status", headers={"Host": host}).status_code == 403
    assert client.get(f"/?token={BOOTSTRAP_TOKEN}", headers={"Host": host},
                      follow_redirects=False).status_code == 403


@pytest.mark.parametrize("headers", [{}, {"X-Launcher-Request": "0"},
    {**MUTATION_HEADERS, "Origin": "http://evil.example"},
    {**MUTATION_HEADERS, "Origin": "http://127.0.0.1:9999"},
    {**MUTATION_HEADERS, "Origin": "https://127.0.0.1:5088"},
    {**MUTATION_HEADERS, "Origin": "null"}])
def test_mutations_require_csrf_header_and_trusted_origin(client, manager, headers):
    bootstrap(client)
    response = client.post("/api/instances", headers=headers, json={"name": "Blocked", "mode": "mock"})
    assert response.status_code == 403
    assert manager.rows() == []


@pytest.mark.parametrize("origin", [None, "http://127.0.0.1:5088", "http://localhost:5088"])
def test_trusted_mutation_and_instance_token_bootstrap(client, manager, origin):
    bootstrap(client)
    headers = dict(MUTATION_HEADERS)
    if origin is not None:
        headers["Origin"] = origin
    response = client.post("/api/instances", headers=headers,
                           json={"name": "Offline", "mode": "mock", "port": free_port()})
    assert response.status_code == 200
    row = response.json()
    path = manager.directory(row["id"]) / "config.json"
    config = AppConfig.load(path)
    credentials_url = f"/api/instances/{row['id']}/credentials"
    assert client.get(credentials_url).json() == {"token": config.webui.access_token}
    assert client.get("/api/status", headers={"Host": "localhost:5088"}).status_code == 200
    # Once a child has a password, the launcher must stop exposing bootstrap credentials.
    auth = config.storage.data_dir / "webui-auth.json"
    auth.parent.mkdir(parents=True)
    auth.write_text("{}", encoding="utf-8")
    assert client.get(credentials_url).status_code == 409
    client.cookies.clear()
    assert client.get(credentials_url).status_code == 401


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
