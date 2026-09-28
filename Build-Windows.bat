@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run Install.bat first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install "pyinstaller>=6.11,<7"
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --windowed --onedir --name WhisperDesk --icon assets/icon.ico --add-data "assets;assets" --add-data "whisperdesk/gpu-packages.json;whisperdesk" --collect-all faster_whisper --collect-all ctranslate2 --collect-all av --collect-all onnxruntime --collect-all tokenizers --collect-all huggingface_hub --copy-metadata faster-whisper main.py
if errorlevel 1 goto fail
echo Build created in dist\WhisperDesk. Copy the entire folder, not just the EXE.
echo Models are downloaded separately on each PC or copied with their model folder.
pause
exit /b 0
:fail
echo Build failed. See the error above.
pause
exit /b 1
