import json
import os
import queue
import threading
import wave
from types import SimpleNamespace as Obj

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import numpy as np
import pytest

from whisperdesk.audio import decode_range, windows, probe, RATE
from whisperdesk.core import (Job, parse_time, validate_range, transcript_text,
                             atomic_text, load_session, select_language, owned_words)


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / 'тест запис.wav'
    rate = 44100
    x = np.arange(rate * 55) / rate
    samples = (.3 * np.sin(2 * np.pi * 440 * x) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(rate)
        f.writeframes(samples.tobytes())
    return path


def test_time_and_validation():
    assert parse_time('27:05:01') == 97501
    for bad in ('12', '00:60:00', '-1:00:00', '00:00:1.5'):
        with pytest.raises(ValueError):
            parse_time(bad)
    for start, end in ((4, 4), (-1, 8), (0, 11)):
        with pytest.raises(ValueError):
            validate_range(start, end, 10)


def test_seek_and_resample(audio):
    event = threading.Event()
    assert probe(audio) == 55
    full = np.concatenate(list(decode_range(audio, 0, 55, event)))
    cut = np.concatenate(list(decode_range(audio, 37.25, 39.75, event)))
    assert len(cut) == 40000
    np.testing.assert_allclose(cut, full[596000:636000], atol=.0001)


@pytest.mark.parametrize('core_seconds', [8, 24])
def test_window_ownership_and_memory(audio, core_seconds):
    output = list(windows(audio, 1.25, 54.5, threading.Event(), core_seconds))
    assert output[0][2] == 1.25
    assert output[-1][3] == 54.5
    assert all(a[3] == b[2] for a, b in zip(output, output[1:]))
    assert sum(round((r - l) * RATE) for _, _, l, r in output) == 53.25 * RATE
    assert max(len(a) for a, *_ in output) <= (core_seconds + 1.6) * RATE + 1


def test_cancel_decode(audio):
    event = threading.Event()
    event.set()
    assert list(windows(audio, 0, 55, event)) == []


@pytest.mark.parametrize('extension', ['mp3', 'm4a', 'ogg'])
def test_compressed_range(audio, tmp_path, extension):
    import shutil
    import subprocess
    if not shutil.which('ffmpeg'):
        pytest.skip('Optional integration test requires ffmpeg')
    encoded = tmp_path / ('encoded.' + extension)
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(audio), '-y', str(encoded)], check=True)
    stop = threading.Event()
    full = np.concatenate(list(decode_range(encoded, 0, 50, stop)))
    cut = np.concatenate(list(decode_range(encoded, 37, 39, stop)))
    assert len(cut) == 2 * RATE
    # Independent resampler startup can shift phase by a fraction of a 16 kHz
    # sample. Require agreement within ONE sample (62.5 microseconds), while
    # ignoring startup transients. Do not mistake this for a seconds-wide seek.
    target = full[37*RATE:39*RATE]
    x = np.arange(1000, len(cut)-1000)
    errors = [np.sqrt(np.mean((cut[x] - np.interp(x + shift, np.arange(len(target)), target))**2))
              for shift in np.linspace(-1, 1, 41)]
    assert min(errors) < .002


def test_language_and_overlap():
    assert select_language([('de', .9), ('en', .07), ('uk', .02), ('ru', .01)])[0] == 'en'
    assert select_language([('uk', .51), ('ru', .49)], 'ru')[0] == 'ru'
    words = [Obj(start=0, end=1), Obj(start=1, end=2), Obj(start=2, end=3)]
    a = owned_words(words, 10, 10, 11.5)
    b = owned_words(words, 10, 11.5, 13)
    assert a + b == words
    assert not set(map(id, a)).intersection(map(id, b))


def test_utf8_export_and_recovery(tmp_path):
    row = dict(start=750, end=754, text='Привіт, world! Привет.')
    path = tmp_path / 'текст.txt'
    atomic_text(path, transcript_text([row], True))
    assert path.read_text(encoding='utf-8') == '[00:12:30 — 00:12:34] Привіт, world! Привет.'
    session = tmp_path / 'session.jsonl'
    session.write_text(json.dumps({'type': 'segment', 'row': row}) + '\n{"type":', encoding='utf-8')
    assert load_session(session) == [row]


def test_engine_with_injected_model(audio, tmp_path, monkeypatch):
    import faster_whisper.vad
    from whisperdesk.engine import transcribe_job
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps',
                        lambda a, *_: [{'start': 0, 'end': len(a)}])
    seen = []

    class FakeModel:
        def __init__(self, *args, **kwargs):
            pass

        def detect_language(self, audio):
            return 'uk', .8, [('uk', .8), ('ru', .1), ('en', .1)]

        def transcribe(self, audio, **kwargs):
            seen.append(kwargs)
            words = [Obj(start=1, end=2, word=' Привіт!')]
            return iter([Obj(words=words)]), None

    channel = queue.Queue()
    job = Job(str(audio), 10, 30, language='mixed', session=str(tmp_path / 'result.jsonl'))
    transcribe_job(job, channel, threading.Event(), FakeModel)
    events = list(channel.queue)
    assert not [e for e in events if e[0] == 'error']
    assert all(x['language'] == 'uk' and x['task'] == 'transcribe' for x in seen)
    assert events[-1][0] == 'done'
    assert [e for e in events if e[0] == 'progress'][-1][1][:2] == (20, 20)
    rows = load_session(job.session)
    assert len(rows) == 3
    assert rows[0]['start'] == 11


