; Neos75 屏幕 installer hooks (electron-builder "nsis.include").
;
; Before files are replaced or removed, the app must be gone, and with it the screen service it started
; (resources\python\python.exe, which holds files in the install folder and the keyboard). The default check
; kills Neos75Screen.exe by force, which would leave the service running. Instead: ask the running app to quit
; (it stops its service first), wait up to 10 s for everything started from the install folder to end, then end
; what is left.
!macro customCheckAppRunning
  IfFileExists "$INSTDIR\${APP_EXECUTABLE_FILENAME}" 0 +2
    ExecWait '"$INSTDIR\${APP_EXECUTABLE_FILENAME}" --quit'
  DetailPrint "正在关闭 ${PRODUCT_NAME}…"
  nsExec::Exec `"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -Command "$$d = '$INSTDIR\'; for ($$i = 0; $$i -lt 20; $$i++) { $$p = @(Get-Process | Where-Object { $$_.Path -and $$_.Path.StartsWith($$d, 'OrdinalIgnoreCase') }); if (-not $$p.Count) { break }; Start-Sleep -Milliseconds 500 }; $$p | Stop-Process -Force -ErrorAction SilentlyContinue"`
  Pop $0
  Sleep 300
!macroend
