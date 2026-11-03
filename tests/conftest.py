from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def resolve_runtime_source() -> Path:
    raw = os.environ.get("WECHAT_RUNTIME_SOURCE")
    candidates = [Path(raw)] if raw else []
    candidates.extend((ROOT.parent / "WechatBot", ROOT.parent / "wechatbot"))
    for candidate in candidates:
        candidate = candidate.resolve()
        for runtime in (candidate, candidate / "bot"):
            if (runtime / "src" / "wechat_onebot" / "__init__.py").is_file():
                return runtime
    raise RuntimeError(
        "Set WECHAT_RUNTIME_SOURCE to the wechatbot gateway checkout before running tests."
    )


RUNTIME_SOURCE = resolve_runtime_source()
os.environ["WECHAT_RUNTIME_SOURCE"] = str(RUNTIME_SOURCE)
os.environ["WECHAT_LAUNCHER_SOURCE"] = str(ROOT / "python")
sys.path.insert(0, str(ROOT / "python"))
