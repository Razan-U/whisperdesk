"""Pre-run validation and summary for queued transcription jobs."""
from collections import Counter
from pathlib import Path
import os
import shutil

from .core import MODELS, validate_range, recovery, fingerprint
from .engine import model_ready
from .eta import estimate_task, format_eta, load_calibration
from .hardware import analyze_hardware, hardware_summary, hardware_messages
from .recommendation import recommend, recommendation_text

MODEL_BYTES = {
    'base': 150 * 1024**2,
    'small': 500 * 1024**2,
    'turbo': 1700 * 1024**2,
    'large-v3': 3100 * 1024**2,
}
CUDA_TEMP_BYTES = 3 * 1024**3


def _format_bytes(value):
    if value is None:
        return 'невідомо'
    value = float(value)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ', 'ТБ'):
        if value < 1024 or unit == 'ТБ':
            return f'{value:.0f} {unit}' if unit != 'ГБ' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} ТБ'


def _cuda_runtime_ready(root):
    """Cheap readiness check only; real CUDA compute is verified by the worker."""
    if os.name != 'nt':
        return True
    try:
        from .gpu import BUNDLE, REQUIRED
        target = Path(root) / 'gpu' / BUNDLE
        return ((target / '.ready').is_file()
                and all(any(target.rglob(name)) for name in REQUIRED))
    except Exception:
        return False


def analyze_queue(tasks, model_folder, root, ready_checker=model_ready):
    """Return a dependency-light preflight report without starting inference."""
    root = Path(root)
    model_folder = Path(model_folder)
    candidates = [t for t in tasks if t.get('status') in ('pending', 'interrupted', 'error')]
    report = {
        'count': len(candidates),
        'seconds': 0.0,
        'blockers': [],
        'warnings': [],
        'notes': [],
        'missing_models': [],
        'models': Counter(),
        'devices': Counter(),
        'free_bytes': None,
        'eta_min_seconds': 0.0,
        'eta_max_seconds': 0.0,
        'eta_confidence': 'початкова',
        'hardware': None,
        'recommendation': None,
    }
    if not candidates:
        report['blockers'].append('У черзі немає файлів, які потрібно запускати.')
        return report

    missing = set()
    eta_confidences = []
    calibration = load_calibration(root)
    try:
        report['hardware'] = analyze_hardware()
        hw_warnings, hw_notes = hardware_messages(report['hardware'])
        report['warnings'].extend(hw_warnings)
        report['notes'].extend(hw_notes)
    except Exception as exc:
        report['warnings'].append(f'Не вдалося проаналізувати залізо: {exc}')
    for task in candidates:
        source = Path(task.get('source', ''))
        name = source.name or 'Невідомий файл'
        if not source.is_file():
            report['blockers'].append(f'{name}: вихідний аудіофайл не знайдено.')

        try:
            start = float(task.get('start', 0))
            end = float(task.get('end', 0))
            duration = float(task.get('duration', 0))
            validate_range(start, end, duration)
            resume_position = max(start, min(float(task.get('position', start) or start), end))
        except (TypeError, ValueError) as exc:
            resume_position = None
            report['blockers'].append(f'{name}: некоректний діапазон — {exc}')

        model = task.get('model')
        if model not in MODELS:
            report['blockers'].append(f'{name}: невідома модель {model!r}.')
        else:
            report['models'][model] += 1
            if not ready_checker(model_folder / model):
                missing.add(model)

        device = task.get('device', 'cpu')
        if device not in ('cpu', 'auto', 'cuda'):
            report['blockers'].append(f'{name}: невідомий режим обчислень {device!r}.')
        else:
            report['devices'][device] += 1

        session = Path(task.get('session', root / 'sessions' / 'unknown.jsonl'))
        parent = session.parent
        if not parent.exists():
            report['blockers'].append(f'{name}: папка автозбереження не існує: {parent}')
        elif not os.access(parent, os.W_OK):
            report['blockers'].append(f'{name}: немає прав запису в папку автозбереження: {parent}')

        if session.is_file():
            try:
                saved = recovery(session)
                header = saved.get('job')
                if not header and task.get('status') in ('interrupted', 'error'):
                    report['blockers'].append(
                        f'{name}: збережений сеанс не містить даних для безпечного продовження.'
                    )
                elif header:
                    if saved.get('position') is not None and resume_position is not None:
                        resume_position = max(start, min(float(saved['position']), end))
                    if source.is_file() and header.get('fingerprint') != fingerprint(source):
                        report['blockers'].append(
                            f'{name}: аудіофайл змінився після створення сеансу. '
                            'Додайте його як нове завдання.'
                        )
                    mismatches = [
                        key for key in ('source', 'start', 'end', 'model', 'language')
                        if key in header and header.get(key) != task.get(key)
                    ]
                    if mismatches:
                        report['blockers'].append(
                            f'{name}: параметри не відповідають збереженому сеансу '
                            f'({", ".join(mismatches)}).'
                        )
            except (OSError, ValueError, KeyError, TypeError) as exc:
                report['blockers'].append(f'{name}: не вдалося перевірити збережений сеанс — {exc}')

        if resume_position is not None:
            remaining = max(0.0, end - resume_position)
            report['seconds'] += remaining
            eta_task = task
            if task.get('device') == 'auto':
                eta_task = dict(task)
                hardware = report.get('hardware') or {}
                if hardware.get('cuda_available'):
                    eta_task['_eta_backend'] = 'cuda'
                elif report.get('hardware') is not None:
                    eta_task['_eta_backend'] = 'cpu'
            estimate = estimate_task(eta_task, remaining, calibration)
            if estimate:
                low, high, confidence = estimate
                report['eta_min_seconds'] += low
                report['eta_max_seconds'] += high
                eta_confidences.append(confidence)

    if 'низька' in eta_confidences:
        report['eta_confidence'] = 'низька'
    elif any(value == 'початкова' for value in eta_confidences):
        report['eta_confidence'] = 'початкова'
    elif eta_confidences:
        report['eta_confidence'] = eta_confidences[0] if len(set(eta_confidences)) == 1 else 'локальна'

    report['missing_models'] = sorted(missing)
    if missing:
        names = ', '.join(MODELS[m][0] for m in sorted(missing))
        report['notes'].append(f'Перед обробкою буде завантажено моделі: {names}.')

    try:
        report['free_bytes'] = shutil.disk_usage(root).free
    except OSError as exc:
        report['warnings'].append(f'Не вдалося перевірити вільне місце: {exc}')

    missing_bytes = sum(MODEL_BYTES.get(m, 0) for m in missing)
    if report['free_bytes'] is not None and missing_bytes and report['free_bytes'] < missing_bytes * 1.15:
        report['warnings'].append(
            f'Вільного місця може не вистачити для моделей: доступно {_format_bytes(report["free_bytes"])}, '
            f'орієнтовно потрібно щонайменше {_format_bytes(missing_bytes)}.'
        )

    cuda_ready = _cuda_runtime_ready(root)
    explicit_cuda = report['devices'].get('cuda', 0) > 0
    auto_cuda = report['devices'].get('auto', 0) > 0
    if os.name == 'nt' and (explicit_cuda or auto_cuda) and not cuda_ready:
        report['notes'].append(
            'Компоненти NVIDIA ще не підготовлені. Якщо NVIDIA буде використана, '
            'перший запуск завантажить приблизно 1,1 ГБ компонентів.'
        )
        free = report['free_bytes']
        if explicit_cuda and free is not None and free < CUDA_TEMP_BYTES:
            report['blockers'].append(
                f'Для першого запуску NVIDIA CUDA потрібно щонайменше 3 ГБ вільного місця; '
                f'зараз доступно {_format_bytes(free)}.'
            )
        elif auto_cuda and free is not None and free < CUDA_TEMP_BYTES:
            report['warnings'].append(
                'Для встановлення компонентів NVIDIA менше 3 ГБ вільного місця. '
                'У режимі «Авто» застосунок може перейти на CPU.'
            )

    if report.get('hardware'):
        profiles = [task.get('profile', 'eco') for task in candidates]
        profile = profiles[0] if profiles and len(set(profiles)) == 1 else 'eco'
        report['recommendation'] = recommend(report['hardware'], report['seconds'], profile)

    if report['devices'].get('auto', 0) and not (
            report.get('hardware') and report['hardware'].get('cuda_available')):
        report['notes'].append(
            'Режим «Авто»: доступна NVIDIA CUDA не виявлена, тому очікується робота на CPU.'
        )
    if report['missing_models']:
        report['notes'].append('Час завантаження відсутніх моделей не входить у ETA.')

    return report


