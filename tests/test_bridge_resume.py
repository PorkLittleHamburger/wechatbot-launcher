"""Launcher intent and gateway capability handshake, without native WeChat."""
from __future__ import annotations

import json
import asyncio
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from wechat_onebot.config import AppConfig, default_config
from wechat_onebot.launcher import worker
from wechat_onebot.launcher.bridge_status import read_bridge_status
from wechat_onebot.launcher.desktop_manager import DesktopManager
from wechat_onebot.launcher.manager import ProcessSlot
from wechat_onebot.config import ConfigError


def status(state="waiting_login"):
    return {"supported": True, "version": 1, "enabled": True, "account": "offline-target",
            "desired": "running", "state": state, "error": ""}


@pytest.mark.parametrize("autostart,enabled,instances,bridges,expected", [
    (True, True, True, True, True), (False, True, True, True, False),
    (True, False, True, True, False), (True, True, True, False, False),
    (True, True, False, True, None),
])
async def test_only_explicit_logon_restoration_requests_bridge(tmp_path, monkeypatch, autostart, enabled, instances, bridges, expected):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    manager.save_settings({"autostart": enabled, "restore_instances": instances, "restore_bridges": bridges})
    (manager.root / "running.json").write_text(json.dumps({"instances": ["remembered"]}), encoding="utf-8")
    calls = []
    async def start(instance_id, *, resume_bridge=False):
        calls.append((instance_id, resume_bridge))
    monkeypatch.setattr(manager, "start", start)
    await manager.initialize(autostart=autostart)
    assert calls == ([] if expected is None else [("remembered", expected)])


def test_existing_settings_do_not_implicitly_consent_to_bridge(tmp_path):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    (manager.root / "settings.json").write_text('{"autostart":true,"restore_instances":true}', encoding="utf-8")
    assert manager.settings()["restore_bridges"] is False


@pytest.mark.parametrize("value", [None, {}, {**status(), "version": 2}, {**status(), "state": []}, {**status(), "error": None}])
def test_old_or_incompatible_worker_is_explicit(value):
    result = read_bridge_status({"bridgeResume": value}, True)
    assert result["state"] == "unsupported" and result["error"]
    assert result["supported"] is False


def test_legacy_worker_without_request_has_no_restore_status():
    assert read_bridge_status({}, False) is None


@pytest.mark.parametrize("state", ["waiting_login", "starting", "stopping", "running", "stopped", "no_target", "failed"])
def test_controller_states_pass_through(state):
    assert read_bridge_status({"bridgeResume": status(state)}, True) == status(state)


@pytest.mark.parametrize("supported,requested", [(False, True), (True, True), (True, False)])
def test_worker_uses_explicit_intent_and_capability(tmp_path, monkeypatch, supported, requested):
    config_path = tmp_path / "config.json"
    raw = default_config()
    raw["driver"]["type"] = "mock"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = AppConfig.load(config_path)
    calls = []
    def create(config, manager, *, start_runtime, cookie_name):
        calls.append(start_runtime)
        app = FastAPI()
        app.state.bridge_resume = SimpleNamespace(snapshot=lambda: {**status(), "enabled": start_runtime})
        return app
    monkeypatch.setattr(worker, "create_webui_app", create)
    monkeypatch.setattr(worker.importlib.util, "find_spec", lambda name: object() if supported else None)
    if supported:
        import sys
        monkeypatch.setitem(sys.modules, "wechat_onebot.webui.bridge_resume", SimpleNamespace(RESUME_CAPABILITY_VERSION=1))
    monkeypatch.setenv("WECHAT_LAUNCHER_PROBE", "offline-secret")
    with TestClient(worker.create_app(config, resume_bridge=requested)) as client:
        assert client.get("/_launcher/health").status_code == 404
        result = client.get("/_launcher/health", headers={"X-Launcher-Probe": "offline-secret"}).json()
        assert result["bridgeResume"]["supported"] is supported
        assert result["bridgeResume"]["enabled"] is (supported and requested if supported else requested)
        assert client.post("/api/autostart").status_code == 409
    assert calls == [supported and requested]


async def test_status_poll_updates_without_claiming_bridge_success(tmp_path, monkeypatch):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    process = object()
    slot = ProcessSlot(state="running", process=process, probe="secret", resume_requested=True)
    manager.slots["id"] = slot
    monkeypatch.setattr(manager, "metadata", lambda key: {"port": 12345})
    current = status()
    def respond(request):
        assert request.headers["X-Launcher-Probe"] == "secret"
        return httpx.Response(200, json={"identity": "secret", "bridgeResume": current})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)))
    await manager.refresh_bridge_status()
    assert slot.state == "running" and slot.bridge_resume["state"] == "waiting_login"
    current = status("running")
    await manager.refresh_bridge_status()
    assert slot.bridge_resume["state"] == "running"
    current = status("stopped")
    await manager.refresh_bridge_status()
    assert slot.bridge_resume["state"] == "stopped"


