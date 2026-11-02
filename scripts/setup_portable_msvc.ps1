#requires -Version 7.0
<#
Extract official Microsoft payloads without running an installer, registering
Visual Studio, or changing machine/user environment variables. x64 host/target.
The payload URLs and hashes are pinned; no third-party repackaged tools are used.
#>
[CmdletBinding()]
param(
    [string]$InstallRoot = 'D:/Tool/MicrosoftVisualStudio/BuildTools',
    [string]$CacheRoot = 'D:/Tool/Cache/WeChatOneBotLauncher',
    [string]$BuildRoot = 'D:/Tool/Build/WeChatOneBotLauncher',
    [string]$RustRoot = 'D:/Tool/Rust',
    [string]$Proxy = 'http://127.0.0.1:7897'
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Add-Type -AssemblyName System.IO.Compression.FileSystem
foreach ($directory in @($InstallRoot, $CacheRoot, $BuildRoot)) {
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
}
$InstallRoot = (Resolve-Path -LiteralPath $InstallRoot).Path
$CacheRoot = (Resolve-Path -LiteralPath $CacheRoot).Path
$BuildRoot = (Resolve-Path -LiteralPath $BuildRoot).Path
$sdkVersion = '10.0.26100.0'
$sdkPackageVersion = '10.0.26100.9457'
$vcVersion = '14.44.35207'
$sdkRoot = Join-Path $InstallRoot 'Windows Kits/10'

function Get-VerifiedPayload([string]$Name, [string]$Url, [string]$Algorithm, [string]$Hash) {
    $uri = [Uri]$Url
    if ($uri.Scheme -ne 'https' -or $uri.Host -notin @('download.visualstudio.microsoft.com', 'api.nuget.org')) {
        throw "Unexpected payload origin: $Url"
    }
    $target = Join-Path $CacheRoot $Name
    if (-not (Test-Path -LiteralPath $target) -or (Get-FileHash -LiteralPath $target -Algorithm $Algorithm).Hash -ne $Hash) {
        $partial = "$target.partial"
        $curlArguments = @('--fail', '--location', '--retry', '2', '--silent', '--show-error')
        if ($Proxy) { $curlArguments += @('--proxy', $Proxy) }
        & "$env:SystemRoot/System32/curl.exe" @curlArguments --output $partial $Url
        if ($LASTEXITCODE -ne 0) { throw "Download failed: $Name" }
        if ((Get-FileHash -LiteralPath $partial -Algorithm $Algorithm).Hash -ne $Hash) {
            throw "Payload checksum mismatch: $Name"
        }
        Move-Item -LiteralPath $partial -Destination $target -Force
    }
    Write-Host "Verified $Name"
    return $target
}

function Expand-PayloadPrefix([string]$ArchivePath, [string]$Prefix, [string]$Destination) {
    $destinationPath = [IO.Path]::GetFullPath($Destination)
    $boundary = $destinationPath.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    try {
        foreach ($entry in $archive.Entries) {
            if (-not $entry.FullName.StartsWith($Prefix, [StringComparison]::OrdinalIgnoreCase) -or $entry.FullName.EndsWith('/')) { continue }
            $relative = [Uri]::UnescapeDataString($entry.FullName.Substring($Prefix.Length))
            $target = [IO.Path]::GetFullPath((Join-Path $destinationPath $relative))
            if (-not $target.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Archive path escapes destination: $($entry.FullName)"
            }
            New-Item -ItemType Directory -Path (Split-Path -Parent $target) -Force | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $target, $true)
        }
    } finally { $archive.Dispose() }
}

