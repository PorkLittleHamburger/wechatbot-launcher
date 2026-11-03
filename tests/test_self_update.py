from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import httpx
import pytest

from wechat_onebot.config import ConfigError
from wechat_onebot.launcher.self_update import LauncherUpdater


@pytest.fixture
def updates(tmp_path, monkeypatch):
    updater = LauncherUpdater(tmp_path)
    payload = b'MZ offline installer fixture'
    state = {'payload': payload, 'digest': hashlib.sha256(payload).hexdigest(), 'fail': False, 'installer': True}
    client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith('/releases/latest'):
            return httpx.Response(302, headers={'location': 'https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.9.0'})
        if request.url.path.endswith('releases.atom'):
            return httpx.Response(200, text='''<feed xmlns="http://www.w3.org/2005/Atom">
              <entry><title>Launcher 0.9.0</title><link href="https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v0.9.0"/><content type="html">&lt;p&gt;Release notes&lt;/p&gt;</content></entry>
              <entry><title>Preview</title><link href="https://github.com/PorkLittleHamburger/wechatbot-launcher/releases/tag/v99.0.0-beta"/></entry>
              <entry><link href="https://evil.example/releases/tag/v99.0.0"/></entry>
            </feed>''')
        if request.method == 'HEAD':
            if request.url.path.endswith('.exe') and not state['installer']:
                return httpx.Response(404)
            return httpx.Response(200)
        if request.url.path.endswith('.sha256'):
            return httpx.Response(200, text=f"{state['digest']}  {updater.asset('0.9.0')}\n")
        if state['fail']:
            return httpx.Response(503)
        return httpx.Response(200, content=state['payload'])
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    return updater, state, calls


@pytest.mark.asyncio
async def test_self_update_catalog_uses_stable_installer_and_plain_notes(updates):
    updater, _, calls = updates
    result = await updater.check()
    assert result['release']['version'] == '0.9.0'
    assert result['release']['notes'] == 'Release notes'
    assert not any('api.github.com' in url or 'runtime-' in url or 'evil.example' in url for url in calls)
    assert calls
    assert all('/PorkLittleHamburger/wechatbot-launcher/' in url for url in calls)


@pytest.mark.asyncio
async def test_self_update_requires_verified_download_and_rechecks_tampering(updates):
    updater, state, _ = updates
    with pytest.raises(ConfigError, match='先下载'):
        await updater.prepare_install()
    row = await updater.download('0.9.0')
    assert row['sha256'] == state['digest']
    assert (await updater.prepare_install()) == row
    from pathlib import Path
    Path(row['path']).write_bytes(b'tampered')
    with pytest.raises(ConfigError, match='改变'):
        await updater.prepare_install()
    assert updater.ready is None


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['network', 'checksum'])
async def test_self_update_failure_can_retry_without_partial_installer(updates, failure):
    updater, state, _ = updates
    if failure == 'network':
        state['fail'] = True
    else:
        state['digest'] = '0' * 64
    with pytest.raises(ConfigError):
        await updater.download('0.9.0')
    assert updater.ready is None
    assert not list(updater.directory.glob('*.partial'))
    assert not list(updater.directory.glob('*.exe'))
    state['fail'] = False
    state['digest'] = hashlib.sha256(state['payload']).hexdigest()
    assert (await updater.download('0.9.0'))['version'] == '0.9.0'


@pytest.mark.asyncio
async def test_self_update_rejects_arbitrary_or_old_version(updates):
    updater, _, _ = updates
    for version in ('../../evil', '0.8.0', '99.0.0-beta'):
        with pytest.raises(ConfigError):
            await updater.download(version)


@pytest.mark.asyncio
async def test_self_update_cancel_cleans_partial_and_releases_lock(updates, monkeypatch):
    updater, _, _ = updates
    def cancel(message, value):
        raise asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await updater.download('0.9.0', cancel)
    assert updater.ready is None
    assert not updater.lock.locked()
    assert not list(updater.directory.glob('*.partial'))
    assert (await updater.download('0.9.0'))['version'] == '0.9.0'


@pytest.mark.asyncio
async def test_current_launcher_does_not_offer_runtime_only_release(updates, monkeypatch):
    import wechat_onebot.launcher.self_update as module
    updater, _, _ = updates
    monkeypatch.setattr(module, '__version__', '0.9.0')
    assert (await updater.check())['release'] is None


@pytest.mark.asyncio
async def test_checksum_manifest_must_name_exact_installer(updates):
    updater, state, _ = updates
    state['digest'] = 'invalid checksum'
    with pytest.raises(ConfigError, match='校验文件无效'):
        await updater.download('0.9.0')
    assert updater.ready is None


