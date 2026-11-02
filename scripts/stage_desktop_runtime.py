"""Build a verified offline runtime resource and an importable version ZIP."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "bot" / "scripts"))
sys.path.insert(0, str(PROJECT_ROOT / "bot" / "src"))
from build_portable import ROOT, bundle_python, copy_tree, copy_launcher
from wechat_onebot import __version__


def locked_runtime(path: Path) -> bool:
    installed = {d.metadata["Name"].lower().replace("_", "-"): d.version
                 for d in importlib.metadata.distributions(path=[str(path / "Lib" / "site-packages")])}
    expected = {}
    for line in (ROOT / "requirements-runtime.lock").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            expected[name.lower().replace("_", "-")] = version
    return path.joinpath("python.exe").is_file() and installed == expected


def main():
    build = Path(os.environ.get("WECHAT_BUILD_ROOT", "D:/Tool/Build/WeChatOneBotLauncher"))
    build.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=build) as temporary:
        stage = Path(temporary) / "runtime"
        stage.mkdir()
        # bundle_python verifies importing wechat_onebot from the staged tree.
        # Source must already exist when building without a cached interpreter.
        for name in ("src", "vendor", "webui", "assets"):
            copy_tree(ROOT / name, stage / name)
        copy_launcher(stage, project=ROOT.parent)
        candidates = sorted((ROOT.parent / "dist").glob("wechat-onebot-portable-*/python"), reverse=True)
        cached = next((p for p in candidates if locked_runtime(p)), None)
        if cached:
            copy_tree(cached, stage / "python")
        else:
            bundle_python(stage)
        for name in ("THIRD_PARTY_NOTICES.md", "requirements-runtime.lock"):
            shutil.copy2(ROOT / name, stage / name)
        files = {}
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                with path.open("rb") as stream:
                    files[path.relative_to(stage).as_posix()] = hashlib.file_digest(stream, "sha256").hexdigest()
        manifest = {"package_schema": 1, "launcher_api": 1, "version": __version__,
                    "architecture": "windows-x64", "files": files}
        (stage / "VERSION_MANIFEST.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        from wechat_onebot.launcher.versions import validate_manifest
        validate_manifest(stage)
        target = ROOT.parent / "launcher" / "desktop" / "src-tauri" / "resources" / "runtime"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or target.resolve() != target.absolute():
                raise RuntimeError("Runtime resource path redirects outside the workspace")
            shutil.rmtree(target)
        shutil.copytree(stage, target)
        output = ROOT.parent / "dist" / f"wechat-onebot-runtime-{__version__}-windows-x64.zip"
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in stage.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(stage))
        print(json.dumps({"runtime": str(target), "package": str(output), "files": len(files)}))


if __name__ == "__main__":
    main()
