param([switch]$NoBrowser, [switch]$Elevated, [int]$Port = 5088)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if (-not $env:WECHAT_RUNTIME_SOURCE) {
    foreach ($candidate in @((Join-Path (Split-Path -Parent $root) 'WechatBot'), (Join-Path (Split-Path -Parent $root) 'wechatbot'))) {
        $rootRuntime = Test-Path -LiteralPath (Join-Path $candidate 'src/wechat_onebot/__init__.py')
        $nestedRuntime = Test-Path -LiteralPath (Join-Path $candidate 'bot/src/wechat_onebot/__init__.py')
        if ($rootRuntime -or $nestedRuntime) {
            $env:WECHAT_RUNTIME_SOURCE = $candidate
            break
        }
    }
}
if (-not $env:WECHAT_RUNTIME_SOURCE) {
    throw 'Set WECHAT_RUNTIME_SOURCE to the wechatbot gateway checkout.'
}
if ($Elevated) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $launchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Port', $Port)
        if ($NoBrowser) { $launchArgs += '-NoBrowser' }
        Start-Process -FilePath (Get-Process -Id $PID).Path -Verb RunAs -ArgumentList $launchArgs -WorkingDirectory $root -WindowStyle Hidden
        exit 0
    }
}
$python = if ($env:WECHAT_PYTHON) { $env:WECHAT_PYTHON } else { Join-Path $root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment missing. Follow README development setup first.' }
$env:WECHAT_LAUNCHER_SOURCE = Join-Path $root 'python'
$env:PYTHONPATH = $env:WECHAT_LAUNCHER_SOURCE
$runArgs = @('-m', 'wechat_onebot.launcher.entry', '--port', $Port)
if ($NoBrowser) { $runArgs += '--no-browser' }
& $python @runArgs
exit $LASTEXITCODE
