param(
    [Parameter(Mandatory)][int]$LauncherPid,
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Sha256,
    [Parameter(Mandatory)][string]$LauncherExe,
    [Parameter(Mandatory)][string]$RestartArguments
)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path -Parent $Installer) 'install.log'
try {
    # Bind to the original process handle before waiting; never kill any process.
    $launcher = Get-Process -Id $LauncherPid -ErrorAction SilentlyContinue
    if ($launcher -and -not $launcher.WaitForExit(120000)) { throw 'Launcher did not exit; installation cancelled.' }
    if ((Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash -ne $Sha256) {
        throw 'Installer checksum changed; installation cancelled.'
    }
    $process = Start-Process -FilePath $Installer -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Installer exited with code $($process.ExitCode)." }
    'Installation completed. Use the installer finish page or Start Menu to launch the updated application.' |
        Out-File -LiteralPath $log -Encoding utf8 -Append
} catch {
    $_ | Out-File -LiteralPath $log -Encoding utf8 -Append
    # Cancellation, UAC denial, or installation failure returns to the existing app.
    if (Test-Path -LiteralPath $LauncherExe) { Start-Process -FilePath $LauncherExe -ArgumentList $RestartArguments -WindowStyle Normal }
    exit 1
} finally {
    Remove-Item -LiteralPath (Join-Path (Split-Path -Parent $Installer) '.handoff') -ErrorAction SilentlyContinue
}
