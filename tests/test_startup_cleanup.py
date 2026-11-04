"""Exercise only isolated HKCU values; never uninstall an actual application."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import uuid

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows startup registry")
@pytest.mark.parametrize("shell_kind", ["pwsh", "windows_powershell"])
def test_uninstall_removes_only_matching_startup_entry_and_keeps_disable_marker(tmp_path, shell_kind):
    import winreg

    paths = [r"Software\Microsoft\Windows\CurrentVersion\Run",
             r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run",
             r"Software\WeChatOneBotLauncher\StartupRegistrations"]
    keys = [winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, p, access=winreg.KEY_ALL_ACCESS | winreg.KEY_WOW64_64KEY) for p in paths]
    own = "WeChat OneBot Launcher - test-" + uuid.uuid4().hex
    foreign = own + "-foreign"
    exe = tmp_path / "isolated installation with spaces" / "launcher.exe"
    helper = exe.parent / "startup" / "wechat-onebot-startup.exe"
    command = f'"{helper}" "--task" "WeChatOneBotLauncher-S-1-5-isolated"'
    try:
        winreg.SetValueEx(keys[0], own, 0, winreg.REG_SZ, command)
        winreg.SetValueEx(keys[0], foreign, 0, winreg.REG_SZ, '"D:\\another installation\\startup\\wechat-onebot-startup.exe" "--task" "WeChatOneBotLauncher-S-1-5-isolated"')
        winreg.SetValueEx(keys[1], own, 0, winreg.REG_BINARY, bytes([3]) + bytes(11))
        winreg.SetValueEx(keys[2], own, 0, winreg.REG_DWORD, 1)
        shell = (str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
                 if shell_kind == "windows_powershell" else shutil.which("pwsh"))
        if not shell:
            pytest.skip("PowerShell 7 unavailable")
        result = subprocess.run([shell, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                 "-File", str(Path(__file__).resolve().parents[1] / "desktop/src-tauri/windows/uninstall.ps1"),
                                 "-InstalledExe", str(exe)], capture_output=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
        for key in keys[:2]:
            with pytest.raises(FileNotFoundError):
                winreg.QueryValueEx(key, own)
        assert winreg.QueryValueEx(keys[2], own)[0] == 1
        assert winreg.QueryValueEx(keys[0], foreign)[0].startswith('"D:\\another installation')
    finally:
        for key in keys:
            for name in (own, foreign):
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    pass
            key.Close()
