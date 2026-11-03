# Repository Guidelines

This repository contains only the WeChat OneBot Launcher. The gateway and native bridge live in the separate `PorkLittleHamburger/wechatbot` repository and are supplied through `WECHAT_RUNTIME_SOURCE`; do not copy gateway source, native DLLs, or gateway tests into this repository.

## Project Structure & Module Organization

- `desktop/src/`: React/TypeScript UI, API bindings, types, and styles.
- `desktop/src-tauri/`: Rust shell, tray/process management, Windows integration, and NSIS installer logic.
- `python/wechat_onebot/launcher/`: supervisor, multi-instance management, locking, updates, backups, and desktop RPC backend.
- `webui/launcher.html`: browser-based multi-instance management page.
- `scripts/`: runtime assembly, desktop build, tool setup, and installer acceptance automation.
- `tests/`: pytest coverage for lifecycle, packaging, versions, security boundaries, and regressions.
- `docs/`: usage, architecture, build, and verification documentation.

Generated paths such as `dist/`, `desktop/dist/`, `desktop/src-tauri/target/`, `desktop/src-tauri/resources/`, `.development/`, and `data/` are not committed.

## Build, Test, and Development Commands

Set `WECHAT_RUNTIME_SOURCE` to a checkout of `PorkLittleHamburger/wechatbot` before running development or build commands.

```powershell
$env:WECHAT_RUNTIME_SOURCE = 'D:\study\project\wechatbot'
.venv/Scripts/python.exe -m pytest -q tests
pwsh -File scripts/build_desktop.ps1
pwsh -File scripts/run_launcher.ps1
cd desktop
npm ci
npm run build
```

`scripts/build_desktop.ps1` runs Python tests, merges launcher files into the external runtime, builds the frontend, runs Rust tests, and creates the NSIS installer plus SHA-256. `-SkipTests` and `-SkipRuntime` are for incremental iteration only.

## Coding Style & Naming Conventions

Use four spaces in Python and Rust and two in TypeScript. Follow existing Python conventions: `snake_case`, type hints, `from __future__ import annotations`, and async APIs where surrounding code is async. Use `camelCase` for TypeScript functions/variables and `PascalCase` for React components and types; keep strict TypeScript enabled. Rust follows `rustfmt`. No JavaScript formatter or linter is configured, so preserve local style.

Tests are named `test_<behavior>`, must be deterministic, use `tmp_path`, and must never inject into WeChat or send real messages.

## Commit & Pull Request Guidelines

Use concise, imperative subjects with `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, or `chore:` prefixes. Pull requests should explain behavior changes, verify commands, linked issues, and UI screenshots when applicable. Never commit credentials, workspace data, generated runtimes, or installer artifacts. Push verified changes; create a GitHub Release only after explicit approval.
