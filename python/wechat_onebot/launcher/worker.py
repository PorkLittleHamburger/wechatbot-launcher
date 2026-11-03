"""Child service: parent pipe EOF also requests graceful shutdown."""
from __future__ import annotations

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
from pathlib import Path


def main() -> None:
    config = AppConfig.load(sys.argv[1])
    _configure_logging()
    app = create_webui_app(config, RuntimeManager(config), start_runtime=False,
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
        return {"identity": secret}

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
    lease = DirectoryLease(Path(sys.argv[1]).parent, create=False)
    try:
        main()
    finally:
        lease.close()