async def test_legacy_worker_and_read_failure_remain_visible(tmp_path, monkeypatch):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    slot = ProcessSlot(state="running", process=object(), probe="secret", resume_requested=True)
    manager.slots["id"] = slot
    monkeypatch.setattr(manager, "metadata", lambda key: {"port": 12345})
    fail = False
    def respond(request):
        if fail:
            raise httpx.ConnectError("offline test disconnect", request=request)
        return httpx.Response(200, json={"identity": "secret"})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)))
    await manager.refresh_bridge_status()
    assert slot.state == "running" and slot.bridge_resume["state"] == "unsupported"
    fail = True
    await manager.refresh_bridge_status()
    assert slot.bridge_resume["state"] == "failed" and "无法读取" in slot.bridge_resume["error"]


async def test_stale_health_reply_does_not_overwrite_restarted_slot(tmp_path, monkeypatch):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    slot = ProcessSlot(state="running", process=object(), probe="before", resume_requested=True)
    manager.slots["id"] = slot
    monkeypatch.setattr(manager, "metadata", lambda key: {"port": 12345})
    def respond(request):
        slot.process, slot.probe, slot.bridge_resume = object(), "after", status("running")
        return httpx.Response(200, json={"identity": "before", "bridgeResume": status()})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)))
    await manager.refresh_bridge_status()
    assert slot.bridge_resume["state"] == "running"


async def test_failed_launch_does_not_leave_resume_intent_for_upgrade(tmp_path, monkeypatch):
    from wechat_onebot.launcher.manager import LauncherManager
    manager = LauncherManager(tmp_path / "workspace", tmp_path)
    monkeypatch.setattr(manager, "require_stopped", lambda key: None)
    def fail(key):
        raise ConfigError("offline config rejected before spawn")
    monkeypatch.setattr(manager, "check_config", fail)
    with pytest.raises(ConfigError, match="before spawn"):
        await manager.start("id", resume_bridge=True)
    assert manager.slots["id"].process is None
    assert manager.slots["id"].resume_requested is False


async def test_old_poll_cannot_overwrite_newer_manual_stop(tmp_path, monkeypatch):
    manager = DesktopManager(tmp_path / "workspace", tmp_path)
    slot = ProcessSlot(state="running", process=object(), probe="secret", resume_requested=True)
    manager.slots["id"] = slot
    monkeypatch.setattr(manager, "metadata", lambda key: {"port": 12345})
    entered, release = asyncio.Event(), asyncio.Event()
    requests = 0
    async def respond(request):
        nonlocal requests
        requests += 1
        if requests == 1:
            entered.set()
            await release.wait()
            state = "waiting_login"
        else:
            state = "stopped"
        return httpx.Response(200, json={"identity": "secret", "bridgeResume": status(state)})
    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: client(**kwargs, transport=httpx.MockTransport(respond)))
    old = asyncio.create_task(manager.refresh_bridge_status())
    await entered.wait()
    await manager.refresh_bridge_status()
    assert slot.bridge_resume["state"] == "stopped"
    release.set()
    await old
    assert slot.bridge_resume["state"] == "stopped"


@pytest.mark.parametrize("stop_while_waiting", [False, True])
def test_worker_waiting_login_and_cancel_with_real_controller(tmp_path, monkeypatch, stop_while_waiting):
    pytest.importorskip("wechat_onebot.webui.bridge_resume", reason="Requires gateway resume capability v1")
    from wechat_onebot.webui import server
    logged = threading.Event()
    calls = []
    class OfflineManager:
        last_error = ""
        def __init__(self, config):
            self.runtime = None
        def get(self, account):
            return self.runtime
        def snapshot(self):
            return []
        async def start_saved_networks(self):
            pass
        async def start_account(self, account, pid=None):
            calls.append(account)
            self.runtime = SimpleNamespace(started=True)
        async def stop_account(self, account):
            self.runtime = None
        async def stop_all(self):
            self.runtime = None
    raw = default_config()
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(raw), encoding="utf-8")
    config = AppConfig.load(config_path)
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "startup.json").write_text(
        '{"last_account":"offline-target","desired":"running"}', encoding="utf-8")
    monkeypatch.setattr(worker, "RuntimeManager", OfflineManager)
    monkeypatch.setattr(server, "RESUME_CHECK_SECONDS", 0.01)
    monkeypatch.setattr(server, "discover_logged_accounts", lambda root: [SimpleNamespace(wxid="offline-target", pid=123)] if logged.is_set() else [])
    monkeypatch.setenv("WECHAT_LAUNCHER_PROBE", "offline-secret")
    with TestClient(worker.create_app(config, resume_bridge=True)) as client:
        def health():
            return client.get("/_launcher/health", headers={"X-Launcher-Probe": "offline-secret"}).json()["bridgeResume"]
        assert health()["state"] == "waiting_login"
        assert calls == []
        if stop_while_waiting:
            assert client.post("/api/login", json={"token": config.webui.access_token}).status_code == 200
            assert client.post("/api/setup-password", json={"password": "offline contract password"}).status_code == 200
            assert client.post("/api/accounts/offline-target/stop").status_code == 200
        logged.set()
        deadline = time.monotonic() + 2
        expected = "stopped" if stop_while_waiting else "running"
        while health()["state"] != expected and time.monotonic() < deadline:
            time.sleep(0.01)
        assert health()["state"] == expected
        time.sleep(0.03)
        assert calls == ([] if stop_while_waiting else ["offline-target"])
