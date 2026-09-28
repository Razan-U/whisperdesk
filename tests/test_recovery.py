import queue
import threading
from pathlib import Path
from types import SimpleNamespace as Obj
import json
import numpy as np
import pytest

from whisperdesk.core import Job, recovery, load_session, fingerprint, write_record, repair_journal
from whisperdesk.queue_store import TaskQueue
from whisperdesk.ui import DEFAULTS


def test_queue_survives_restart_and_reorder(tmp_path):
    q = TaskQueue(tmp_path)
    a = q.add('a.wav', 40, DEFAULTS)
    b = q.add('b.wav', 50, DEFAULTS)
    q.move(b['id'], -1)
    b['status'] = 'running'
    q.save()
    restored = TaskQueue(tmp_path)
    assert restored.tasks[0]['id'] == b['id']
    assert restored.tasks[0]['status'] == 'interrupted'
    assert restored.next()['id'] == a['id']
    restored.remove(b['id'])
    assert len(TaskQueue(tmp_path).tasks) == 1


def test_checkpoint_transaction_resume_and_changed_source(tmp_path, monkeypatch):
    from whisperdesk.engine import transcribe_job
    import whisperdesk.audio
    import faster_whisper.vad
    source = tmp_path / 'record.wav'
    source.write_bytes(b'audio identity')
    session = tmp_path / 'state.jsonl'
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps', lambda a, *_: [{'start': 0, 'end': len(a)}])
    def fake_windows(path, start, end, cancel, **kwargs):
        for left in range(int(start), int(end), 8):
            yield np.ones(8*16000, dtype=np.float32), left, left, min(left+8, end)
    monkeypatch.setattr(whisperdesk.audio, 'windows', fake_windows)
    class Crash:
        def __init__(self, *a, **kw): self.calls = 0
        def transcribe(self, audio, **kwargs):
            self.calls += 1
            def segments():
                yield Obj(words=[Obj(start=1, end=2, word=' hello')])
                if self.calls == 2: raise RuntimeError('simulated inference failure mid-window')
            return segments(), None
    job = Job(str(source), 0, 24, language='en', session=str(session))
    output = queue.Queue()
    transcribe_job(job, output, threading.Event(), Crash)
    saved = recovery(session)
    assert saved['position'] == 8
    assert len(saved['rows']) == 1  # Partial second window was NOT committed.
    assert any(e[0] == 'error' for e in output.queue)
    with session.open('ab') as f: f.write(b'{"type":"checkp')
    class Good:
        def __init__(self, *a, **kw): pass
        def transcribe(self, audio, **kwargs):
            return iter([Obj(words=[Obj(start=1, end=2, word=' hello')])]), None
    job.resume = saved['position']
    transcribe_job(job, queue.Queue(), threading.Event(), Good)
    final = recovery(session)
    assert final['complete'] and final['position'] == 24
    assert [r['start'] for r in final['rows']] == [1, 9, 17]
    assert load_session(session) == final['rows']
    source.write_bytes(b'changed audio identity')
    errors = queue.Queue()
    transcribe_job(job, errors, threading.Event(), Good)
    assert any('змінився' in e[1] for e in errors.queue if e[0] == 'error')
    assert recovery(session)['rows'] == final['rows']


def test_cancel_keeps_last_committed_window(tmp_path, monkeypatch):
    from whisperdesk.engine import transcribe_job
    import whisperdesk.audio
    import faster_whisper.vad
    source = tmp_path/'source.wav'; source.write_bytes(b'data')
    stop = threading.Event()
    monkeypatch.setattr(faster_whisper.vad, 'get_speech_timestamps', lambda a, *_: [{'start': 0, 'end': len(a)}])
    def fake_windows(*args, **kwargs):
        for left in [0, 8]: yield np.ones(128000, dtype=np.float32), left, left, left+8
    monkeypatch.setattr(whisperdesk.audio, 'windows', fake_windows)
    class Model:
        def __init__(self, *args, **kwargs): self.calls=0
        def transcribe(self, *args, **kwargs):
            self.calls+=1
            def segments():
                if self.calls==2: stop.set()
                yield Obj(words=[Obj(start=1,end=2,word='test')])
            return segments(), None
    job=Job(str(source),0,16,language='en',session=str(tmp_path/'cancel.jsonl'))
    transcribe_job(job,queue.Queue(),stop,Model)
    result=recovery(job.session)
    assert result['position']==8 and not result['complete']
    assert len(result['rows'])==1


def test_checkpoint_owns_queue_progress_even_if_ui_crashes(tmp_path):
    q=TaskQueue(tmp_path)
    task=q.add('a.wav',20,DEFAULTS)
    task['status']='running'; q.save()
    Path(task['session']).parent.mkdir()
    with open(task['session'],'w',encoding='utf-8') as f:
        write_record(f,dict(type='job',version=2,start=0))
        write_record(f,dict(type='checkpoint',position=16,rows=[],language='uk'))
    restored=TaskQueue(tmp_path)
    assert restored.tasks[0]['position']==16
    assert restored.tasks[0]['status']=='interrupted'
