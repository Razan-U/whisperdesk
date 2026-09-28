"""Bounded-memory audio decoding. All times are relative to audio stream start."""
import math
import av
import numpy as np

RATE = 16000


def probe(path):
    with av.open(str(path)) as container:
        if not container.streams.audio:
            raise ValueError('Файл не містить аудіодоріжки.')
        stream = container.streams.audio[0]
        duration = (float(stream.duration * stream.time_base) if stream.duration is not None
                    else float(container.duration / av.time_base) if container.duration else 0)
        if duration <= 0 or not math.isfinite(duration):
            raise ValueError('Не вдалося визначити тривалість. Перетворіть файл у WAV або FLAC.')
        return duration


def decode_range(path, start, end, cancel):
    """Yield mono float32 packets without decoding the complete file into RAM.

    Seek to an earlier packet, then trim using resampled PTS. Fill actual timeline
    gaps with silence in bounded packets; never compress the original timestamps.
    """
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        origin = float((stream.start_time or 0) * stream.time_base)
        if start > 0:
            container.seek(int((origin + max(0, start - 2)) / stream.time_base),
                           stream=stream, backward=True)
        resampler = av.AudioResampler(format='fltp', layout='mono', rate=RATE)
        cursor = round(start * RATE)
        stop = round(end * RATE)
        fallback = 0

        def frames():
            for frame in container.decode(stream):
                if cancel.is_set():
                    return
                yield from resampler.resample(frame)
            yield from resampler.resample(None)

        for frame in frames():
            if cancel.is_set() or cursor >= stop:
                break
            if frame.pts is None and start > 0:
                raise ValueError('У файлі немає часових позначок для точного діапазону. Збережіть його як WAV.')
            pos = round((float(frame.pts * frame.time_base) - origin) * RATE) if frame.pts is not None else fallback
            samples = frame.to_ndarray().reshape(-1).astype(np.float32, copy=False)
            fallback = pos + len(samples)
            if fallback <= cursor:
                continue
            while cursor < min(pos, stop):
                size = min(RATE, pos - cursor, stop - cursor)
                yield np.zeros(size, dtype=np.float32)
                cursor += size
            lo = max(0, cursor - pos)
            hi = min(len(samples), stop - pos)
            if hi > lo:
                yield samples[lo:hi]
                cursor = pos + hi


def windows(path, start, end, cancel, core_seconds=24, overlap_seconds=.8):
    """Yield (samples, absolute offset, ownership start, ownership end).

    Keep one window plus left/right context in memory. Prefer a low-energy cut
    in the final two seconds. Ownership intervals are contiguous and disjoint.
    """
    buffer = np.empty(0, dtype=np.float32)
    base = round(start * RATE)
    owner = base
    hard_end = round(end * RATE)
    size = round(core_seconds * RATE)
    overlap = round(overlap_seconds * RATE)

    def take(final=False):
        nonlocal buffer, base, owner
        available = base + len(buffer)
        target = min(owner + size, hard_end)
        if not final and available < min(target + overlap, hard_end):
            return None
        if available <= owner:
            return None
        boundary = min(target, available)
        if boundary == target and target < hard_end and not final:
            # 100 ms energy bins: only move a cut if there is a genuine quiet gap.
            left = max(owner + RATE, target - 2 * RATE)
            candidates = []
            for p in range(left, target, RATE // 10):
                part = buffer[p - base:min(p - base + RATE // 10, len(buffer))]
                if len(part):
                    candidates.append((float(np.mean(part * part)), p + len(part) // 2))
            if candidates and min(candidates)[0] < .0001:
                boundary = min(candidates)[1]
        right = min(available, boundary + overlap, hard_end)
        result = (buffer[:right - base].copy(), base / RATE, owner / RATE, boundary / RATE)
        next_base = max(base, boundary - overlap)
        buffer = buffer[next_base - base:]
        base, owner = next_base, boundary
        return result

    for packet in decode_range(path, start, end, cancel):
        buffer = np.concatenate((buffer, packet))
        while not cancel.is_set():
            item = take()
            if item is None:
                break
            yield item
    while not cancel.is_set():
        item = take(final=True)
        if item is None:
            break
        yield item
