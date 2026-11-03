param(
    [switch]$SkipTests,
    [switch]$SkipRuntime,
    [string]$RuntimeSource = $env:WECHAT_RUNTIME_SOURCE,
    [string]$Python = $env:WECHAT_PYTHON
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'desktop_env.ps1')
if (-not $RuntimeSource) {
    throw 'Set WECHAT_RUNTIME_SOURCE or pass -RuntimeSource with the wechatbot gateway checkout.'
}
$env:WECHAT_RUNTIME_SOURCE = (Resolve-Path -LiteralPath $RuntimeSource).Path
$env:WECHAT_LAUNCHER_SOURCE = Join-Path $root 'python'
$env:PYTHONPATH = $env:WECHAT_LAUNCHER_SOURCE
Set-Location -LiteralPath $root
if (-not $Python) { $Python = Join-Path $root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Python environment missing. Follow README development setup first or pass -Python.'
}
if (-not $SkipTests) {
    & $Python -m pytest -q tests
    if ($LASTEXITCODE -ne 0) { throw 'Python verification failed.' }
}
if (-not $SkipRuntime) {
    & $Python (Join-Path $PSScriptRoot 'stage_desktop_runtime.py')
    if ($LASTEXITCODE -ne 0) { throw 'Offline runtime staging failed.' }
}
& (Join-Path $PSScriptRoot 'stage_desktop_prerequisites.ps1')
Set-Location -LiteralPath (Join-Path $root 'desktop')
& npm.cmd ci
if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
& npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Frontend verification failed.' }
if (-not $SkipTests) {
    Push-Location -LiteralPath (Join-Path $root 'desktop/src-tauri')
    try {
        $env:WECHAT_SHELL_SMOKE_RUNTIME = Join-Path $root 'desktop/src-tauri/resources/runtime'
        & cargo test --locked -- --include-ignored
        if ($LASTEXITCODE -ne 0) { throw 'Native desktop verification failed.' }
    } finally { Pop-Location }
}
& npm.cmd run tauri -- build --bundles nsis
if ($LASTEXITCODE -ne 0) { throw 'Desktop build failed.' }
$installer = Get-ChildItem -LiteralPath (Join-Path $env:CARGO_TARGET_DIR 'release/bundle/nsis') -Filter '*.exe' |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $installer) { throw 'No NSIS installer was produced.' }
$destination = Join-Path $root 'dist/WeChat-OneBot-Launcher_0.8.1_x64-setup.exe'
Copy-Item -LiteralPath $installer.FullName -Destination $destination -Force
$hash = (Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$destination.sha256" -Value "$hash  $([IO.Path]::GetFileName($destination))" -Encoding ascii
Write-Output $destination
