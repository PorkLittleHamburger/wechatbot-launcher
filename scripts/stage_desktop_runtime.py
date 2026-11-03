"""Assemble a verified offline runtime from an external gateway checkout and launcher files."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

import wechat_onebot
from wechat_onebot.launcher import __version__

PYTHON_VERSION = "3.13.9"
PYTHON_ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"


def resolve_runtime_source() -> Path:
    raw = os.environ.get("WECHAT_RUNTIME_SOURCE")
    if not raw:
        raise RuntimeError("Set WECHAT_RUNTIME_SOURCE to the wechatbot gateway checkout.")
    root = Path(raw).resolve()
    for candidate in (root, root / "bot"):
        if (candidate / "src" / "wechat_onebot" / "__init__.py").is_file():
            return candidate
    raise RuntimeError(f"Invalid WECHAT_RUNTIME_SOURCE: {root}")


def ignore(directory: str, names: list[str]):
    blocked = {".git", "__pycache__", ".pytest_cache", ".venv", ".runtime", "data", "logs"}
    return [name for name in names if name in blocked or name.endswith(".egg-info")]


def copy_tree(source: Path, destination: Path):
    shutil.copytree(source, destination, ignore=ignore, dirs_exist_ok=True)


def locked_runtime(path: Path, lock: Path) -> bool:
    if not (path / "python.exe").is_file():
        return False
    installed = {d.metadata["Name"].lower().replace("_", "-"): d.version
                 for d in importlib.metadata.distributions(path=[str(path / "Lib" / "site-packages")])}
    expected = {}
    for line in lock.read_text(encoding="utf-8").splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            expected[name.lower().replace("_", "-")] = version
    return installed == expected


def bundle_python(target: Path, runtime: Path) -> None:
    cache = Path(os.environ.get("WECHAT_BUILD_CACHE", str(ROOT / "dist" / ".cache")))
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / PYTHON_ARCHIVE
    if not archive.is_file():
        urllib.request.urlretrieve(
            f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}", archive
        )
    python = target / "python"
    python.mkdir()
    with zipfile.ZipFile(archive) as package:
        package.extractall(python)
    (python / "python313._pth").write_text(
        "python313.zip\n.\nLib\\site-packages\n..\\src\n", encoding="utf-8"
    )
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--only-binary=:all:", "--no-compile",
         "--python-version", "3.13", "--implementation", "cp", "--abi", "cp313",
         "--platform", "win_amd64", "--target", str(python / "Lib" / "site-packages"),
         "-r", str(runtime / "requirements-runtime.lock")],
        check=True,
    )


def verify_runtime(target: Path) -> None:
    """Exercise the assembled interpreter, including cached Python runtimes."""
    python = str(target / "python" / "python.exe")
    commands = [
        [python, "-B", "-X", "utf8", "-c",
         "import sys, fastapi, uvicorn, cryptography, PIL, wechat_onebot, "
         "wechat_onebot.launcher.worker; assert sys.flags.isolated; "
         "print(wechat_onebot.__version__)"],
        [python, "-B", "-X", "utf8",
         str(target / "vendor" / "helper" / "native" / "bridge_watchdog.py"), "--help"],
    ]
    for command in commands:
        result = subprocess.run(
            command, cwd=target, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        if result.returncode:
            raise RuntimeError(
                f"Staged runtime check failed (exit {result.returncode}): {command!r}\n"
                f"{result.stdout}\n{result.stderr}"
            )


def main():
    runtime = resolve_runtime_source()
    os.environ["WECHAT_RUNTIME_SOURCE"] = str(runtime)
    wechat_onebot.extend_runtime_path()
    build = Path(os.environ.get("WECHAT_BUILD_ROOT", "D:/Tool/Build/WeChatOneBotLauncher"))
    build.mkdir(parents=True, exist_ok=True)
    lock = runtime / "requirements-runtime.lock"
    with tempfile.TemporaryDirectory(prefix="runtime-", dir=build) as temporary:
        stage = Path(temporary) / "runtime"
        stage.mkdir()
        for name in ("src", "vendor", "webui", "assets"):
            copy_tree(runtime / name, stage / name)
        copy_tree(ROOT / "python" / "wechat_onebot" / "launcher", stage / "src" / "wechat_onebot" / "launcher")
        for name in ("THIRD_PARTY_NOTICES.md", "requirements-runtime.lock"):
            shutil.copy2(runtime / name, stage / name)
        candidates = []
        if os.environ.get("WECHAT_PYTHON_RUNTIME"):
            candidates.append(Path(os.environ["WECHAT_PYTHON_RUNTIME"]))
        candidates.append(Path(os.environ.get("WECHAT_BUILD_CACHE", str(ROOT / "dist" / ".cache"))) / "python-runtime")
        cached = next((path for path in candidates if path.is_dir() and locked_runtime(path, lock)), None)
        if cached:
            copy_tree(cached, stage / "python")
        else:
            bundle_python(stage, runtime)
        verify_runtime(stage)
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
        target = ROOT / "desktop" / "src-tauri" / "resources" / "runtime"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.is_symlink() or target.resolve() != target.absolute():
                raise RuntimeError("Runtime resource path redirects outside the workspace")
            shutil.rmtree(target)
        shutil.copytree(stage, target)
        output = ROOT / "dist" / f"wechat-onebot-runtime-{__version__}-windows-x64.zip"
        output.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in stage.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(stage))
        print(json.dumps({"runtime": str(target), "package": str(output), "files": len(files)}))


if __name__ == "__main__":
    main()
