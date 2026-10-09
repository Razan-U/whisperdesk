from pathlib import Path

from whisperdesk.preflight import analyze_queue, report_text
from whisperdesk.eta import estimate_task, load_calibration, record_sample
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


def test_first_run_turbo_cpu_range_covers_observed_slow_cpu():
    # Real-world validation: 19:31 of audio took about 45 minutes on CPU.
    settings = {'model': 'turbo', 'device': 'cpu', 'profile': 'eco', 'threads': 0}
    low, high, confidence = estimate_task(settings, 19 * 60 + 31)
    assert low < 45 * 60 < high
    assert confidence == 'початкова'


def test_eta_calibrates_from_completed_local_job(tmp_path):
    settings = {'model': 'turbo', 'device': 'cpu', 'profile': 'eco', 'threads': 0}
    audio = 19 * 60 + 31
    elapsed = 45 * 60

    baseline = estimate_task(settings, audio)
    assert record_sample(tmp_path, settings, audio, elapsed)
    calibration = load_calibration(tmp_path)
    calibrated = estimate_task(settings, audio, calibration)

    assert calibrated[0] < elapsed < calibrated[1]
    assert calibrated[2] == 'локальна · 1 замір'
    assert calibrated[1] - calibrated[0] < baseline[1] - baseline[0]


def test_preflight_uses_saved_local_eta_calibration(tmp_path):
    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    settings = task(
        tmp_path,
        source,
        duration=19 * 60 + 31,
        end=19 * 60 + 31,
        model='turbo',
        device='cpu',
    )
    assert record_sample(tmp_path, settings, 19 * 60 + 31, 45 * 60)

    report = analyze_queue(
        [settings],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    assert report['eta_confidence'] == 'локальна · 1 замір'
    assert report['eta_min_seconds'] < 45 * 60 < report['eta_max_seconds']
    assert 'локальна · 1 замір' in report_text(report)


def test_preflight_shows_non_binding_hardware_recommendation(tmp_path, monkeypatch):
    import whisperdesk.preflight as preflight

    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    monkeypatch.setattr(
        preflight,
        'analyze_hardware',
        lambda: {
            'cpu_logical': 12,
            'ram_bytes': 32 * 1024**3,
            'gpus': [{'name': 'NVIDIA Test', 'vram_bytes': 12 * 1024**3}],
            'cuda_count': 1,
            'cuda_available': True,
        },
    )

    original = task(tmp_path, source, duration=1800, end=1800, model='base', device='cpu')
    report = preflight.analyze_queue(
        [original],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )

    assert report['recommendation']['model'] == 'turbo'
    assert report['recommendation']['device'] == 'auto'
    text = preflight.report_text(report)
    assert 'Рекомендовано:' in text
    assert 'turbo + Авто' in text
    # Recommendation must not mutate the queued task.
    assert original['model'] == 'base'
    assert original['device'] == 'cpu'
