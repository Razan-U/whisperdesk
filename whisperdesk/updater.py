"""Safe GitHub Releases based update client for WhisperDesk."""
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen, build_opener, ProxyHandler
import hashlib
import json
import os
import re
import subprocess

REPOSITORY = 'Razan-U/whisperdesk'
RELEASES_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30'
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
USER_AGENT = 'WhisperDesk-Updater/1'
VERSION_RE = re.compile(
    r'^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$'
)
SHA256_RE = re.compile(r'\b([0-9a-fA-F]{64})\b')


class UpdateError(RuntimeError):
    pass


def normalize_version(value):
    match = VERSION_RE.fullmatch(str(value).strip())
    if not match:
        raise ValueError(f'Некоректна версія: {value!r}')
    base = '.'.join(match.group(i) for i in (1, 2, 3))
    return base + (f'-{match.group(4)}' if match.group(4) else '')


def version_key(value):
    """SemVer-compatible ordering for the subset WhisperDesk publishes."""
    text = normalize_version(value)
    main, sep, prerelease = text.partition('-')
    major, minor, patch = map(int, main.split('.'))
    if not sep:
        return major, minor, patch, 1, ()
    parts = []
    for item in prerelease.split('.'):
        if item.isdigit():
            parts.append((0, int(item)))
        else:
            parts.append((1, item.lower()))
    return major, minor, patch, 0, tuple(parts)


def is_newer(candidate, current):
    return version_key(candidate) > version_key(current)


def _asset(release, name):
    return next((item for item in release.get('assets', []) if item.get('name') == name), None)


