"""Child service: parent pipe EOF also requests graceful shutdown."""
from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import threading

import uvicorn
from fastapi import Request, Response

from ..cli import _configure_logging
from ..config import AppConfig
from ..core.runtime import RuntimeManager
from ..webui import create_webui_app
from .locking import DirectoryLease
from .bridge_status import RESUME_VERSION, unavailable
from pathlib import Path


def create_app(config: AppConfig, *, resume_bridge: bool = False):
    capability = importlib.util.find_spec("wechat_onebot.webui.bridge_resume")
    supported = False
    if capability is not None:
        from ..webui.bridge_resume import RESUME_CAPABILITY_VERSION
        supported = RESUME_CAPABILITY_VERSION == RESUME_VERSION
    app = create_webui_app(config, RuntimeManager(config), start_runtime=resume_bridge and supported,
                           cookie_name=f"wechat_onebot_webui_{config.path.parent.name}")
    app.state.launcher_managed = True
    @app.middleware("http")
    async def block_single_service_autostart(request, call_next):
        if request.url.path == "/api/autostart":
            return Response("实例自启由启动器管理", status_code=409)
        return await call_next(request)

    secret = os.environ.pop("WECHAT_LAUNCHER_PROBE")

    @app.get("/_launcher/health")
    async def identity(request: Request):
        if request.headers.get("X-Launcher-Probe") != secret:
            return Response(status_code=404)
        status = (app.state.bridge_resume.snapshot() if supported else unavailable(
            "所选 Bot 不支持桥接恢复，请升级 Bot 运行版本", enabled=resume_bridge))
        return {"identity": secret, "bridgeResume": status}

    return app


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--resume-bridge", action="store_true")
    args = parser.parse_args()
    lease = DirectoryLease(args.config.parent, create=False)
    try:
        _configure_logging()
        config = AppConfig.load(args.config)
        run(create_app(config, resume_bridge=args.resume_bridge), config)
    finally:
        lease.close()


def run(app, config: AppConfig) -> None:

    server = uvicorn.Server(uvicorn.Config(
        app, host=config.webui.host, port=config.webui.port, access_log=False,
        timeout_graceful_shutdown=3,
    ))

    def watch_parent():
        # A closed pipe covers both normal stop and launcher crashes.
        sys.stdin.buffer.readline()
        server.should_exit = True

    threading.Thread(target=watch_parent, daemon=True).start()
    server.run()


if __name__ == "__main__":
    main()
