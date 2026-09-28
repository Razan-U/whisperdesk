Unicode true
Name "WhisperDesk"
OutFile "../../windows-build/payload/WhisperDesk.exe"
Icon "../assets/icon.ico"
RequestExecutionLevel user
SilentInstall silent
AutoCloseWindow true
Section
  SetOutPath "$EXEDIR"
  ExecWait '"$EXEDIR\runtime\pythonw.exe" "$EXEDIR\app\main.py"' $0
  IfErrors launch_error
  IntCmp $0 0 done
  MessageBox MB_OK|MB_ICONEXCLAMATION "WhisperDesk could not start. See Data\startup-error.log, or reinstall the application."
  Goto done
launch_error:
  MessageBox MB_OK|MB_ICONEXCLAMATION "Cannot start the bundled Python runtime. Please reinstall WhisperDesk."
done:
SectionEnd