# VS 2022 17.14 official VSIX payloads. Each checksum is verified independently
# of the channel/catalog envelope; archive Contents/ preserves the VC layout.
$vcPayloads = @(
    @('Microsoft.VC.14.44.17.14.Tools.HostX64.TargetX64.base.vsix', 'aa209763-134c-427b-9960-9ca54ccc1734', 'f4ad3cee4ef18e9f36382399261de27be6910ab3467178d7e687377ec841306d'),
    @('Microsoft.VC.14.44.17.14.Tools.HostX64.TargetX64.Res.base.enu.vsix', 'aa209763-134c-427b-9960-9ca54ccc1734', '603375e4d6d639bc6e0a7d0d657b17e0c3b72e52450f78eb41e987f40c3b5305'),
    @('Microsoft.VC.14.44.17.14.CRT.Headers.base.vsix', 'c610cd8c-801b-44b8-a80a-82cc382aeb43', '852382a9aa73502b7849c1bcadfb603ba7175c4e8b60e6aba03c7de711d4ece5'),
    @('Microsoft.VC.14.44.17.14.CRT.Headers.Resources.base.enu.vsix', 'c610cd8c-801b-44b8-a80a-82cc382aeb43', '7b6ead51c6d5cf775acaf50df36e89486eeea6c805c37785db6a337da20ee340'),
    @('Microsoft.VC.14.44.17.14.CRT.x64.Desktop.base.vsix', '67cf767c-5e71-47c2-a54a-cd5631e28942', 'f01f701a7bcd9587a340898c851424f6a52bb913a70c185ff0d5bf0288c5831a'),
    @('Microsoft.VC.14.44.17.14.CRT.x64.Store.base.vsix', '67cf767c-5e71-47c2-a54a-cd5631e28942', '9135b03c0df53c7a0aa9bef7230a1c2ff4263a0ee7baa7e419d034f484f6bb56')
)
foreach ($payload in $vcPayloads) {
    $url = "https://download.visualstudio.microsoft.com/download/pr/$($payload[1])/$($payload[2])/$($payload[0])"
    $file = Get-VerifiedPayload $payload[0] $url 'SHA256' $payload[2]
    Expand-PayloadPrefix $file 'Contents/' $InstallRoot
}

# SHA512 values are from NuGet's official catalog packageHash fields.
$sdkPayloads = @(
    @('microsoft.windows.sdk.cpp', '/a5F/u7SsHhxxUrEhLG+9FTnT/CC7JpkCAGWDfi2LTppKktfBxdW85coVnwoxYyLZ1H0xq+RYWhMsOBBC6/4Ww=='),
    @('microsoft.windows.sdk.cpp.x64', '/jGLVgSrtNwzFgT555FP3YuISYkr+sMoj5KkOwwTk+VlSc9TloEoosYnCxPC3j7GcfSyVF3WDOGTRJc3Vz6QGg=='),
    @('microsoft.windows.sdk.buildtools', 'bVwznDT8AETSle4ePhMLXMFrjvLjHje6AoHzX0cZgJ7nhBfixZID1t5GJvpiNANNbndO58BbLv9VfMHUbQ9mTA==')
)
foreach ($payload in $sdkPayloads) {
    $id = $payload[0]
    $name = "$id.$sdkPackageVersion.nupkg"
    $url = "https://api.nuget.org/v3-flatcontainer/$id/$sdkPackageVersion/$name"
    $hash = [Convert]::ToHexString([Convert]::FromBase64String($payload[1]))
    $file = Get-VerifiedPayload $name $url 'SHA512' $hash
    switch ($id) {
        'microsoft.windows.sdk.cpp' {
            Expand-PayloadPrefix $file "c/Include/$sdkVersion/" (Join-Path $sdkRoot "Include/$sdkVersion")
        }
        'microsoft.windows.sdk.cpp.x64' {
            Expand-PayloadPrefix $file 'c/um/x64/' (Join-Path $sdkRoot "Lib/$sdkVersion/um/x64")
            Expand-PayloadPrefix $file 'c/ucrt/x64/' (Join-Path $sdkRoot "Lib/$sdkVersion/ucrt/x64")
        }
        'microsoft.windows.sdk.buildtools' {
            Expand-PayloadPrefix $file "bin/$sdkVersion/x64/" (Join-Path $sdkRoot "bin/$sdkVersion/x64")
        }
    }
}

$vcRoot = Join-Path $InstallRoot "VC/Tools/MSVC/$vcVersion"
$vcBin = Join-Path $vcRoot 'bin/Hostx64/x64'
$sdkBin = Join-Path $sdkRoot "bin/$sdkVersion/x64"
foreach ($file in @("$vcBin/cl.exe", "$vcBin/link.exe", "$vcBin/c1xx.dll", "$sdkBin/rc.exe", "$sdkBin/mt.exe")) {
    $signature = Get-AuthenticodeSignature -LiteralPath $file
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw "Microsoft signature validation failed: $file ($($signature.Status))"
    }
}
foreach ($file in @(
    "$vcRoot/include/vector", "$vcRoot/lib/x64/libcmt.lib", "$vcRoot/lib/x64/vcruntime.lib",
    "$sdkRoot/Include/$sdkVersion/um/Windows.h", "$sdkRoot/Include/$sdkVersion/ucrt/stdio.h",
    "$sdkRoot/Lib/$sdkVersion/ucrt/x64/ucrt.lib",
    "$sdkRoot/Lib/$sdkVersion/um/x64/kernel32.lib", "$sdkRoot/Lib/$sdkVersion/um/x64/user32.lib",
    "$sdkRoot/Lib/$sdkVersion/um/x64/advapi32.lib"
)) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing toolchain file: $file" }
}

