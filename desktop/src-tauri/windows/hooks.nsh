!include LogicLib.nsh
!define WOB_HOOK_DIR "${__FILEDIR__}"

; Replace Tauri's stock force-kill macro too, so its subsequent checks cannot
; terminate a running launcher. Register the exact installed executable only.
!macroundef CheckIfAppIsRunning
!macro CheckIfAppIsRunning executablePath productName
  !define WOB_CHECK_ID ${__LINE__}
  wob_check_${WOB_CHECK_ID}:
    !insertmacro RestartManager_StartSession $R0
    ${If} $R0 == ""
      Abort "无法检查运行中的启动器，请关闭启动器后重试。"
    ${EndIf}
    !insertmacro RestartManager_RegisterFile $R0 "${executablePath}"
    ${If} $0 != 0
      !insertmacro RestartManager_EndSession $R0
      Abort "无法检查程序文件是否正在使用。"
    ${EndIf}
    System::Call 'RSTRTMGR::RmGetList(p R0, *i .r1, *i .r2, p 0, *i .r3) i .r0'
    StrCpy $R4 $0
    !insertmacro RestartManager_EndSession $R0
    ${If} $R4 == 234
      IfSilent wob_abort_${WOB_CHECK_ID}
      MessageBox MB_RETRYCANCEL|MB_ICONEXCLAMATION "${productName} 正在运行。请从托盘选择“退出”，等待实例完成关闭后点击“重试”。安装程序不会强制结束进程。" IDRETRY wob_check_${WOB_CHECK_ID}
      wob_abort_${WOB_CHECK_ID}:
      Abort "启动器仍在运行，操作已取消。"
    ${ElseIf} $R4 != 0
      Abort "无法确认程序已停止，操作已取消。"
    ${EndIf}
  !undef WOB_CHECK_ID
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro CheckIfAppIsRunning "$INSTDIR\wechat-onebot-launcher.exe" "WeChat OneBot Launcher"
  ; The native bridge imports MSVCP140. Ship its official offline prerequisite
  ; rather than relying on a pre-existing developer machine installation.
  InitPluginsDir
  File /oname=$PLUGINSDIR\vc_redist.x64.exe "${WOB_HOOK_DIR}\..\resources\vc_redist.x64.exe"
  ExecWait '"$PLUGINSDIR\vc_redist.x64.exe" /install /quiet /norestart' $R8
  ${If} $R8 == 3010
    SetRebootFlag true
  ${ElseIf} $R8 != 0
  ${AndIf} $R8 != 1638
    Abort "Visual C++ 运行库安装失败（$R8），安装已停止。"
  ${EndIf}
  Delete "$PLUGINSDIR\vc_redist.x64.exe"
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro CheckIfAppIsRunning "$INSTDIR\wechat-onebot-launcher.exe" "WeChat OneBot Launcher"
  ; An upgrade keeps the same executable path and must retain its logon tasks.
  ${If} $UpdateMode != 1
  InitPluginsDir
  File /oname=$PLUGINSDIR\wechat-onebot-uninstall.ps1 "${WOB_HOOK_DIR}\uninstall.ps1"
  StrCpy $R5 ""
  ; Unattended uninstall always preserves user data.
    IfSilent wob_preserve_data
    MessageBox MB_YESNO|MB_DEFBUTTON2|MB_ICONQUESTION "默认保留实例、配置、消息、媒体和备份。是否另外删除当前用户 LOCALAPPDATA 下 WeChatOneBotLauncher 中的全部数据？此操作不可恢复。" IDNO wob_preserve_data
    StrCpy $R5 "-RemoveData"
  wob_preserve_data:
  nsExec::ExecToStack '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$PLUGINSDIR\wechat-onebot-uninstall.ps1" -InstalledExe "$INSTDIR\wechat-onebot-launcher.exe" $R5'
  Pop $R6
  Pop $R7
  ${If} $R6 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "启动任务或数据清理失败，卸载已停止：$R7"
    Abort
  ${EndIf}
  ${EndIf}
!macroend
