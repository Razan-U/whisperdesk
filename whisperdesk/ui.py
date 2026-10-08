from pathlib import Path
import json
import math
import multiprocessing as mp
import os
import queue
import shutil
import threading
import time

from PySide6.QtCore import Qt, QTimer, QUrl, QSize, QLockFile
from PySide6.QtGui import QDesktopServices, QTextCursor, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout,
    QHBoxLayout, QLabel, QPushButton, QComboBox, QFileDialog, QLineEdit, QCheckBox,
    QProgressBar, QPlainTextEdit, QMessageBox, QFrame, QListWidget, QListWidgetItem,
    QSpinBox, QDialog, QFormLayout, QDialogButtonBox, QScrollArea)
from . import __version__
from .core import (MODELS, Job, data_dir, parse_time, clock, validate_range,
                   atomic_text, transcript_text, load_session, recovery)
from .engine import model_ready, download_worker, transcribe_job
from .queue_store import TaskQueue
from .preflight import analyze_queue, report_text
from .eta import record_sample
from .hardware import analyze_hardware, hardware_summary
from .updater import (auto_check_due, check_for_update, download_update,
                      launch_installer)
from .theme import ACCENTS, stylesheet

ASSETS = Path(__file__).resolve().parent.parent / 'assets'
STATUS = {'pending': 'У черзі', 'running': 'Обробляється', 'done': 'Готово',
          'interrupted': 'Перервано · можна продовжити', 'error': 'Помилка · можна повторити'}
DEFAULTS = dict(language='uk', model='base', device='cpu', profile='eco', threads=0)


def button(text, callback, primary=False):
    widget = QPushButton(text)
    widget.clicked.connect(callback)
    if primary:
        widget.setObjectName('primary')
    return widget


def label(text, name=None):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    return widget


