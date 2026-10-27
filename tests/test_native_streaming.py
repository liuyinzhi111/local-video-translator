import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'runtime/python-site')]
from backend.native_streaming import NativeTranslationStream, PhrasePlanner, Phrase, complete_prefix
from backend.settings import load_config
from backend.numbers import normalize_large_numbers


class PhraseTests(unittest.TestCase):
    def test_large_spoken_numbers_are_preserved_without_guessing_language(self):
        self.assertEqual(normalize_large_numbers('Il monte raggiunge quattromila ottocento novantadue metri.'),
                         'Il monte raggiunge 4892 metri.')
        self.assertEqual(normalize_large_numbers('The distance is two thousand three hundred meters.'),
                         'The distance is 2300 meters.')
        self.assertEqual(normalize_large_numbers('Le four est chaud, et cent personnes attendent.'),
                         'Le four est chaud, et cent personnes attendent.')
        self.assertEqual(normalize_large_numbers('La distance est de trois mille deux cent cinquante mètres.'),
                         'La distance est de 3250 mètres.')
        self.assertEqual(normalize_large_numbers('Thousands of people travel thousands of miles.'),
                         'Thousands of people travel thousands of miles.')
    def test_incomplete_word_is_not_sent_to_translation(self):
        self.assertEqual(complete_prefix('Now widely ava', False), 'Now widely')
        self.assertEqual(complete_prefix('Now widely available', True), 'Now widely available')

    def test_long_utterance_preserves_every_word_in_order(self):
        jobs = []
        planner = PhrasePlanner(load_config(), jobs.append)
        text = ('When you call someone who is thousands of miles away, you are using a satellite. '
                'It transmits the signal across the ocean to another country and helps you speak '
                'to people who are far away without needing to travel there yourself.')
        words = text.split()
        for count in range(1, len(words)+1):
            planner.update(' '.join(words[:count]), False, count/3, count/3)
        planner.update(text, True, len(words)/3, len(words)/3+.6)
        finals = [job.source for job in jobs if job.final]
        self.assertEqual(' '.join(finals).split(), words)
        self.assertGreater(len(finals), 1)
        self.assertTrue(any(not job.final for job in jobs))

    def test_negated_tail_keeps_its_subject_and_verb(self):
        jobs=[]
        planner=PhrasePlanner(load_config(),jobs.append)
        text=('le passage de l\'absolutisme au républicanisme, le nationalisme et la conviction '
              'que le pays appartient au peuple et non à un seul dirigeant')
        for count in range(1,len(text.split())+1):
            planner.update(' '.join(text.split()[:count]),False,count/3,count/3)
        planner.update(text,True,10,10)
        self.assertEqual([job.source for job in jobs if job.final],[text])

    def test_cjk_sentence_boundaries_do_not_drop_characters(self):
        jobs = []
        planner = PhrasePlanner(load_config(), jobs.append)
        text = '这是为了确认连续字幕不会丢失前面的文字。后面的内容应该接着显示，并且保持它原来的意思。'
        for count in range(4, len(text), 4):
            planner.update(text[:count], False, count/5, count/5)
        planner.update(text, True, len(text)/5, len(text)/5+.6)
        self.assertEqual(''.join(job.source for job in jobs if job.final), text)


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_audio_is_accepted_while_translation_waits(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Engine:
            async def translate_stream(self, text, language, cancelled=None):
                entered.set()
                await release.wait()
                yield '新的字幕', True
        messages=[]
        async def send(event): messages.append(event)
        stream=NativeTranslationStream(Engine(),load_config(),send,'en')
        stream.worker=asyncio.create_task(stream._translate_loop())
        stream._queue(Phrase(1,'This is a phrase.',False,1,0))
        await asyncio.wait_for(entered.wait(),1)
        stream.receive(bytes(2560))
        self.assertEqual(stream.received,1280)
        self.assertFalse(stream.audio_queue.empty())
        release.set()
        await asyncio.sleep(.02)
        await stream.close()
        self.assertEqual(messages[-1]['translation'],'新的字幕')

    async def test_seek_drops_an_inflight_translation(self):
        entered, release = asyncio.Event(), asyncio.Event()
        class Engine:
            async def translate_stream(self, text, language, cancelled=None):
                entered.set()
                await release.wait()
                yield '旧画面的字幕', True
        messages=[]
        async def send(event): messages.append(event)
        stream=NativeTranslationStream(Engine(),load_config(),send,'en')
        stream.worker=asyncio.create_task(stream._translate_loop())
        stream._queue(Phrase(1,'Old video content.',False,1,0))
        await asyncio.wait_for(entered.wait(),1)
        async def connect_(): pass
        with patch.object(stream,'_connect',connect_):
            await stream.reset('seek')
        release.set()
        await asyncio.sleep(.02)
        await stream.close()
        self.assertEqual([event['type'] for event in messages],['clear'])

    async def test_final_pending_content_cannot_be_replaced_by_draft(self):
        async def send(event): pass
        stream=NativeTranslationStream(None,load_config(),send,'en')
        stream._queue(Phrase(1,'Final content',True,1,0))
        stream._queue(Phrase(1,'Older draft',False,.8,0))
        self.assertEqual(stream.pending[1].source,'Final content')
        await stream.close()


if __name__=='__main__':
    unittest.main()
