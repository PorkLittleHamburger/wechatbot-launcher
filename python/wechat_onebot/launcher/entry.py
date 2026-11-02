from __future__ import annotations

import secrets
import threading
import webbrowser
from pathlib import Path

from .. import __file__ as core_package_file

import uvicorn

from .locking import DirectoryLease
from .manager import LauncherManager
from .server import create_launcher_app


def command_launcher(args) -> int:
    project = Path(core_package_file).resolve().parents[2]
    root = Path(args.data_dir).resolve() if args.data_dir else project / "data" / "launcher"
    lease = DirectoryLease(root)
    try:
        manager = LauncherManager(root, project, args.port)
        token = secrets.token_urlsafe(32)
        url = f"http://127.0.0.1:{args.port}/?token={token}"
        print(f"WeChat OneBot 多实例启动器\n数据目录: {root}\n打开管理页: {url}", flush=True)
        if not args.no_browser:
            timer = threading.Timer(1.2, lambda: webbrowser.open(url))
            timer.daemon = True
            timer.start()
        uvicorn.run(create_launcher_app(manager, token), host="127.0.0.1", port=args.port,
                    access_log=False)
    finally:
        lease.close()
    return 0