# Single-quoted literals ensure generated values do not depend on setup scope.
$installLiteral = $InstallRoot.Replace("'", "''")
$rustLiteral = ([IO.Path]::GetFullPath($RustRoot)).Replace("'", "''")
$environment = @'
# Dot-source this file in each build PowerShell process. No registry changes.
$portableMsvcRoot = '__INSTALL__'
$portableMsvcVc = Join-Path $portableMsvcRoot 'VC/Tools/MSVC/14.44.35207'
$portableMsvcSdk = Join-Path $portableMsvcRoot 'Windows Kits/10'
$portableMsvcSdkVersion = '10.0.26100.0'
$portableMsvcBin = Join-Path $portableMsvcVc 'bin/Hostx64/x64'
$portableMsvcSdkBin = Join-Path $portableMsvcSdk "bin/$portableMsvcSdkVersion/x64"
$env:CARGO_HOME = '__RUST__/cargo'
$env:RUSTUP_HOME = '__RUST__/rustup'
$env:PATH = (@($portableMsvcBin, $portableMsvcSdkBin, "$env:CARGO_HOME/bin") + ($env:PATH -split ';') | Select-Object -Unique) -join ';'
$env:INCLUDE = @("$portableMsvcVc/include", "$portableMsvcSdk/Include/$portableMsvcSdkVersion/ucrt", "$portableMsvcSdk/Include/$portableMsvcSdkVersion/shared", "$portableMsvcSdk/Include/$portableMsvcSdkVersion/um", "$portableMsvcSdk/Include/$portableMsvcSdkVersion/winrt", "$portableMsvcSdk/Include/$portableMsvcSdkVersion/cppwinrt") -join ';'
$env:LIB = @("$portableMsvcVc/lib/x64", "$portableMsvcSdk/Lib/$portableMsvcSdkVersion/ucrt/x64", "$portableMsvcSdk/Lib/$portableMsvcSdkVersion/um/x64") -join ';'
$env:VSINSTALLDIR = "$portableMsvcRoot\"
$env:VCINSTALLDIR = "$portableMsvcRoot/VC\"
$env:VCToolsInstallDir = "$portableMsvcVc\"
$env:VCToolsVersion = '14.44.35207'
$env:WindowsSdkDir = "$portableMsvcSdk\"
$env:WindowsSDKVersion = "$portableMsvcSdkVersion\"
$env:WindowsSDKLibVersion = "$portableMsvcSdkVersion\"
$env:WindowsSdkBinPath = "$portableMsvcSdk/bin\"
$env:WindowsSdkVerBinPath = "$portableMsvcSdk/bin/$portableMsvcSdkVersion\"
$env:UniversalCRTSdkDir = "$portableMsvcSdk\"
$env:UCRTVersion = $portableMsvcSdkVersion
$env:VSCMD_ARG_HOST_ARCH = 'x64'
$env:VSCMD_ARG_TGT_ARCH = 'x64'
$env:CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_LINKER = "$portableMsvcBin/link.exe"
$env:CC_x86_64_pc_windows_msvc = "$portableMsvcBin/cl.exe"
$env:CXX_x86_64_pc_windows_msvc = "$portableMsvcBin/cl.exe"
$env:RC = "$portableMsvcSdkBin/rc.exe"
$env:MT = "$portableMsvcSdkBin/mt.exe"
'@
$environment = $environment.Replace('__INSTALL__', $installLiteral).Replace('__RUST__', $rustLiteral)
$envPath = Join-Path $BuildRoot 'msvc-env.ps1'
Set-Content -LiteralPath $envPath -Value $environment -Encoding utf8
Write-Host "Portable x64 compiler and SDK extracted. Dot-source: $envPath"
