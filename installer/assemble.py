"""Assemble an offline Windows payload from trusted CPython ZIP and Windows wheels.

Usage: python installer/assemble.py BUILD_DIRECTORY
BUILD_DIRECTORY/downloads/python.zip and vc_redist.x64.exe must be present;
BUILD_DIRECTORY/wheels contains pinned Windows x64 wheels. No pip on target PC.
"""
from pathlib import Path
import sys, zipfile, shutil, json, hashlib
root = Path(__file__).resolve().parents[1]
build = Path(sys.argv[1]).resolve()
payload = build / 'payload'
runtime = payload / 'runtime'
runtime.mkdir(parents=True, exist_ok=True)
with zipfile.ZipFile(build/'downloads/python.zip') as archive:
    archive.extractall(runtime)
site = runtime/'Lib/site-packages'
site.mkdir(parents=True, exist_ok=True)
manifest = []
for wheel in sorted((build/'wheels').glob('*.whl')):
    manifest.append({'file': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest()})
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            parts = Path(member.filename).parts
            if any(p == '..' for p in parts): raise ValueError('Invalid wheel path')
            target = site / member.filename
            if parts[0].endswith('.data'):
                if parts[1] in ('purelib', 'platlib'): target = site / Path(*parts[2:])
                elif parts[1] == 'data': target = runtime / Path(*parts[2:])
                else: continue
            if not member.is_dir():
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(member))
(runtime/'python312._pth').write_text('python312.zip\n.\nLib/site-packages\n../app\nimport site\n')
app = payload/'app'
app.mkdir(exist_ok=True)
for name in ('whisperdesk', 'assets'):
    shutil.copytree(root/name, app/name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
for name in ('main.py', 'README_UA.md', 'LICENSE'):
    shutil.copy2(root/name, app/name)
shutil.copy2(build/'downloads/vc_redist.x64.exe', payload/'vc_redist.x64.exe')
(app/'BUILD-MANIFEST.json').write_text(json.dumps(manifest, indent=2))
print('Payload:', payload)
print('Bytes:', sum(p.stat().st_size for p in payload.rglob('*') if p.is_file()))