def select_release(releases, current_version, channel='stable'):
    """Pick the newest usable release for Stable or Test channel."""
    if channel not in ('stable', 'test'):
        raise ValueError('Канал оновлень має бути stable або test.')
    current_key = version_key(current_version)
    candidates = []
    for release in releases:
        if release.get('draft'):
            continue
        try:
            version = normalize_version(release.get('tag_name', ''))
            key = version_key(version)
        except ValueError:
            continue
        is_prerelease = bool(release.get('prerelease')) or key[3] == 0
        if channel == 'stable' and is_prerelease:
            continue
        if key <= current_key:
            continue

        filename = f'WhisperDesk-Update-{version}.exe'
        binary = _asset(release, filename)
        if not binary or not binary.get('browser_download_url'):
            continue

        digest = binary.get('digest') or ''
        digest_sha = digest.split(':', 1)[1].lower() if digest.startswith('sha256:') else None
        checksum = _asset(release, filename + '.sha256')
        if not digest_sha and (not checksum or not checksum.get('browser_download_url')):
            # Never install an asset that cannot be integrity checked.
            continue

        candidates.append((key, {
            'version': version,
            'name': release.get('name') or f'WhisperDesk {version}',
            'notes': release.get('body') or '',
            'page_url': release.get('html_url') or '',
            'published_at': release.get('published_at') or '',
            'prerelease': is_prerelease,
            'asset_name': filename,
            'download_url': binary['browser_download_url'],
            'api_download_url': binary.get('url') or '',
            'size': int(binary.get('size') or 0),
            'sha256': digest_sha,
            'checksum_url': checksum.get('browser_download_url') if checksum else None,
        }))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def fetch_releases(timeout=12):
    request = Request(
        RELEASES_URL,
        headers={
            'Accept': 'application/vnd.github+json',
            'User-Agent': USER_AGENT,
            'X-GitHub-Api-Version': '2022-11-28',
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            data = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                'Сховище оновлень недоступне. Перевірте, що репозиторій WhisperDesk публічний.'
            ) from exc
        raise UpdateError(f'GitHub повернув помилку HTTP {exc.code}.') from exc
    except (URLError, OSError, ValueError) as exc:
        raise UpdateError(f'Не вдалося перевірити оновлення: {exc}') from exc
    if not isinstance(data, list):
        raise UpdateError('GitHub повернув неочікувану відповідь.')
    return data


def check_for_update(current_version, channel='stable', timeout=12):
    return select_release(fetch_releases(timeout), current_version, channel)


def _safe_download_url(url):
    parsed = urlparse(str(url))
    if parsed.scheme != 'https' or parsed.hostname not in {
        'github.com', 'api.github.com', 'objects.githubusercontent.com',
        'release-assets.githubusercontent.com',
    }:
        raise UpdateError('Оновлення має завантажуватися лише через HTTPS з GitHub.')
    return str(url)


def _read_checksum(url, timeout=20):
    request = Request(_safe_download_url(url), headers={'User-Agent': USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            text = response.read(32 * 1024).decode('utf-8', errors='replace')
    except (HTTPError, URLError, OSError) as exc:
        raise UpdateError(f'Не вдалося отримати контрольну суму: {exc}') from exc
    match = SHA256_RE.search(text)
    if not match:
        raise UpdateError('Файл контрольної суми пошкоджений або має невідомий формат.')
    return match.group(1).lower()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def download_update(info, destination, progress=None, timeout=30):
    """Download to .part, verify SHA-256 and atomically expose the updater EXE."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    filename = Path(info['asset_name']).name
    if filename != info['asset_name'] or not filename.lower().endswith('.exe'):
        raise UpdateError('Некоректне ім’я пакета оновлення.')
    expected = (info.get('sha256') or '').lower()
    if not expected:
        checksum_url = info.get('checksum_url')
        if not checksum_url:
            raise UpdateError('Для оновлення немає SHA-256 контрольної суми.')
        expected = _read_checksum(checksum_url, timeout)
    if not SHA256_RE.fullmatch(expected):
        raise UpdateError('Некоректна SHA-256 контрольна сума.')

    target = destination / filename
    if target.is_file() and sha256_file(target) == expected:
        if progress:
            progress(target.stat().st_size, target.stat().st_size)
        return target

    part = target.with_name(target.name + '.part')
    expected_size = int(info.get('size') or 0)

    candidates = []
    primary = _safe_download_url(info['download_url'])
    candidates.append(('release', primary, False))
    api_url = info.get('api_download_url')
    if api_url:
        api_url = _safe_download_url(api_url)
        if api_url != primary:
            candidates.append(('api', api_url, False))
    # A stale/broken Windows proxy can resolve api.github.com but fail on the
    # release/CDN hop. Retrying directly keeps public GitHub updates usable.
    candidates += [(kind + '-direct', url, True) for kind, url, _ in list(candidates)]

    errors = []
    for kind, url, direct in candidates:
        part.unlink(missing_ok=True)
        digest = hashlib.sha256()
        received = 0
        headers = {'User-Agent': USER_AGENT}
        if kind.startswith('api'):
            headers['Accept'] = 'application/octet-stream'
            headers['X-GitHub-Api-Version'] = '2022-11-28'
        request = Request(url, headers=headers)
        opener = build_opener(ProxyHandler({})) if direct else None
        try:
            response_cm = opener.open(request, timeout=timeout) if opener else urlopen(request, timeout=timeout)
            with response_cm as response, part.open('wb') as output:
                total = expected_size or int(response.headers.get('Content-Length') or 0)
                while True:
                    block = response.read(1024 * 1024)
                    if not block:
                        break
                    output.write(block)
                    digest.update(block)
                    received += len(block)
                    if progress:
                        progress(received, total)
                output.flush()
                os.fsync(output.fileno())
        except (HTTPError, URLError, OSError) as exc:
            part.unlink(missing_ok=True)
            errors.append(f'{kind}: {exc}')
            continue

        if expected_size and received != expected_size:
            part.unlink(missing_ok=True)
            errors.append(f'{kind}: отримано {received} із {expected_size} байтів')
            continue
        actual = digest.hexdigest()
        if actual != expected:
            part.unlink(missing_ok=True)
            errors.append(f'{kind}: SHA-256 не збігається')
            continue

        os.replace(part, target)
        return target

    detail = '; '.join(errors[-3:]) if errors else 'невідома мережева помилка'
    raise UpdateError(
        'Не вдалося завантажити оновлення через GitHub. '
        'Перевірте інтернет/DNS або системний proxy та повторіть спробу. '
        f'Деталі: {detail}'
    )


def launch_installer(path):
    path = Path(path).resolve()
    if not path.is_file() or path.suffix.lower() != '.exe':
        raise UpdateError('Файл оновлення не знайдено.')
    # NSIS carries a requireAdministrator manifest. On Windows it must be opened
    # through ShellExecute so the OS can show the UAC consent dialog; CreateProcess
    # would fail with ERROR_ELEVATION_REQUIRED instead of prompting.
    if os.name == 'nt':
        os.startfile(str(path))
    else:
        subprocess.Popen([str(path)], close_fds=True)


def auto_check_due(last_check, now):
    try:
        last = float(last_check or 0)
    except (TypeError, ValueError):
        last = 0
    return now - last >= CHECK_INTERVAL_SECONDS
