"""Crash marker helpers for recovering the active transcription after app/OS failure."""
from pathlib import Path
import json
import time
import uuid

from .core import atomic_text, recovery

MARKER_FILE = 'active-run.json'
MARKER_VERSION = 1


def marker_path(root):
    return Path(root) / MARKER_FILE


def load_marker(root):
    path = marker_path(root)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return None
    if data.get('version') != MARKER_VERSION:
        return None
    return data


def write_marker(root, task, app_version=''):
    payload = {
        'version': MARKER_VERSION,
        'app_version': str(app_version or ''),
        'task_id': task.get('id', ''),
        'source': str(task.get('source') or ''),
        'duration': float(task.get('duration') or 0),
        'start': float(task.get('start') or 0),
        'end': float(task.get('end') or task.get('duration') or 0),
        'position': float(task.get('position') or task.get('start') or 0),
        'language': task.get('language', 'uk'),
        'model': task.get('model', 'base'),
        'device': task.get('device', 'cpu'),
        'profile': task.get('profile', 'eco'),
        'threads': int(task.get('threads') or 0),
        'session': str(task.get('session') or ''),
        'started_at': time.time(),
        'updated_at': time.time(),
    }
    atomic_text(marker_path(root), json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def update_checkpoint(root, task_id, position):
    data = load_marker(root)
    if not data or data.get('task_id') != task_id:
        return False
    data['position'] = float(position)
    data['updated_at'] = time.time()
    atomic_text(marker_path(root), json.dumps(data, ensure_ascii=False, indent=2))
    return True


def clear_marker(root):
    try:
        marker_path(root).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def restore_missing_task(queue, data):
    """Recreate a queue row from a surviving marker + session journal."""
    if not data:
        return None
    ident = str(data.get('task_id') or '')
    existing = queue.get(ident) if ident else None
    if existing:
        return existing

    session = Path(data.get('session') or '')
    if not session.is_file():
        return None
    try:
        state = recovery(session)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    header = state.get('job')
    if not header:
        return None

    source = str(header.get('source') or data.get('source') or '')
    if not source:
        return None
    start = float(header.get('start', data.get('start', 0)) or 0)
    end = float(header.get('end', data.get('end', 0)) or 0)
    duration = max(float(data.get('duration') or 0), end)
    position = state.get('position')
    position = float(position if position is not None else start)

    task = {
        'id': ident or uuid.uuid4().hex,
        'source': source,
        'duration': duration,
        'start': start,
        'end': end,
        'position': position,
        'status': 'done' if state.get('complete') else 'interrupted',
        'error': '',
        'session': str(session),
        'language': header.get('language', data.get('language', 'uk')),
        'model': header.get('model', data.get('model', 'base')),
        'device': data.get('device', 'cpu'),
        'profile': data.get('profile', 'eco'),
        'threads': int(data.get('threads') or 0),
    }
    queue.tasks.append(task)
    queue.save()
    return task
