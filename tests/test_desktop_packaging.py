from __future__ import annotations

import importlib
import json
from pathlib import Path


def test_runtime_staging_injects_launcher_into_external_gateway(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    staging = importlib.import_module("stage_desktop_runtime")
    runtime = tmp_path / "gateway"
    for name, content in {
        "src/wechat_onebot/__init__.py": '__version__ = "0.8.0"\n',
        "webui/index.html": "<html>gateway</html>",
        "THIRD_PARTY_NOTICES.md": "fixture\n",
        "requirements-runtime.lock": "fixture==1.0\n",
        "pyproject.toml": '[project]\nname = "fixture"\ndependencies = []\n',
    }.items():
        path = runtime / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in ("vendor", "assets"):
        (runtime / name).mkdir()
    launcher = tmp_path / "launcher"
    backend = launcher / "python/wechat_onebot/launcher"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text('__version__ = "0.8.0"\n', encoding="utf-8")
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
    staging.main()
    assert len(calls) == 1
    assert calls[0][1] == runtime
    installed = launcher / "desktop/src-tauri/resources/runtime"
    manifest = json.loads((installed / "VERSION_MANIFEST.json").read_text())
    assert "src/wechat_onebot/__init__.py" in manifest["files"]
    worker = installed / "src/wechat_onebot/launcher/worker.py"
    assert worker.read_text() == "# launcher worker fixture\n"
    assert (launcher / "dist/wechat-onebot-runtime-0.8.0-windows-x64.zip").is_file()
