"""Conservative model/device recommendations based on local hardware and workload."""

from .core import MODELS

GB = 1024 ** 3


def _largest_vram(info):
    values = []
    for gpu in info.get('gpus') or []:
        try:
            value = int(gpu.get('vram_bytes') or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            values.append(value)
    return max(values) if values else None


def recommend(hardware, audio_seconds, profile='eco'):
    """Return a non-binding recommendation for one queue run.

    The rules are intentionally conservative. A recommendation should reduce
    avoidable slowdowns or OOM risk, never override the user's explicit choice.
    """
    info = hardware or {}
    seconds = max(0.0, float(audio_seconds or 0))
    hours = seconds / 3600.0
    cpu_threads = max(1, int(info.get('cpu_logical') or 1))
    ram = info.get('ram_bytes')
    try:
        ram = int(ram) if ram is not None else None
    except (TypeError, ValueError):
        ram = None
    vram = _largest_vram(info)
    cuda = bool(info.get('cuda_available'))

    if cuda:
        device = 'auto'
        # Turbo is the default NVIDIA recommendation: much better throughput
        # than large-v3 while preserving strong accuracy for general use.
        if vram is not None and vram < 4 * GB:
            model = 'small'
            reason = 'CUDA доступна, але VRAM менше 4 ГБ — small безпечніша для GPU.'
        elif ram is not None and ram < 8 * GB:
            model = 'small'
            reason = 'CUDA доступна, але системної RAM менше 8 ГБ — small знижує ризик нестачі пам’яті.'
        elif hours >= 4:
            model = 'small'
            reason = 'Для дуже довгої черги small дає кращий баланс швидкості та стабільності.'
        else:
            model = 'turbo'
            reason = 'CUDA доступна — turbo є рекомендованим балансом швидкості та якості; Auto збереже fallback на CPU.'
    else:
        device = 'cpu'
        weak_ram = ram is not None and ram < 12 * GB
        weak_cpu = cpu_threads <= 6
        long_audio = hours >= 1.5
        if weak_ram or weak_cpu or long_audio:
            model = 'base'
            parts = []
            if weak_cpu:
                parts.append(f'{cpu_threads} CPU-потоків')
            if weak_ram:
                parts.append('менше 12 ГБ RAM')
            if long_audio:
                parts.append('довге аудіо')
            reason = 'На CPU base є найнадійнішим швидким вибором' + (f' ({", ".join(parts)})' if parts else '') + '.'
        else:
            model = 'small'
            reason = 'Без CUDA small дає кращий баланс якості та часу на цьому CPU; turbo/large-v3 можуть бути значно повільнішими.'

    if profile == 'fast' and device == 'cpu' and model == 'base' and cpu_threads >= 8 and (ram is None or ram >= 12 * GB) and hours < 1.5:
        model = 'small'
        reason = 'Профіль «Більше потоків» і достатні ресурси дозволяють рекомендувати small на CPU.'

    return {
        'model': model,
        'device': device,
        'reason': reason,
    }


def recommendation_text(item):
    if not item:
        return ''
    device_names = {'cpu': 'CPU', 'auto': 'Авто', 'cuda': 'NVIDIA CUDA'}
    model = MODELS.get(item.get('model'), (item.get('model', 'невідома модель'), '', ''))[0]
    device = device_names.get(item.get('device'), item.get('device', 'невідомий режим'))
    reason = item.get('reason', '').strip()
    return f'{model} + {device}' + (f' — {reason}' if reason else '')
