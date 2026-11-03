param(
    [Parameter(Mandatory)][int]$LauncherPid,
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$Sha256,
    [Parameter(Mandatory)][string]$LauncherExe,
    [Parameter(Mandatory)][string]$RestartArguments
)
$ErrorActionPreference = 'Stop'
$log = Join-Path (Split-Path -Parent $Installer) 'install.log'
$exitCode = 0
$restart = $false
try {
    # Bind to the original process handle before waiting; never kill any process.
    $launcher = Get-Process -Id $LauncherPid -ErrorAction SilentlyContinue
    if ($launcher -and -not $launcher.WaitForExit(120000)) { throw 'Launcher did not exit; installation cancelled.' }
    $restart = $true
    # Use .NET directly so the stock Windows PowerShell needs no optional modules.
    $stream = [IO.File]::OpenRead($Installer)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $hasher.Dispose() }
    if ($actualHash -ne $Sha256) {
        throw 'Installer checksum changed; installation cancelled.'
    }
    $installDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($LauncherExe))
    # NSIS requires /D= to be last and unquoted, including paths with spaces.
    # /UPDATE preserves existing installation tasks and skips reinstall choices.
    $process = Start-Process -FilePath $Installer -ArgumentList "/S /UPDATE /D=$installDirectory" -Verb RunAs -Wait -PassThru
    if ($process.ExitCode -notin @(0, 3010)) { throw "Installer exited with code $($process.ExitCode)." }
    'Silent update completed. Restarting the application with its existing workspace.' |
        Out-File -LiteralPath $log -Encoding utf8 -Append
} catch {
    $_ | Out-File -LiteralPath $log -Encoding utf8 -Append
    $exitCode = 1
} finally {
    Remove-Item -LiteralPath (Join-Path (Split-Path -Parent $Installer) '.handoff') -ErrorAction SilentlyContinue
}
# Success and recoverable failure both return to the same executable/workspace.
# A still-running launcher is left alone when the exit wait times out.
if ($restart -and (Test-Path -LiteralPath $LauncherExe)) {
    try { Start-Process -FilePath $LauncherExe -ArgumentList $RestartArguments -WindowStyle Normal | Out-Null }
    catch {
        $_ | Out-File -LiteralPath $log -Encoding utf8 -Append
        $exitCode = 1
    }
}
exit $exitCode
