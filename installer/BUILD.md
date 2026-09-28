# Build a Windows x64 installer

The distributed installer includes CPython and all Windows wheels. It does not
run pip on the target machine. NSIS can compile the installer on Windows or Linux.

From the parent directory of the source folder `WhisperDesk`:

1. Create `windows-build/downloads` and `windows-build/wheels`.
2. Download official CPython embedded x64 ZIP:
   https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip
   Save as `windows-build/downloads/python.zip`.
3. Download Microsoft's official runtime:
   https://aka.ms/vc14/vc_redist.x64.exe
   Save as `windows-build/downloads/vc_redist.x64.exe`.
4. Download wheels using Python/pip:

   ```text
   python -m pip download --platform win_amd64 --python-version 312 --only-binary=:all: --dest windows-build/wheels -r WhisperDesk/requirements.txt colorama==0.4.6
   ```

   Use BUILD-MANIFEST.json for the exact versions and SHA-256 used for this build.
   `colorama` is explicit because cross-platform pip may evaluate OS markers
   against its Linux host. Check transitive dependency markers for Windows.

5. Assemble the runtime:

   ```text
   python WhisperDesk/installer/assemble.py windows-build
   ```

6. Install NSIS from https://nsis.sourceforge.io/ and compile:

   ```text
   makensis WhisperDesk/installer/launcher.nsi
   makensis WhisperDesk/installer/update.nsi
   makensis WhisperDesk/installer/setup.nsi
   ```

   Outputs: `windows-build/payload/WhisperDesk.exe`, `WhisperDesk-Update-0.3.0.exe`, and `WhisperDesk-Setup-0.3.0.exe`.
   The launcher starts the bundled pythonw.exe with app/main.py. Spawn workers
   use that same embedded runtime. The icon is included in the PE resources.

7. Test on Windows 10/11 x64: install, desktop shortcut, download base, offline
   transcription, queue error continuation, cancellation and checkpoint resume.

The `.data/purelib` and `.data/platlib` wheel paths are relocated to site-packages.
Console scripts and headers are unnecessary for this GUI runtime. Package license
and dist-info metadata are retained. `_pth` explicitly includes app and site-packages.

The standard `Build-Windows.bat` from the first version remains an alternative
PyInstaller folder build; use the NSIS flow above for the self-contained installer.
Do not distribute the source ZIP as if it were the installer.
