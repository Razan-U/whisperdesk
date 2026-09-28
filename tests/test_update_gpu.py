import importlib.util
import queue
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest

from whisperdesk import gpu


def test_preflight_consumes_lazy_decoder_error():
    class Model:
        def detect_language(self, audio): return 'en', 1, []
        def transcribe(self, audio, **kwargs):
            def results():
                raise RuntimeError('cublas64_12.dll')
                yield
            return results(), None
    with pytest.raises(RuntimeError, match='cublas'):
        gpu.preflight(Model())


def test_corrupt_gpu_download_is_rejected(tmp_path, monkeypatch):
    import io
    class Response(io.BytesIO):
        status = 200
        headers = {}
    monkeypatch.setattr(gpu.urllib.request, 'urlopen', lambda *a, **k: Response(b'bad'))
    package = dict(name='test', filename='test.whl', size=3, sha256='0'*64, url='https://example.invalid/test')
    with pytest.raises(RuntimeError, match='SHA-256'):
        gpu.download(package, tmp_path, queue.Queue(), threading.Event())
    assert not list(tmp_path.iterdir())


def test_update_preserves_data_and_rolls_back(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location('update_app', Path(__file__).parents[1]/'installer/update_app.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    install = tmp_path/'installed'; app = install/'app'; source = tmp_path/'new'
    (app/'whisperdesk').mkdir(parents=True)
    (app/'whisperdesk/__init__.py').write_text('__version__ = "0.2.0"')
    (app/'data-path.txt').write_text('original-data-path', encoding='utf-16')
    (install/'runtime').mkdir(); (install/'runtime/python.exe').touch()
    (install/'Data').mkdir(); (install/'Data/queue.json').write_text('user-data')
    (source/'whisperdesk').mkdir(parents=True)
    for name in ('main.py', 'whisperdesk/ui.py', 'whisperdesk/engine.py'):
        (source/name).write_text('# new code')
    (source/'whisperdesk/gpu-packages.json').write_text('[]')
    (source/'whisperdesk/__init__.py').write_text('__version__ = "0.3.0"')
    original_rename = Path.rename
    def fail_stage(self, target):
        if self.name.startswith('.update-'): raise OSError('simulated disk error')
        return original_rename(self, target)
    monkeypatch.setattr(Path, 'rename', fail_stage)
    with pytest.raises(OSError, match='disk'):
        module.replace_app(install, source, validate=False)
    assert '0.2.0' in (app/'whisperdesk/__init__.py').read_text()
    monkeypatch.setattr(Path, 'rename', original_rename)
    backup = module.replace_app(install, source, validate=False)
    assert '0.2.0' in (backup/'whisperdesk/__init__.py').read_text()
    assert '0.3.0' in (app/'whisperdesk/__init__.py').read_text()
    assert (app/'data-path.txt').read_text(encoding='utf-16') == 'original-data-path'
    assert (install/'Data/queue.json').read_text() == 'user-data'


@pytest.mark.parametrize('device,cancelled,already_retried,expected', [
    ('auto', False, False, 'pending'), ('cuda', False, False, 'error'),
    ('auto', True, False, 'interrupted'), ('auto', False, True, 'error')])
def test_native_crash_fallback_once(tmp_path, monkeypatch, device, cancelled, already_retried, expected):
    from PySide6.QtWidgets import QApplication
    from whisperdesk.ui import Window, DEFAULTS
    app = QApplication.instance() or QApplication([])
    monkeypatch.setenv('WHISPERDESK_DATA', str(tmp_path))
    w = Window(auto_start=False); w.timer.stop()
    task = w.tasks.add(str(tmp_path/'a.wav'), 20, {**DEFAULTS, 'device': device})
    task['status'] = 'running'
    Path(task['session'] + '.gpu-attempt').write_text('cuda')
    w.active_id = task['id']; w.operation = 'transcribe'
    w.finished_message = None; w.failed = False
    w.cancel_deadline = 1 if cancelled else None
    w.forced_stop = cancelled
    if already_retried: w.cpu_fallback_ids.add(task['id'])
    w.process = SimpleNamespace(is_alive=lambda: False, join=lambda: None, close=lambda: None, exitcode=-1073741819)
    class Channel(queue.Queue):
        def close(self): pass
    w.channel = Channel()
    w.poll()
    assert task['status'] == expected
    if expected == 'pending':
        assert task['warning'] and task['id'] in w.cpu_fallback_ids
        w.show_diagnostics(task)
        assert w.diagnostics.toPlainText() == task['warning']
    w.close()


def test_running_app_blocks_update(tmp_path, monkeypatch):
    from PySide6.QtCore import QLockFile
    spec = importlib.util.spec_from_file_location('update_guard', Path(__file__).parents[1]/'installer/update_app.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    install = tmp_path/'install'
    (install/'app/whisperdesk').mkdir(parents=True)
    (install/'app/whisperdesk/__init__.py').write_text('__version__ = "0.2.0"')
    (install/'runtime').mkdir(); (install/'runtime/python.exe').touch()
    data = tmp_path/'data'; data.mkdir()
    monkeypatch.setenv('WHISPERDESK_DATA', str(data))
    lock = QLockFile(str(data/'app.lock')); assert lock.tryLock(100)
    try:
        with pytest.raises(RuntimeError, match='закрийте'):
            module.replace_app(install, tmp_path/'missing-package')
        assert '0.2.0' in (install/'app/whisperdesk/__init__.py').read_text()
    finally:
        lock.unlock()


@pytest.mark.parametrize('partial_response', [True, False])
def test_download_resumes_or_restarts_if_range_ignored(tmp_path, monkeypatch, partial_response):
    import io, hashlib
    complete = b'abcdef'
    (tmp_path/'test.part').write_bytes(b'abc')
    class Response(io.BytesIO):
        status = 206 if partial_response else 200
        headers = {'Content-Range': 'bytes 3-5/6'} if partial_response else {}
    def open_url(request, **kwargs):
        assert request.get_header('Range') == 'bytes=3-'
        return Response(b'def' if partial_response else complete)
    monkeypatch.setattr(gpu.urllib.request, 'urlopen', open_url)
    package = dict(name='test', filename='test.whl', size=6, sha256=hashlib.sha256(complete).hexdigest(), url='https://example.invalid/test')
    result = gpu.download(package, tmp_path, queue.Queue(), threading.Event())
    assert result.read_bytes() == complete
    assert not (tmp_path/'test.part').exists()
