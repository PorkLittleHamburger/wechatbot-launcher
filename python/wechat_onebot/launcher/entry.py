from __future__ import annotations

import argparse
import os
import secrets
import threading
import webbrowser
from pathlib import Path

import uvicorn

from .locking import DirectoryLease
from .manager import LauncherManager
from .server import create_launcher_app


def runtime_source() -> Path:
    raw = os.environ.get("WECHAT_RUNTIME_SOURCE")
    if not raw:
        raise RuntimeError(
            "WECHAT_RUNTIME_SOURCE must point to the wechatbot gateway checkout."
        )
    root = Path(raw).resolve()
    for candidate in (root, root / "bot"):
        if (candidate / "src" / "wechat_onebot" / "__init__.py").is_file():
            return candidate
    raise RuntimeError(f"Invalid WECHAT_RUNTIME_SOURCE: {root}")


def command_launcher(args) -> int:
    project = runtime_source()
    root = Path(args.data_dir).resolve() if args.data_dir else Path.cwd() / "data" / "launcher"
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wechatbot-launcher")
    parser.add_argument("--port", type=int, default=5088)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--no-browser", action="store_true")
    return command_launcher(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
