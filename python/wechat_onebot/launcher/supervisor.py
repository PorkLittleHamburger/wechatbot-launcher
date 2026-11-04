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
from .operations import DesktopOperations, MESSAGES


class Dispatcher:
    def __init__(self, manager: DesktopManager, emit=lambda value: None):
        self.manager = manager
        self.emit = emit
        self.operations = DesktopOperations(manager)

    async def dispatch(self, method: str, params: dict | None = None):
        params = {} if params is None else params
        if not isinstance(params, dict):
            raise ConfigError("请求参数必须是对象")
        params = dict(params)
        operation_id = params.pop("_operation_id", None)
        if operation_id is not None and (not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128):
            raise ConfigError("操作关联标识无效")
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
        if method == "launcher_update_check":
            return await manager.updater.check()
        if method == "launcher_update_prepare_install":
            return await manager.updater.prepare_install()
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
        if method not in MESSAGES:
            raise ConfigError(f"不支持的管理方法: {method}")
        start, success = MESSAGES[method]
        def progress(message, value=None, phase="progress"):
            self.emit({"event": "operation-progress", "data": {
                "operation": method, "id": instance_id or params.get("version"), "operation_id": operation_id,
                "message": message, "progress": value, "phase": phase}})
        progress(start, 0, "start")
        try:
            result = await self.operations.execute(method, params, progress)
        except asyncio.CancelledError:
            progress("操作已取消；清理和正在执行的文件步骤已结束", None, "cancelled")
            raise
        except Exception as error:
            progress(f"操作失败：{error}", None, "error")
            raise
        progress(success, 100, "success")
        return result


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
