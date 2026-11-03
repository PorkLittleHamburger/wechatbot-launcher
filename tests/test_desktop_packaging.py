from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("watchdog_exit", [0, 1])
def test_runtime_staging_checks_watchdog_before_export(tmp_path, monkeypatch, cached, watchdog_exit):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    staging = importlib.import_module("stage_desktop_runtime")
    runtime = tmp_path / "gateway"
    for name, content in {
        "src/wechat_onebot/__init__.py": '__version__ = "0.8.2"\n',
        "webui/index.html": "<html>gateway</html>",
        "THIRD_PARTY_NOTICES.md": "fixture\n",
        "requirements-runtime.lock": "fixture==1.0\n",
        "pyproject.toml": '[project]\nname = "fixture"\ndependencies = []\n',
        "vendor/helper/native/bridge_watchdog.py": "# watchdog fixture\n",
    }.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in ("vendor", "assets"):
        (runtime / name).mkdir(exist_ok=True)
    launcher = tmp_path / "launcher"
    backend = launcher / "python/wechat_onebot/launcher"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text('__version__ = "0.8.2"\n', encoding="utf-8")
    (backend / "worker.py").write_text("# launcher worker fixture\n", encoding="utf-8")
    calls = []

    def fresh_python(target, runtime_root):
        assert (target / "src/wechat_onebot/__init__.py").is_file()
        assert (target / "src/wechat_onebot/launcher/worker.py").is_file()
        calls.append((target, runtime_root))
        (target / "python").mkdir()
        (target / "python/python.exe").write_bytes(b"offline package fixture")

    monkeypatch.setattr(staging, "ROOT", launcher)
    monkeypatch.setattr(staging, "bundle_python", fresh_python)
    monkeypatch.setenv("WECHAT_RUNTIME_SOURCE", str(runtime))
    monkeypatch.setenv("WECHAT_BUILD_ROOT", str(tmp_path / "build"))
    cache = tmp_path / "cache" / "python-runtime"
    if cached:
        cache.mkdir(parents=True)
        (cache / "python.exe").write_bytes(b"cached Python fixture")
    monkeypatch.setenv("WECHAT_BUILD_CACHE", str(cache.parent))
    monkeypatch.delenv("WECHAT_PYTHON_RUNTIME", raising=False)
    monkeypatch.setattr(staging, "locked_runtime", lambda path, lock: cached and path == cache)
    probes = []

    def probe(command, **kwargs):
        probes.append(command)
        watchdog = command[-1] == "--help"
        return SimpleNamespace(
            returncode=watchdog_exit if watchdog else 0,
            stdout="", stderr="ModuleNotFoundError: run_selftest" if watchdog_exit and watchdog else "",
        )

    monkeypatch.setattr(staging.subprocess, "run", probe)
    if watchdog_exit:
        with pytest.raises(RuntimeError, match="ModuleNotFoundError: run_selftest"):
            staging.main()
        assert not (launcher / "desktop/src-tauri/resources/runtime").exists()
        assert not (launcher / "dist").exists()
    else:
        staging.main()
    assert len(calls) == (0 if cached else 1)
    assert any(command[-1] == "--help" and "bridge_watchdog.py" in command[-2] for command in probes)
    if watchdog_exit:
        return
    installed = launcher / "desktop/src-tauri/resources/runtime"
    manifest = json.loads((installed / "VERSION_MANIFEST.json").read_text())
    assert "src/wechat_onebot/__init__.py" in manifest["files"]
    worker = installed / "src/wechat_onebot/launcher/worker.py"
    assert worker.read_text() == "# launcher worker fixture\n"
    assert (launcher / f"dist/wechat-onebot-runtime-{staging.__version__}-windows-x64.zip").is_file()
