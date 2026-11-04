# Repository Guidelines

This repository contains only the desktop WeChat OneBot Launcher. The gateway and native bridge live in the separate `PorkLittleHamburger/wechatbot` repository and are supplied through `WECHAT_RUNTIME_SOURCE`; do not copy gateway source, native DLLs, or gateway tests into this repository. Browser-based launcher management is intentionally not part of this project.

## Required Handoff & Documentation Maintenance

Every AI taking over this project must read [docs/HANDOFF.md](docs/HANDOFF.md) before editing, then inspect the referenced code and current Git/Release state. Historical validation is not proof of current behavior or deployment.

On takeover and during documentation audits, inventory all project documents accumulated since development began. For every update, review affected documentation against the final code, including the remote repository README. Correct stale versions, defaults, commands, removed features, and machine-specific assumptions. Use portable examples; do not publish personal paths, account details, proxy settings, or credentials as general requirements. Maintain the handoff with architecture, decisions, evidence, limitations, and outstanding work; create it if absent. Separate current instructions from historical records and label unverified checks. Verify links and examples before pushing, then confirm the remote README matches the committed file. Push verified updates; publish a Release only on explicit user request.

## Project Structure & Module Organization

- `desktop/src/`: React/TypeScript UI, API bindings, types, and styles.
- `desktop/src-tauri/`: Rust shell, tray/process management, Windows integration, and NSIS installer logic.
- `python/wechat_onebot/launcher/`: supervisor, multi-instance management, locking, updates, backups, and desktop RPC backend.
- `scripts/`: runtime assembly, desktop build, tool setup, and installer acceptance automation.
- `tests/`: pytest coverage for desktop lifecycle, packaging, versions, security boundaries, and regressions.
- `docs/`: usage, architecture, build, and verification documentation.

Generated paths such as `dist/`, `desktop/dist/`, `desktop/src-tauri/target/`, `desktop/src-tauri/resources/`, and `.development/` are not committed.

## Build, Test, and Development Commands

Set `WECHAT_RUNTIME_SOURCE` to a checkout of `PorkLittleHamburger/wechatbot` before running development or build commands.

```powershell
$env:WECHAT_RUNTIME_SOURCE = (Resolve-Path ../wechatbot).Path
.venv/Scripts/python.exe -m pytest -q tests
pwsh -File scripts/build_desktop.ps1
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
