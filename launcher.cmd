@echo off
setlocal
set "PWSH=D:\Tool\PowerShell-7.6.6-win-x64\pwsh.exe"
if not exist "%PWSH%" set "PWSH=pwsh.exe"
where "%PWSH%" >nul 2>nul
if errorlevel 1 set "PWSH=powershell.exe"
"%PWSH%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run_launcher.ps1" %*
if errorlevel 1 pause
