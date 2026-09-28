import multiprocessing
import os
import sys
import traceback
from pathlib import Path

# GUI Python has no stdout/stderr. Keep library diagnostics out of the UI and
# leave a readable report for startup/import errors, including worker failures.
if sys.stdout is None or sys.stderr is None:
    fallback = Path(os.environ.get('LOCALAPPDATA', Path.home())) / 'WhisperDesk'
    fallback.mkdir(parents=True, exist_ok=True)
    try:
        from whisperdesk.core import data_dir
        fallback = data_dir()
    except Exception:
        pass
    log = open(fallback / 'startup-error.log', 'a', encoding='utf-8', buffering=1)
    sys.stdout = sys.stderr = log

if __name__ == '__main__':
    multiprocessing.freeze_support()
    try:
        from whisperdesk.ui import main
        raise SystemExit(main())
    except Exception:
        traceback.print_exc()
        raise SystemExit(1)
