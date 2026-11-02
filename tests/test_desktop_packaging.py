from __future__ import annotations

import importlib
import json
import tomllib
from pathlib import Path


def test_fresh_runtime_stages_source_before_python_import_check(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    staging = importlib.import_module("stage_desktop_runtime")
    root = tmp_path / "project" / "bot"
    for name, content in {
        "src/wechat_onebot/__init__.py": '__version__ = "0.8.0"\n',
        "src/wechat_onebot/launcher/worker.py": "# offline fixture\n",
        "webui/index.html": "<html>fixture</html>",
        "THIRD_PARTY_NOTICES.md": "fixture\n",
        "requirements-runtime.lock": "fixture==1.0\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    for name in ("vendor", "assets"):
        (root / name).mkdir()
    backend = root.parent / "launcher/python/wechat_onebot/launcher"
    backend.mkdir(parents=True)
    (backend / "__init__.py").write_text("# launcher package fixture\n", encoding="utf-8")
    (backend / "worker.py").write_text("# launcher worker fixture\n", encoding="utf-8")
    panel = root.parent / "launcher/webui/launcher.html"
    panel.parent.mkdir(parents=True)
    panel.write_text("<html>launcher fixture</html>", encoding="utf-8")
    calls = []

    def fresh_python(target):
        # This models the real bundled interpreter's import smoke check and
        # fails if source is copied only after bundle_python returns.
        assert (target / "src/wechat_onebot/__init__.py").is_file()
        calls.append(target)
        (target / "python").mkdir()
        (target / "python/python.exe").write_bytes(b"offline package fixture")

    monkeypatch.setattr(staging, "ROOT", root)
    monkeypatch.setattr(staging, "bundle_python", fresh_python)
    monkeypatch.setenv("WECHAT_BUILD_ROOT", str(tmp_path / "build"))
    staging.main()
    assert len(calls) == 1
    installed = root.parent / "launcher/desktop/src-tauri/resources/runtime"
    manifest = json.loads((installed / "VERSION_MANIFEST.json").read_text())
    assert "src/wechat_onebot/__init__.py" in manifest["files"]
    assert (installed / "src/wechat_onebot/launcher/worker.py").read_text() == "# launcher worker fixture\n"
    assert (installed / "webui/launcher.html").read_text() == "<html>launcher fixture</html>"
    assert (root.parent / "dist/wechat-onebot-runtime-0.8.0-windows-x64.zip").is_file()


def test_portable_metadata_has_no_monorepo_package_paths(tmp_path, monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "bot/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    packaging = importlib.import_module("build_portable")
    packaging.write_portable_metadata(tmp_path)
    metadata = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    assert "package-dir" not in metadata["tool"]["setuptools"]
    assert metadata["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert metadata["project"]["version"] == "0.8.0"
