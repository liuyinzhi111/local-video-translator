from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass
import re
import time
from typing import Callable

import numpy as np

RATE = 16000
FRAME = 512


@dataclass(frozen=True)
class Snapshot:
    epoch: int
    segment: int
    audio: np.ndarray
    start: int
    end: int
    final: bool
    overlapped: bool = False


class SpeechSegments:
    """Bounded speech windows. Silence is not sent to ASR; final windows are never coalesced."""

    def __init__(self, config: dict, score: Callable[[np.ndarray], float], epoch: int = 0):
        self.config = config
        self.score = score
        self.epoch = epoch
        self.received = 0
        self.processed = 0
        self.pending = np.empty(0, dtype=np.float32)
        self.preroll = deque(maxlen=32)
        self.frames: list[np.ndarray] = []
        self.segment = 0
        self.start = 0
        self.voiced = 0
        self.quiet = 0
        self.last_partial = 0
        self.overlapped = False

    def feed(self, samples: np.ndarray) -> list[Snapshot]:
        self.received += len(samples)
        self.pending = np.concatenate((self.pending, samples))
        outputs = []
        while len(self.pending) >= FRAME:
            frame = self.pending[:FRAME].copy()
            self.pending = self.pending[FRAME:]
            self.processed += FRAME
            voiced = self.score(frame) >= self.config['speech_threshold']
            if not self.frames:
                if not voiced:
                    self.preroll.append(frame)
                    continue
                self.segment += 1
                self.frames = list(self.preroll)
                self.start = self.processed - FRAME - len(self.frames) * FRAME
                self.preroll.clear()
                self.last_partial = self.start
                self.voiced = 0
                self.quiet = 0
            self.frames.append(frame)
            if voiced:
                self.voiced += FRAME
                self.quiet = 0
            else:
                self.quiet += FRAME
            duration = self.processed - self.start
            enough_speech = self.voiced >= RATE * self.config['minimum_speech_seconds']
            quiet_end = self.quiet >= RATE * self.config['silence_seconds']
            forced_end = duration >= RATE * self.config['maximum_segment_seconds']
            if quiet_end or forced_end:
                if enough_speech:
                    outputs.append(self._snapshot(True))
                # Small overlap only at a forced cut, never on naturally repeated phrases.
                carry = self.frames[-10:] if forced_end and not quiet_end else []
                self.frames = []
                self.preroll.clear()
                self.preroll.extend(carry)
                self.overlapped = bool(carry)
                self.voiced = self.quiet = 0
            elif enough_speech and self.processed - self.last_partial >= RATE * self.config['partial_interval_seconds']:
                outputs.append(self._snapshot(False))
                self.last_partial = self.processed
        return outputs

    def _snapshot(self, final: bool) -> Snapshot:
        audio = np.concatenate(self.frames)
        # Keep a small final pause for punctuation, without paying for all of the silence.
        trim = max(0, self.quiet - int(0.16 * RATE)) if final else 0
        if trim:
            audio = audio[:-trim]
        return Snapshot(self.epoch, self.segment, audio, self.start,
                        self.start + len(audio), final, self.overlapped)

    def flush(self) -> list[Snapshot]:
        outputs = []
        if self.frames and self.voiced >= RATE * self.config['minimum_speech_seconds']:
            outputs.append(self._snapshot(True))
        self.frames = []
        self.preroll.clear()
        self.pending = np.empty(0, dtype=np.float32)
        self.processed = self.received
        self.voiced = self.quiet = 0
        self.overlapped = False
        return outputs


def remove_overlap(previous: str, current: str) -> str:
    """Only used when the audio windows overlap at the forced maximum length."""
    old = list(re.finditer(r"[\w]+(?:['’][\w]+)*", previous.casefold()))
    new = list(re.finditer(r"[\w]+(?:['’][\w]+)*", current.casefold()))
    for count in range(min(8, len(old), len(new)), 0, -1):
        if [m.group() for m in old[-count:]] == [m.group() for m in new[:count]]:
            if count == 1 and len(new[0].group()) < 5:
                continue
            return current[new[count - 1].end():].lstrip(' ,.;:!?—-')
    return current


