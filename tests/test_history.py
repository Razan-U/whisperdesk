from pathlib import Path

from whisperdesk.history import HistoryStore


def sample_task(tmp_path):
    return {
        'id': 'task-1',
        'source': str(tmp_path / 'audio.wav'),
        'duration': 120.0,
        'start': 10.0,
        'end': 90.0,
        'status': 'done',
        'language': 'uk',
        'model': 'base',
        'device': 'auto',
        'profile': 'eco',
        'threads': 0,
        'session': str(tmp_path / 'sessions' / 'task-1.jsonl'),
    }


def test_history_store_persists_run(tmp_path):
    store = HistoryStore(tmp_path)
    record = store.add_run(
        sample_task(tmp_path),
        'done',
        35.5,
        actual_device='cuda',
        message='Готово',
    )

    loaded = HistoryStore(tmp_path)
    assert len(loaded.records) == 1
    item = loaded.records[0]
    assert item['id'] == record['id']
    assert item['source'].endswith('audio.wav')
    assert item['audio_seconds'] == 80
    assert item['elapsed_seconds'] == 35.5
    assert item['requested_device'] == 'auto'
    assert item['actual_device'] == 'cuda'
    assert item['status'] == 'done'
    assert item['result_txt'].endswith('task-1.txt')


def test_history_newest_returns_reverse_order(tmp_path):
    store = HistoryStore(tmp_path)
    a = store.add_run(sample_task(tmp_path), 'error', 3, message='a')
    b = store.add_run(sample_task(tmp_path), 'done', 4, message='b')
    assert [item['id'] for item in store.newest()] == [b['id'], a['id']]


def test_history_is_independent_from_source_and_result_existence(tmp_path):
    store = HistoryStore(tmp_path)
    record = store.add_run(sample_task(tmp_path), 'done', 5)
    assert not Path(record['source']).exists()
    assert not Path(record['result_txt']).exists()
    # History remains readable even if files were moved/deleted later.
    assert HistoryStore(tmp_path).get(record['id'])['status'] == 'done'
