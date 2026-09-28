"""Small, dependency-light data and persistence layer."""
from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import tempfile

MODELS = {
    'base': ('Швидка · base', '≈150 МБ', 'Для слабших ноутбуків і чернеток'),
    'small': ('Збалансована · small', '≈500 МБ', 'Початковий вибір для 16 ГБ RAM'),
    'large-v3': ('Точніша · large-v3', '≈3,1 ГБ', 'Повільна на CPU; потребує більше пам’яті'),
    'turbo': ('Прискорена велика · turbo', '≈1,7 ГБ', 'Для потужніших ПК, особливо NVIDIA'),
}


def data_dir():
    override = os.environ.get('WHISPERDESK_DATA')
    config = Path(__file__).resolve().parent.parent / 'install.json'
    installed_path = config.with_name('data-path.txt')
    if override:
        p = Path(override)
    elif installed_path.exists():
        p = Path(installed_path.read_text(encoding='utf-16').strip())
    elif config.exists():
        settings = json.loads(config.read_text(encoding='utf-8-sig'))
        p = Path(settings['data_dir'])
    elif os.name == 'nt' and Path('D:/').exists():
        p = Path('D:/WhisperDesk/Data')
    else:
        p = Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'WhisperDesk'
    p.mkdir(parents=True, exist_ok=True)
    return p


def parse_time(value):
    parts = value.strip().split(':')
    if len(parts) != 3 or not all(re.fullmatch(r'\d+', x) for x in parts):
        raise ValueError('Введіть час у форматі год:хв:сек, наприклад 00:12:30.')
    h, m, s = map(int, parts)
    if m >= 60 or s >= 60:
        raise ValueError('Хвилини й секунди мають бути від 00 до 59.')
    return h * 3600 + m * 60 + s


def clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'


def validate_range(start, end, duration):
    if not 0 <= start < end <= duration + .05:
        raise ValueError('Потрібно: 0 ≤ початок < кінець ≤ тривалість файлу.')


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix='.tmp-')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def transcript_text(rows, timestamps=False):
    return '\n'.join(
        (f'[{clock(r["start"])} — {clock(r["end"])}] ' if timestamps else '') + r['text'].strip()
        for r in rows if r['text'].strip()
    )


def load_session(path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            try:
                item = json.loads(line)
                if item.get('type') == 'segment':
                    rows.append(item['row'])
                elif item.get('type') == 'checkpoint':
                    rows.extend(item['rows'])
            except (ValueError, KeyError):
                continue  # A forcibly stopped worker may leave an incomplete final line.
    return rows


def select_language(probs, previous=None):
    allowed = {k: float(v) for k, v in probs if k in ('uk', 'en', 'ru')}
    if not allowed:
        return previous or 'uk', 0.0
    best = max(allowed, key=allowed.get)
    total = sum(allowed.values()) or 1.0
    confidence = allowed[best] / total
    if previous in allowed and confidence < .58:
        best = previous
    return best, allowed[best] / total


def owned_words(words, offset, start, end):
    """Half-open ownership of overlap words prevents duplicate boundary tokens."""
    return [w for w in words if start <= offset + (w.start + w.end) / 2 < end]


@dataclass
class Job:
    source: str
    start: float
    end: float
    model: str = 'base'
    language: str = 'uk'
    device: str = 'cpu'
    profile: str = 'eco'
    threads: int = 0
    models_dir: str = ''
    session: str = ''
    resume: float | None = None
    previous_language: str | None = None


def fingerprint(path):
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns}


def recovery(path):
    """Only complete, committed windows can advance the resume cursor."""
    state = {'job': None, 'rows': [], 'position': None, 'language': None, 'complete': False}
    with open(path, encoding='utf-8') as stream:
        for line in stream:
            try:
                item = json.loads(line)
                if item.get('type') == 'job' and 'version' in item:
                    state['job'] = item
                    if state['position'] is None:
                        state['position'] = item['start']
                elif item.get('type') == 'checkpoint':
                    state['rows'].extend(item['rows'])
                    state['position'] = item['position']
                    state['language'] = item.get('language')
                elif item.get('type') == 'complete':
                    state['complete'] = True
            except (ValueError, KeyError):
                break
    return state


def repair_journal(path):
    """Remove an incomplete tail before appending new transactions."""
    p = Path(path)
    if not p.exists():
        return
    good = 0
    with p.open('rb') as f:
        for line in f:
            try:
                json.loads(line)
            except ValueError:
                break
            if not line.endswith(b'\n'):
                break
            good = f.tell()
    with p.open('r+b') as f:
        f.truncate(good)


def write_record(stream, item):
    stream.write(json.dumps(item, ensure_ascii=False) + '\n')
    stream.flush()
    os.fsync(stream.fileno())


def thread_count(profile, requested=0):
    available = os.cpu_count() or 2
    if requested:
        return max(1, min(requested, available))
    return max(1, min(4, available // 2)) if profile == 'eco' else max(1, available - 1)