class TranslationStream:
    def __init__(self, engine, config: dict, send: Callable, language: str):
        self.engine = engine
        self.config = config
        self.send = send
        self.language = language
        self.epoch = 0
        self.closed = False
        self.paused = False
        self.paused_at = None
        self.history: deque[tuple[str, str]] = deque(maxlen=2)
        self.history_language = ''
        self.last_final = None
        self.pending: OrderedDict[tuple[int, int], Snapshot] = OrderedDict()
        self.wakeup = asyncio.Event()
        self.cache = {}
        self.started = time.monotonic()
        self.received = 0
        self.completed = 0
        self.inference_count = 0
        self._reset_segmenter()
        self.worker = asyncio.create_task(self._worker())

    def _reset_segmenter(self):
        self.engine.vad.reset_states()
        import torch

        def score(frame):
            # VAD alone gets a bounded gain boost; original audio and playback stay unchanged.
            # This prevents loud clips from masking a following quiet speaker in recurrent VAD state.
            detector_audio = np.clip(frame * self.config.get('vad_gain', 5.0), -1.0, 1.0)
            return float(self.engine.vad(torch.from_numpy(detector_audio), RATE).item())

        self.segmenter = SpeechSegments(self.config, score, self.epoch)

    def receive(self, raw: bytes):
        if self.closed or self.paused:
            return
        if len(raw) % 2 or len(raw) > RATE * 2 * 2:
            raise ValueError('音频帧格式或长度无效。')
        samples = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0
        for snapshot in self.segmenter.feed(samples):
            self._queue(snapshot)
        self.received = self.segmenter.received
        if self.pending:
            oldest = next(iter(self.pending.values()))
            if (self.received - oldest.start) / RATE > self.config['maximum_backlog_seconds']:
                raise RuntimeError('本机处理速度持续落后，已停止采集。请关闭其他占用显卡的程序后重新开始。')

    def _queue(self, snapshot: Snapshot):
        key = (snapshot.epoch, snapshot.segment)
        previous = self.pending.get(key)
        if previous is not None and previous.final:
            return
        self.pending[key] = snapshot
        self.wakeup.set()

    def flush(self):
        for snapshot in self.segmenter.flush():
            self._queue(snapshot)

    def pause(self):
        if not self.paused:
            self.flush()
            self.paused = True
            self.paused_at = time.monotonic()

    def resume(self):
        if self.paused_at is not None:
            self.started += time.monotonic() - self.paused_at
        self.paused_at = None
        self.paused = False

    async def reset(self, reason: str = ''):
        self.epoch += 1
        self.pending.clear()
        self.history.clear()
        self.history_language = ''
        self.last_final = None
        self.cache.clear()
        self.started = time.monotonic()
        self.paused = False
        self.paused_at = None
        self.received = self.completed = 0
        self._reset_segmenter()
        await self.send({'type': 'clear', 'epoch': self.epoch, 'reason': reason})

    async def _worker(self):
        try:
            while not self.closed:
                await self.wakeup.wait()
                if self.closed:
                    return
                if not self.pending:
                    self.wakeup.clear()
                    continue
                key, snapshot = self.pending.popitem(last=False)
                if not self.pending:
                    self.wakeup.clear()
                epoch = self.epoch
                queued_at = time.monotonic()
                text, detected, asr_time = await asyncio.to_thread(
                    self.engine.transcribe, snapshot.audio, self.language)
                if self.closed or epoch != self.epoch:
                    continue
                if detected and detected != self.history_language:
                    self.history.clear()
                    self.history_language = detected
                    self.last_final = None
                # A finished version waiting behind this draft supersedes the draft.
                replacement = self.pending.get(key)
                if not snapshot.final and replacement and replacement.final:
                    continue
                if snapshot.overlapped and self.history:
                    text = remove_overlap(self.history[-1][0], text)
                if not text:
                    if snapshot.final:
                        self.cache.pop(key, None)
                        await self.send({'type': 'caption', 'id': snapshot.segment,
                                         'epoch': epoch, 'source': '', 'translation': '', 'final': True})
                    continue
                # A brief continuation can be ambiguous alone ("It is unity", "Not yet").
                # Translate it together with one recent phrase and replace that caption,
                # rather than asking the small MT model to ignore a separate context block.
                source = text
                previous = self.last_final
                joined = bool(previous and previous['joined'] == 0 and
                              snapshot.start - previous['end'] <= 2 * RATE and
                              len(snapshot.audio) <= 5.5 * RATE and len(text) <= 80 and
                              len(previous['source']) + len(text) <= 240)
                if joined:
                    source = previous['source'] + ' ' + text
                cached = self.cache.get(key)
                if cached and cached[0] == source:
                    translated, mt_time = cached[1], 0.0
                else:
                    translated, mt_time = await asyncio.to_thread(
                        self.engine.translate, source, detected, list(self.history))
                if self.closed or epoch != self.epoch:
                    continue
                self.cache[key] = (source, translated)
                if snapshot.final:
                    self.history.append((text, translated))
                    self.cache.pop(key, None)
                    self.last_final = {'id': snapshot.segment, 'end': snapshot.end,
                                       'source': source, 'joined': int(joined)}
                self.completed += int(snapshot.final)
                self.inference_count += 1
                lag = max(0.0, time.monotonic() - self.started - snapshot.end / RATE)
                await self.send({
                    'type': 'caption', 'id': snapshot.segment, 'epoch': epoch,
                    'source': source, 'raw_source': text, 'translation': translated, 'language': detected,
                    'replace_id': previous['id'] if joined else None,
                    'final': snapshot.final, 'audio_start': round(snapshot.start / RATE, 3),
                    'audio_end': round(snapshot.end / RATE, 3),
                    'asr_ms': round(asr_time * 1000), 'translation_ms': round(mt_time * 1000),
                    'processing_ms': round((time.monotonic() - queued_at) * 1000),
                    'lag_seconds': round(lag, 2), 'waiting': len(self.pending),
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.closed = True
            try:
                await self.send({'type': 'error', 'message': str(exc)})
            except Exception:
                pass

    async def close(self):
        self.closed = True
        self.pending.clear()
        self.wakeup.set()
        # Await outstanding inference before another session may use GPU or VAD state.
        await self.worker
