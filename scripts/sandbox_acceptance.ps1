# Executed inside Windows Sandbox only. The host artifact mapping is read-only.
$ErrorActionPreference = 'Stop'
if ($env:USERNAME -ne 'WDAGUtilityAccount' -or -not (Test-Path 'C:/HostArtifacts')) {
    throw 'This script must only run in the generated Windows Sandbox environment.'
}
Start-Transcript -LiteralPath 'C:/AcceptanceResults/install.log' -Force
try {
    $source = 'C:/HostArtifacts/WeChat-OneBot-Launcher_0.8.11_x64-setup.exe'
    $installer = 'C:/Windows/Temp/WeChat-OneBot-setup.exe'
    Copy-Item -LiteralPath $source -Destination $installer
    $install = Start-Process -FilePath $installer -ArgumentList '/S' -Wait -PassThru
    if ($install.ExitCode -ne 0) { throw "Offline installation failed: $($install.ExitCode)" }
    $entries = @(
        Get-ItemProperty 'HKLM:/SOFTWARE/Microsoft/Windows/CurrentVersion/Uninstall/*' -ErrorAction SilentlyContinue
        Get-ItemProperty 'HKLM:/SOFTWARE/WOW6432Node/Microsoft/Windows/CurrentVersion/Uninstall/*' -ErrorAction SilentlyContinue
    ) | Where-Object DisplayName -eq 'WeChat OneBot Launcher'
    $entry = $entries | Select-Object -First 1
    if (-not $entry) { throw 'Uninstall registry entry missing.' }
    $app = Join-Path $entry.InstallLocation 'wechat-onebot-launcher.exe'
    if (-not (Test-Path -LiteralPath $app)) { throw 'Installed desktop EXE missing.' }
    $links = Get-ChildItem 'C:/ProgramData/Microsoft/Windows/Start Menu/Programs' -Filter '*WeChat*OneBot*.lnk' -Recurse
    if (-not $links) { throw 'Start Menu shortcut missing.' }
    $upgrade = Start-Process -FilePath $installer -ArgumentList '/S' -Wait -PassThru
    if ($upgrade.ExitCode -ne 0) { throw "Cover installation failed: $($upgrade.ExitCode)" }
    $workspace = Join-Path $env:LOCALAPPDATA 'WeChatOneBotLauncher'
    New-Item -ItemType Directory -Path $workspace -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $workspace 'acceptance-preserve.txt') -Value 'Sandbox test data'
    # Leave the real desktop visible for manual UI verification in this sandbox.
    Start-Process -FilePath $app -WindowStyle Normal
    @{offlineInstall='passed';coverInstall='passed';shortcut='passed';app=$app;workspace=$workspace;
      remaining='Verify UI, tray, mock instances; exit launcher before uninstalling and verify acceptance-preserve.txt survives.'} |
        ConvertTo-Json | Set-Content 'C:/AcceptanceResults/result.json' -Encoding utf8
    Write-Host 'Installation checks passed. Complete desktop and uninstall checks inside this isolated session.'
} catch {
    @{failure=$_.Exception.Message} | ConvertTo-Json | Set-Content 'C:/AcceptanceResults/result.json' -Encoding utf8
    throw
} finally {
    Stop-Transcript
}
