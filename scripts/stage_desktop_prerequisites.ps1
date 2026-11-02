$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$cache = if ($env:WECHAT_BUILD_CACHE) { $env:WECHAT_BUILD_CACHE } else { 'D:/Tool/Cache/WeChatOneBotLauncher' }
New-Item -ItemType Directory -Force -Path $cache | Out-Null
$name = 'vc_redist.x64.exe'
$expected = 'CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B'
$url = 'https://download.visualstudio.microsoft.com/download/pr/990c720a-46ce-4dc7-87b9-c2eb615d34b9/CC0FF0EB1DC3F5188AE6300FAEF32BF5BEEBA4BDD6E8E445A9184072096B713B/VC_redist.x64.exe'
$source = Join-Path $cache $name
if (-not (Test-Path -LiteralPath $source) -or (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne $expected) {
    Invoke-WebRequest -Uri $url -OutFile "$source.partial"
    if ((Get-FileHash -LiteralPath "$source.partial" -Algorithm SHA256).Hash -ne $expected) { throw 'Visual C++ redistributable checksum mismatch.' }
    Move-Item -LiteralPath "$source.partial" -Destination $source -Force
}
$signature = Get-AuthenticodeSignature -LiteralPath $source
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
    throw 'Visual C++ redistributable Microsoft signature verification failed.'
}
$resources = Join-Path $root 'desktop/src-tauri/resources'
New-Item -ItemType Directory -Force -Path $resources | Out-Null
Copy-Item -LiteralPath $source -Destination (Join-Path $resources $name) -Force
Write-Output 'Staged Microsoft Visual C++ 14.44.35211 x64 offline prerequisite.'
