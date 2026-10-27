import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import threading
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from backend.settings import load_config
from backend.streaming import SpeechSegments, TranslationStream, Snapshot, RATE, remove_overlap


class SegmentationTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config()

    def segmenter(self):
        return SpeechSegments(self.config, lambda frame: float(np.max(np.abs(frame)) > .1))

    def test_silence_never_creates_captions(self):
        stream = self.segmenter()
        for _ in range(200):
            self.assertEqual(stream.feed(np.zeros(1280, dtype=np.float32)), [])
        self.assertEqual(stream.flush(), [])
        self.assertLessEqual(sum(len(x) for x in stream.preroll), 32*512)

    def test_natural_pause_finalizes_and_preserves_audio(self):
        stream = self.segmenter()
        source = np.full(3*RATE, .2, dtype=np.float32)
        events = stream.feed(source) + stream.feed(np.zeros(RATE, dtype=np.float32))
        finals = [event for event in events if event.final]
        self.assertEqual(len(finals), 1)
        self.assertGreaterEqual(np.count_nonzero(finals[0].audio), len(source)-512)
        self.assertTrue(any(not event.final for event in events))

    def test_long_speech_uses_bounded_overlapping_windows(self):
        self.config['maximum_segment_seconds'] = 9
        stream = self.segmenter()
        events = []
        for _ in range(600):
            events.extend(stream.feed(np.full(1280,.2,dtype=np.float32)))
        events.extend(stream.flush())
        finals = [e for e in events if e.final]
        self.assertGreaterEqual(len(finals), 5)
        self.assertTrue(all(len(e.audio) <= 9*RATE+512 for e in finals))
        self.assertTrue(any(e.overlapped for e in finals[1:]))
        for before, after in zip(finals, finals[1:]):
            self.assertLessEqual(after.start, before.end)

    def test_overlap_only_removes_matching_boundary(self):
        self.assertEqual(remove_overlap('This is a language model.', 'language model. It runs locally.'), 'It runs locally.')
        self.assertEqual(remove_overlap('I do not agree.', 'This does not work.'), 'This does not work.')
        self.assertEqual(remove_overlap('This is a', 'a good test'), 'a good test')

    def test_flush_discards_tiny_noise(self):
        stream=self.segmenter()
        stream.feed(np.full(2000,.2,dtype=np.float32))
        self.assertEqual(stream.flush(), [])


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_short_continuation_replaces_previous_caption_with_context(self):
        class Engine:
            text='The meeting is open to everyone.'
            def transcribe(self,audio,language):return self.text,'English',.01
            def translate(self,text,language,history):return text,.01
        engine=Engine();messages=[];completed=asyncio.Event()
        async def send(event):messages.append(event);completed.set()
        def segmenter(stream):
            stream.segmenter=SpeechSegments(stream.config,lambda frame:1.0,stream.epoch)
        with patch.object(TranslationStream,'_reset_segmenter',segmenter):
            stream=TranslationStream(engine,load_config(),send,'auto')
            stream._queue(Snapshot(0,1,np.zeros(3*RATE,dtype=np.float32),0,3*RATE,True))
            await asyncio.wait_for(completed.wait(),2)
            completed.clear();engine.text='It is free.'
            stream._queue(Snapshot(0,2,np.zeros(RATE,dtype=np.float32),4*RATE,5*RATE,True))
            await asyncio.wait_for(completed.wait(),2)
            await stream.close()
        self.assertEqual(messages[-1]['source'],'The meeting is open to everyone. It is free.')
        self.assertEqual(messages[-1]['replace_id'],1)

    async def test_seek_discards_inflight_result(self):
        entered, release = threading.Event(), threading.Event()
        class Engine:
            def transcribe(self, audio, language):
                entered.set()
                release.wait(3)
                return 'old video', 'English', .1
            def translate(self, text, language, history):
                return '旧视频', .1
        messages=[]
        async def send(event): messages.append(event)
        def segmenter(stream):
            stream.segmenter=SpeechSegments(stream.config,lambda frame:1.0,stream.epoch)
        with patch.object(TranslationStream,'_reset_segmenter',segmenter):
            stream=TranslationStream(Engine(),load_config(),send,'auto')
            stream._queue(Snapshot(0,1,np.zeros(RATE,dtype=np.float32),0,RATE,True))
            await asyncio.wait_for(asyncio.to_thread(entered.wait),2)
            await stream.reset('seek')
            release.set()
            await asyncio.sleep(.1)
            await stream.close()
        self.assertEqual([x['type'] for x in messages],['clear'])

    async def test_language_switch_does_not_reuse_previous_language(self):
        captured=[]
        class Engine:
            language='Spanish'
            def transcribe(self,audio,language): return self.language, self.language, .01
            def translate(self,text,language,history):
                captured.append((language,list(history)))
                return '译文',.01
        engine=Engine()
        completed=asyncio.Event()
        async def send(event): completed.set()
        def segmenter(stream):
            stream.segmenter=SpeechSegments(stream.config,lambda frame:1.0,stream.epoch)
        with patch.object(TranslationStream,'_reset_segmenter',segmenter):
            stream=TranslationStream(engine,load_config(),send,'auto')
            stream._queue(Snapshot(0,1,np.zeros(RATE,dtype=np.float32),0,RATE,True))
            await asyncio.wait_for(completed.wait(),2)
            completed.clear();engine.language='Italian'
            stream._queue(Snapshot(0,2,np.zeros(RATE,dtype=np.float32),RATE,2*RATE,True))
            await asyncio.wait_for(completed.wait(),2)
            await stream.close()
        self.assertEqual(captured,[('Spanish',[]),('Italian',[])])


if __name__ == '__main__':
    unittest.main()
