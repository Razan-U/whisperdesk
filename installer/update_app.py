"""Run with the installed Python; replace code only while holding the app lock."""
from pathlib import Path
import json
import os
import shutil
import sys
import time
import uuid


def replace_app(install, source, validate=True):
    install, source = Path(install).resolve(), Path(source).resolve()
    app = install / 'app'
    if not (app/'whisperdesk/__init__.py').is_file() or not (install/'runtime/python.exe').is_file():
        raise RuntimeError('Виберіть папку встановленого WhisperDesk 0.2 або 0.3.')
    version = (app/'whisperdesk/__init__.py').read_text(encoding='utf-8')
    if not any(f'"{v}"' in version or f"'{v}'" in version for v in ('0.2.0', '0.3.0')):
        raise RuntimeError('Це оновлення підтримує лише WhisperDesk 0.2 / 0.3.')
    lock = None
    if validate:
        # Imports from the installed app resolve the actual data path, including
        # environment overrides and UTF-16 installer configuration.
        sys.path.insert(0, str(app))
        from whisperdesk.core import data_dir
        from PySide6.QtCore import QLockFile
        lock = QLockFile(str(data_dir()/'app.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            raise RuntimeError('Спочатку закрийте WhisperDesk і повторіть оновлення.')
    stage = install / ('.update-' + uuid.uuid4().hex)
    backup = install / ('app-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    try:
        shutil.copytree(source, stage)
        for name in ('data-path.txt', 'install.json'):
            if (app/name).exists():
                shutil.copy2(app/name, stage/name)
        for relative in ('main.py', 'whisperdesk/ui.py', 'whisperdesk/engine.py', 'whisperdesk/gpu-packages.json'):
            if not (stage/relative).is_file():
                raise RuntimeError('Неповний пакет оновлення. Завантажте його повторно.')
        # Every source module must parse before the installed tree is touched.
        for module in stage.rglob('*.py'):
            compile(module.read_bytes(), str(module), 'exec')
        app.rename(backup)
        try:
            stage.rename(app)
        except BaseException:
            backup.rename(app)
            raise
        return backup
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if lock:
            lock.unlock()


if __name__ == '__main__':
    try:
        backup = replace_app(sys.argv[1], sys.argv[2])
        print('Updated to 0.3.0. Code backup:', backup)
    except Exception as exc:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(exc), 'WhisperDesk — оновлення не встановлено', 0x10)
        print(str(exc), file=sys.stderr)
        sys.exit(1)