class Window(QMainWindow):
    def __init__(self, auto_start=True):
        super().__init__()
        self.setWindowTitle('WhisperDesk')
        self.setWindowIcon(QIcon(str(ASSETS / 'icon.ico')))
        self.resize(1140, 790)
        self.setMinimumSize(890, 660)
        screen = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1140, screen.width()-40), min(790, screen.height()-40))
        self.setAcceptDrops(True)
        self.root = data_dir()
        self.model_folder = self.root / 'models'
        self.sessions = self.root / 'sessions'
        self.sessions.mkdir(exist_ok=True)
        self.settings = {**DEFAULTS, 'night': False, 'accent': 0, 'update_channel': 'stable'}
        try:
            self.settings.update(json.loads((self.root / 'settings.json').read_text(encoding='utf-8')))
        except (OSError, ValueError):
            pass
        self.settings['accent'] = max(0, min(7, int(self.settings.get('accent', 0))))
        if self.settings.get('update_channel') not in ('stable', 'test'):
            self.settings['update_channel'] = 'stable'
        self.queue_warning = None
        try:
            self.tasks = TaskQueue(self.root)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Keep the corrupt queue for recovery instead of overwriting user data.
            broken = self.root / 'queue.json'
            if broken.exists():
                broken.rename(self.root / f'queue-damaged-{time.time_ns()}.json')
            self.tasks = TaskQueue(self.root)
            self.queue_warning = f'Не вдалося відкрити чергу: {exc}. Сеанси можна відкрити вручну.'
        self.process = self.channel = self.cancel_event = None
        self.active_id = None
        self.operation = None
        self.operation_started_at = None
        self.operation_eta_sample = None
        self.running_queue = False
        self.cancel_deadline = None
        self.close_when_stopped = False
        self.forced_stop = False
        self.update_events = queue.Queue()
        self.update_thread = None
        self.update_download_thread = None
        self.available_update = None
        self.downloaded_update = None
        self.update_after_queue = False
        self.pending_update_path = None
        self.rows = []
        self.cpu_fallback_ids = set()
        self.ctx = mp.get_context('spawn')
        host = QWidget()
        self.setCentralWidget(host)
        layout = QVBoxLayout(host)
        layout.setContentsMargins(16, 12, 16, 14)
        header = QHBoxLayout()
        header.addWidget(label('▥', 'heading'))
        icon = QLabel()
        icon.setPixmap(QPixmap(str(ASSETS / 'icon.png')).scaled(38, 38, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        header.addWidget(icon)
        header.addWidget(label('WhisperDesk', 'brand'))
        header.addStretch()
        self.theme_button = button('☾  Нічна тема', self.toggle_theme)
        header.addWidget(self.theme_button)
        self.settings_button = button('⚙  Налаштування', self.open_settings)
        header.addWidget(self.settings_button)
        layout.addLayout(header)
        body = QHBoxLayout()
        sidebar = QFrame()
        sidebar.setObjectName('panel')
        sidebar.setFixedWidth(245)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(12, 15, 12, 12)
        side.addWidget(label('Черга файлів', 'heading'))
        self.add_button = button('+  Додати файли', self.pick_files, True)
        side.addWidget(self.add_button)
        self.list = QListWidget()
        self.list.currentRowChanged.connect(self.select_task)
        side.addWidget(self.list, 1)
        tools = QHBoxLayout()
        tools.addWidget(button('↑', lambda: self.move_task(-1)))
        tools.addWidget(button('↓', lambda: self.move_task(1)))
        tools.addWidget(button('Прибрати', self.remove_task))
        side.addLayout(tools)
        side.addWidget(button('Відкрити сеанс', self.restore_session))
        self.queue_count = label('0 файлів', 'muted')
        side.addWidget(self.queue_count)
        body.addWidget(sidebar)
        main = QVBoxLayout()
        self.editor = QFrame()
        self.editor.setObjectName('panel')
        editor = QVBoxLayout(self.editor)
        editor.setContentsMargins(14, 12, 14, 12)
        self.file_title = label('Налаштування нових файлів', 'heading')
        self.file_title.setWordWrap(True)
        editor.addWidget(self.file_title)
        row = QHBoxLayout()
        self.language = QComboBox()
        for text, value in [('Українська → українська', 'uk'), ('Англійська → англійська', 'en'), ('Змішаний UK / EN / RU', 'mixed')]:
            self.language.addItem(text, value)
        self.model = QComboBox()
        for name, (text, _, _) in MODELS.items():
            self.model.addItem(text, name)
        for title, widget in [('Мова', self.language), ('Модель', self.model)]:
            col = QVBoxLayout()
            col.addWidget(label(title, 'muted'))
            col.addWidget(widget)
            row.addLayout(col, 1)
        editor.addLayout(row)
        self.mixed_help = label('Автовизначення української, англійської та російської. Короткі вставки можуть містити помилки.', 'muted')
        self.mixed_help.setWordWrap(True)
        editor.addWidget(self.mixed_help)
        self.language.currentIndexChanged.connect(self.update_help)
        row = QHBoxLayout()
        self.entire = QCheckBox('Увесь файл')
        self.entire.setChecked(True)
        self.begin, self.finish = QLineEdit('00:00:00'), QLineEdit('00:00:00')
        for field in (self.begin, self.finish):
            field.setMaximumWidth(108)
            field.setEnabled(False)
        self.entire.toggled.connect(lambda v: [x.setEnabled(not v) for x in (self.begin, self.finish)])
        row.addWidget(self.entire)
        row.addStretch()
        row.addWidget(label('Від', 'muted')); row.addWidget(self.begin)
        row.addWidget(label('До', 'muted')); row.addWidget(self.finish)
        editor.addLayout(row)
        row = QHBoxLayout()
        self.device = QComboBox()
        for text, value in [('CPU · сумісний', 'cpu'), ('Авто · NVIDIA / CPU', 'auto'), ('NVIDIA CUDA', 'cuda')]:
            self.device.addItem(text, value)
        self.profile = QComboBox()
        self.profile.addItem('Економний', 'eco'); self.profile.addItem('Більше потоків', 'fast')
        self.threads = QSpinBox()
        self.threads.setRange(0, os.cpu_count() or 2); self.threads.setSpecialValueText('Авто')
        row.addWidget(self.device, 1); row.addWidget(self.profile, 1)
        row.addWidget(label('Потоки', 'muted')); row.addWidget(self.threads)
        editor.addLayout(row)
        row = QHBoxLayout()
        self.apply_button = button('Застосувати до файлу', self.apply_current)
        self.apply_all_button = button('До всіх нових у черзі', self.apply_all)
        row.addWidget(self.apply_button); row.addWidget(self.apply_all_button)
        editor.addLayout(row)
        self.model_info = label('', 'muted'); self.model_info.setWordWrap(True)
        editor.addWidget(self.model_info)
        self.model.currentIndexChanged.connect(self.update_help)
        main.addWidget(self.editor)
        controls = QFrame(); controls.setObjectName('panel')
        control = QVBoxLayout(controls)
        self.status = label('Додайте аудіофайли, щоб почати.'); self.status.setWordWrap(True)
        control.addWidget(self.status)
        self.diagnostics = QPlainTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMaximumHeight(78)
        self.diagnostics.setPlaceholderText('Діагностика вибраного файлу')
        self.diagnostics.hide()
        control.addWidget(self.diagnostics)
        self.progress = QProgressBar(); self.progress.setValue(0)
        control.addWidget(self.progress)
        self.runtime = label('Готово до роботи', 'muted')
        control.addWidget(self.runtime)
        self.update_status = label('', 'muted'); self.update_status.setWordWrap(True); self.update_status.hide()
        control.addWidget(self.update_status)
        row = QHBoxLayout()
        self.start_button = button('▶  Почати чергу', self.start_queue, True)
        self.stop_file = button('Зупинити файл', lambda: self.stop_job(False))
        self.stop_queue = button('Зупинити чергу', lambda: self.stop_job(True))
        self.stop_file.setEnabled(False); self.stop_queue.setEnabled(False)
        row.addWidget(self.start_button); row.addWidget(self.stop_file); row.addWidget(self.stop_queue)
        control.addLayout(row)
        main.addWidget(controls)
        row = QHBoxLayout()
        row.addWidget(label('Транскрипція', 'heading')); row.addStretch()
        self.stamps = QCheckBox('Часові позначки'); self.stamps.toggled.connect(self.render)
        row.addWidget(self.stamps)
        main.addLayout(row)
        self.text = QPlainTextEdit(); self.text.setReadOnly(True)
        self.text.setPlaceholderText('Тут з’явиться текст вибраного файлу…')
        main.addWidget(self.text, 1)
        row = QHBoxLayout()
        self.search = QLineEdit(); self.search.setPlaceholderText('Знайти в тексті…')
        self.search.returnPressed.connect(self.find_text)
        row.addWidget(self.search, 1)
        row.addWidget(button('Знайти', self.find_text))
        row.addWidget(button('Копіювати', lambda: QApplication.clipboard().setText(self.text.toPlainText())))
        row.addWidget(button('Зберегти TXT', self.export, True))
        main.addLayout(row)
        body.addLayout(main, 1)
        layout.addLayout(body, 1)
        self.load_fields(self.settings)
        self.apply_theme()
        self.refresh_list()
        if self.tasks.tasks:
            self.list.setCurrentRow(0)
        self.timer = QTimer(self)
        self.timer.setInterval(100); self.timer.timeout.connect(self.poll); self.timer.start()
        if auto_start:
            QTimer.singleShot(500, self.startup)
            QTimer.singleShot(1800, lambda: self.check_updates(manual=False))

    def info(self, message):
        QMessageBox.information(self, 'WhisperDesk', str(message))

    def save_settings(self):
        atomic_text(self.root / 'settings.json', json.dumps(self.settings, ensure_ascii=False))

    def startup(self):
        marker_path = self.root / 'update-result.json'
        marker = None
        try:
            if marker_path.exists():
                marker = json.loads(marker_path.read_text(encoding='utf-8'))
                marker_path.unlink()
        except (OSError, ValueError):
            marker = None
        previous = self.settings.get('last_seen_version')
        if marker and marker.get('to') == __version__:
            old = marker.get('from')
            text = f'WhisperDesk оновлено до {__version__}.'
            if old:
                text += f' Попередня версія: {old}.'
            QTimer.singleShot(800, lambda message=text: self.info(message))
        elif previous and previous != __version__:
            QTimer.singleShot(800, lambda: self.info(f'WhisperDesk оновлено до {__version__}.'))
        if previous != __version__:
            self.settings['last_seen_version'] = __version__
            self.save_settings()
        if self.queue_warning:
            self.info(self.queue_warning)
        incomplete = [t for t in self.tasks.tasks if t['status'] in ('interrupted', 'error')]
        if incomplete:
            answer = QMessageBox.question(self, 'Відновлення', f'Є незавершені файли: {len(incomplete)}. Продовжити з останніх контрольних точок?')
            if answer == QMessageBox.StandardButton.Yes:
                self.start_queue()
            return
        if not self.settings.get('initial_download_attempted'):
            self.settings['initial_download_attempted'] = True
            self.save_settings()
            if not model_ready(self.model_folder / 'base'):
                self.status.setText('Перший запуск: автоматичне завантаження базової моделі…')
                self.begin_process(download_worker, ('base', str(self.model_folder)), 'download')

    def selected(self):
        item = self.list.currentItem()
        return self.tasks.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh_list(self):
        selected = self.selected()
        ident = selected['id'] if selected else None
        self.list.blockSignals(True); self.list.clear()
        for i, task in enumerate(self.tasks.tasks):
            fraction = (task.get('position', task['start']) - task['start']) / max(task['end'] - task['start'], .001)
            text = f'{Path(task["source"]).name}\n{STATUS[task["status"]]}'
            if task['status'] == 'running':
                text += f' · {fraction:.0%}'
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, task['id'])
            item.setToolTip(task.get('error') or task['source'])
            item.setSizeHint(QSize(210, 88))
            self.list.addItem(item)
            card = QWidget()
            card.setStyleSheet('background: transparent;')
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 9, 10, 9)
            name_label = label(Path(task['source']).name)
            name_label.setWordWrap(True)
            card_layout.addWidget(name_label)
            status_label = label(STATUS[task['status']] + (f' · {fraction:.0%}' if task['status'] == 'running' else ''), 'muted')
            status_label.setWordWrap(True)
            card_layout.addWidget(status_label)
            self.list.setItemWidget(item, card)
            if ident == task['id']:
                self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        done = sum(t['status'] == 'done' for t in self.tasks.tasks)
        self.queue_count.setText(f'Готово {done} із {len(self.tasks.tasks)}')

    def load_fields(self, data):
        for key in ('language', 'model', 'device', 'profile'):
            widget = getattr(self, key)
            index = widget.findData(data.get(key, DEFAULTS[key]))
            if index >= 0: widget.setCurrentIndex(index)
        self.threads.setValue(data.get('threads', 0))
        duration = data.get('duration', 0)
        start, end = data.get('start', 0), data.get('end', duration)
        self.entire.setChecked(start == 0 and end == duration)
        self.begin.setText(clock(start)); self.finish.setText(clock(math.ceil(end)))
        self.update_help()

    def update_help(self):
        self.mixed_help.setVisible(self.language.currentData() == 'mixed')
        name = self.model.currentData()
        ready = model_ready(self.model_folder / name)
        self.model_info.setText(f'{"Завантажена" if ready else "Завантажиться перед обробкою"} · {MODELS[name][1]} · {MODELS[name][2]}')

    def select_task(self, *_):
        task = self.selected()
        if not task:
            return
        self.show_diagnostics(task)
        self.file_title.setText(Path(task['source']).name)
        self.file_title.setToolTip(task['source'])
        self.load_fields(task)
        self.rows = load_session(task['session']) if Path(task['session']).exists() else []
        self.render()
        locked = task['status'] == 'running' or Path(task['session']).exists()
        # A resumed transcript must use the same range/model/language. Runtime
        # controls remain editable after a failure (e.g. switching GPU to CPU).
        for widget in (self.language, self.model, self.entire, self.begin, self.finish):
            widget.setEnabled(not locked)
        if not locked:
            self.begin.setEnabled(not self.entire.isChecked()); self.finish.setEnabled(not self.entire.isChecked())
        self.apply_button.setEnabled(task['status'] not in ('running', 'done'))
        if not self.process:
            self.status.setText(task.get('error') or STATUS[task['status']])
            self.progress.setRange(0, 1000)
            self.progress.setValue(round(1000 * (task.get('position', task['start'])-task['start']) / max(task['end']-task['start'], .001)))

    def show_diagnostics(self, task):
        detail = '\n'.join(x for x in (task.get('warning'), task.get('error')) if x)
        self.diagnostics.setPlainText(detail)
        self.diagnostics.setVisible(bool(detail))

    def fields(self):
        result = {k: getattr(self, k).currentData() for k in ('language', 'model', 'device', 'profile')}
        result['threads'] = self.threads.value()
        return result

    def apply_current(self):
        task = self.selected()
        try:
            values = self.fields()
            self.settings.update(values); self.save_settings()
            if task and task['status'] not in ('running', 'done'):
                if Path(task['session']).exists():
                    task.update({k: values[k] for k in ('device', 'profile', 'threads')})
                else:
                    start = 0 if self.entire.isChecked() else parse_time(self.begin.text())
                    end = task['duration'] if self.entire.isChecked() else parse_time(self.finish.text())
                    if end == math.ceil(task['duration']): end = task['duration']
                    validate_range(start, end, task['duration'])
                    task.update(values, start=start, end=end, position=start)
                self.tasks.save(); self.refresh_list()
            self.status.setText('Налаштування збережено.')
            return True
        except Exception as exc:
            self.info(exc)
            return False

    def apply_all(self):
        values = self.fields()
        self.settings.update(values); self.save_settings()
        for task in self.tasks.tasks:
            if task['status'] == 'pending' and not Path(task['session']).exists():
                task.update(values)
        self.tasks.save(); self.refresh_list()
        self.status.setText('Мову, модель і ресурси застосовано до нових файлів у черзі. Діапазони збережено.')

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, 'Додати аудіофайли', '', 'Аудіо (*.mp3 *.wav *.m4a *.flac *.ogg *.opus *.aac *.wma *.mp4 *.webm);;Усі файли (*)')
        self.add_files(files)

    def add_files(self, files):
        from .audio import probe
        errors = []
        new_id = None
        for path in files:
            try:
                task = self.tasks.add(path, probe(path), self.fields())
                new_id = task['id']
            except Exception as exc:
                errors.append(f'{Path(path).name}: {exc}')
        self.refresh_list()
        if new_id and not self.process:
            self.list.setCurrentRow(len(self.tasks.tasks)-1)
        if errors: self.info('\n'.join(errors))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event):
        self.add_files([u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()])

    def move_task(self, delta):
        task = self.selected()
        if task: self.tasks.move(task['id'], delta); self.refresh_list()

    def remove_task(self):
        task = self.selected()
        if task and task['id'] != self.active_id:
            self.tasks.remove(task['id']); self.refresh_list()
            if self.tasks.tasks: self.list.setCurrentRow(0)
            else:
                self.rows = []; self.render(); self.file_title.setText('Налаштування нових файлів')
                for w in (self.language, self.model, self.entire): w.setEnabled(True)
                self.load_fields(self.settings)

    def confirm_preflight(self):
        report = analyze_queue(self.tasks.tasks, self.model_folder, self.root)
        dlg = QDialog(self)
        dlg.setWindowTitle('Перевірка перед запуском')
        dlg.resize(640, 460)
        layout = QVBoxLayout(dlg)
        title = 'Потрібна увага' if report['blockers'] else 'Готово до запуску'
        subtitle = ('Виправте критичні проблеми перед стартом.'
                    if report['blockers'] else
                    'Перевірте параметри. Транскрипція почнеться лише після підтвердження.')
        layout.addWidget(label(title, 'heading'))
        hint = label(subtitle, 'muted')
        hint.setWordWrap(True)
        layout.addWidget(hint)

        details = QPlainTextEdit()
        details.setReadOnly(True)
        details.setPlainText(report_text(report))
        layout.addWidget(details, 1)

        buttons = QDialogButtonBox()
        if report['blockers']:
            close_button = buttons.addButton(
                'Повернутися до налаштувань',
                QDialogButtonBox.ButtonRole.RejectRole,
            )
            close_button.clicked.connect(dlg.reject)
        else:
            back_button = buttons.addButton('Назад', QDialogButtonBox.ButtonRole.RejectRole)
            start_button = buttons.addButton('▶  Почати', QDialogButtonBox.ButtonRole.AcceptRole)
            back_button.clicked.connect(dlg.reject)
            start_button.clicked.connect(dlg.accept)
        layout.addWidget(buttons)
        return dlg.exec() == QDialog.DialogCode.Accepted

    def start_queue(self):
        if self.process: return
        if not self.tasks.tasks: self.pick_files()
        if not self.tasks.tasks: return
        if not self.apply_current(): return
        if not self.confirm_preflight(): return
        for task in self.tasks.tasks:
            if task['status'] in ('interrupted', 'error'):
                task['status'] = 'pending'; task['error'] = ''
        self.tasks.save()
        self.cpu_fallback_ids.clear()
        self.running_queue = True
        self.next_task()

    def next_task(self):
        if self.process or not self.running_queue: return
        task = self.tasks.next()
        if not task:
            self.running_queue = False
            self.active_id = None
            counts = {key: sum(t['status'] == key for t in self.tasks.tasks) for key in ('done', 'error', 'interrupted')}
            self.status.setText(f"Чергу завершено · готово: {counts['done']} · помилок: {counts['error']} · перервано: {counts['interrupted']}. Подробиці — у вибраному файлі.")
            self.start_button.setEnabled(True)
            if self.update_after_queue and self.downloaded_update:
                QTimer.singleShot(500, lambda: self.install_downloaded_update())
            return
        self.active_id = task['id']
        if not model_ready(self.model_folder / task['model']):
            self.begin_process(download_worker, (task['model'], str(self.model_folder)), 'download')
            return
        try:
            if not Path(task['source']).is_file(): raise ValueError('Аудіофайл не знайдено. Поверніть його за початковим шляхом.')
            resume, previous = None, None
            if Path(task['session']).exists():
                state = recovery(task['session'])
                if state['job']:
                    resume, previous = state['position'], state['language']
                if state['complete']:
                    task['status'] = 'done'; self.tasks.save(); self.refresh_list()
                    QTimer.singleShot(0, self.next_task); return
            task['status'] = 'running'; self.tasks.save(); self.refresh_list()
            self.list.setCurrentRow(self.tasks.tasks.index(task))
            self.select_task()
            job = Job(**{k: task[k] for k in ('source', 'start', 'end', 'model', 'language', 'device', 'profile', 'threads', 'session')},
                      models_dir=str(self.model_folder), resume=resume, previous_language=previous)
            if task['id'] in self.cpu_fallback_ids:
                job.device = 'cpu'
            Path(job.session + '.gpu-attempt').unlink(missing_ok=True)
            self.begin_process(transcribe_job, (job,), 'transcribe')
        except Exception as exc:
            task['status'] = 'error'; task['error'] = str(exc); self.tasks.save(); self.refresh_list()
            QTimer.singleShot(0, self.next_task)

    def begin_process(self, target, args, operation):
        self.channel = self.ctx.Queue(); self.cancel_event = self.ctx.Event()
        self.process = self.ctx.Process(target=target, args=(*args, self.channel, self.cancel_event), daemon=True)
        self.operation = operation; self.finished_message = None; self.failed = False
        self.operation_started_at = time.monotonic()
        self.operation_eta_sample = None
        if operation == 'transcribe' and args:
            job = args[0]
            resume = job.resume if job.resume is not None else job.start
            self.operation_eta_sample = {
                'task': {
                    'model': job.model,
                    'device': job.device,
                    'profile': job.profile,
                    'threads': job.threads,
                },
                'audio_seconds': max(0.0, job.end - resume),
            }
        self.cancel_deadline = None; self.forced_stop = False
        try:
            self.process.start()
        except Exception:
            self.process = None; self.channel.close(); raise
        self.start_button.setEnabled(False); self.stop_file.setEnabled(True); self.stop_queue.setEnabled(True)
        self.settings_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.status.setText('Завантаження моделі з інтернету…' if operation == 'download' else 'Завантаження моделі в пам’ять…')

    def stop_job(self, all_tasks=True):
        if all_tasks: self.running_queue = False
        if self.process:
            self.cancel_event.set()
            if not self.cancel_deadline: self.cancel_deadline = time.monotonic()+5
            self.stop_file.setEnabled(False); self.stop_queue.setEnabled(False)
            self.status.setText('Зупинка… Останній завершений фрагмент збережено.')

    def consume(self):
        for _ in range(200):
            try: kind, payload = self.channel.get_nowait()
            except queue.Empty: break
            task = self.tasks.get(self.active_id)
            selected = self.selected()
            if kind == 'segment' and task and selected and task['id'] == selected['id']:
                self.rows.append(payload)
                self.text.appendPlainText(transcript_text([payload], self.stamps.isChecked()))
            elif kind == 'checkpoint' and task:
                task['position'] = payload
                # Journal is authoritative. Queue is saved at lifecycle boundaries.
                self.runtime.setText(f'Автозбережено · {clock(payload)}')
                self.refresh_list()
            elif kind == 'progress':
                done, total, eta = payload
                self.progress.setRange(0, 1000); self.progress.setValue(round(1000*done/total))
                if not self.cancel_deadline:
                    self.status.setText(f'Оброблено {clock(done)} із {clock(total)} · залишилося ≈{clock(eta)}')
            elif kind == 'warning' and task:
                task['warning'] = payload
                if selected and selected['id'] == task['id']: self.show_diagnostics(task)
            elif kind in ('device', 'language'): self.runtime.setText(payload)
            elif kind == 'status' and not self.cancel_deadline: self.status.setText(payload)
            elif kind in ('done', 'error'):
                self.finished_message = payload
                self.failed = kind == 'error'

    def poll(self):
        self.poll_updates()
        if self.process is None: return
        if not self.forced_stop: self.consume()
        if self.cancel_deadline and time.monotonic() > self.cancel_deadline and self.process.is_alive():
            self.process.terminate(); self.process.join(timeout=.2); self.forced_stop = True
        if self.process.is_alive(): return
        self.process.join()
        if not self.forced_stop: self.consume()
        exitcode = self.process.exitcode
        message = self.finished_message or ('Зупинено. Можна продовжити.' if self.cancel_deadline else f'Процес завершився з кодом {exitcode}. Спробуйте CPU.')
        failed = self.failed or (not self.finished_message and not self.cancel_deadline) or exitcode not in (0, None) and not self.cancel_deadline
        task = self.tasks.get(self.active_id)
        if task:
            if self.operation == 'transcribe':
                state = recovery(task['session']) if Path(task['session']).exists() else None
                if state and state['complete']:
                    task['status'] = 'done'; task['position'] = task['end']
                    if (not failed and not self.cancel_deadline and self.operation_eta_sample
                            and self.operation_started_at is not None):
                        try:
                            record_sample(
                                self.root,
                                self.operation_eta_sample['task'],
                                self.operation_eta_sample['audio_seconds'],
                                time.monotonic() - self.operation_started_at,
                            )
                        except (OSError, ValueError, TypeError):
                            pass
                else:
                    task['status'] = 'error' if failed else 'interrupted'
                    if state and state['position'] is not None: task['position'] = state['position']
                task['error'] = message if failed else ''
                try:
                    if state:
                        atomic_text(Path(task['session']).with_suffix('.txt'), transcript_text(state['rows']))
                except OSError as exc:
                    message += f' TXT не збережено: {exc}'
            elif failed or self.cancel_deadline:
                task['status'] = 'error' if failed else 'interrupted'
                task['error'] = message if failed else ''
        if (task and self.operation == 'transcribe' and failed and not self.cancel_deadline
                and task['device'] == 'auto' and task['id'] not in self.cpu_fallback_ids
                and Path(task['session'] + '.gpu-attempt').exists() and task['status'] != 'done'):
            self.cpu_fallback_ids.add(task['id'])
            task['status'] = 'pending'
            task['warning'] = 'NVIDIA недоступна. Продовжено на CPU з останнього автозбереження.\n' + message
            task['error'] = ''
            message = 'Авто: повторний запуск на CPU з останнього автозбереження…'
        self.channel.close(); self.process.close(); self.process = None
        self.operation_started_at = None
        self.operation_eta_sample = None
        self.active_id = None
        self.start_button.setEnabled(True); self.stop_file.setEnabled(False); self.stop_queue.setEnabled(False)
        self.settings_button.setEnabled(True)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 1000); self.progress.setValue(0)
        self.tasks.save(); self.refresh_list(); self.select_task(); self.update_help()
        self.status.setText(message)
        if self.close_when_stopped:
            self.close(); return
        if self.running_queue:
            QTimer.singleShot(0, self.next_task)
        elif self.update_after_queue and self.downloaded_update:
            QTimer.singleShot(250, self.install_downloaded_update)

    def render(self, *_):
        self.text.setPlainText(transcript_text(self.rows, self.stamps.isChecked()))

    def find_text(self):
        query = self.search.text()
        if query and not self.text.find(query):
            cursor = self.text.textCursor(); cursor.movePosition(QTextCursor.MoveOperation.Start)
            self.text.setTextCursor(cursor); self.text.find(query)

    def export(self):
        if not self.text.toPlainText().strip(): self.info('Тексту для експорту ще немає.'); return
        task = self.selected()
        name = Path(task['source']).stem if task else 'transcript'
        path, _ = QFileDialog.getSaveFileName(self, 'Зберегти TXT', str(self.root / (name+'.txt')), 'Текст UTF-8 (*.txt)')
        if path:
            if not path.lower().endswith('.txt'): path += '.txt'
            try: atomic_text(path, self.text.toPlainText()+'\n'); self.status.setText(f'Збережено: {path}')
            except OSError as exc: self.info(exc)

    def restore_session(self):
        if self.process: self.info('Спочатку зупиніть чергу.'); return
        path, _ = QFileDialog.getOpenFileName(self, 'Відкрити сеанс', str(self.sessions), 'Сеанс (*.jsonl)')
        if not path: return
        try:
            state = recovery(path)
            header = state['job']
            if not header:
                self.rows = load_session(path); self.render()
                self.status.setText('Текст старого сеансу відкрито. Автопродовження доступне для сеансів 0.2.')
                return
            task = next((t for t in self.tasks.tasks if Path(t['session']) == Path(path)), None)
            if not task:
                from .audio import probe
                task = self.tasks.add(header['source'], probe(header['source']), {**DEFAULTS, 'model': header['model'], 'language': header['language']})
                task.update(session=path, start=header['start'], end=header['end'],
                            position=state['position'], status='done' if state['complete'] else 'interrupted')
                self.tasks.save()
            self.refresh_list(); self.list.setCurrentRow(self.tasks.tasks.index(task)); self.select_task()
        except Exception as exc: self.info(exc)

    def toggle_theme(self):
        self.settings['night'] = not self.settings['night']; self.apply_theme(); self.save_settings()

    def apply_theme(self):
        self.setStyleSheet(stylesheet(self.settings['night'], self.settings['accent']))
        self.theme_button.setText('☀  Світла тема' if self.settings['night'] else '☾  Нічна тема')

    def open_settings(self):
        dlg = QDialog(self); dlg.setWindowTitle('Налаштування'); dlg.resize(620, 560)
        layout = QVBoxLayout(dlg)
        layout.addWidget(label('Акцентний колір', 'heading'))
        row = QHBoxLayout()
        for i, (name, color) in enumerate(ACCENTS):
            swatch = QPushButton('✓' if self.settings['accent'] == i else '')
            swatch.setToolTip(name); swatch.setFixedSize(46, 40)
            swatch.setStyleSheet(f'background:{color}; color:{"#231A14" if i in (1,2) else "white"}; border:2px solid #AA9278; border-radius:6px;')
            def choose(checked=False, index=i):
                self.settings['accent'] = index; self.apply_theme(); self.save_settings(); dlg.accept()
            swatch.clicked.connect(choose); row.addWidget(swatch)
        layout.addLayout(row)
        layout.addWidget(label('Моделі', 'heading'))
        model = QComboBox()
        for key, (name, size, _) in MODELS.items():
            model.addItem(f'{name} · {size} · {"є" if model_ready(self.model_folder/key) else "немає"}', key)
        layout.addWidget(model)
        row = QHBoxLayout()
        def download():
            key = model.currentData(); dlg.accept()
            if model_ready(self.model_folder/key): self.info('Ця модель вже завантажена.'); return
            self.active_id = None
            self.begin_process(download_worker, (key, str(self.model_folder)), 'download')
        def remove():
            key = model.currentData()
            if QMessageBox.question(dlg, 'Видалити модель?', f'Видалити {key} з диска?') == QMessageBox.StandardButton.Yes:
                try: shutil.rmtree(self.model_folder/key); dlg.accept(); self.update_help()
                except OSError as exc: self.info(exc)
        row.addWidget(button('Завантажити', download)); row.addWidget(button('Видалити', remove))
        layout.addLayout(row)
        path_label = label('Дані, моделі й автозбереження:\n'+str(self.root), 'muted'); path_label.setWordWrap(True)
        layout.addWidget(path_label)
        row = QHBoxLayout()
        row.addWidget(button('Відкрити папку', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))))
        def migrate():
            old = Path(os.environ.get('LOCALAPPDATA', Path.home()/'.local/share'))/'WhisperDesk'/'models'
            if old.resolve() == self.model_folder.resolve() or not old.exists():
                self.info('Моделі попередньої версії в стандартній папці не знайдено.'); return
            self.model_folder.mkdir(parents=True, exist_ok=True)
            # Move rather than duplicate many gigabytes. Only model directories.
            try:
                for key in MODELS:
                    if model_ready(old/key) and not (self.model_folder/key).exists(): shutil.move(str(old/key), str(self.model_folder/key))
                self.update_help(); dlg.accept(); self.info('Наявні моделі перенесено.')
            except OSError as exc: self.info(exc)
        row.addWidget(button('Перенести моделі з 0.1', migrate)); layout.addLayout(row)

        layout.addWidget(label('Система / Залізо', 'heading'))
        hardware_label = label('', 'muted'); hardware_label.setWordWrap(True)
        layout.addWidget(hardware_label)
        def refresh_hardware():
            try:
                clear_cache = getattr(analyze_hardware, 'cache_clear', None)
                if clear_cache:
                    clear_cache()
                hardware_label.setText(hardware_summary(analyze_hardware()))
            except Exception as exc:
                hardware_label.setText(f'Не вдалося отримати інформацію про залізо: {exc}')
        refresh_hardware()
        layout.addWidget(button('Оновити інформацію про залізо', refresh_hardware))

        layout.addWidget(label('Оновлення', 'heading'))
        update_row = QHBoxLayout()
        update_row.addWidget(label(f'Версія {__version__}', 'muted'))
        channel = QComboBox()
        channel.addItem('Stable · лише стабільні', 'stable')
        channel.addItem('Test · beta / RC / stable', 'test')
        channel.setCurrentIndex(max(0, channel.findData(self.settings.get('update_channel', 'stable'))))
        def save_channel():
            self.settings['update_channel'] = channel.currentData()
            self.save_settings()
        channel.currentIndexChanged.connect(save_channel)
        update_row.addWidget(channel, 1)
        def manual_check():
            save_channel(); dlg.accept(); self.check_updates(manual=True)
        update_row.addWidget(button('Перевірити оновлення', manual_check))
        layout.addLayout(update_row)
        note = label('Stable отримує тільки звичайні GitHub Releases. Test також бачить prerelease (beta / RC).', 'muted')
        note.setWordWrap(True); layout.addWidget(note)
        layout.addWidget(button('Закрити', dlg.accept))
        dlg.exec()

    def check_updates(self, manual=False):
        if self.update_thread and self.update_thread.is_alive():
            if manual: self.info('Перевірка оновлень уже виконується.')
            return
        now = time.time()
        if not manual and not auto_check_due(self.settings.get('last_update_check'), now):
            return
        channel = self.settings.get('update_channel', 'stable')
        if manual:
            self.update_status.setText('Перевірка оновлень…'); self.update_status.show()

        def worker():
            try:
                info = check_for_update(__version__, channel)
                self.update_events.put(('check_done', (manual, info, now)))
            except Exception as exc:
                self.update_events.put(('check_error', (manual, str(exc), now)))
        self.update_thread = threading.Thread(target=worker, daemon=True)
        self.update_thread.start()

    def offer_update(self, info):
        self.available_update = info
        box = QMessageBox(self)
        box.setWindowTitle('Доступне оновлення WhisperDesk')
        channel = 'Test' if info.get('prerelease') else 'Stable'
        box.setText(f'Доступна версія {info["version"]} · {channel}')
        notes = (info.get('notes') or '').strip()
        if len(notes) > 1600: notes = notes[:1600].rstrip() + '…'
        box.setInformativeText(notes or 'Нова версія готова до встановлення.')
        later = box.addButton('Пізніше', QMessageBox.ButtonRole.RejectRole)
        if self.process or self.running_queue:
            after = box.addButton('Оновити після завершення черги', QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(after)
            box.exec()
            if box.clickedButton() is after:
                self.start_update_download(info, after_queue=True)
        else:
            install = box.addButton('Оновити', QMessageBox.ButtonRole.AcceptRole)
            box.setDefaultButton(install)
            box.exec()
            if box.clickedButton() is install:
                self.start_update_download(info, after_queue=False)

    def start_update_download(self, info, after_queue=False):
        if self.update_download_thread and self.update_download_thread.is_alive():
            self.update_after_queue = self.update_after_queue or after_queue
            self.update_status.setText('Оновлення вже завантажується у фоні…'); self.update_status.show()
            return
        self.update_after_queue = bool(after_queue)
        self.update_status.setText(f'Завантаження WhisperDesk {info["version"]}…'); self.update_status.show()

        def progress(done, total):
            self.update_events.put(('download_progress', (done, total, info['version'])))

        def worker():
            try:
                path = download_update(info, self.root / 'updates', progress=progress)
                self.update_events.put(('download_done', (str(path), info['version'])))
            except Exception as exc:
                self.update_events.put(('download_error', str(exc)))
        self.update_download_thread = threading.Thread(target=worker, daemon=True)
        self.update_download_thread.start()

    def poll_updates(self):
        for _ in range(20):
            try:
                kind, payload = self.update_events.get_nowait()
            except queue.Empty:
                break
            if kind in ('check_done', 'check_error'):
                manual, result, checked_at = payload
                self.settings['last_update_check'] = checked_at
                self.save_settings()
                if kind == 'check_error':
                    if manual:
                        self.update_status.hide(); self.info(result)
                    continue
                self.update_status.hide()
                if result:
                    self.offer_update(result)
                elif manual:
                    self.info(f'У вас актуальна версія WhisperDesk {__version__}.')
            elif kind == 'download_progress':
                done, total, version = payload
                if total:
                    self.update_status.setText(f'Оновлення {version}: {done / total:.0%} завантажено')
                else:
                    self.update_status.setText(f'Оновлення {version}: завантажено {done / 1024**2:.1f} МБ')
                self.update_status.show()
            elif kind == 'download_done':
                path, version = payload
                self.downloaded_update = path
                self.update_status.setText(f'WhisperDesk {version} завантажено й перевірено.')
                self.update_status.show()
                if self.update_after_queue and (self.process or self.running_queue):
                    self.update_status.setText(
                        f'WhisperDesk {version} готовий. Встановлення почнеться після завершення черги.'
                    )
                else:
                    QTimer.singleShot(250, self.install_downloaded_update)
            elif kind == 'download_error':
                self.update_after_queue = False
                self.downloaded_update = None
                self.update_status.hide()
                self.info(f'Оновлення не встановлюватиметься. {payload}')

    def install_downloaded_update(self):
        if not self.downloaded_update:
            return
        if self.process or self.running_queue:
            self.update_after_queue = True
            self.update_status.setText('Оновлення буде встановлено після завершення черги.')
            self.update_status.show()
            return
        self.pending_update_path = self.downloaded_update
        self.update_status.setText('Перезапуск для встановлення оновлення…'); self.update_status.show()
        self.close()

    def closeEvent(self, event):
        if self.process:
            self.close_when_stopped = True; self.stop_job(True); event.ignore(); return
        try: self.tasks.save(); self.save_settings()
        except OSError as exc:
            self.info(f'Не вдалося зберегти чергу: {exc}')
        event.accept()


def main():
    app = QApplication([]); app.setStyle('Fusion')
    # Avoid two processes writing the same queue / checkpoint journals.
    lock = QLockFile(str(data_dir() / 'app.lock'))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, 'WhisperDesk', 'Застосунок уже працює для цієї папки даних.')
        return 0
    window = Window(); window.show()
    result = app.exec()
    pending_update = window.pending_update_path
    lock.unlock()
    if pending_update:
        try:
            launch_installer(pending_update)
        except Exception as exc:
            QMessageBox.information(None, 'WhisperDesk — оновлення', str(exc))
            return 1
    return result
