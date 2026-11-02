from __future__ import annotations

import hmac
import json
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, StrictInt

from .. import __version__
from ..config import ConfigError
from .manager import LauncherManager


class CreateInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    mode: str = "mock"
    port: StrictInt | None = None


class EditInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    port: StrictInt


def create_launcher_app(manager: LauncherManager, token: str) -> FastAPI:
    session = secrets.token_urlsafe(32)
    cookie_name = f"launcher_session_{manager.launcher_port}"

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await manager.close()

    app = FastAPI(title="WeChat OneBot Launcher", lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def authenticate(request: Request, call_next):
        # Validate Host as well as credentials to prevent DNS rebinding.
        expected_hosts = {f"127.0.0.1:{manager.launcher_port}", f"localhost:{manager.launcher_port}"}
        if request.headers.get("host") not in expected_hosts:
            return JSONResponse({"detail": "无效 Host"}, status_code=403)
        if request.url.path == "/" and hmac.compare_digest(request.query_params.get("token", ""), token):
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(cookie_name, session, httponly=True, samesite="strict")
        else:
            if not hmac.compare_digest(request.cookies.get(cookie_name, ""), session):
                return JSONResponse({"detail": "请通过启动器控制台中的地址打开页面"}, status_code=401)
            if request.method not in ("GET", "HEAD"):
                if request.headers.get("X-Launcher-Request") != "1":
                    return JSONResponse({"detail": "无效请求来源"}, status_code=403)
                origin = request.headers.get("origin")
                if origin and origin not in {f"http://{host}" for host in expected_hosts}:
                    return JSONResponse({"detail": "无效请求来源"}, status_code=403)
            response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(ConfigError)
    async def config_error(request, error):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(OSError)
    async def filesystem_error(request, error):
        return JSONResponse({"detail": f"系统操作失败: {error}"}, status_code=409)

    @app.get("/")
    async def panel():
        panel_path = manager.project / "webui" / "launcher.html"
        if not panel_path.is_file():
            panel_path = manager.project.parent / "launcher/webui/launcher.html"
        return FileResponse(panel_path)

    @app.get("/api/status")
    async def status():
        return {"version": __version__, "root": str(manager.root)}

    @app.get("/api/instances")
    async def instances():
        return {"instances": manager.rows()}

    @app.post("/api/instances")
    async def create(body: CreateInstance):
        return await manager.create(body.name, body.mode, body.port)

    @app.patch("/api/instances/{instance_id}")
    async def update(instance_id: str, body: EditInstance):
        await manager.update(instance_id, body.name, body.port)
        return {"ok": True}

    @app.post("/api/instances/{instance_id}/start")
    async def start(instance_id: str):
        await manager.start(instance_id)
        return {"ok": True}

    @app.post("/api/instances/{instance_id}/stop")
    async def stop(instance_id: str):
        await manager.stop(instance_id)
        return {"ok": True}

    @app.post("/api/instances/{instance_id}/restart")
    async def restart(instance_id: str):
        await manager.stop(instance_id)
        await manager.start(instance_id)
        return {"ok": True}

    @app.post("/api/instances/{instance_id}/backup")
    async def backup(instance_id: str):
        return {"ok": True, "name": await manager.backup(instance_id)}

    @app.delete("/api/instances/{instance_id}")
    async def archive(instance_id: str):
        await manager.archive(instance_id)
        return {"ok": True}

    @app.get("/api/instances/{instance_id}/logs")
    async def logs(instance_id: str):
        return {"text": manager.logs(instance_id)}

    @app.get("/api/instances/{instance_id}/credentials")
    async def credentials(instance_id: str):
        manager.metadata(instance_id)
        directory = manager.directory(instance_id)
        raw = json.loads((directory / "config.json").read_text(encoding="utf-8-sig"))
        if (directory / raw["storage"]["data_dir"] / "webui-auth.json").exists():
            raise ConfigError("该实例已设置管理密码，请使用密码登录")
        return {"token": raw["webui"]["access_token"]}

    @app.get("/api/backups")
    async def backups():
        return {"backups": [{"name": p.name, "size": p.stat().st_size}
                            for p in sorted((manager.root / "backups").glob("*.zip"), reverse=True)]}

    return app
