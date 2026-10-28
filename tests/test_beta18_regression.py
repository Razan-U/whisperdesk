import json
import queue
import threading
from types import SimpleNamespace as Obj

import pytest
from PySide6.QtWidgets import QApplication

from whisperdesk.core import Job
from whisperdesk.ui import Window, DEFAULTS
from whisperdesk.eta import (record_sample, load_calibration, calibration_key,
                             migrate_calibration_from_history, estimate_task)
from whisperdesk.history import HistoryStore
from whisperdesk.queue_store import TaskQueue
from whisperdesk.crash_recovery import load_marker
from whisperdesk.settings import load_settings


class FakeProcess:
    def start(self):
        pass


class FakeContext:
    Queue = queue.Queue
    Event = threading.Event

    def Process(self, **kwargs):
        return FakeProcess()


@pytest.mark.parametrize('language,requested,actual', [
    ('mixed', 'auto', 'cuda'), ('en', 'auto', 'cuda'),
    ('mixed', 'auto', 'cpu'), ('uk', 'cpu', 'cpu'),
])
def test_worker_to_calibration_preserves_language_and_requested_device(tmp_path, language, requested, actual):
    task = dict(DEFAULTS, language=language, model='turbo', device=requested)
    state = Obj(ctx=FakeContext(), active_id='1', history_run_started={},
                tasks=Obj(get=lambda _: task), update_run_controls=lambda: None,
                progress=Obj(setRange=lambda *_: None), status=Obj(setText=lambda *_: None))
    # Auto fallback changes the worker to CPU, not the user's requested mode.
    job = Job(source='unused.wav', start=0, end=600, model='turbo',
              language=language, device=actual if actual == 'cpu' else requested)
    Window.begin_process(state, lambda: None, (job,), 'transcribe')
    sample = state.operation_eta_sample
    record_sample(tmp_path, sample['task'], sample['audio_seconds'], 60, actual_device=actual)
    expected = calibration_key(task, actual)
    assert list(load_calibration(tmp_path)) == [expected]


def test_v2_rebuilt_once_from_history_with_backup(tmp_path):
    path = tmp_path / 'eta-calibration.json'
    original = {'version': 2, 'profiles': {'wrong-fixed': {'samples': [9]}}}
    path.write_text(json.dumps(original), encoding='utf-8')
    records = [dict(status='done', model='turbo', requested_device='auto', actual_device='cuda',
                    language='mixed', profile='eco', threads=0, audio_seconds=600, elapsed_seconds=90)]
    assert migrate_calibration_from_history(tmp_path, records)
    key = calibration_key(dict(DEFAULTS, model='turbo', language='mixed', device='auto'), 'cuda')
    assert load_calibration(tmp_path) == {key: {'samples': [0.15]}}
    backups = list(tmp_path.glob('eta-calibration-before-v3-*.json'))
    assert len(backups) == 1 and json.loads(backups[0].read_text()) == original
    assert not migrate_calibration_from_history(tmp_path, records)


@pytest.mark.parametrize('payload', [[], None, 'wrong'])
def test_invalid_json_top_levels_are_safe(tmp_path, payload):
    for name in ('active-run.json', 'history.json', 'eta-calibration.json', 'queue.json'):
        (tmp_path / name).write_text(json.dumps(payload), encoding='utf-8')
    assert load_marker(tmp_path) is None
    assert list(tmp_path.glob('active-run-damaged-*.json'))
    assert load_calibration(tmp_path) == {}
    with pytest.raises(ValueError):
        HistoryStore(tmp_path)
    with pytest.raises(ValueError):
        TaskQueue(tmp_path)


def test_window_recovers_invalid_configuration(tmp_path):
    (tmp_path / 'settings.json').write_text('{"accent":"bad","model":[],"threads":null}', encoding='utf-8')
    (tmp_path / 'history.json').write_text('[]', encoding='utf-8')
    (tmp_path / 'active-run.json').write_text('[]', encoding='utf-8')
    app = QApplication.instance() or QApplication([])
    window = Window(auto_start=False)
    assert window.settings['accent'] == 0
    assert window.settings['model'] == 'base'
    assert window.settings['threads'] == 0
    assert window.settings_warning and window.history_warning
    assert list(tmp_path.glob('settings-damaged-*.json'))
    assert list(tmp_path.glob('history-damaged-*.json'))
    window.close()
    app.processEvents()


def test_corrupt_calibration_samples_are_ignored(tmp_path):
    task = dict(DEFAULTS)
    key = calibration_key(task)
    (tmp_path / 'eta-calibration.json').write_text(json.dumps({
        'version': 3, 'profiles': {key: {'samples': [None, 'bad', {}, 0.5]}, 'broken': []}}), encoding='utf-8')
    assert load_calibration(tmp_path) == {key: {'samples': [0.5]}}


def test_auto_cuda_uses_cuda_startup_overhead():
    task = dict(DEFAULTS, device='auto', _eta_backend='cuda')
    explicit = dict(task, device='cuda')
    a = estimate_task(task, 600, {calibration_key(task): {'samples': [0.05]}})
    b = estimate_task(explicit, 600, {calibration_key(explicit): {'samples': [0.05]}})
    assert a == b


def test_powershell_probe_declares_encoding(monkeypatch):
    from whisperdesk import hardware
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        return Obj(returncode=0, stdout='[{"Name":"Тестовий адаптер","AdapterRAM":1024}]')
    monkeypatch.setattr(hardware.subprocess, 'run', run)
    result = hardware._windows_video_adapters()
    assert result[0]['name'] == 'Тестовий адаптер'
    assert calls[0][1]['encoding'] == 'utf-8'
    assert 'OutputEncoding' in calls[0][0][-1]
