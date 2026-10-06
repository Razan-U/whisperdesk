"""Heuristic processing-time estimates before local hardware/history calibration."""
from .core import thread_count

MODEL_RTF = {
    'base': {'cpu': (0.18, 0.50), 'cuda': (0.03, 0.09)},
    'small': {'cpu': (0.35, 0.90), 'cuda': (0.05, 0.14)},
    'turbo': {'cpu': (0.45, 1.15), 'cuda': (0.06, 0.18)},
    'large-v3': {'cpu': (0.80, 2.00), 'cuda': (0.10, 0.28)},
}
OVERHEAD = {'cpu': (4.0, 12.0), 'cuda': (6.0, 18.0)}


def _cpu_scale(task):
    threads = thread_count(task.get('profile', 'eco'), int(task.get('threads') or 0))
    return max(0.65, min(2.0, (4.0 / max(1, threads)) ** 0.45))


def estimate_task(task, remaining_seconds):
    model, device = task.get('model'), task.get('device', 'cpu')
    if model not in MODEL_RTF or device not in ('cpu', 'auto', 'cuda'):
        return None
    remaining = max(0.0, float(remaining_seconds))
    if device == 'cpu':
        low, high = MODEL_RTF[model]['cpu']
        scale = _cpu_scale(task)
        low, high = low * scale, high * scale
        overhead, confidence = OVERHEAD['cpu'], 'середня'
    elif device == 'cuda':
        low, high = MODEL_RTF[model]['cuda']
        overhead, confidence = OVERHEAD['cuda'], 'середня'
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
