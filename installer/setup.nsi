Unicode true
!ifndef VERSION
!define VERSION "0.3.1"
!endif
!ifndef VERSION4
!define VERSION4 "0.3.1.0"
!endif
!include "MUI2.nsh"
!include "x64.nsh"
!include "LogicLib.nsh"
Name "WhisperDesk ${VERSION}"
OutFile "../../WhisperDesk-Setup-${VERSION}.exe"
InstallDir "D:\WhisperDesk"
RequestExecutionLevel admin
SetCompressor zlib
Icon "../assets/icon.ico"
UninstallIcon "../assets/icon.ico"
VIProductVersion "${VERSION4}"
VIAddVersionKey /LANG=1033 "ProductName" "WhisperDesk"
VIAddVersionKey /LANG=1033 "FileDescription" "WhisperDesk installer"
VIAddVersionKey /LANG=1033 "FileVersion" "${VERSION}"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Nazar Svyryd 2026"
!define MUI_ABORTWARNING
!define MUI_ICON "../assets/icon.ico"
!define MUI_UNICON "../assets/icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\WhisperDesk.exe"
!define MUI_FINISHPAGE_RUN_NOTCHECKED
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Ukrainian"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_OK|MB_ICONSTOP "WhisperDesk requires 64-bit Windows."
    Abort
  ${EndIf}
  ReadRegStr $0 HKLM "Software\WhisperDesk" "InstallDir"
  ${If} $0 != ""
    StrCpy $INSTDIR $0
  ${Else}
    IfFileExists "D:\*.*" has_d no_d
no_d:
    StrCpy $INSTDIR "$PROGRAMFILES64\WhisperDesk"
has_d:
  ${EndIf}
FunctionEnd
Function .onVerifyInstDir
  StrLen $0 $INSTDIR
  ${If} $0 <= 3
    Abort
  ${EndIf}
  ${If} $INSTDIR == $WINDIR
  ${OrIf} $INSTDIR == $PROGRAMFILES64
  ${OrIf} $INSTDIR == $PROGRAMFILES
    Abort
  ${EndIf}
  IfFileExists "$INSTDIR\app\*.*" 0 safe_dir
  IfFileExists "$INSTDIR\WhisperDesk.exe" safe_dir 0
    Abort
safe_dir:
FunctionEnd
Section "WhisperDesk"
  SetShellVarContext all
  ; Existing supported installations update only app code under the app lock.
  IfFileExists "$INSTDIR\runtime\python.exe" existing_install fresh_install
existing_install:
  InitPluginsDir
  SetOutPath "$PLUGINSDIR\update"
  File "update_app.py"
  SetOutPath "$PLUGINSDIR\update\app"
  File /r "..\..\windows-build\payload\app\*.*"
  SetOutPath "$PLUGINSDIR"
  ExecWait '"$INSTDIR\runtime\python.exe" "$PLUGINSDIR\update\update_app.py" "$INSTDIR" "$PLUGINSDIR\update\app"' $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONSTOP "Оновлення не встановлено. Закрийте WhisperDesk і повторіть."
    Abort
  ${EndIf}
  Goto shortcuts
fresh_install:
  SetOutPath "$INSTDIR"
  File /r "..\..\windows-build\payload\*.*"
  ; Install Microsoft's official runtime automatically, with no component choices.
  ExecWait '"$INSTDIR\vc_redist.x64.exe" /install /quiet /norestart' $0
  ${If} $0 != 0
  ${AndIf} $0 != 1638
  ${AndIf} $0 != 3010
    MessageBox MB_OK|MB_ICONSTOP "Microsoft runtime installation failed (code $0). Installation cannot continue."
    Abort
  ${EndIf}
  ${If} $0 == 3010
    SetRebootFlag true
  ${EndIf}
  Delete "$INSTDIR\vc_redist.x64.exe"
  ; Runtime data belongs beside the selected installation directory, including D:.
  ; For Program Files fallback use ProgramData to remain writable without UAC.
  StrCpy $1 "$INSTDIR\Data"
  StrCmp $INSTDIR "$PROGRAMFILES64\WhisperDesk" 0 +2
    StrCpy $1 "$APPDATA\WhisperDesk"
  CreateDirectory "$1"
  ; Grant only the installing account modify rights to the data directory.
  ReadEnvStr $3 USERNAME
  nsExec::ExecToLog '"$SYSDIR\icacls.exe" "$1" /grant "$3:(OI)(CI)M"'
  Pop $0
  FileOpen $2 "$INSTDIR\app\data-path.txt" w
  FileWriteUTF16LE /BOM $2 "$1"
  FileClose $2
shortcuts:
  SetOutPath "$INSTDIR"
  CreateShortCut "$DESKTOP\WhisperDesk.lnk" "$INSTDIR\WhisperDesk.exe" "" "$INSTDIR\app\assets\icon.ico"
  CreateDirectory "$SMPROGRAMS\WhisperDesk"
  CreateShortCut "$SMPROGRAMS\WhisperDesk\WhisperDesk.lnk" "$INSTDIR\WhisperDesk.exe" "" "$INSTDIR\app\assets\icon.ico"
  WriteRegStr HKLM "Software\WhisperDesk" "InstallDir" "$INSTDIR"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk" "DisplayName" "WhisperDesk"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk" "DisplayIcon" "$INSTDIR\app\assets\icon.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
SectionEnd
Section "Uninstall"
  SetShellVarContext all
  ; Remove known application-owned trees only. NEVER delete Data or user transcripts.
  RMDir /r "$INSTDIR\runtime"
  RMDir /r "$INSTDIR\app"
  Delete "$INSTDIR\WhisperDesk.exe"
  Delete "$INSTDIR\Uninstall.exe"
  Delete "$DESKTOP\WhisperDesk.lnk"
  Delete "$SMPROGRAMS\WhisperDesk\WhisperDesk.lnk"
  RMDir "$SMPROGRAMS\WhisperDesk"
  DeleteRegKey HKLM "Software\WhisperDesk"
  DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk"
  RMDir "$INSTDIR"
SectionEnd
