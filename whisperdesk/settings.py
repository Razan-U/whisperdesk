"""Validate persisted UI settings before they reach Qt widgets."""
from pathlib import Path
import json
import time

from .core import MODELS


def load_settings(root, defaults):
    path = Path(root) / 'settings.json'
    settings = dict(defaults)
    warning = None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            raise ValueError('Очікується JSON-об’єкт налаштувань.')
        settings.update(data)
    except FileNotFoundError:
        return settings, None
    except (OSError, ValueError, TypeError) as exc:
        if path.exists():
            path.rename(path.with_name(f'settings-damaged-{time.time_ns()}.json'))
        return dict(defaults), f'Налаштування відновлено до початкових значень: {exc}'

    def integer(key, low, high):
        try:
            settings[key] = max(low, min(high, int(settings.get(key, defaults[key]))))
        except (TypeError, ValueError, OverflowError):
            settings[key] = defaults[key]

    integer('accent', 0, 7)
    integer('threads', 0, 256)
    integer('history_retention_months', 1, 60)
    for key, allowed in {
        'language': ('uk', 'en', 'mixed'), 'model': tuple(MODELS),
        'device': ('cpu', 'auto', 'cuda'), 'profile': ('eco', 'fast'),
        'update_channel': ('stable', 'test'),
    }.items():
        if settings.get(key) not in allowed:
            settings[key] = defaults[key]
    for key in ('night', 'history_auto_cleanup'):
        if not isinstance(settings.get(key), bool):
            settings[key] = defaults[key]
    if any(settings.get(k) != data.get(k) for k in data if k in defaults):
        warning = 'Некоректні значення налаштувань замінено безпечними початковими значеннями.'
        path.rename(path.with_name(f'settings-damaged-{time.time_ns()}.json'))
    return settings, warning
