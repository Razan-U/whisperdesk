"""Persistent FIFO tasks. State changes are atomic; interrupted work is explicit."""
from pathlib import Path
import json
import uuid
from .core import atomic_text, recovery, transcript_text


class TaskQueue:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / 'queue.json'
        self.tasks = []
        if self.path.exists():
            self.tasks = json.loads(self.path.read_text(encoding='utf-8'))['tasks']
        changed = False
        for task in self.tasks:
            previous_status = task.get('status')
            if previous_status == 'running':
                task['status'] = 'interrupted'
                changed = True
            session = Path(task['session'])
            if session.exists():
                state = recovery(session)
                position = state['position'] if state['position'] is not None else task['start']
                if task.get('position') != position:
                    task['position'] = position
                    changed = True
                if state['complete'] and task.get('status') != 'done':
                    task['status'] = 'done'
                    changed = True
                elif (state.get('job') and position > float(task.get('start') or 0) + .05
                      and task.get('status') == 'pending'):
                    task['status'] = 'interrupted'
                    changed = True
                if state.get('rows'):
                    try:
                        atomic_text(session.with_suffix('.txt'), transcript_text(state['rows']))
                    except OSError:
                        pass
        if changed:
            self.save()

    def save(self):
        atomic_text(self.path, json.dumps({'version': 2, 'tasks': self.tasks}, ensure_ascii=False, indent=2))

    def add(self, source, duration, settings):
        ident = uuid.uuid4().hex
        task = dict(id=ident, source=str(Path(source).resolve()), duration=duration,
                    start=0.0, end=duration, position=0.0, status='pending', error='',
                    session=str(self.root / 'sessions' / (ident + '.jsonl')), **settings)
        self.tasks.append(task)
        self.save()
        return task

    def get(self, ident):
        return next((t for t in self.tasks if t['id'] == ident), None)

    def next(self):
        return next((t for t in self.tasks if t['status'] == 'pending'), None)

    def move(self, ident, delta):
        task = self.get(ident)
        index = self.tasks.index(task)
        other = index + delta
        if 0 <= other < len(self.tasks) and task['status'] != 'running' and self.tasks[other]['status'] != 'running':
            self.tasks[index], self.tasks[other] = self.tasks[other], self.tasks[index]
            self.save()

    def remove(self, ident):
        task = self.get(ident)
        if task and task['status'] != 'running':
            self.tasks.remove(task)
            self.save()  # Session remains recoverable; deleting a queue row never deletes text.
