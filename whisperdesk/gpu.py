"""Private, versioned NVIDIA DLLs; no global PATH or CUDA installation changes."""
from pathlib import Path
import hashlib
import json
import os
import shutil
import urllib.request
import zipfile

BUNDLE = 'cuda12.4-cudnn9.1-v1'
REQUIRED = ('cublas64_12.dll', 'cublasLt64_12.dll', 'cudnn64_9.dll', 'cudart64_12.dll', 'nvrtc64_120_0.dll')
_handles = []


class Cancelled(Exception):
    pass


def check_cancel(cancel):
    if cancel.is_set():
        raise Cancelled()


def verified(path, package):
    if not path.is_file() or path.stat().st_size != package['size']:
        return False
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''):
            digest.update(block)
    return digest.hexdigest() == package['sha256']


def download(package, cache, queue, cancel):
    path = cache / package['filename']
    if verified(path, package):
        return path
    part = path.with_suffix('.part')
    offset = part.stat().st_size if part.exists() else 0
    if offset >= package['size']:
        if verified(part, package):
            part.replace(path)
            return path
        part.unlink(); offset = 0
    request = urllib.request.Request(package['url'], headers={'Range': f'bytes={offset}-'} if offset else {})
    with urllib.request.urlopen(request, timeout=20) as response:
        if offset and response.status != 206:
            offset = 0
        if response.status == 206 and not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-'):
            raise RuntimeError('Некоректна відповідь сервера NVIDIA. Повторіть завантаження.')
        with part.open('ab' if offset else 'wb') as output:
            while True:
                check_cancel(cancel)
                block = response.read(1024*1024)
                if not block:
                    break
                output.write(block); offset += len(block)
                queue.put(('status', f"Компоненти NVIDIA · {package['name']} · {offset/1024**2:.0f} / {package['size']/1024**2:.0f} МБ"))
    check_cancel(cancel)
    if not verified(part, package):
        part.unlink(missing_ok=True)
        raise RuntimeError('Перевірка SHA-256 компонентів NVIDIA не пройдена. Спробуйте ще раз.')
    part.replace(path)
    return path


def activate(folder):
    directories = sorted({p.parent for p in folder.rglob('*.dll')})
    for directory in directories:
        _handles.append(os.add_dll_directory(str(directory)))
    os.environ['PATH'] = os.pathsep.join(map(str, directories)) + os.pathsep + os.environ.get('PATH', '')


def ensure_gpu(root, queue, cancel):
    if os.name != 'nt':
        return  # Linux developer environments use their own CUDA installation.
    root = Path(root) / 'gpu'
    target = root / BUNDLE
    ready = target / '.ready'
    if not ready.is_file() or not all(any(target.rglob(name)) for name in REQUIRED):
        root.mkdir(parents=True, exist_ok=True)
        cache = root / 'downloads'; cache.mkdir(exist_ok=True)
        stage = root / (BUNDLE + '.staging')
        if stage.exists():
            shutil.rmtree(stage)
        stage.mkdir()
        packages = json.loads(Path(__file__).with_name('gpu-packages.json').read_text())
        if shutil.disk_usage(root).free < 3*1024**3:
            raise RuntimeError('Для встановлення компонентів NVIDIA потрібно щонайменше 3 ГБ вільного місця.')
        queue.put(('status', 'Перше використання NVIDIA: завантаження ≈1,1 ГБ компонентів. Можна зупинити та продовжити пізніше.'))
        for package in packages:
            check_cancel(cancel)
            wheel = download(package, cache, queue, cancel)
            with zipfile.ZipFile(wheel) as archive:
                for member in archive.infolist():
                    check_cancel(cancel)
                    relative = Path(member.filename)
                    if relative.is_absolute() or '..' in relative.parts or ':' in member.filename or '\\' in member.filename:
                        raise ValueError('Небезпечний шлях в архіві NVIDIA')
                    # Include DLL dependencies and vendor license texts, not headers/libs.
                    if member.is_dir() or not (relative.suffix.lower() == '.dll' or 'license' in member.filename.lower()):
                        continue
                    output = stage / relative
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(member) as source, output.open('wb') as dest:
                        shutil.copyfileobj(source, dest, 1024*1024)
        if not all(any(stage.rglob(name)) for name in REQUIRED):
            raise RuntimeError('В архівах NVIDIA відсутні необхідні DLL.')
        check_cancel(cancel)
        (stage / '.ready').write_text(BUNDLE)
        if target.exists():
            shutil.rmtree(target)
        stage.rename(target)
        shutil.rmtree(cache)
    check_cancel(cancel)
    activate(target)


def preflight(model):
    """Exercise both encoder and lazy decoder DLL loading before processing audio."""
    import numpy as np
    audio = np.zeros(16000, dtype=np.float32)
    model.detect_language(audio)
    segments, _ = model.transcribe(audio, language='en', beam_size=1, vad_filter=False,
                                   condition_on_previous_text=False, word_timestamps=True)
    list(segments)
