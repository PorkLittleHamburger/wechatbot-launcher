# Process-local build environment. Existing installations are left in place.
$env:RUSTUP_HOME = 'D:\Tool\Rust\rustup'
$env:CARGO_HOME = 'D:\Tool\Rust\cargo'
$env:CARGO_TARGET_DIR = 'D:\Tool\Build\WeChatOneBotLauncher\cargo-target'
$env:WECHAT_BUILD_ROOT = 'D:\Tool\Build\WeChatOneBotLauncher'
$env:WECHAT_BUILD_CACHE = 'D:\Tool\Cache\WeChatOneBotLauncher'
$env:PIP_CACHE_DIR = Join-Path $env:WECHAT_BUILD_CACHE 'pip'
$env:npm_config_cache = Join-Path $env:WECHAT_BUILD_CACHE 'npm'
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PYTHONUTF8 = '1'
$env:PATH = "$env:CARGO_HOME\bin;D:\nodejs;$env:PATH"
foreach ($directory in @($env:WECHAT_BUILD_ROOT, $env:WECHAT_BUILD_CACHE)) {
    New-Item -ItemType Directory -Force -Path $directory | Out-Null
}
$compilerEnvironment = Join-Path $env:WECHAT_BUILD_ROOT 'msvc-env.ps1'
if (Test-Path -LiteralPath $compilerEnvironment) { . $compilerEnvironment }
