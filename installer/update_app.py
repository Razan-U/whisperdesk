"""Replace WhisperDesk application code atomically while preserving user data."""
from pathlib import Path
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

VERSION_RE = re.compile(
    r'__version__\s*=\s*["\'](\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)["\']'
)


def read_version(path):
    match = VERSION_RE.search(Path(path).read_text(encoding='utf-8'))
    if not match:
        raise RuntimeError(f'Не вдалося визначити версію пакета: {path}')
    return match.group(1)


def version_key(value):
    main, sep, pre = value.partition('-')
    major, minor, patch = map(int, main.split('.'))
    if not sep:
        return major, minor, patch, 1, ()
    parts = tuple((0, int(x)) if x.isdigit() else (1, x.lower()) for x in pre.split('.'))
    return major, minor, patch, 0, parts


def smoke_test(install):
    install = Path(install)
    runtime = install / 'runtime' / 'python.exe'
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(install / 'app')!r}); "
        "import whisperdesk.ui, whisperdesk.engine, whisperdesk.updater"
    )
    result = subprocess.run(
        [str(runtime), '-c', code],
        cwd=str(install),
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode:
        detail = (result.stderr or result.stdout or '').strip()[-1600:]
        raise RuntimeError('Перевірка нового коду не пройдена. ' + detail)


def replace_app(install, source, validate=True):
    install, source = Path(install).resolve(), Path(source).resolve()
    app = install / 'app'
    runtime = install / 'runtime' / 'python.exe'
    current_file = app / 'whisperdesk' / '__init__.py'
    target_file = source / 'whisperdesk' / '__init__.py'
    if not current_file.is_file() or not runtime.is_file():
        raise RuntimeError('Виберіть папку встановленого WhisperDesk 0.2 або новішого.')
    if not target_file.is_file():
        raise RuntimeError('Неповний пакет оновлення: відсутня інформація про версію.')

    current = read_version(current_file)
    target = read_version(target_file)
    if version_key(current) < version_key('0.2.0'):
        raise RuntimeError('Автоматичне оновлення підтримується починаючи з WhisperDesk 0.2.')
    if version_key(target) < version_key(current):
        raise RuntimeError(f'Пакет {target} старіший за встановлену версію {current}.')

    lock = None
    if validate:
        # Imports from the installed app resolve the actual data path, including
        # environment overrides and UTF-16 installer configuration.
        sys.path.insert(0, str(app))
        from whisperdesk.core import data_dir
        from PySide6.QtCore import QLockFile
        lock = QLockFile(str(data_dir() / 'app.lock'))
        lock.setStaleLockTime(0)
        if not lock.tryLock(100):
            raise RuntimeError('Спочатку закрийте WhisperDesk і повторіть оновлення.')

    stage = install / ('.update-' + uuid.uuid4().hex)
    backup = install / ('app-backup-' + time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    swapped = False
    try:
        shutil.copytree(source, stage)
        for name in ('data-path.txt', 'install.json'):
            if (app / name).exists():
                shutil.copy2(app / name, stage / name)

        for relative in (
            'main.py', 'whisperdesk/ui.py', 'whisperdesk/engine.py',
            'whisperdesk/gpu-packages.json', 'whisperdesk/__init__.py'
        ):
            if not (stage / relative).is_file():
                raise RuntimeError('Неповний пакет оновлення. Завантажте його повторно.')

        # Every Python module must parse before the installed tree is touched.
        for module in stage.rglob('*.py'):
            compile(module.read_bytes(), str(module), 'exec')

        app.rename(backup)
        try:
            stage.rename(app)
            swapped = True
            if validate:
                smoke_test(install)
        except BaseException:
            if app.exists():
                shutil.rmtree(app)
            backup.rename(app)
            swapped = False
            raise
        return backup
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        # A failed swap always restores backup above; a successful one keeps it
        # for manual recovery and forensic diagnostics.
        if lock:
            lock.unlock()


if __name__ == '__main__':
    try:
        install = Path(sys.argv[1])
        source = Path(sys.argv[2])
        target = read_version(source / 'whisperdesk' / '__init__.py')
        backup = replace_app(install, source)
        print(f'Updated to {target}. Code backup: {backup}')
    except Exception as exc:
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                None, str(exc), 'WhisperDesk — оновлення не встановлено', 0x10
            )
        print(str(exc), file=sys.stderr)
        sys.exit(1)
