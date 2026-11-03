# Process-local build environment. Existing installations are left in place.
$root = Split-Path -Parent $PSScriptRoot
if (-not $env:RUSTUP_HOME -and (Test-Path -LiteralPath 'D:\Tool\Rust\rustup')) {
    $env:RUSTUP_HOME = 'D:\Tool\Rust\rustup'
}
if (-not $env:CARGO_HOME -and (Test-Path -LiteralPath 'D:\Tool\Rust\cargo')) {
    $env:CARGO_HOME = 'D:\Tool\Rust\cargo'
}
if (-not $env:CARGO_TARGET_DIR) {
    $env:CARGO_TARGET_DIR = if (Test-Path -LiteralPath 'D:\Tool\Build') {
        'D:\Tool\Build\WeChatOneBotLauncher\cargo-target'
    } else {
        Join-Path $root 'build/cargo-target'
    }
}
if (-not $env:WECHAT_BUILD_ROOT) {
    $env:WECHAT_BUILD_ROOT = if (Test-Path -LiteralPath 'D:\Tool\Build') {
        'D:\Tool\Build\WeChatOneBotLauncher'
    } else {
        Join-Path $root 'build'
    }
}
if (-not $env:WECHAT_BUILD_CACHE) {
    $env:WECHAT_BUILD_CACHE = if (Test-Path -LiteralPath 'D:\Tool\Cache') {
        'D:\Tool\Cache\WeChatOneBotLauncher'
    } else {
        Join-Path $root 'build/cache'
    }
}
if (-not $env:PIP_CACHE_DIR) { $env:PIP_CACHE_DIR = Join-Path $env:WECHAT_BUILD_CACHE 'pip' }
if (-not $env:npm_config_cache) { $env:npm_config_cache = Join-Path $env:WECHAT_BUILD_CACHE 'npm' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$prepend = @()
if ($env:CARGO_HOME) {
    $cargoBin = Join-Path $env:CARGO_HOME 'bin'
    if (Test-Path -LiteralPath $cargoBin) { $prepend += $cargoBin }
}
if (Test-Path -LiteralPath 'D:\nodejs') { $prepend += 'D:\nodejs' }
if ($prepend.Count) { $env:PATH = (($prepend -join ';') + ';' + $env:PATH) }
foreach ($directory in @($env:WECHAT_BUILD_ROOT, $env:WECHAT_BUILD_CACHE, $env:PIP_CACHE_DIR, $env:npm_config_cache)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}
$compilerEnvironment = Join-Path $env:WECHAT_BUILD_ROOT 'msvc-env.ps1'
if (Test-Path -LiteralPath $compilerEnvironment) { . $compilerEnvironment }
