from pathlib import Path

from whisperdesk.preflight import analyze_queue, report_text
from whisperdesk.eta import (estimate_task, load_calibration, record_sample,
                             calibration_key, migrate_calibration_from_history)
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
    # Recommendation is available to the dedicated dialog, not duplicated in
    # the compact main preflight summary.
    assert 'Рекомендовано:' not in text
    assert report['recommendation']['reason']
    # Recommendation must not mutate the queued task.
    assert original['model'] == 'base'
    assert original['device'] == 'cpu'



def test_main_preflight_text_stays_compact_and_hides_hardware_recommendation(tmp_path, monkeypatch):
    import whisperdesk.preflight as preflight

    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    monkeypatch.setattr(
        preflight,
        'analyze_hardware',
        lambda: {
            'cpu_logical': 12,
            'ram_bytes': 16 * 1024**3,
            'gpus': [{'name': 'NVIDIA Test', 'vram_bytes': 4 * 1024**3}],
            'cuda_count': 1,
            'cuda_available': True,
        },
    )

    report = preflight.analyze_queue(
        [task(tmp_path, source, duration=1200, end=1200)],
        tmp_path / 'models',
        tmp_path,
        ready_checker=lambda path: True,
    )
    text = preflight.report_text(report)

    assert report['recommendation']
    assert 'Залізо:' not in text
    assert 'Рекомендовано:' not in text
    assert 'Вільно в папці даних:' not in text
    assert 'CUDA доступна: знайдено' not in text
    assert 'Файлів до запуску:' in text
    assert 'Орієнтовний час обробки:' in text



def test_auto_eta_calibration_is_split_by_actual_backend(tmp_path):
    task_data = {'model': 'turbo', 'device': 'auto', 'profile': 'fast', 'threads': 0}
    assert record_sample(tmp_path, task_data, 600, 30, actual_device='cuda')
    assert record_sample(tmp_path, task_data, 600, 900, actual_device='cpu')
    calibration = load_calibration(tmp_path)

    cuda_task = {**task_data, '_eta_backend': 'cuda'}
    cpu_task = {**task_data, '_eta_backend': 'cpu'}
    assert calibration_key(cuda_task) != calibration_key(cpu_task)

    cuda_eta = estimate_task(cuda_task, 600, calibration)
    cpu_eta = estimate_task(cpu_task, 600, calibration)
    assert cuda_eta[1] < cpu_eta[0]


def test_auto_eta_ignores_legacy_mixed_auto_bucket(tmp_path):
    import json
    legacy_key = 'turbo|auto|fast|4'
    (tmp_path / 'eta-calibration.json').write_text(
        json.dumps({'version': 1, 'profiles': {legacy_key: {'samples': [0.05, 1.5]}}}),
        encoding='utf-8',
    )
    calibration = load_calibration(tmp_path)
    task_data = {
        'model': 'turbo', 'device': 'auto', 'profile': 'fast', 'threads': 4,
        '_eta_backend': 'cuda',
    }
    low, high, confidence = estimate_task(task_data, 600, calibration)
    assert confidence == 'низька'
    assert high > low


def test_calibrated_eta_resists_single_slow_outlier(tmp_path):
    settings = {'model': 'turbo', 'device': 'cuda', 'profile': 'fast', 'threads': 4}
    for rtf in (0.043, 0.047, 0.049, 0.052, 0.30):
        assert record_sample(tmp_path, settings, 600, 600 * rtf)
    calibration = load_calibration(tmp_path)
    low, high, confidence = estimate_task(settings, 600, calibration)
    assert confidence == 'локальна · 5 замірів'
    assert high < 60
    assert low < 30.1 < high



def test_eta_calibration_splits_mixed_from_fixed_language():
    base = {'model': 'turbo', 'device': 'auto', 'profile': 'fast', 'threads': 0}
    en = {**base, 'language': 'en', '_eta_backend': 'cuda'}
    uk = {**base, 'language': 'uk', '_eta_backend': 'cuda'}
    mixed = {**base, 'language': 'mixed', '_eta_backend': 'cuda'}

    assert calibration_key(en) == calibration_key(uk)
    assert calibration_key(en) != calibration_key(mixed)


def test_eta_v2_migrates_backend_and_language_from_history(tmp_path):
    import json

    # Legacy v1 samples are ambiguous and must not be reused directly.
    (tmp_path / 'eta-calibration.json').write_text(
        json.dumps({
            'version': 1,
            'profiles': {'turbo|auto|fast|4': {'samples': [0.05, 0.13]}},
        }),
        encoding='utf-8',
    )
    records = [
        {
            'status': 'done',
            'audio_seconds': 1200,
            'elapsed_seconds': 60,
            'model': 'turbo',
            'requested_device': 'auto',
            'actual_device': 'cuda',
            'profile': 'fast',
            'threads': 4,
            'language': 'en',
        },
        {
            'status': 'done',
            'audio_seconds': 1200,
            'elapsed_seconds': 160,
            'model': 'turbo',
            'requested_device': 'auto',
            'actual_device': 'cuda',
            'profile': 'fast',
            'threads': 4,
            'language': 'mixed',
        },
    ]

    assert migrate_calibration_from_history(tmp_path, records) is True
    calibration = load_calibration(tmp_path)

    fixed_key = calibration_key({
        'model': 'turbo', 'device': 'auto', 'profile': 'fast', 'threads': 4,
        'language': 'en', '_eta_backend': 'cuda',
    })
    mixed_key = calibration_key({
        'model': 'turbo', 'device': 'auto', 'profile': 'fast', 'threads': 4,
        'language': 'mixed', '_eta_backend': 'cuda',
    })

    assert calibration[fixed_key]['samples'] == [0.05]
    assert round(calibration[mixed_key]['samples'][0], 3) == 0.133
    assert migrate_calibration_from_history(tmp_path, records) is False


def test_preflight_uses_separate_auto_cuda_eta_for_mixed_and_fixed(tmp_path, monkeypatch):
    import whisperdesk.preflight as preflight

    (tmp_path / 'sessions').mkdir()
    source = tmp_path / 'audio.wav'
    source.write_bytes(b'audio')
    monkeypatch.setattr(
        preflight,
        'analyze_hardware',
        lambda: {
            'cpu_logical': 12,
            'ram_bytes': 16 * 1024**3,
            'gpus': [{'name': 'NVIDIA Test', 'vram_bytes': 4 * 1024**3}],
            'cuda_count': 1,
            'cuda_available': True,
        },
    )

    fixed_settings = task(
        tmp_path, source, duration=1200, end=1200,
        model='turbo', device='auto', profile='fast', language='en',
    )
    mixed_settings = dict(fixed_settings)
    mixed_settings['language'] = 'mixed'
    mixed_settings['id'] = '2'
    mixed_settings['session'] = str(tmp_path / 'sessions' / '2.jsonl')

    assert record_sample(
        tmp_path, fixed_settings, 1200, 60, actual_device='cuda'
    )
    assert record_sample(
        tmp_path, mixed_settings, 1200, 160, actual_device='cuda'
    )

    fixed_report = preflight.analyze_queue(
        [fixed_settings], tmp_path / 'models', tmp_path,
        ready_checker=lambda path: True,
    )
    mixed_report = preflight.analyze_queue(
        [mixed_settings], tmp_path / 'models', tmp_path,
        ready_checker=lambda path: True,
    )

    assert fixed_report['eta_confidence'] == 'локальна · 1 замір'
    assert mixed_report['eta_confidence'] == 'локальна · 1 замір'
    assert mixed_report['eta_min_seconds'] > fixed_report['eta_max_seconds']