@pytest.mark.skipif(os.name != 'nt', reason='Windows installer handoff')
@pytest.mark.parametrize('case', ['success', 'reboot_required', 'checksum', 'installer_failure', 'uac_denied', 'exit_timeout'])
@pytest.mark.parametrize('shell_kind', ['preferred', 'windows_powershell'])
def test_installer_handoff_without_running_installer(tmp_path, case, shell_kind):
    installer = tmp_path / 'installer with spaces.exe'
    installer.write_bytes(b'offline fixture, never executed')
    launcher = tmp_path / 'existing install with spaces' / 'launcher.exe'
    launcher.parent.mkdir()
    launcher.write_bytes(b'offline fixture, never executed')
    env = os.environ.copy()
    env.pop('PSModulePath', None)
    env.update({
        'TEST_HELPER': str(Path(__file__).resolve().parents[1] / 'desktop/src-tauri/windows/install-update.ps1'),
        'TEST_INSTALLER': str(installer), 'TEST_LAUNCHER': str(launcher),
        'TEST_CALLS': str(tmp_path / 'calls.jsonl'),
        'TEST_HASH': '0' * 64 if case == 'checksum' else hashlib.sha256(installer.read_bytes()).hexdigest(),
        'TEST_EXIT': '1' if case == 'installer_failure' else ('3010' if case == 'reboot_required' else '0'),
        'TEST_CASE': case,
    })
    # Stub the OS launch boundary; no installer, UAC, launcher, or live instance runs.
    script = r'''
function global:Start-Process {
    param($FilePath, $Verb, [switch]$Wait, [switch]$PassThru, $WindowStyle, $ArgumentList)
    @{path=$FilePath;verb=$Verb;args=$ArgumentList} | ConvertTo-Json -Compress | Add-Content -LiteralPath $env:TEST_CALLS -Encoding UTF8
    if ($env:TEST_CASE -eq 'uac_denied' -and $FilePath -eq $env:TEST_INSTALLER) { throw 'UAC denied' }
    [pscustomobject]@{ExitCode=[int]$env:TEST_EXIT}
}
function global:Get-Process {
    param($Id, $ErrorAction)
    if ($env:TEST_CASE -eq 'exit_timeout') {
        $value = [pscustomobject]@{}
        $value | Add-Member ScriptMethod WaitForExit { param($timeout) return $false }
        return $value
    }
}
& $env:TEST_HELPER -LauncherPid 2147483647 -Installer $env:TEST_INSTALLER -Sha256 $env:TEST_HASH -LauncherExe $env:TEST_LAUNCHER -RestartArguments '--workspace "D:\offline workspace"'
'''
    preferred = Path('D:/Tool/PowerShell-7.6.6-win-x64/pwsh.exe')
    shell = (str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe')
             if shell_kind == 'windows_powershell' else
             (str(preferred) if preferred.is_file() else (shutil.which('pwsh') or 'powershell.exe')))
    result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', script], env=env,
                            capture_output=True, timeout=30)
    path = tmp_path / 'calls.jsonl'
    calls = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines()] if path.exists() else []
    if case == 'exit_timeout':
        assert result.returncode == 1
        assert calls == []
        return
    if case in ('success', 'reboot_required'):
        assert result.returncode == 0, result.stderr
        assert len(calls) == 2
    else:
        assert result.returncode == 1
        assert calls[-1]['path'] == str(launcher)
        assert calls[-1]['args'] == r'--workspace "D:\offline workspace"'
        if case == 'checksum':
            assert len(calls) == 1
    if case != 'checksum':
        assert calls[0] == {'path': str(installer), 'verb': 'RunAs',
                            'args': f'/S /UPDATE /D={launcher.parent}'}
    assert calls[-1]['path'] == str(launcher)
    assert calls[-1]['args'] == r'--workspace "D:\offline workspace"'


@pytest.mark.asyncio
async def test_latest_missing_installer_is_not_reported_as_up_to_date(updates):
    updater, state, _ = updates
    state['installer'] = False
    with pytest.raises(ConfigError, match='暂无完整安装器'):
        await updater.check()


@pytest.mark.asyncio
async def test_total_download_timeout_cancels_operation_and_cleans_cache(updates, monkeypatch):
    import wechat_onebot.launcher.self_update as module
    updater, _, _ = updates
    monkeypatch.setattr(module, 'DOWNLOAD_TIMEOUT', 0.02)
    original = updater.limited_get
    async def blocked(client, url, limit):
        if url.endswith('.sha256'):
            await asyncio.Event().wait()
        return await original(client, url, limit)
    monkeypatch.setattr(updater, 'limited_get', blocked)
    with pytest.raises(ConfigError, match='已取消并清理'):
        await updater.download('0.9.0')
    assert updater.ready is None and not updater.lock.locked()
    assert list(updater.directory.iterdir()) == []


@pytest.mark.asyncio
async def test_metadata_stream_limit_is_enforced(updates):
    updater, _, _ = updates
    async with httpx.AsyncClient() as client:
        with pytest.raises(ConfigError, match='超过大小限制'):
            await updater.limited_get(client, 'https://github.com/PorkLittleHamburger/wechatbot-launcher/releases.atom', 4)


@pytest.mark.asyncio
async def test_successful_update_cache_is_bounded_and_keeps_handoff(updates):
    updater, _, _ = updates
    first = Path((await updater.download('0.9.0'))['path'])
    (first.parent / '.handoff').touch()
    for _ in range(5):
        await updater.download('0.9.0')
    assert first.exists()
    assert len(list(updater.directory.rglob('*.exe'))) <= 4
