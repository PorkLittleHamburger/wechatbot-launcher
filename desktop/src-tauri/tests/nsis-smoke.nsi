; Compile-only harness. Never execute this fixture's installer/uninstaller.
Unicode true
!include LogicLib.nsh
!include Win\RestartManager.nsh
!ifndef SHELL_SMOKE_OUTPUT
  !error "Pass /DSHELL_SMOKE_OUTPUT=<build-target-path>"
!endif
Name "WeChat OneBot NSIS hook syntax test"
OutFile "${SHELL_SMOKE_OUTPUT}"
RequestExecutionLevel user
Var UpdateMode

; Match Tauri's inclusion order: the stock macro exists before our hooks.
!macro CheckIfAppIsRunning executablePath productName
  !error "The force-kill stock macro was not replaced"
!macroend
!include "${__FILEDIR__}\..\windows\hooks.nsh"

Section
  StrCpy $UpdateMode 0
  !insertmacro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "$INSTDIR\wechat-onebot-launcher.exe" "Hook smoke"
  WriteUninstaller "$TEMP\wechat-onebot-hook-smoke-uninstall.exe"
SectionEnd

Section Uninstall
  StrCpy $UpdateMode 0
  !insertmacro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "$INSTDIR\wechat-onebot-launcher.exe" "Hook smoke"
SectionEnd