def test_engine_silence_progress(audio, tmp_path, monkeypatch):
    import faster_whisper.vad
    from whisperdesk.engine import transcribe_job
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps', lambda *a: [])

    class Silent:
        def __init__(self, *a, **kw):
            pass

        def transcribe(self, *a, **kw):
            raise AssertionError('Silence must not reach inference')

    channel = queue.Queue()
    job = Job(str(audio), 0, 50, session=str(tmp_path / 'silence.jsonl'))
    transcribe_job(job, channel, threading.Event(), Silent)
    assert channel.queue[-1][0] == 'done'
    assert [e for e in channel.queue if e[0] == 'progress'][-1][1] == (50, 50, 0)


def test_ui_smoke(tmp_path, monkeypatch, audio):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from whisperdesk.ui import Window
    app = QApplication.instance() or QApplication([])
    w = Window(auto_start=False)
    w.add_files([str(audio)])
    assert w.selected()['duration'] == 55
    assert w.mixed_help.isHidden()
    w.language.setCurrentIndex(2)
    assert not w.mixed_help.isHidden()
    w.language.setCurrentIndex(0)
    assert w.mixed_help.isHidden()
    w.rows = [dict(start=13, end=15, text='Україна / English')]
    w.render()
    assert w.text.toPlainText() == 'Україна / English'
    w.stamps.setChecked(True)
    assert '[00:00:13' in w.text.toPlainText()
    w.close()
    app.processEvents()


def test_preflight_cancel_preserves_queue_state(tmp_path, monkeypatch, audio):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from whisperdesk.ui import Window

    app = QApplication.instance() or QApplication([])
    w = Window(auto_start=False)
    w.add_files([str(audio)])
    task = w.selected()
    task['status'] = 'error'
    task['error'] = 'previous failure'
    w.tasks.save()

    monkeypatch.setattr(w, 'confirm_preflight', lambda: False)
    w.start_queue()

    assert not w.running_queue
    assert w.process is None
    assert task['status'] == 'error'
    assert task['error'] == 'previous failure'
    w.close()
    app.processEvents()


def test_settings_show_hardware_summary(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication, QDialog, QLabel
    import whisperdesk.ui as ui_module

    app = QApplication.instance() or QApplication([])
    w = ui_module.Window(auto_start=False)
    monkeypatch.setattr(
        ui_module,
        'analyze_hardware',
        lambda: {
            'cpu_logical': 16,
            'ram_bytes': 32 * 1024**3,
            'gpus': [{'name': 'NVIDIA Test GPU', 'vram_bytes': 8 * 1024**3}],
            'cuda_count': 1,
            'cuda_available': True,
        },
    )
    monkeypatch.setattr(QDialog, 'exec', lambda self: 0)

    w.open_settings()

    texts = [item.text() for item in w.findChildren(QLabel)]
    assert any('CPU: 16 потоків' in text for text in texts)
    assert any('NVIDIA Test GPU' in text for text in texts)
    assert any('CUDA доступна' in text for text in texts)
    w.close()
    app.processEvents()



def test_recommendation_dialog_reject_keeps_current_choice(tmp_path, monkeypatch, audio):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication, QDialog, QPushButton
    import whisperdesk.ui as ui_module

    app = QApplication.instance() or QApplication([])
    w = ui_module.Window(auto_start=False)
    w.add_files([str(audio)])
    task = w.selected()
    assert task['model'] == 'base'
    assert task['device'] == 'cpu'

    seen = {}
    def fake_exec(dialog):
        seen['buttons'] = [b.text() for b in dialog.findChildren(QPushButton)]
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, 'exec', fake_exec)
    applied = w.show_recommendation(
        {
            'recommendation': {
                'model': 'turbo',
                'device': 'auto',
                'reason': 'Тестова рекомендація.',
            }
        }
    )

    assert applied is False
    assert 'Відхилити' in seen['buttons']
    assert '✓  Застосувати' in seen['buttons']
    assert task['model'] == 'base'
    assert task['device'] == 'cpu'
    w.close()
    app.processEvents()



def test_repeat_history_record_recreates_task_with_same_settings(tmp_path, monkeypatch, audio):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    import whisperdesk.ui as ui_module

    app = QApplication.instance() or QApplication([])
    w = ui_module.Window(auto_start=False)

    record = {
        'source': str(audio),
        'duration': 55.0,
        'start': 5.0,
        'end': 25.0,
        'language': 'mixed',
        'model': 'small',
        'requested_device': 'auto',
        'profile': 'fast',
        'threads': 3,
    }

    assert w.repeat_history_record(record) is True
    task = w.tasks.tasks[-1]
    assert task['source'] == str(audio.resolve())
    assert task['start'] == 5.0
    assert task['end'] == 25.0
    assert task['language'] == 'mixed'
    assert task['model'] == 'small'
    assert task['device'] == 'auto'
    assert task['profile'] == 'fast'
    assert task['threads'] == 3
    assert task['status'] == 'pending'
    w.close()
    app.processEvents()


def test_repeat_history_full_file_tracks_new_duration(tmp_path, monkeypatch, audio):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    from PySide6.QtWidgets import QApplication
    import whisperdesk.ui as ui_module

    app = QApplication.instance() or QApplication([])
    w = ui_module.Window(auto_start=False)

    record = {
        'source': str(audio),
        'duration': 55.0,
        'start': 0.0,
        'end': 55.0,
        'language': 'uk',
        'model': 'base',
        'requested_device': 'cpu',
        'profile': 'eco',
        'threads': 0,
    }

    assert w.repeat_history_record(record) is True
    task = w.tasks.tasks[-1]
    assert task['start'] == 0.0
    assert task['end'] == task['duration'] == 55
    w.close()
    app.processEvents()
