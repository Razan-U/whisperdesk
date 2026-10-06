import hashlib
import io
import os

import pytest

from whisperdesk import updater


def release(version, prerelease=False, draft=False, with_hash=True):
    filename = f'WhisperDesk-Update-{version}.exe'
    assets = [{
        'name': filename,
        'browser_download_url': f'https://github.com/Razan-U/whisperdesk/releases/download/v{version}/{filename}',
        'size': 123,
        'url': f'https://api.github.com/repos/Razan-U/whisperdesk/releases/assets/{version}',
    }]
    if with_hash:
        assets.append({
            'name': filename + '.sha256',
            'browser_download_url': f'https://github.com/Razan-U/whisperdesk/releases/download/v{version}/{filename}.sha256',
            'size': 80,
        })
    return {
        'tag_name': 'v' + version,
        'name': 'WhisperDesk ' + version,
        'body': 'notes',
        'html_url': f'https://github.com/Razan-U/whisperdesk/releases/tag/v{version}',
        'published_at': '2026-10-06T00:00:00Z',
        'prerelease': prerelease,
        'draft': draft,
        'assets': assets,
    }


def test_semver_and_channels():
    releases = [
        release('0.3.1'),
        release('0.3.2-beta.1', prerelease=True),
        release('0.3.3', draft=True),
    ]
    stable = updater.select_release(releases, '0.3.0', 'stable')
    test = updater.select_release(releases, '0.3.0', 'test')
    assert stable['version'] == '0.3.1'
    assert test['version'] == '0.3.2-beta.1'
    assert updater.is_newer('0.4.0', '0.3.9')
    assert updater.is_newer('0.4.0', '0.4.0-rc.2')
    assert not updater.is_newer('0.4.0-beta.1', '0.4.0')


def test_release_without_integrity_data_is_ignored():
    assert updater.select_release([release('0.3.1', with_hash=False)], '0.3.0', 'stable') is None


def test_no_downgrade_or_same_version():
    releases = [release('0.3.1'), release('0.3.0')]
    assert updater.select_release(releases, '0.3.1', 'stable') is None


class Response(io.BytesIO):
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.headers = headers or {}
        self.status = status


def test_download_verifies_sha256_and_is_atomic(tmp_path, monkeypatch):
    payload = b'fake windows updater bytes'
    digest = hashlib.sha256(payload).hexdigest()
    filename = 'WhisperDesk-Update-0.3.1.exe'
    info = {
        'asset_name': filename,
        'download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.1/' + filename,
        'checksum_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.1/' + filename + '.sha256',
        'sha256': None,
        'size': len(payload),
    }

    def fake_open(request, timeout=0):
        if request.full_url.endswith('.sha256'):
            return Response(f'{digest}  {filename}\n'.encode())
        return Response(payload, {'Content-Length': str(len(payload))})

    monkeypatch.setattr(updater, 'urlopen', fake_open)
    progress = []
    result = updater.download_update(info, tmp_path, progress=lambda done, total: progress.append((done, total)))
    assert result.read_bytes() == payload
    assert updater.sha256_file(result) == digest
    assert progress[-1] == (len(payload), len(payload))
    assert not (tmp_path / (filename + '.part')).exists()


def test_bad_sha256_never_exposes_exe(tmp_path, monkeypatch):
    payload = b'tampered'
    filename = 'WhisperDesk-Update-0.3.1.exe'
    info = {
        'asset_name': filename,
        'download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.1/' + filename,
        'checksum_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.1/' + filename + '.sha256',
        'sha256': None,
        'size': len(payload),
    }

    def fake_open(request, timeout=0):
        if request.full_url.endswith('.sha256'):
            return Response((('0' * 64) + f'  {filename}\n').encode())
        return Response(payload, {'Content-Length': str(len(payload))})

    monkeypatch.setattr(updater, 'urlopen', fake_open)
    with pytest.raises(updater.UpdateError, match='SHA-256'):
        updater.download_update(info, tmp_path)
    assert not (tmp_path / filename).exists()
    assert not (tmp_path / (filename + '.part')).exists()


def test_incomplete_download_is_rejected(tmp_path, monkeypatch):
    payload = b'short'
    digest = hashlib.sha256(payload).hexdigest()
    filename = 'WhisperDesk-Update-0.3.1.exe'
    info = {
        'asset_name': filename,
        'download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.1/' + filename,
        'checksum_url': None,
        'sha256': digest,
        'size': len(payload) + 10,
    }
    monkeypatch.setattr(
        updater, 'urlopen',
        lambda request, timeout=0: Response(payload, {'Content-Length': str(len(payload))})
    )
    with pytest.raises(updater.UpdateError, match='не повністю'):
        updater.download_update(info, tmp_path)
    assert not (tmp_path / filename).exists()


def test_auto_check_interval():
    day = updater.CHECK_INTERVAL_SECONDS
    assert updater.auto_check_due(0, day + 1)
    assert not updater.auto_check_due(1000, 1000 + day - 1)
    assert updater.auto_check_due(1000, 1000 + day)


@pytest.mark.skipif(os.name != 'nt', reason='Windows ShellExecute behavior')
def test_launch_installer_uses_windows_shell(tmp_path, monkeypatch):
    path = tmp_path / 'WhisperDesk-Update-0.3.1.exe'
    path.write_bytes(b'test')
    opened = []
    monkeypatch.setattr(updater.os, 'startfile', lambda value: opened.append(value))
    updater.launch_installer(path)
    assert opened == [str(path.resolve())]


def test_download_rejects_non_github_host(tmp_path):
    info = {
        'asset_name': 'WhisperDesk-Update-0.3.1.exe',
        'download_url': 'https://example.invalid/update.exe',
        'checksum_url': None,
        'sha256': '0' * 64,
        'size': 10,
    }
    with pytest.raises(updater.UpdateError, match='GitHub'):
        updater.download_update(info, tmp_path)


def test_download_falls_back_to_api_asset_after_release_dns_error(tmp_path, monkeypatch):
    from urllib.error import URLError

    payload = b'fallback updater bytes'
    digest = hashlib.sha256(payload).hexdigest()
    filename = 'WhisperDesk-Update-0.3.2-beta.2.exe'
    info = {
        'asset_name': filename,
        'download_url': 'https://github.com/Razan-U/whisperdesk/releases/download/v0.3.2-beta.2/' + filename,
        'api_download_url': 'https://api.github.com/repos/Razan-U/whisperdesk/releases/assets/12345',
        'checksum_url': None,
        'sha256': digest,
        'size': len(payload),
    }
    calls = []

    def fake_open(request, timeout=0):
        calls.append(request.full_url)
        if request.full_url.startswith('https://github.com/'):
            raise URLError(OSError(11001, 'getaddrinfo failed'))
        return Response(payload, {'Content-Length': str(len(payload))})

    monkeypatch.setattr(updater, 'urlopen', fake_open)
    result = updater.download_update(info, tmp_path)

    assert result.read_bytes() == payload
    assert calls[0].startswith('https://github.com/')
    assert calls[1].startswith('https://api.github.com/')
