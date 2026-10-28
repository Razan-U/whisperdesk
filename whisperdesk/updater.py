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
import socket
import struct
import threading

REPOSITORY = 'Razan-U/whisperdesk'
RELEASES_URL = f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=30'
CHECK_INTERVAL_SECONDS = 24 * 60 * 60
USER_AGENT = 'WhisperDesk-Updater/1'
VERSION_RE = re.compile(
    r'^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$'
)
SHA256_RE = re.compile(r'\b([0-9a-fA-F]{64})\b')
PUBLIC_DNS_SERVERS = ('1.1.1.1', '8.8.8.8')
_DNS_OVERRIDE_LOCK = threading.Lock()


def _allowed_github_host(host):
    host = str(host or '').lower().rstrip('.')
    return host in {'github.com', 'api.github.com'} or host.endswith('.githubusercontent.com')


def _is_dns_failure(exc):
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).lower()
        errno = getattr(current, 'errno', None)
        if errno in {11001, getattr(socket, 'EAI_NONAME', -2), getattr(socket, 'EAI_AGAIN', -3)}:
            return True
        if 'getaddrinfo failed' in text or 'name or service not known' in text or 'temporary failure in name resolution' in text:
            return True
        current = getattr(current, 'reason', None) or getattr(current, '__cause__', None)
    return False


def _skip_dns_name(data, offset):
    while offset < len(data):
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1 + length
    raise ValueError('Пошкоджена DNS-відповідь.')


def _parse_dns_a_response(data):
    if len(data) < 12:
        raise ValueError('Коротка DNS-відповідь.')
    _ident, flags, qdcount, ancount, _nscount, _arcount = struct.unpack('!HHHHHH', data[:12])
    if flags & 0x000F:
        return []
    offset = 12
    for _ in range(qdcount):
        offset = _skip_dns_name(data, offset)
        offset += 4
        if offset > len(data):
            raise ValueError('Пошкоджена DNS-відповідь.')
    result = []
    for _ in range(ancount):
        offset = _skip_dns_name(data, offset)
        if offset + 10 > len(data):
            raise ValueError('Пошкоджена DNS-відповідь.')
        rtype, rclass, _ttl, rdlength = struct.unpack('!HHIH', data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + rdlength]
        offset += rdlength
        if rtype == 1 and rclass == 1 and rdlength == 4:
            result.append(socket.inet_ntoa(rdata))
    return result


def _public_dns_a(host, timeout=2.0):
    labels = str(host).strip('.').split('.')
    qname = b''.join(bytes([len(label.encode('idna'))]) + label.encode('idna') for label in labels) + b'\x00'
    ident = int.from_bytes(os.urandom(2), 'big')
    packet = struct.pack('!HHHHHH', ident, 0x0100, 1, 0, 0, 0) + qname + struct.pack('!HH', 1, 1)
    errors = []
    for server in PUBLIC_DNS_SERVERS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            sock.sendto(packet, (server, 53))
            data, _ = sock.recvfrom(4096)
            ips = _parse_dns_a_response(data)
            if ips:
                return ips
        except OSError as exc:
            errors.append(str(exc))
        finally:
            sock.close()
    raise OSError('Публічний DNS не повернув IPv4 для GitHub: ' + '; '.join(errors[-2:]))


def _open_with_public_dns(request, timeout):
    host = urlparse(request.full_url).hostname
    if not _allowed_github_host(host):
        raise UpdateError('Public-DNS fallback дозволений лише для GitHub.')
    opener = build_opener(ProxyHandler({}))
    cache = {}
    with _DNS_OVERRIDE_LOCK:
        original = socket.getaddrinfo

        def patched_getaddrinfo(target, port, family=0, socktype=0, proto=0, flags=0):
            target_text = target.decode() if isinstance(target, bytes) else str(target)
            if _allowed_github_host(target_text):
                ips = cache.get(target_text)
                if ips is None:
                    ips = _public_dns_a(target_text)
                    cache[target_text] = ips
                stype = socktype or socket.SOCK_STREAM
                ptype = proto or socket.IPPROTO_TCP
                return [(socket.AF_INET, stype, ptype, '', (ip, port)) for ip in ips]
            return original(target, port, family, socktype, proto, flags)

        socket.getaddrinfo = patched_getaddrinfo
        try:
            return opener.open(request, timeout=timeout)
        finally:
            socket.getaddrinfo = original



class UpdateError(RuntimeError):
    pass


def _open_with_direct_fallback(request, timeout):
    """Try system networking, no-proxy networking, then public DNS for GitHub only."""
    try:
        return urlopen(request, timeout=timeout)
    except HTTPError:
        raise
    except (URLError, OSError) as primary:
        try:
            return build_opener(ProxyHandler({})).open(request, timeout=timeout)
        except HTTPError:
            raise
        except (URLError, OSError) as direct:
            if _is_dns_failure(primary) or _is_dns_failure(direct):
                try:
                    return _open_with_public_dns(request, timeout)
                except HTTPError:
                    raise
                except (URLError, OSError, UpdateError) as public_dns:
                    raise URLError(
                        f'system: {primary}; direct: {direct}; public-dns: {public_dns}'
                    ) from public_dns
            raise URLError(f'system: {primary}; direct: {direct}') from direct



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
        with _open_with_direct_fallback(request, timeout) as response:
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
        with _open_with_direct_fallback(request, timeout) as response:
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
    candidates += [(kind.replace('-direct', '') + '-public-dns', url, 'public-dns')
                   for kind, url, _ in list(candidates) if kind.endswith('-direct')]

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
        opener = build_opener(ProxyHandler({})) if direct is True else None
        try:
            if direct == 'public-dns':
                response_cm = _open_with_public_dns(request, timeout)
            else:
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
            raise UpdateError(
                f'Пакет завантажився не повністю: {received} із {expected_size} байтів.'
            )
        actual = digest.hexdigest()
        if actual != expected:
            part.unlink(missing_ok=True)
            raise UpdateError('SHA-256 пакета не збігається. Оновлення не буде запущено.')

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
