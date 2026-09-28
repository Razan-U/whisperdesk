Unicode true
!include "MUI2.nsh"
!include "LogicLib.nsh"
Name "WhisperDesk — оновлення 0.3"
OutFile "../../WhisperDesk-Update-0.3.0.exe"
InstallDir "D:\WhisperDesk"
RequestExecutionLevel admin
SetCompressor zlib
Icon "../assets/icon.ico"
VIProductVersion "0.3.0.0"
VIAddVersionKey /LANG=1033 "ProductName" "WhisperDesk"
VIAddVersionKey /LANG=1033 "FileDescription" "WhisperDesk 0.2 to 0.3 updater"
VIAddVersionKey /LANG=1033 "FileVersion" "0.3.0"
VIAddVersionKey /LANG=1033 "LegalCopyright" "Nazar Svyryd 2026"
!define MUI_ICON "../assets/icon.ico"
!define MUI_DIRECTORYPAGE_TEXT_TOP "Закрийте WhisperDesk. Виберіть папку вже встановленої версії 0.2 або 0.3. Моделі, налаштування та автозбереження залишаться на місці."
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_LANGUAGE "Ukrainian"
!insertmacro MUI_LANGUAGE "English"
Function .onInit
 ReadRegStr $0 HKLM "Software\WhisperDesk" "InstallDir"
 ${If} $0 != ""
  StrCpy $INSTDIR $0
 ${EndIf}
FunctionEnd
Section "Оновлення"
 IfFileExists "$INSTDIR\runtime\python.exe" 0 invalid
 IfFileExists "$INSTDIR\app\whisperdesk\__init__.py" valid invalid
invalid:
 MessageBox MB_OK|MB_ICONSTOP "Встановлений WhisperDesk не знайдено. Використайте повний інсталятор."
 Abort
valid:
 InitPluginsDir
 SetOutPath "$PLUGINSDIR\update"
 File "update_app.py"
 SetOutPath "$PLUGINSDIR\update\app"
 File /r "../../windows-build/payload/app/*.*"
 SetOutPath "$PLUGINSDIR"
 ExecWait '"$INSTDIR\runtime\python.exe" "$PLUGINSDIR\update\update_app.py" "$INSTDIR" "$PLUGINSDIR\update\app"' $0
 ${If} $0 != 0
  MessageBox MB_OK|MB_ICONSTOP "Оновлення не встановлено. Закрийте програму та повторіть. Попередня версія і ваші дані збережені."
  Abort
 ${EndIf}
 SetShellVarContext all
 CreateShortCut "$DESKTOP\WhisperDesk.lnk" "$INSTDIR\WhisperDesk.exe" "" "$INSTDIR\app\assets\icon.ico"
 WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\WhisperDesk" "DisplayVersion" "0.3.0"
SectionEnd
