"""Adaptive preflight ETA estimates calibrated from local completed jobs."""
from pathlib import Path
import json
import statistics

from .core import atomic_text, thread_count

CALIBRATION_VERSION = 2
CALIBRATION_FILE = 'eta-calibration.json'
MAX_SAMPLES = 12

# Conservative first-run real-time factors: processing seconds / audio second.
# They are intentionally wide until this PC has local completed-job samples.
MODEL_RTF = {
    'base': {'cpu': (0.35, 1.20), 'cuda': (0.03, 0.10)},
    'small': {'cpu': (0.65, 2.00), 'cuda': (0.05, 0.16)},
    'turbo': {'cpu': (1.10, 3.20), 'cuda': (0.06, 0.20)},
    'large-v3': {'cpu': (1.60, 4.50), 'cuda': (0.10, 0.32)},
}
OVERHEAD = {'cpu': (4.0, 12.0), 'cuda': (6.0, 18.0)}


def _cpu_scale(task):
    threads = thread_count(task.get('profile', 'eco'), int(task.get('threads') or 0))
    # Extra threads help, but the old 0.65 floor was too optimistic on real CPUs.
    return max(0.85, min(1.60, (4.0 / max(1, threads)) ** 0.30))


def _language_bucket(task):
    return 'mixed' if task.get('language') == 'mixed' else 'fixed'


def calibration_key(task, actual_device=None):
    model = task.get('model', '')
    device = task.get('device', 'cpu')
    profile = task.get('profile', 'eco')
    threads = thread_count(profile, int(task.get('threads') or 0))
    if device == 'auto':
        backend = actual_device or task.get('_eta_backend')
        device = f'auto>{backend}' if backend in ('cpu', 'cuda') else 'auto>unknown'
    return f'{model}|{device}|{profile}|{threads}|{_language_bucket(task)}'


def load_calibration(root):
    path = Path(root) / CALIBRATION_FILE
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        return {}
    if data.get('version') != CALIBRATION_VERSION or not isinstance(data.get('profiles'), dict):
        return {}
    return data['profiles']


def record_sample(root, task, audio_seconds, elapsed_seconds, actual_device=None):
    """Persist one completed local speed sample. Never stores file names or transcript text.

    Auto is calibrated against the backend that actually completed the job,
    so old CPU Auto runs cannot pollute CUDA ETA (and vice versa).
    """
    audio = float(audio_seconds)
    elapsed = float(elapsed_seconds)
    if audio < 60 or elapsed <= 0:
        return False
    rtf = elapsed / audio
    if not 0.01 <= rtf <= 20:
        return False

    profiles = load_calibration(root)
    key = calibration_key(task, actual_device)
    item = profiles.get(key) if isinstance(profiles.get(key), dict) else {}
    samples = item.get('samples') if isinstance(item.get('samples'), list) else []
    clean = []
    for value in samples:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if 0.01 <= value <= 20:
            clean.append(value)
    clean.append(rtf)
    clean = clean[-MAX_SAMPLES:]
    profiles[key] = {'samples': clean}
    payload = {'version': CALIBRATION_VERSION, 'profiles': profiles}
    atomic_text(Path(root) / CALIBRATION_FILE, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def migrate_calibration_from_history(root, records):
    """One-time v2 migration from local History.

    v1 mixed Auto samples cannot be separated reliably because the old file did
    not store actual backend or language mode. History does, so rebuild from
    completed local runs instead of guessing.
    """
    root = Path(root)
    path = root / CALIBRATION_FILE
    try:
        current = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError, TypeError):
        current = None
    if isinstance(current, dict) and current.get('version') == CALIBRATION_VERSION:
        return False

    profiles = {}
    for record in records or []:
        if not isinstance(record, dict) or record.get('status') != 'done':
            continue
        try:
            audio = float(record.get('audio_seconds') or 0)
            elapsed = float(record.get('elapsed_seconds') or 0)
        except (TypeError, ValueError):
            continue
        actual = record.get('actual_device')
        if audio < 60 or elapsed <= 0 or actual not in ('cpu', 'cuda'):
            continue
        rtf = elapsed / audio
        if not 0.01 <= rtf <= 20:
            continue
        task = {
            'model': record.get('model', ''),
            'device': record.get('requested_device', 'cpu'),
            'profile': record.get('profile', 'eco'),
            'threads': int(record.get('threads') or 0),
            'language': record.get('language', 'uk'),
        }
        key = calibration_key(task, actual)
        item = profiles.setdefault(key, {'samples': []})
        item['samples'].append(rtf)
        item['samples'] = item['samples'][-MAX_SAMPLES:]

    payload = {'version': CALIBRATION_VERSION, 'profiles': profiles}
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2))
    return True


def _calibrated_range(samples):
    clean = sorted(float(x) for x in samples if 0.01 <= float(x) <= 20)
    if not clean:
        return None
    center = statistics.median(clean)
    count = len(clean)
    if count == 1:
        low, high = center * 0.75, center * 1.30
        confidence = 'локальна · 1 замір'
    elif count <= 3:
        low, high = min(clean) * 0.85, max(clean) * 1.15
        confidence = f'локальна · {count} заміри'
    else:
        # Once several measurements exist, one throttled/background-load run
        # must not keep ETA permanently huge. Median absolute deviation gives a
        # robust spread while retaining at least ±18% natural runtime variance.
        deviations = [abs(value - center) for value in clean]
        mad = statistics.median(deviations)
        spread = max(center * 0.18, mad * 3.0)
        low = max(center * 0.50, center - spread)
        high = center + spread
        confidence = f'локальна · {count} замірів'
    return low, high, confidence


def estimate_task(task, remaining_seconds, calibration=None):
    model, device = task.get('model'), task.get('device', 'cpu')
    if model not in MODEL_RTF or device not in ('cpu', 'auto', 'cuda'):
        return None

    remaining = max(0.0, float(remaining_seconds))
    local = None
    if calibration:
        item = calibration.get(calibration_key(task))
        if isinstance(item, dict):
            local = _calibrated_range(item.get('samples') or [])

    if local:
        low, high, confidence = local
        overhead = OVERHEAD['cuda'] if device == 'cuda' else OVERHEAD['cpu']
    elif device == 'cpu':
        low, high = MODEL_RTF[model]['cpu']
        scale = _cpu_scale(task)
        low, high = low * scale, high * scale
        overhead, confidence = OVERHEAD['cpu'], 'початкова'
    elif device == 'cuda':
        low, high = MODEL_RTF[model]['cuda']
        overhead, confidence = OVERHEAD['cuda'], 'початкова'
    else:
        low = MODEL_RTF[model]['cuda'][0]
        high = MODEL_RTF[model]['cpu'][1] * _cpu_scale(task)
        overhead, confidence = (OVERHEAD['cpu'][0], OVERHEAD['cuda'][1]), 'низька'

    return remaining * low + overhead[0], remaining * high + overhead[1], confidence


def format_eta(seconds):
    seconds = max(0, int(round(float(seconds))))
    if seconds < 60:
        return f'≈{max(10, int(round(seconds / 5.0) * 5))} с'
    minutes = int(round(seconds / 60.0))
    if minutes < 60:
        return f'≈{minutes} хв'
    hours, minutes = divmod(minutes, 60)
    return f'≈{hours} год {minutes:02d} хв'
