"""Install a built Setup EXE and smoke-test its own runtime on a disposable runner.

This writes Windows installer registration and shortcuts. Run only on a
dedicated Windows CI runner, never against a personal installation.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup', type=Path, required=True)
    parser.add_argument('--version', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if os.name != 'nt' or os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('This installer check is restricted to disposable Windows GitHub runners.')
    install = Path(tempfile.mkdtemp(prefix='whisperdesk-clean-install-')) / 'WhisperDesk'
    setup = args.setup.resolve(strict=True)
    subprocess.run([str(setup), '/S', f'/D={install}'], check=True, timeout=300)
    for relative in ('WhisperDesk.exe', 'Uninstall.exe', 'runtime/python.exe',
                     'app/main.py', 'app/data-path.txt', 'app/BUILD-MANIFEST.json'):
        if not (install / relative).is_file():
            raise RuntimeError(f'Installed file missing: {relative}')
    data = install / 'Data'
    if Path((install / 'app/data-path.txt').read_text(encoding='utf-16').strip()) != data:
        raise RuntimeError('Setup did not configure its own writable Data folder.')
    env = dict(os.environ, QT_QPA_PLATFORM='offscreen', PYTHONUTF8='1')
    env.pop('WHISPERDESK_DATA', None)
    code = '''
import json, pathlib, sys
import whisperdesk, whisperdesk.engine, whisperdesk.updater
from PySide6.QtWidgets import QApplication
from whisperdesk.ui import Window
root = pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(whisperdesk.__file__).resolve().is_relative_to(root / 'app')
assert whisperdesk.__version__ == sys.argv[2]
app = QApplication([])
w = Window(auto_start=False)
assert w.root.resolve() == root / 'Data'
assert not w.tasks.tasks and not w.history.records
assert not any((w.settings_warning, w.queue_warning, w.history_warning))
assert w.settings['model'] == 'base' and w.settings['device'] == 'cpu'
w.show()
app.processEvents()
assert w.isVisible()
w.close()
app.processEvents()
print(json.dumps({'version': whisperdesk.__version__, 'runtime': sys.executable,
                  'module': whisperdesk.__file__, 'data': str(w.root), 'ui_smoke': 'passed'}))
'''
    result = subprocess.run(
        [str(install / 'runtime/python.exe'), '-c', code, str(install), args.version],
        cwd=install, env=env, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=60,
    )
    if result.returncode:
        raise RuntimeError(f'Installed runtime/UI failed:\n{result.stdout}\n{result.stderr}')
    report = json.loads(result.stdout.strip())
    report['setup'] = str(setup)
    report['clean_install'] = 'passed'
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
