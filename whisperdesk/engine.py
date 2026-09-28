"""Spawn-safe worker; Qt never imports inference libraries in its UI thread."""
from pathlib import Path
import json
import os
import time
import traceback

from .core import select_language, owned_words, thread_count, recovery, repair_journal, write_record, fingerprint, data_dir

# Older official converted models legitimately omit preprocessor_config.json;
# faster-whisper uses its built-in 16 kHz feature extractor defaults in that case.
REQUIRED_MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json')


def model_ready(path):
    return all((Path(path) / name).is_file() for name in REQUIRED_MODEL_FILES) and (Path(path) / '.ready').is_file()


def download_worker(name, folder, queue, cancel):
    try:
        from faster_whisper.utils import download_model
        target = Path(folder) / name
        queue.put(('status', 'Завантаження моделі. Перший запуск потребує інтернету…'))
        download_model(name, output_dir=str(target))
        if not cancel.is_set():
            if not all((target / f).is_file() for f in REQUIRED_MODEL_FILES):
                raise RuntimeError('Завантаження неповне. Спробуйте ще раз.')
            (target / '.ready').write_text('ok', encoding='utf-8')
            queue.put(('done', 'Модель завантажено. Можна починати офлайн.'))
    except Exception as exc:
        queue.put(('error', f'Не вдалося завантажити модель: {exc}'))


def transcribe_job(job, queue, cancel, model_factory=None):
    """model_factory is an injection seam used by offline integration tests."""
    os.environ['OMP_NUM_THREADS'] = str(thread_count(job.profile, job.threads))
    os.environ['HF_HUB_OFFLINE'] = '1'
    journal = None
    try:
        import numpy as np
        from faster_whisper import WhisperModel
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        import ctranslate2
        from .audio import windows, RATE

        factory = model_factory or WhisperModel
        queue.put(('status', 'Завантаження моделі в пам’ять…'))
        device = job.device
        if device == 'auto':
            Path(job.session + '.gpu-attempt').write_text('detection', encoding='ascii')
            try:
                device = 'cuda' if ctranslate2.get_cuda_device_count() > 0 else 'cpu'
            except Exception as exc:
                device = 'cpu'
                queue.put(('warning', f'Не вдалося перевірити NVIDIA: {exc}. Використовується CPU.'))
        if device == 'cpu':
            Path(job.session + '.gpu-attempt').unlink(missing_ok=True)
        if device == 'cuda':
            # Persist synchronously BEFORE native CUDA calls. The UI can recover
            # even if the process dies before multiprocessing.Queue flushes.
            Path(job.session + '.gpu-attempt').write_text('cuda', encoding='ascii')
            queue.put(('gpu_attempt', True))
            if model_factory is None:
                from .gpu import ensure_gpu
                ensure_gpu(data_dir(), queue, cancel)
        kwargs = dict(device=device, compute_type='int8_float16' if device == 'cuda' else 'int8',
                      cpu_threads=thread_count(job.profile, job.threads), num_workers=1,
                      local_files_only=True)
        model = factory(str(Path(job.models_dir) / job.model), **kwargs)
        if device == 'cuda' and model_factory is None:
            from .gpu import preflight
            queue.put(('status', 'Перевірка реального обчислення на NVIDIA…'))
            preflight(model)
        if cancel.is_set():
            queue.put(('done', 'Скасовано.'))
            return
        queue.put(('device', f'{device.upper()} · {kwargs["compute_type"]} · потоків CPU: {kwargs["cpu_threads"]}'))
        repair_journal(job.session)
        if job.resume is not None:
            saved = recovery(job.session)
            header = saved['job']
            if not header or header['fingerprint'] != fingerprint(job.source):
                raise ValueError('Аудіофайл змінився. Додайте його як нове завдання.')
            if any(header[k] != getattr(job, k) for k in ('source', 'start', 'end', 'model', 'language')):
                raise ValueError('Налаштування не відповідають збереженому сеансу.')
        journal = open(job.session, 'a', encoding='utf-8', buffering=1)
        if job.resume is None:
            write_record(journal, {'type': 'job', 'version': 2, 'source': job.source,
                                  'start': job.start, 'end': job.end, 'model': job.model,
                                  'language': job.language, 'fingerprint': fingerprint(job.source)})
        started = time.monotonic()
        previous = job.previous_language
        resume_start = job.resume if job.resume is not None else job.start
        processed = resume_start - job.start
        for audio, offset, left, right in windows(job.source, resume_start, job.end, cancel,
                                                 core_seconds=8 if job.language == 'mixed' else 24):
            if cancel.is_set():
                break
            queue.put(('status', 'Розпізнавання мовлення…'))
            chunk_rows = []
            core = audio[max(0, round((left - offset) * RATE)):round((right - offset) * RATE)]
            speech = get_speech_timestamps(core, VadOptions(min_silence_duration_ms=400, speech_pad_ms=200,
                                                           min_speech_duration_ms=100, threshold=.35))
            language, confidence = job.language, 1.0
            if speech:
                if language == 'mixed':
                    voiced = np.concatenate([core[s['start']:s['end']] for s in speech])
                    _, _, probs = model.detect_language(voiced)
                    language, confidence = select_language(probs, previous)
                    previous = language
                    queue.put(('language', f'{language.upper()} · відносна впевненість {confidence:.0%}'))
                segments, _ = model.transcribe(
                    audio, language=language, task='transcribe',
                    beam_size=1 if job.model == 'base' else 3 if job.model in ('small', 'turbo') else 5,
                    temperature=0, condition_on_previous_text=False, word_timestamps=True,
                    vad_filter=True, vad_parameters={'min_silence_duration_ms': 400,
                                                     'speech_pad_ms': 200, 'threshold': .35},
                )
                for segment in segments:
                    if cancel.is_set():
                        break
                    words = owned_words(segment.words or [], offset, left, right)
                    if not words:
                        continue
                    row = {'start': max(job.start, offset + words[0].start),
                           'end': min(job.end, offset + words[-1].end),
                           'text': ''.join(w.word for w in words).strip(), 'language': language}
                    if row['text']:
                        chunk_rows.append(row)
            if cancel.is_set():
                break
            write_record(journal, {'type': 'checkpoint', 'position': right, 'language': previous, 'rows': chunk_rows})
            for row in chunk_rows:
                queue.put(('segment', row))
            queue.put(('checkpoint', right))
            processed = right - job.start
            total = job.end - job.start
            elapsed = time.monotonic() - started
            queue.put(('progress', (processed, total, elapsed * (total - processed) / max(right - resume_start, .001))))
        if cancel.is_set():
            queue.put(('done', 'Скасовано. Готові фрагменти збережено.'))
        elif processed < job.end - job.start - .5:
            queue.put(('error', 'Аудіо закінчилося раніше вказаної тривалості. Збережено доступний текст.'))
        else:
            write_record(journal, {'type': 'complete'})
            queue.put(('progress', (job.end - job.start, job.end - job.start, 0)))
            queue.put(('done', 'Готово. Текст можна скопіювати або зберегти.'))
    except Exception as exc:
        if cancel.is_set():
            queue.put(('done', 'Зупинено. Готові фрагменти збережено.'))
            return
        detail = traceback.format_exc()
        try:
            Path(job.session + '.error.log').write_text(detail, encoding='utf-8')
        except OSError:
            pass
        queue.put(('error', f'{type(exc).__name__}: {exc}\nСпробуйте CPU, меншу модель або перевірте аудіофайл.'))
    finally:
        if journal:
            journal.close()
