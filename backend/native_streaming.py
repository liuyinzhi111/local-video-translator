"""Cached ASR audio stream and an independent, coalescing translation worker."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import contextlib
import json
import re
import time

from websockets.asyncio.client import connect

from .settings import LANGUAGES, STREAMING_LANGUAGES


@dataclass(frozen=True)
class Phrase:
    id: int
    source: str
    final: bool
    audio_end: float
    created_at: float


def complete_prefix(text: str, final: bool) -> str:
    """RNNT deltas can end in the middle of a word. Never translate that fragment."""
    text = text.strip()
    if final or not text or re.search(r'[.,!?;:。！？；，：]$', text):
        return text
    if re.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', text):
        return text
    boundary = text.rfind(' ')
    return text[:boundary] if boundary >= 0 else ''


class PhrasePlanner:
    """Keep the editable region short; finalize only complete words/clauses."""

    def __init__(self, config, emit):
        self.config = config
        self.emit = emit
        self.next_id = 1
        self.consumed_words = 0
        self.consumed_chars = 0
        self.last_source = ''
        self.last_emitted_at = 0.0

    def update(self, text, final, audio_end, now=None):
        now = time.monotonic() if now is None else now
        usable = complete_prefix(text, final)
        cjk = bool(re.search(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]', usable))
        if cjk:
            remaining = usable[self.consumed_chars:].lstrip()
        else:
            words = list(re.finditer(r'\S+', usable))
            remaining = usable[words[self.consumed_words].start():] if len(words) > self.consumed_words else ''
        # Finish clauses when following words confirm their boundary. Otherwise cap the
        # growing translation window; old audio is still processed exactly once by ASR.
        while remaining and not final:
            boundary = 0
            for match in re.finditer(r'[.!?;。！？；]\s*', remaining):
                prefix, suffix = remaining[:match.end()], remaining[match.end():]
                size = len(prefix) if cjk else len(prefix.split())
                minimum = 14 if cjk else 4
                if size >= minimum and (len(suffix) >= 3 if cjk else len(suffix.split()) >= 2):
                    boundary = match.end()
                    break
            words = list(re.finditer(r'\S+', remaining))
            if not boundary and not cjk and len(words) >= self.config.get('streaming_phrase_max_words', 72):
                # Prefer a clause boundary even at the emergency context limit.
                commas = [m.end() for m in re.finditer(r'[,，]\s*', remaining)
                          if len(remaining[:m.end()].split()) >= 20]
                boundary = commas[-1] if commas else words[55].end()
            if not boundary and len(remaining) > self.config.get('streaming_phrase_max_chars', 500):
                boundary = 160 if cjk else next((m.end() for m in words if m.end() >= 400), len(remaining))
            if not boundary:
                break
            source = remaining[:boundary].strip()
            self.emit(Phrase(self.next_id, source, True, audio_end, now))
            self.consumed_words += len(source.split())
            self.consumed_chars += boundary
            self.next_id += 1
            self.last_source = ''
            remaining = remaining[boundary:].lstrip()
        enough = len(remaining) >= (4 if cjk else 8) and (cjk or len(remaining.split()) >= 2)
        interval = self.config.get('streaming_translation_interval', .55)
        if remaining and (final or (enough and remaining != self.last_source and now-self.last_emitted_at >= interval)):
            self.emit(Phrase(self.next_id, remaining, final, audio_end, now))
            self.last_source = remaining
            self.last_emitted_at = now
        if final:
            # Endpointing starts a new utterance while retaining native encoder context.
            if remaining or self.last_source:
                self.next_id += 1
            self.consumed_words = self.consumed_chars = 0
            self.last_source = ''
            self.last_emitted_at = 0.0


class NativeTranslationStream:
    def __init__(self, engine, config, send, language):
        self.engine, self.config, self.send, self.language = engine, config, send, language
        self.epoch = 0
        self.closed = False
        self.paused = False
        self.paused_at = None
        self.started = time.monotonic()
        self.received = self.completed = self.inference_count = 0
        self.sent_samples = 0
        self.native_base = 0.0
        self.native_processed = 0.0
        self.native_text = ''
        self.queued_audio_bytes = 0
        self.audio_queue = asyncio.Queue()
        self.pending = OrderedDict()
        self.cache = {}
        self.wakeup = asyncio.Event()
        self.commit_done = asyncio.Event()
        self.socket = None
        self.audio_worker = self.reader = self.worker = None
        self.planner = PhrasePlanner(config, self._queue)
        self.last_caption_id = 0
        self.first_caption_at = None
        self.last_final = None
        self.continuations = {}

    async def start(self):
        await self._connect()
        self.worker = asyncio.create_task(self._guard(self._translate_loop()))

    async def _connect(self):
        url = self.engine.asr_url.replace('http:', 'ws:') + '/v1/audio/transcriptions/realtime'
        self.socket = await connect(url, additional_headers={'Authorization': 'Bearer ' + self.engine.asr_key},
                                    proxy=None, ping_interval=15, ping_timeout=10, open_timeout=5)
        await asyncio.wait_for(self.socket.recv(), 5)
        await self.socket.send(json.dumps({'type': 'session.update', 'session': {
            'sample_rate': 16000, 'language': STREAMING_LANGUAGES.get(self.language, 'auto'),
            'endpointing_ms': self.config.get('streaming_endpoint_ms', 650),
            'automatic_punctuation': True, 'word_timestamps': True}}))
        response = json.loads(await asyncio.wait_for(self.socket.recv(), 5))
        if response.get('type') == 'error':
            raise RuntimeError(response.get('error', {}).get('message', '流式识别连接失败。'))
        self.audio_worker = asyncio.create_task(self._guard(self._send_audio()))
        self.reader = asyncio.create_task(self._guard(self._read_native()))

    async def _guard(self, operation):
        try:
            await operation
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if not self.closed:
                self.closed = True
                with contextlib.suppress(Exception):
                    await self.send({'type': 'error', 'message': f'流式翻译已停止：{error}'})

    def receive(self, raw):
        if self.closed or self.paused:
            return
        if len(raw) % 2 or len(raw) > 64000:
            raise ValueError('音频帧格式或长度无效。')
        self.received += len(raw) // 2
        self.queued_audio_bytes += len(raw)
        if self.queued_audio_bytes > 32000 * self.config.get('streaming_max_audio_backlog_seconds', 8):
            raise RuntimeError('识别速度持续落后，已停止采集，避免字幕越积越迟。')
        self.audio_queue.put_nowait(raw)

    def flush(self):
        if not self.closed:
            self.audio_queue.put_nowait(None)

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

    async def _send_audio(self):
        last_commit_samples = 0
        while not self.closed:
            raw = await self.audio_queue.get()
            try:
                if raw is None:
                    if self.sent_samples == last_commit_samples:
                        continue
                    self.commit_done.clear()
                    await self.socket.send('{"type":"input_audio_buffer.commit"}')
                    await asyncio.wait_for(self.commit_done.wait(), 8)
                    last_commit_samples = self.sent_samples
                else:
                    self.queued_audio_bytes -= len(raw)
                    await self.socket.send(raw)
                    self.sent_samples += len(raw) // 2
            finally:
                self.audio_queue.task_done()

    async def _read_native(self):
        async for raw in self.socket:
            event = json.loads(raw)
            kind = event.get('type', '')
            if kind == 'error':
                raise RuntimeError(event.get('error', {}).get('message', '语音识别错误'))
            if kind == 'input_audio_buffer.committed':
                self.native_base = self.sent_samples / 16000
                self.native_processed = self.native_base
                self.native_text = ''
                self.commit_done.set()
                continue
            if not kind.endswith(('.delta', '.completed')):
                continue
            final = kind.endswith('.completed')
            if final:
                text = event.get('transcript', '')
            else:
                self.native_text += event.get('delta', '')
                text = self.native_text
            self.native_processed = self.native_base + float(event.get('audio_processed', 0))
            audio_end = self.native_processed
            if final and event.get('words'):
                audio_end = self.native_base + event['words'][-1]['end']
            self.planner.update(text, final, audio_end)
            if final:
                self.native_text = ''
            # A blocked downstream model must never silently accumulate minutes of audio.
            if self.received / 16000 - self.native_processed > self.config.get('streaming_max_audio_backlog_seconds', 8):
                raise RuntimeError('语音识别持续落后，请停止其他占用显卡的任务后重试。')

    def _queue(self, phrase):
        previous = self.pending.get(phrase.id)
        if previous and previous.final:
            return
        self.pending[phrase.id] = phrase
        if len(self.pending) > 12:
            raise RuntimeError('翻译积压过多，未跳过已说出的内容。请重新开始。')
        self.wakeup.set()

    async def _caption(self, phrase, text, done, started, epoch, source=None, replace_id=None):
        if self.closed or self.epoch != epoch:
            return
        if not text:
            return
        now = time.monotonic()
        if self.first_caption_at is None:
            self.first_caption_at = now
        self.last_caption_id = phrase.id
        await self.send({
            'type': 'caption', 'id': phrase.id, 'epoch': epoch, 'source': source or phrase.source,
            'raw_source': phrase.source, 'translation': text, 'final': phrase.final and done,
            'replace_id': replace_id,
            'translation_streaming': not done, 'language': LANGUAGES.get(self.language, {}).get('model', 'auto'),
            'audio_end': round(phrase.audio_end, 3),
            'translation_ms': round((now-started)*1000), 'asr_ms': None,
            'processing_ms': round((now-started)*1000),
            # Transport/processing offset only; not advertised as speech-to-subtitle latency.
            'lag_seconds': round(max(0, now-self.started-phrase.audio_end), 3),
            'waiting': len(self.pending), 'streaming': True,
            'first_caption_seconds': round(self.first_caption_at-self.started, 3),
        })

    async def _translate_loop(self):
        while not self.closed:
            await self.wakeup.wait()
            if self.closed:
                return
            if not self.pending:
                self.wakeup.clear()
                continue
            identifier, phrase = self.pending.popitem(last=False)
            if not self.pending:
                self.wakeup.clear()
            epoch = self.epoch
            started = time.monotonic()
            # Acoustic endpointing also fires at rhetorical pauses. Keep a bounded
            # unfinished sentence together, including its negation and subject.
            if identifier not in self.continuations:
                previous = self.last_final
                can_join = bool(previous and phrase.created_at-previous['at'] < 4.5
                    and not re.search(r'[.!?。！？]$', previous['source'])
                    and len(previous['source']) <= 500)
                self.continuations[identifier] = previous if can_join else None
            continuation = self.continuations[identifier]
            source = (continuation['source'] + ' ' if continuation else '') + phrase.source
            replace_id = continuation['id'] if continuation else None
            cached = self.cache.get(identifier)
            if cached and cached[0] == source:
                text = cached[1]
                await self._caption(phrase, text, True, started, epoch, source, replace_id)
            else:
                previous_emit = 0.0
                text = ''
                async for text, done in self.engine.translate_stream(
                    source, LANGUAGES.get(self.language, {}).get('model', ''),
                    cancelled=lambda: self.closed or self.epoch != epoch):
                    if self.closed or self.epoch != epoch:
                        break
                    now = time.monotonic()
                    if done or (len(text) >= 3 and now-previous_emit >= .06):
                        await self._caption(phrase, text, done, started, epoch, source, replace_id)
                        previous_emit = now
                if self.epoch == epoch and text:
                    self.cache[identifier] = (source, text)
            if self.epoch != epoch:
                continue
            self.inference_count += 1
            if phrase.final:
                self.completed += 1
                self.cache.pop(identifier, None)
                self.continuations.pop(identifier, None)
                self.last_final = {'id': identifier, 'source': source, 'at': phrase.created_at}

    async def reset(self, reason=''):
        self.epoch += 1
        self.pending.clear()
        self.cache.clear()
        self.continuations.clear()
        self.last_final = None
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        await self._disconnect_native()
        self.audio_queue = asyncio.Queue()
        self.queued_audio_bytes = self.sent_samples = self.received = self.completed = 0
        self.native_base = self.native_processed = 0.0
        self.native_text = ''
        self.paused = False
        self.paused_at = None
        self.started = time.monotonic()
        self.first_caption_at = None
        self.planner = PhrasePlanner(self.config, self._queue)
        await self._connect()
        self.worker = asyncio.create_task(self._guard(self._translate_loop()))
        await self.send({'type': 'clear', 'epoch': self.epoch, 'reason': reason})

    async def _disconnect_native(self):
        tasks = [task for task in (self.audio_worker, self.reader) if task]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.socket:
            await self.socket.close()
        self.socket = None

    async def close(self):
        self.closed = True
        self.pending.clear()
        if self.worker:
            self.worker.cancel()
        await self._disconnect_native()
        if self.worker:
            await asyncio.gather(self.worker, return_exceptions=True)
