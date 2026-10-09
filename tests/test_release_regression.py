"""Cross-feature release gate for WhisperDesk 0.4."""
from pathlib import Path
from types import SimpleNamespace
import queue
import time
import wave

import numpy as np
import pytest

from whisperdesk.core import fingerprint, write_record
from whisperdesk.history import HistoryStore
from whisperdesk.preflight import analyze_queue
from whisperdesk.queue_store import TaskQueue
from whisperdesk.ui import DEFAULTS
from whisperdesk import updater


@pytest.fixture
def regression_audio(tmp_path):
    path = tmp_path / 'regression.wav'
    rate = 16000
    samples = np.zeros(rate * 12, dtype='<i2')
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    return path


def _write_resumable_session(task, position, rows=None):
    session = Path(task['session'])
    session.parent.mkdir(parents=True, exist_ok=True)
    with session.open('w', encoding='utf-8') as stream:
        write_record(stream, {
            'type': 'job',
            'version': 2,
            'source': task['source'],
            'start': task['start'],
            'end': task['end'],
            'model': task['model'],
            'language': task['language'],
            'fingerprint': fingerprint(task['source']),
        })
        write_record(stream, {
            'type': 'checkpoint',
            'position': position,
            'language': task['language'],
            'rows': rows or [],
        })
    return session


def test_release_regression_crash_recovery_feeds_remaining_eta(tmp_path):
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio identity')

    q = TaskQueue(tmp_path)
    task = q.add(str(source), 120.0, DEFAULTS)
    task['status'] = 'running'
    _write_resumable_session(task, 48.0)
    q.save()

    restored = TaskQueue(tmp_path)
    item = restored.get(task['id'])
    assert item['status'] == 'interrupted'
    assert item['position'] == 48.0

    report = analyze_queue(
        restored.tasks,
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    assert report['count'] == 1
    assert report['seconds'] == 72.0
    assert report['eta_min_seconds'] > 0


def test_release_regression_history_cleanup_never_deletes_user_files(tmp_path):
    source = tmp_path / 'source.wav'
    source.write_bytes(b'audio')
    session = tmp_path / 'sessions' / 'run.jsonl'
    session.parent.mkdir(parents=True)
    session.write_text('journal', encoding='utf-8')
    result = session.with_suffix('.txt')
    result.write_text('transcript', encoding='utf-8')

    store = HistoryStore(tmp_path)
    task = {
        'id': 'run',
        'source': str(source),
        'duration': 60.0,
        'start': 0.0,
        'end': 60.0,
        'language': 'uk',
        'model': 'base',
        'device': 'cpu',
        'profile': 'eco',
        'threads': 0,
        'session': str(session),
    }
    record = store.add_run(task, 'done', 30.0, actual_device='cpu')
    record['created_at'] = 1.0
    store.save()

    assert store.purge_older_than_months(1, now=time.time()) == 1
    assert store.records == []
    assert source.read_bytes() == b'audio'
    assert session.read_text(encoding='utf-8') == 'journal'
    assert result.read_text(encoding='utf-8') == 'transcript'


def test_release_regression_pause_finalization_does_not_create_history(tmp_path, monkeypatch, regression_audio):
    monkeypatch.setenv('WHISPERDESK_DATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from whisperdesk.ui import Window

    app = QApplication.instance() or QApplication([])
    w = Window(auto_start=False)
    w.timer.stop()
    w.add_files([str(regression_audio)])
    task = w.selected()
    task['status'] = 'running'
    task['position'] = 0.0
    _write_resumable_session(task, 8.0, [{
        'start': 1.0, 'end': 2.0, 'text': 'checkpoint', 'language': 'uk'
    }])
    w.tasks.save()

    class Channel(queue.Queue):
        def close(self):
            pass

    w.active_id = task['id']
    w.operation = 'transcribe'
    w.running_queue = True
    w.queue_paused = True
    w.pause_requested = True
    w.paused_task_id = task['id']
    w.cancel_deadline = time.monotonic() + 5
    w.finished_message = 'Скасовано. Готові фрагменти збережено.'
    w.failed = False
    w.operation_started_at = time.monotonic() - 2
    w.history_run_started[task['id']] = time.monotonic() - 2
    w.channel = Channel()
    w.process = SimpleNamespace(
        is_alive=lambda: False,
        join=lambda *args, **kwargs: None,
        close=lambda: None,
        exitcode=0,
    )

    w.poll()

    assert task['status'] == 'interrupted'
    assert task['position'] == 8.0
    assert w.queue_paused is True
    assert not w.history.records
    assert w.history_run_elapsed[task['id']] > 0
    assert 'паузі' in w.status.text().lower()

    w.running_queue = False
    w.queue_paused = False
    w.close()
    app.processEvents()


def _release(version, prerelease):
    name = f'WhisperDesk-Update-{version}.exe'
    return {
        'tag_name': 'v' + version,
        'name': 'WhisperDesk ' + version,
        'body': '',
        'html_url': 'https://github.com/Razan-U/whisperdesk/releases/tag/v' + version,
        'published_at': '2026-10-09T00:00:00Z',
        'prerelease': prerelease,
        'draft': False,
        'assets': [
            {
                'name': name,
                'browser_download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v' + version + '/' + name,
                'size': 123,
                'url': 'https://api.github.com/repos/Razan-U/whisperdesk/releases/assets/' + version,
            },
            {
                'name': name + '.sha256',
                'browser_download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v' + version + '/' + name + '.sha256',
                'size': 80,
            },
        ],
    }


def test_release_regression_update_channels_handle_rc_and_stable():
    releases = [
        _release('0.4.0-rc.1', True),
        _release('0.3.1', False),
    ]
    assert updater.select_release(releases, '0.3.1', 'stable') is None
    assert updater.select_release(releases, '0.3.1', 'test')['version'] == '0.4.0-rc.1'

    releases.insert(0, _release('0.4.0', False))
    assert updater.select_release(releases, '0.3.1', 'stable')['version'] == '0.4.0'
    assert updater.select_release(releases, '0.4.0-rc.1', 'test')['version'] == '0.4.0'
    assert updater.select_release(releases, '0.4.0', 'test') is None
