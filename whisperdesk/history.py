"""Persistent local transcription history, independent from the active queue."""
from pathlib import Path
import json
import time
import uuid

from .core import atomic_text

HISTORY_VERSION = 1
MAX_HISTORY_ITEMS = 1000


class HistoryStore:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'history.json'
        self.records = []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding='utf-8'))
            if data.get('version') != HISTORY_VERSION or not isinstance(data.get('records'), list):
                raise ValueError('Непідтримуваний формат історії.')
            self.records = [item for item in data['records'] if isinstance(item, dict)]

    def save(self):
        atomic_text(
            self.path,
            json.dumps(
                {'version': HISTORY_VERSION, 'records': self.records[-MAX_HISTORY_ITEMS:]},
                ensure_ascii=False,
                indent=2,
            ),
        )

    def add_run(self, task, status, elapsed_seconds, actual_device=None, message=''):
        now = time.time()
        session = str(task.get('session') or '')
        txt_path = str(Path(session).with_suffix('.txt')) if session else ''
        record = {
            'id': uuid.uuid4().hex,
            'task_id': task.get('id', ''),
            'created_at': now,
            'source': str(task.get('source') or ''),
            'duration': float(task.get('duration') or 0),
            'start': float(task.get('start') or 0),
            'end': float(task.get('end') or task.get('duration') or 0),
            'audio_seconds': max(0.0, float(task.get('end') or 0) - float(task.get('start') or 0)),
            'status': str(status or 'error'),
            'message': str(message or ''),
            'elapsed_seconds': max(0.0, float(elapsed_seconds or 0)),
            'language': task.get('language', 'uk'),
            'model': task.get('model', 'base'),
            'requested_device': task.get('device', 'cpu'),
            'actual_device': actual_device or task.get('device', 'cpu'),
            'profile': task.get('profile', 'eco'),
            'threads': int(task.get('threads') or 0),
            'session': session,
            'result_txt': txt_path,
        }
        self.records.append(record)
        self.records = self.records[-MAX_HISTORY_ITEMS:]
        self.save()
        return record

    def newest(self):
        return list(reversed(self.records))

    def get(self, ident):
        return next((item for item in self.records if item.get('id') == ident), None)
