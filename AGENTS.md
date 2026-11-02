# Repository Guidelines

This directory is the WeChat OneBot Launcher within the shared `WechatBot` Git repository. Run commands from the repository root unless noted. The launcher depends on `bot/` for protocol and native-bridge sources; Python packaging maps `launcher/python/wechat_onebot/launcher` into the `wechat-onebot` wheel.

## Project Structure & Module Organization

- `desktop/src/`: React/TypeScript UI, API bindings, types, and styles.
- `desktop/src-tauri/`: Rust shell, tray/process management, Windows integration, and NSIS installer logic.
- `python/wechat_onebot/launcher/`: Python supervisor, multi-instance management, locking, updates, and desktop RPC backend.
- `webui/launcher.html`: browser management page retained for compatibility.
- `scripts/`: runtime staging and desktop/installer build automation.
- `tests/`: pytest coverage for the manager, desktop backend, packaging, and regressions.
- `docs/`: architecture, usage, build, and verification guides.

Generated files such as `desktop/dist/`, `desktop/src-tauri/target/`, `desktop/src-tauri/resources/runtime/`, `.development/`, and repository-root `dist/` are not committed.

## Build, Test, and Development Commands

```powershell
# Install development dependencies
.venv/Scripts/python.exe -m pip install -e "./bot[dev]"

# Run launcher and integrated Python tests
.venv/Scripts/python.exe -m pytest -q bot/tests launcher/tests -c bot/pyproject.toml

# Build the offline Windows installer after configuring tools per docs/building.md
pwsh -File launcher/scripts/build_desktop.ps1

# Frontend-only development and type/build checks
cd launcher/desktop
npm ci
npm run dev
npm run build
```

`build_desktop.ps1` runs tests, stages the runtime, builds the frontend, executes Rust tests, and produces the NSIS installer plus SHA-256 file. Use `-SkipTests`/`-SkipRuntime` only for iteration, not final validation.

## Coding Style & Naming Conventions

Use four spaces in Python and Rust, two in TypeScript. Follow existing Python conventions: `snake_case`, type hints, `from __future__ import annotations`, and async APIs where the surrounding code is async. Use `camelCase` for TS functions/variables and `PascalCase` for React components and types; keep strict TypeScript enabled. Rust follows standard `rustfmt`; run `cargo fmt` from `desktop/src-tauri`. No JavaScript formatter/linter is configured, so preserve local style.

Tests are named `test_<behavior>` and should be deterministic, use `tmp_path`, and prefer mock workers or offline fixtures. Tests must not inject into WeChat or send real messages.

## Commit & Pull Request Guidelines

Use concise, imperative subjects and conventional prefixes where applicable: `feat:`, `fix:`, `refactor:`, `test:`, `docs:`, or `chore:`. Keep commits scoped. Pull requests should state the problem, summarize behavior changes, link issues, and list commands/results used for verification. Include screenshots for UI changes and call out configuration migrations or security-sensitive changes. Never commit `config.json`, runtime data, secrets, generated runtimes, or installer artifacts. Push verified changes to the remote; create a GitHub Release only after explicit approval.
