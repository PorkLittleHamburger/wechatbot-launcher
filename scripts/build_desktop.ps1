param([switch]$SkipTests, [switch]$SkipRuntime)
$ErrorActionPreference = 'Stop'
$launcherRoot = Split-Path -Parent $PSScriptRoot
$root = Split-Path -Parent $launcherRoot
. (Join-Path $PSScriptRoot 'desktop_env.ps1')
Set-Location -LiteralPath $root
$python = Join-Path $root '.venv/Scripts/python.exe'
if (-not $SkipTests) {
    & $python -m pytest -q bot/tests launcher/tests -c bot/pyproject.toml
    if ($LASTEXITCODE -ne 0) { throw 'Python verification failed.' }
}
if (-not $SkipRuntime) {
    & $python (Join-Path $PSScriptRoot 'stage_desktop_runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Offline runtime staging failed.' }
}
& (Join-Path $PSScriptRoot 'stage_desktop_prerequisites.ps1')
Set-Location -LiteralPath (Join-Path $launcherRoot 'desktop')
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend verification failed.' }
if (-not $SkipTests) {
    Push-Location -LiteralPath (Join-Path $launcherRoot 'desktop/src-tauri')
    try {
        $env:WECHAT_SHELL_SMOKE_RUNTIME = Join-Path $launcherRoot 'desktop/src-tauri/resources/runtime'
        & cargo test --locked -- --include-ignored
        if ($LASTEXITCODE -ne 0) { throw 'Native desktop verification failed.' }
    } finally { Pop-Location }
}
& npm.cmd run tauri -- build --bundles nsis
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
$installer = Get-ChildItem -LiteralPath (Join-Path $env:CARGO_TARGET_DIR 'release/bundle/nsis') -Filter '*.exe' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw 'No NSIS installer was produced.' }
$destination = Join-Path $root 'dist/WeChat-OneBot-Launcher_0.8.0_x64-setup.exe'
Copy-Item -LiteralPath $installer.FullName -Destination $destination -Force
$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$destination.sha256" -Value "$hash  $([IO.Path]::GetFileName($destination))" -Encoding ascii
Write-Output $destination
