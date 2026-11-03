"""JSON-lines desktop IPC. stdout is reserved exclusively for protocol frames."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from ..config import ConfigError
from .desktop_manager import DesktopManager
from .locking import DirectoryLease


class Dispatcher:
    def __init__(self, manager: DesktopManager, emit=lambda value: None):
        self.manager = manager
        self.emit = emit
        self.mutations = asyncio.Lock()

    async def dispatch(self, method: str, params: dict | None = None):
        params = params or {}
        if not isinstance(params, dict):
            raise ConfigError("请求参数必须是对象")
        manager = self.manager
        instance_id = params.get("id")
        if method == "snapshot":
            return manager.snapshot()
        if method == "settings_get":
            return manager.settings()
        if method == "versions_list":
            return manager.versions.list()
        if method == "releases":
            return await manager.versions.releases()
        if method == "logs":
            return {"text": manager.logs(instance_id)}
        if method == "instance_info":
            row = next((r for r in manager.rows() if r["id"] == instance_id), None)
            if row is None or row["state"] != "running":
                raise ConfigError("请先启动实例")
            return row
        if method == "credentials":
            manager.metadata(instance_id)
            directory = manager.directory(instance_id)
            raw = json.loads((directory / "config.json").read_text(encoding="utf-8-sig"))
            if (directory / raw["storage"]["data_dir"] / "webui-auth.json").exists():
                raise ConfigError("实例已设置管理密码，请使用密码登录")
            return {"token": raw["webui"]["access_token"]}
        async with self.mutations:
            self.emit({"event": "operation-progress", "data": {
                "operation": method, "message": "正在处理", "progress": 0}})
            result = await self._mutate(method, params)
            self.emit({"event": "operation-progress", "data": {
                "operation": method, "message": "操作完成", "progress": 100}})
            return result

    async def _mutate(self, method: str, p: dict):
        manager, instance_id = self.manager, p.get("id")
        if method == "settings_set":
            return manager.save_settings(p)
        if method == "create":
            return await manager.create(p.get("name"), p.get("mode", "helper"), p.get("port"), p.get("version"))
        if method == "update":
            await manager.update(instance_id, p.get("name"), p.get("port"))
        elif method == "start":
            await manager.start(instance_id)
        elif method == "stop":
            await manager.stop(instance_id)
        elif method == "restart":
            await manager.stop(instance_id)
            await manager.start(instance_id)
        elif method == "archive":
            await manager.archive(instance_id)
        elif method == "backup":
            return {"name": await manager.backup(instance_id)}
        elif method == "restore":
            return await manager.restore(p.get("name"), path=p.get("path"))
        elif method == "restore_archive":
            return await manager.restore_archive(p.get("archive_name"))
        elif method == "upgrade":
            return await manager.upgrade(instance_id, p.get("version"))
        elif method == "version_import":
            referenced_versions = {r["version"] for r in manager.rows() + manager.archives()}
            return await manager.versions.import_archive(
                Path(p["path"]), replace=True, referenced_versions=referenced_versions)
        elif method == "version_download":
            return await manager.versions.download(p.get("version"), lambda message, progress: self.emit({
                "event": "operation-progress", "data": {"operation": method, "message": message, "progress": progress}}))
        elif method == "version_remove":
            referenced = {r["version"] for r in manager.rows() + manager.archives()}
            manager.versions.remove(p.get("version"), referenced)
        else:
            raise ConfigError(f"不支持的管理方法: {method}")
        return {"ok": True}


async def serve(root: Path, project: Path):
    output = sys.stdout
    sys.stdout = sys.stderr
    def emit(value):
        output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()
    lease = DirectoryLease(root)
    manager = DesktopManager(root, project)
    dispatcher = Dispatcher(manager, emit)
    tasks: set[asyncio.Task] = set()
    async def handle(request):
        request_id = request.get("id")
        try:
            if not isinstance(request_id, (int, str)) or isinstance(request_id, bool):
                raise ConfigError("请求 ID 无效")
            result = await dispatcher.dispatch(request.get("method"), request.get("params"))
            emit({"id": request_id, "result": result})
        except Exception as error:
            emit({"id": request_id, "error": str(error)})
    try:
        await manager.initialize()
        emit({"event": "ready", "data": {"root": str(root), "protocol": 1}})
        while True:
            line = await asyncio.to_thread(sys.stdin.buffer.readline, 1024 * 1024 + 1)
            if not line:
                break
            try:
                if len(line) > 1024 * 1024:
                    emit({"id": None, "error": "请求超过长度限制，连接已关闭"})
                    break
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ConfigError("请求必须是对象")
                if request.get("method") == "shutdown":
                    for task in list(tasks):
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await manager.close()
                    emit({"id": request.get("id"), "result": {"ok": True}})
                    return
                task = asyncio.create_task(handle(request))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            except (ValueError, ConfigError) as error:
                emit({"id": None, "error": str(error)})
    except Exception as error:
        emit({"event": "fatal", "data": {"message": str(error)}})
        raise
    finally:
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if not manager.closing:
            await manager.close()
        lease.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(serve(args.root.resolve(), args.project.resolve()))


if __name__ == "__main__":
    main()
