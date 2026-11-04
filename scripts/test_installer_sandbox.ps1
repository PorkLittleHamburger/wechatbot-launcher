param([switch]$Launch)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dist = Join-Path $root 'dist'
$installer = Join-Path $dist 'WeChat-OneBot-Launcher_0.8.6_x64-setup.exe'
if (-not (Test-Path -LiteralPath $installer)) { throw 'Build the offline installer first.' }
$results = Join-Path $dist 'installer-acceptance'
New-Item -ItemType Directory -Force -Path $results | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'sandbox_acceptance.ps1') -Destination (Join-Path $dist 'sandbox-acceptance.ps1') -Force
$escapedDist = [Security.SecurityElement]::Escape($dist)
$escapedResults = [Security.SecurityElement]::Escape($results)
$configuration = @"
<Configuration>
  <Networking>Disable</Networking>
  <ClipboardRedirection>Disable</ClipboardRedirection>
  <MappedFolders>
    <MappedFolder><HostFolder>$escapedDist</HostFolder><SandboxFolder>C:\HostArtifacts</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>$escapedResults</HostFolder><SandboxFolder>C:\AcceptanceResults</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\HostArtifacts\sandbox-acceptance.ps1</Command></LogonCommand>
</Configuration>
"@
$path = Join-Path $dist 'WeChat-OneBot-Offline-Acceptance.wsb'
Set-Content -LiteralPath $path -Value $configuration -Encoding utf8
Write-Output $path
if ($Launch) {
    $sandbox = Join-Path $env:SystemRoot 'System32/WindowsSandbox.exe'
    if (-not (Test-Path -LiteralPath $sandbox)) { throw 'Windows Sandbox is not enabled. The generated .wsb can be used on an enabled Windows 11 host.' }
    Start-Process -FilePath $sandbox -ArgumentList ('"' + $path + '"') -WindowStyle Hidden
}
