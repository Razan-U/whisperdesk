from pathlib import Path

from whisperdesk.preflight import analyze_queue, report_text
from whisperdesk.eta import estimate_task
from whisperdesk.core import fingerprint


def task(root, source, **overrides):
    data = dict(
        id='1',
        source=str(source),
        duration=20.0,
        start=0.0,
        end=20.0,
        position=0.0,
        status='pending',
        error='',
        session=str(root / 'sessions' / '1.jsonl'),
        language='uk',
        model='base',
        device='cpu',
        profile='eco',
        threads=0,
    )
    data.update(overrides)
    return data


def test_preflight_valid_queue(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    report = analyze_queue(
        [task(tmp_path, source)],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    assert report['count'] == 1
    assert report['seconds'] == 20
    assert not report['blockers']
    assert report['models']['base'] == 1
    assert report['devices']['cpu'] == 1
    assert 'Критичних проблем не виявлено' in report_text(report)


def test_preflight_blocks_missing_source_and_bad_range(tmp_path):
    (tmp_path / 'sessions').mkdir()
    report = analyze_queue(
        [task(tmp_path, tmp_path / 'missing.wav', start=12, end=25, duration=20)],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    text = '\n'.join(report['blockers'])
    assert 'не знайдено' in text
    assert 'некоректний діапазон' in text
    assert 'ПОТРІБНО ВИПРАВИТИ' in report_text(report)


def test_preflight_reports_model_download_without_blocking(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    report = analyze_queue(
        [task(tmp_path, source, model='small')],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: False,
    )
    assert not report['blockers']
    assert report['missing_models'] == ['small']
    assert any('завантажено моделі' in note for note in report['notes'])


def test_preflight_ignores_completed_jobs(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    report = analyze_queue(
        [task(tmp_path, source, status='done')],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    assert report['count'] == 0
    assert any('немає файлів' in item for item in report['blockers'])


def test_preflight_blocks_changed_source_for_resume(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'original')
    session = tmp_path / 'sessions' / '1.jsonl'
    header = {
        'type': 'job',
        'version': 2,
        'source': str(source),
        'start': 0.0,
        'end': 20.0,
        'model': 'base',
        'language': 'uk',
        'fingerprint': fingerprint(source),
    }
    import json
    session.write_text(json.dumps(header) + '\n', encoding='utf-8')
    source.write_bytes(b'changed')

    report = analyze_queue(
        [task(tmp_path, source, status='interrupted')],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    assert any('аудіофайл змінився' in item for item in report['blockers'])


def test_eta_respects_model_and_device():
    base_cpu = estimate_task({'model': 'base', 'device': 'cpu', 'profile': 'eco', 'threads': 4}, 600)
    large_cpu = estimate_task({'model': 'large-v3', 'device': 'cpu', 'profile': 'eco', 'threads': 4}, 600)
    large_cuda = estimate_task({'model': 'large-v3', 'device': 'cuda', 'profile': 'eco', 'threads': 4}, 600)
    auto = estimate_task({'model': 'large-v3', 'device': 'auto', 'profile': 'eco', 'threads': 4}, 600)
    assert base_cpu[1] < large_cpu[1]
    assert large_cuda[1] < large_cpu[1]
    assert auto[0] <= large_cuda[0]
    assert auto[1] >= large_cpu[1]
    assert auto[2] == 'низька'


def test_preflight_eta_uses_remaining_resume_time(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    report = analyze_queue(
        [task(tmp_path, source, duration=600, end=600, position=300, status='interrupted')],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    # No session file: queue position still represents the remaining half.
    assert report['seconds'] == 300
    assert report['eta_min_seconds'] > 0
    assert report['eta_max_seconds'] > report['eta_min_seconds']
    assert 'Орієнтовний час обробки:' in report_text(report)