def report_text(report):
    """Human-readable Ukrainian summary used by the preflight dialog."""
    lines = [
        f'Файлів до запуску: {report["count"]}',
        f'Аудіо до обробки: {_clock(report["seconds"])}',
    ]
    if report.get('eta_max_seconds', 0) > 0:
        lines.append(
            f'Орієнтовний час обробки: {format_eta(report["eta_min_seconds"])} – '
            f'{format_eta(report["eta_max_seconds"])} '
            f'(точність: {report.get("eta_confidence", "середня")})'
        )
    if report['models']:
        lines.append('Моделі: ' + ', '.join(f'{MODELS[key][0]} × {count}' for key, count in report['models'].items()))
    if report['devices']:
        names = {'cpu': 'CPU', 'auto': 'Авто', 'cuda': 'NVIDIA CUDA'}
        lines.append('Режими: ' + ', '.join(f'{names.get(key, key)} × {count}' for key, count in report['devices'].items()))
    if report['blockers']:
        lines += ['', 'ПОТРІБНО ВИПРАВИТИ:']
        lines += [f'• {item}' for item in report['blockers']]
    if report['warnings']:
        lines += ['', 'ПОПЕРЕДЖЕННЯ:']
        lines += [f'• {item}' for item in report['warnings']]
    if report['notes']:
        lines += ['', 'ПЕРЕД ЗАПУСКОМ:']
        lines += [f'• {item}' for item in report['notes']]
    if not report['blockers']:
        lines += ['', '✓ Критичних проблем не виявлено.']
    return '\n'.join(lines)


def _clock(seconds):
    seconds = max(0, int(seconds))
    return f'{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}'
