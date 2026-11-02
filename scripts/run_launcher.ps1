param([switch]$NoBrowser, [switch]$Elevated, [int]$Port = 5088)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
if (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $root) 'bot/src/wechat_onebot/__init__.py')) {
    $root = Join-Path (Split-Path -Parent $root) 'bot'
}
Set-Location -LiteralPath $root
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
if ($Elevated) {
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $launchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'), '-Port', $Port)
        if ($NoBrowser) { $launchArgs += '-NoBrowser' }
        Start-Process -FilePath (Get-Process -Id $PID).Path -Verb RunAs -ArgumentList $launchArgs -WorkingDirectory $root -WindowStyle Hidden
        exit 0
    }
}
$python = Join-Path $root 'python/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path $root '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python)) { $python = Join-Path (Split-Path -Parent $root) '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Python environment missing. Follow README source installation first.' }
$env:PYTHONPATH = Join-Path $root 'src'
$runArgs = @('-m', 'wechat_onebot', 'launcher', '--port', $Port)
if ($NoBrowser) { $runArgs += '--no-browser' }
& $python @runArgs
exit $LASTEXITCODE
