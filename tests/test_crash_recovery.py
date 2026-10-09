from pathlib import Path

from whisperdesk.core import write_record
from whisperdesk.crash_recovery import (
    clear_marker,
    load_marker,
    restore_missing_task,
    update_checkpoint,
    write_marker,
)
from whisperdesk.queue_store import TaskQueue
from whisperdesk.ui import DEFAULTS


def task(tmp_path):
    q = TaskQueue(tmp_path)
    item = q.add(str(tmp_path / 'audio.wav'), 120.0, DEFAULTS)
    item['start'] = 10.0
    item['end'] = 90.0
    item['position'] = 10.0
    q.save()
    return q, item


def test_crash_marker_roundtrip_and_checkpoint(tmp_path):
    q, item = task(tmp_path)
    marker = write_marker(tmp_path, item, '0.3.2-beta.test')
    assert marker['task_id'] == item['id']
    assert load_marker(tmp_path)['position'] == 10.0

    assert update_checkpoint(tmp_path, item['id'], 42.5) is True
    loaded = load_marker(tmp_path)
    assert loaded['position'] == 42.5
    assert loaded['app_version'] == '0.3.2-beta.test'

    assert clear_marker(tmp_path) is True
    assert load_marker(tmp_path) is None


def test_checkpoint_update_ignores_other_task(tmp_path):
    q, item = task(tmp_path)
    write_marker(tmp_path, item, 'x')
    assert update_checkpoint(tmp_path, 'other-task', 50) is False
    assert load_marker(tmp_path)['position'] == 10.0


def test_restore_missing_queue_task_from_marker_and_session(tmp_path):
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    session = tmp_path / 'sessions' / 'lost.jsonl'
    session.parent.mkdir()

    with session.open('w', encoding='utf-8') as stream:
        write_record(stream, {
            'type': 'job',
            'version': 2,
            'source': str(source),
            'start': 0.0,
            'end': 60.0,
            'model': 'small',
            'language': 'uk',
            'fingerprint': {'size': source.stat().st_size, 'mtime_ns': source.stat().st_mtime_ns},
        })
        write_record(stream, {
            'type': 'checkpoint',
            'position': 24.0,
            'language': 'uk',
            'rows': [{'start': 1.0, 'end': 2.0, 'text': 'тест', 'language': 'uk'}],
        })

    marker = {
        'version': 1,
        'task_id': 'lost-task',
        'source': str(source),
        'duration': 60.0,
        'start': 0.0,
        'end': 60.0,
        'position': 24.0,
        'language': 'uk',
        'model': 'small',
        'device': 'auto',
        'profile': 'fast',
        'threads': 3,
        'session': str(session),
    }

    q = TaskQueue(tmp_path)
    restored = restore_missing_task(q, marker)
    assert restored is not None
    assert restored['id'] == 'lost-task'
    assert restored['status'] == 'interrupted'
    assert restored['position'] == 24.0
    assert restored['model'] == 'small'
    assert restored['device'] == 'auto'
    assert restored['profile'] == 'fast'
    assert restored['threads'] == 3

    persisted = TaskQueue(tmp_path)
    assert persisted.get('lost-task')['position'] == 24.0


def test_restore_missing_task_requires_surviving_session(tmp_path):
    q = TaskQueue(tmp_path)
    marker = {
        'version': 1,
        'task_id': 'lost',
        'session': str(tmp_path / 'missing.jsonl'),
    }
    assert restore_missing_task(q, marker) is None
