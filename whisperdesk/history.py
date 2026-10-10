"""Persistent local transcription history, independent from the active queue."""
from pathlib import Path
import calendar
from datetime import datetime
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
            'actual_device': actual_device or '',
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

    def delete(self, ident):
        before = len(self.records)
        self.records = [item for item in self.records if item.get('id') != ident]
        changed = len(self.records) != before
        if changed:
            self.save()
        return changed

    def clear(self):
        count = len(self.records)
        if count:
            self.records = []
            self.save()
        return count

    def purge_older_than_months(self, months, now=None):
        cutoff = cutoff_timestamp_months(months, now)
        kept = []
        removed = 0
        for item in self.records:
            try:
                created = float(item.get('created_at'))
            except (TypeError, ValueError):
                kept.append(item)
                continue
            if created < cutoff:
                removed += 1
            else:
                kept.append(item)
        if removed:
            self.records = kept
            self.save()
        return removed



def cutoff_timestamp_months(months, now=None):
    """Calendar-aware cutoff: the same local day/time N months ago."""
    months = max(1, int(months))
    current = datetime.fromtimestamp(time.time() if now is None else float(now))
    total = current.year * 12 + (current.month - 1) - months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(current.day, calendar.monthrange(year, month)[1])
    cutoff = current.replace(year=year, month=month, day=day)
    return cutoff.timestamp()
