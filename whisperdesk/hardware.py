"""Dependency-light local hardware analysis for preflight diagnostics."""
from functools import lru_cache
import ctypes
import json
import os
import platform
import shutil
import subprocess


def _total_memory_bytes():
    if os.name == 'nt':
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ('dwLength', ctypes.c_ulong),
                ('dwMemoryLoad', ctypes.c_ulong),
                ('ullTotalPhys', ctypes.c_ulonglong),
                ('ullAvailPhys', ctypes.c_ulonglong),
                ('ullTotalPageFile', ctypes.c_ulonglong),
                ('ullAvailPageFile', ctypes.c_ulonglong),
                ('ullTotalVirtual', ctypes.c_ulonglong),
                ('ullAvailVirtual', ctypes.c_ulonglong),
                ('ullAvailExtendedVirtual', ctypes.c_ulonglong),
            ]
        status = MEMORYSTATUSEX()
        status.dwLength = ctypes.sizeof(status)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return int(status.ullTotalPhys)
    try:
        pages = os.sysconf('SC_PHYS_PAGES')
        page_size = os.sysconf('SC_PAGE_SIZE')
        return int(pages * page_size)
    except (AttributeError, OSError, ValueError):
        return None


def _parse_nvidia_smi(text):
    gpus = []
    for line in str(text or '').splitlines():
        parts = [part.strip() for part in line.split(',')]
        if len(parts) < 2:
            continue
        name = parts[0]
        try:
            memory_mb = int(float(parts[1]))
        except (TypeError, ValueError):
            memory_mb = None
        driver = parts[2] if len(parts) > 2 else ''
        gpus.append({
            'name': name,
            'vram_bytes': memory_mb * 1024**2 if memory_mb is not None else None,
            'driver': driver,
        })
    return gpus


def _nvidia_smi():
    candidates = [shutil.which('nvidia-smi')]
    if os.name == 'nt':
        candidates += [
            os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                         'NVIDIA Corporation', 'NVSMI', 'nvidia-smi.exe'),
            os.path.join(os.environ.get('WINDIR', r'C:\Windows'),
                         'System32', 'nvidia-smi.exe'),
        ]
    exe = next((path for path in candidates if path and os.path.isfile(path)), None)
    if not exe:
        return []
    creationflags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
    try:
        result = subprocess.run(
            [exe, '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits'],
            capture_output=True,
            text=True,
            timeout=3,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode:
        return []
    return _parse_nvidia_smi(result.stdout)


def _windows_video_adapters():
    if os.name != 'nt':
        return []
    command = [
        'powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
        'Get-CimInstance Win32_VideoController | '
        'Select-Object Name,AdapterRAM | ConvertTo-Json -Compress'
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=4,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
        if result.returncode or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        return []
    if isinstance(data, dict):
        data = [data]
    result_items = []
    for item in data if isinstance(data, list) else []:
        name = str(item.get('Name') or '').strip()
        if not name:
            continue
        try:
            ram = int(item.get('AdapterRAM') or 0) or None
        except (TypeError, ValueError):
            ram = None
        result_items.append({'name': name, 'vram_bytes': ram})
    return result_items


def _cuda_count():
    try:
        import ctranslate2
        return max(0, int(ctranslate2.get_cuda_device_count()))
    except Exception:
        return 0


@lru_cache(maxsize=1)
def analyze_hardware():
    """Return a safe local snapshot. Any probe failure degrades to unknown."""
    logical = os.cpu_count() or 1
    ram = _total_memory_bytes()
    gpus = _nvidia_smi()
    adapters = _windows_video_adapters() if os.name == 'nt' else []
    cuda_count = _cuda_count()

    if not gpus:
        # WMI's AdapterRAM can be truncated on some modern GPUs, so use it only
        # as descriptive fallback when nvidia-smi is unavailable.
        gpus = [item for item in adapters if 'nvidia' in item['name'].lower()]

    return {
        'cpu_logical': logical,
        'ram_bytes': ram,
        'architecture': platform.machine() or 'unknown',
        'gpus': gpus,
        'cuda_count': cuda_count,
        'cuda_available': cuda_count > 0,
        'video_adapters': adapters,
    }


def _gb(value):
    if value is None:
        return 'невідомо'
    return f'{float(value) / 1024**3:.1f} ГБ'


def hardware_summary(info):
    cpu = f'CPU: {int(info.get("cpu_logical") or 1)} потоків'
    ram = f'RAM: {_gb(info.get("ram_bytes"))}'
    gpus = info.get('gpus') or []
    if gpus:
        first = gpus[0]
        gpu = first.get('name') or 'NVIDIA'
        if first.get('vram_bytes'):
            gpu += f' · VRAM {_gb(first["vram_bytes"])}'
        if len(gpus) > 1:
            gpu += f' · GPU × {len(gpus)}'
        gpu += ' · CUDA доступна' if info.get('cuda_available') else ' · CUDA недоступна'
    elif info.get('cuda_available'):
        gpu = f'NVIDIA CUDA: доступна · пристроїв {int(info.get("cuda_count") or 1)}'
    else:
        gpu = 'NVIDIA CUDA: не виявлена'
    return f'{cpu} · {ram} · {gpu}'


def hardware_messages(info):
    warnings, notes = [], []
    ram = info.get('ram_bytes')
    if ram is not None and ram < 8 * 1024**3:
        warnings.append('Оперативної пам’яті менше 8 ГБ. Для довгих файлів і великих моделей можливе уповільнення.')
    gpus = info.get('gpus') or []
    if gpus and not info.get('cuda_available'):
        notes.append('NVIDIA знайдена, але CUDA недоступна. Авто-режим використає CPU; варто перевірити драйвер NVIDIA.')
    return warnings, notes
