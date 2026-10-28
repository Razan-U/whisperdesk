import os

from whisperdesk import hardware


def test_parse_nvidia_smi_rows():
    rows = hardware._parse_nvidia_smi(
        'NVIDIA GeForce RTX 3060, 12288, 555.99\n'
        'NVIDIA RTX A2000, 6144, 551.23\n'
    )
    assert rows[0]['name'] == 'NVIDIA GeForce RTX 3060'
    assert rows[0]['vram_bytes'] == 12288 * 1024**2
    assert rows[1]['driver'] == '551.23'


def test_hardware_summary_cuda_gpu():
    text = hardware.hardware_summary({
        'cpu_logical': 12,
        'ram_bytes': 32 * 1024**3,
        'gpus': [{'name': 'NVIDIA GeForce RTX 3060', 'vram_bytes': 12 * 1024**3}],
        'cuda_count': 1,
        'cuda_available': True,
    })
    assert 'CPU: 12 потоків' in text
    assert 'RAM: 32.0 ГБ' in text
    assert 'RTX 3060' in text
    assert 'CUDA доступна' in text


def test_hardware_messages_low_ram_and_cuda_missing():
    warnings, notes = hardware.hardware_messages({
        'ram_bytes': 6 * 1024**3,
        'gpus': [{'name': 'NVIDIA GeForce GTX 1650', 'vram_bytes': 4 * 1024**3}],
        'cuda_available': False,
        'cuda_count': 0,
    })
    assert any('менше 8 ГБ' in item for item in warnings)
    assert any('CUDA недоступна' in item for item in notes)


def test_analyze_hardware_degrades_safely(monkeypatch):
    hardware.analyze_hardware.cache_clear()
    monkeypatch.setattr(hardware, '_total_memory_bytes', lambda: 16 * 1024**3)
    monkeypatch.setattr(hardware, '_nvidia_smi', lambda: [])
    monkeypatch.setattr(hardware, '_windows_video_adapters', lambda: [])
    monkeypatch.setattr(hardware, '_cuda_count', lambda: 0)

    info = hardware.analyze_hardware()
    assert info['cpu_logical'] >= 1
    assert info['ram_bytes'] == 16 * 1024**3
    assert info['cuda_available'] is False
    hardware.analyze_hardware.cache_clear()
