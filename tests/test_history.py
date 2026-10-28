from pathlib import Path
from datetime import datetime

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



def test_history_delete_one_record(tmp_path):
    store = HistoryStore(tmp_path)
    first = store.add_run(sample_task(tmp_path), 'done', 5)
    second = store.add_run(sample_task(tmp_path), 'error', 6)

    assert store.delete(first['id']) is True
    assert store.get(first['id']) is None
    assert store.get(second['id']) is not None
    assert store.delete('missing') is False
    assert len(HistoryStore(tmp_path).records) == 1


def test_history_clear_all_records(tmp_path):
    store = HistoryStore(tmp_path)
    store.add_run(sample_task(tmp_path), 'done', 5)
    store.add_run(sample_task(tmp_path), 'error', 6)

    assert store.clear() == 2
    assert store.records == []
    assert HistoryStore(tmp_path).records == []
    assert store.clear() == 0


def test_history_purge_older_than_calendar_months(tmp_path):
    store = HistoryStore(tmp_path)
    now = datetime(2026, 10, 9, 12, 0, 0).timestamp()

    old = store.add_run(sample_task(tmp_path), 'done', 5)
    recent = store.add_run(sample_task(tmp_path), 'done', 5)
    old['created_at'] = datetime(2026, 7, 8, 12, 0, 0).timestamp()
    recent['created_at'] = datetime(2026, 7, 9, 12, 0, 0).timestamp()
    store.save()

    assert store.purge_older_than_months(3, now=now) == 1
    assert store.get(old['id']) is None
    assert store.get(recent['id']) is not None


def test_history_purge_keeps_record_without_valid_timestamp(tmp_path):
    store = HistoryStore(tmp_path)
    record = store.add_run(sample_task(tmp_path), 'done', 5)
    record['created_at'] = None
    store.save()

    assert store.purge_older_than_months(3, now=datetime(2026, 10, 9).timestamp()) == 0
    assert store.get(record['id']) is not None
