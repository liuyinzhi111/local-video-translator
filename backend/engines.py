from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import threading
import time
from pathlib import Path

import httpx
import numpy as np

from .settings import ROOT, LANGUAGES


class LocalEngines:
    """Own one ASR and one MT process. Call inference from a serial worker."""

    def __init__(self, config: dict):
        self.config = config
        self.asr = None
        self.vad = None
        self.mt_process = None
        self.asr_process = None
        self.asr_log = None
        self.asr_key = secrets.token_urlsafe(24)
        self.asr_url = f"http://127.0.0.1:{config.get('streaming_asr_port', 18767)}"
        self.mt_log = None
        self.mt_key = secrets.token_urlsafe(24)
        self.mt_url = f"http://127.0.0.1:{config['translation_port']}"
        self.http = httpx.Client(timeout=30, trust_env=False)
        self.phase = 'idle'
        self.error = ''
        self.load_seconds = 0.0
        self._load_lock = threading.Lock()
        self._inference_lock = threading.Lock()

    def load(self):
        with self._load_lock:
            if self.phase == 'ready':
                return
            started = time.perf_counter()
            self.error = ''
            try:
                self.phase = 'loading_asr'
                if self.config.get('asr_backend') == 'nemotron':
                    self._start_streaming_asr()
                else:
                    import torch
                    from qwen_asr import Qwen3ASRModel
                    from silero_vad import load_silero_vad
                    if not torch.cuda.is_available():
                        raise RuntimeError('没有检测到可用的 NVIDIA CUDA 显卡。')
                    torch.set_num_threads(6)
                    self.vad = load_silero_vad(onnx=True)
                    torch.set_num_threads(6)
                    self.asr = Qwen3ASRModel.from_pretrained(
                        self.config['asr_model'], dtype=torch.bfloat16,
                        device_map='cuda:0', attn_implementation='sdpa',
                        max_inference_batch_size=1, max_new_tokens=256,
                        local_files_only=True,
                    )
                    self.asr.model.eval()
                # Warm up outside the first video utterance.
                self.phase = 'loading_translation'
                self._start_translation()
                self.phase = 'warming_up'
                self.translate('This is a local translation test.', 'English', [])
                self.load_seconds = round(time.perf_counter() - started, 2)
                self.phase = 'ready'
            except Exception as exc:
                self.error = str(exc)
                self.phase = 'error'
                self.close()
                raise

    def _start_streaming_asr(self):
        cfg = self.config
        for name in ('streaming_asr_runtime', 'streaming_asr_model'):
            if not Path(cfg[name]).is_file():
                raise FileNotFoundError(f'文件不存在：{cfg[name]}')
        (ROOT / 'logs').mkdir(exist_ok=True)
        self.asr_log = (ROOT / 'logs' / 'streaming-asr.log').open('w', encoding='utf-8')
        env = os.environ.copy()
        env['NEMO_SPEECH_HTTP_API_KEY'] = self.asr_key
        args = [cfg['streaming_asr_runtime'], 'serve', '--asr-model', cfg['streaming_asr_model'],
                '--host', '127.0.0.1', '--port', str(cfg['streaming_asr_port']),
                '--device', 'cuda:0', '--no-ui',
                '--asr.streaming.rnnt_right_context', str(cfg.get('streaming_right_context', 6)),
                '--asr.endpointing.enable=true',
                '--asr.endpointing.stop_history_eou_ms', str(cfg.get('streaming_endpoint_ms', 650))]
        self.asr_process = subprocess.Popen(args, env=env,
            cwd=str(Path(cfg['streaming_asr_runtime']).parent), stdout=self.asr_log,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        until = time.monotonic() + 90
        while time.monotonic() < until:
            if self.asr_process.poll() is not None:
                raise RuntimeError('流式识别启动失败，请查看 logs/streaming-asr.log。')
            try:
                response = self.http.get(self.asr_url + '/ready', timeout=2)
                if response.status_code == 200:
                    if response.json().get('device') != 'cuda:0':
                        raise RuntimeError('流式识别未运行在 NVIDIA 独显上。')
                    return
            except httpx.HTTPError:
                pass
            time.sleep(.2)
        raise RuntimeError('流式语音模型加载超时。')

    def _start_translation(self):
        cfg = self.config
        for key in ('translation_runtime', 'translation_model'):
            if not Path(cfg[key]).is_file():
                raise FileNotFoundError(f'文件不存在：{cfg[key]}')
        log_dir = ROOT / 'logs'
        log_dir.mkdir(exist_ok=True)
        self.mt_log = (log_dir / 'translation-runtime.log').open('w', encoding='utf-8')
        args = [cfg['translation_runtime'], '-m', cfg['translation_model'],
                '--host', '127.0.0.1', '--port', str(cfg['translation_port']),
                '-ngl', str(cfg['translation_gpu_layers']),
                '-c', str(cfg['translation_context']), '-np', '1',
                '-t', '6', '--jinja', '--api-key', self.mt_key, '--no-webui']
        self.mt_process = subprocess.Popen(
            args, cwd=str(Path(cfg['translation_runtime']).parent),
            stdout=self.mt_log, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
        )
        until = time.monotonic() + 120
        while time.monotonic() < until:
            if self.mt_process.poll() is not None:
                raise RuntimeError('翻译运行程序启动失败，请查看 logs/translation-runtime.log。')
            try:
                result = self.http.get(self.mt_url + '/health', timeout=2)
                if result.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        raise RuntimeError('翻译模型加载超时。')

    def transcribe(self, audio: np.ndarray, language: str = 'auto') -> tuple[str, str, float]:
        import torch
        forced_language = LANGUAGES.get(language, {}).get('model')
        started = time.perf_counter()
        with self._inference_lock, torch.inference_mode():
            result = self.asr.transcribe(
                audio=(np.asarray(audio, dtype=np.float32), 16000),
                language=forced_language, context='', return_time_stamps=False,
            )[0]
        text = re.sub(r'\s+', ' ', result.text).strip()
        return text, result.language or forced_language or '', time.perf_counter() - started

    def translate(self, text: str, language: str, history: list[tuple[str, str]]) -> tuple[str, float]:
        if not text.strip():
            return '', 0.0
        supported = {entry['model'] for entry in LANGUAGES.values()}
        detected = set(language.split(',')) - {''}
        if detected and not detected.issubset(supported):
            return f'〔检测到 {language}，当前模型组合暂不支持可靠的中文翻译〕', 0.0
        started = time.perf_counter()
        glossary = self.config.get('glossary', {})
        terms = [f'{key} → {value}' for key, value in glossary.items()
                 if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', text, flags=re.I)]
        parts = []
        # The small MT model sometimes retranslates supplied context. Keep the prompt
        # scoped to the current phrase; history remains available for overlap removal.
        if terms:
            parts.append('参考术语：\n' + '\n'.join(terms))
        parts.append('将以下待翻译文本翻译为简体中文，注意只需要输出翻译后的结果，不要额外解释。'
                     '保留人名、数字和否定含义。不补全未说完的句子。\n[待翻译文本]\n' + text)
        result = self.http.post(
            self.mt_url + '/v1/chat/completions',
            headers={'Authorization': 'Bearer ' + self.mt_key},
            json={'model': 'local-translator', 'messages': [{'role': 'user', 'content': '\n\n'.join(parts)}],
                  'temperature': 0.2, 'top_p': 0.6, 'top_k': 20,
                  'repeat_penalty': 1.05, 'max_tokens': 384, 'stream': False},
        )
        result.raise_for_status()
        data = result.json()
        if data['choices'][0].get('finish_reason') == 'length':
            raise RuntimeError('这一段译文超出长度限制，未显示不完整的翻译。')
        translated = data['choices'][0]['message']['content'].strip()
        translated = re.sub(r'^<think>.*?</think>\s*', '', translated, flags=re.S).strip()
        if not translated:
            raise RuntimeError('翻译模型返回空内容。')
        return translated, time.perf_counter() - started

    async def translate_stream(self, text: str, language: str, cancelled=None):
        """Yield cumulative Chinese output from llama.cpp as tokens arrive."""
        from .numbers import normalize_large_numbers
        text = normalize_large_numbers(text, language)
        terms = [f'{key} → {value}' for key, value in self.config.get('glossary', {}).items()
                 if re.search(r'(?<!\w)' + re.escape(key) + r'(?!\w)', text, flags=re.I)]
        prompt = ('参考术语：\n' + '\n'.join(terms) + '\n') if terms else ''
        prompt += ('将以下文本翻译为简体中文，只输出译文。保留人名、数字和否定含义。阿拉伯数字原样保留。'
                   '文本可能尚未说完，不要猜测后文，不要补充原文没有的信息。\n[待翻译文本]\n' + text)
        if language == 'Chinese':
            yield text, True
            return
        translated = ''
        async with httpx.AsyncClient(trust_env=False, timeout=15) as client:
            async with client.stream('POST', self.mt_url + '/v1/chat/completions',
                headers={'Authorization': 'Bearer ' + self.mt_key}, json={
                    'model': 'local-translator', 'messages': [{'role': 'user', 'content': prompt}],
                    'temperature': 0.1, 'top_p': 0.6, 'top_k': 20, 'repeat_penalty': 1.05,
                    'max_tokens': 384, 'stream': True}) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if cancelled and cancelled():
                        return
                    if not line.startswith('data: '):
                        continue
                    payload = line[6:]
                    if payload == '[DONE]':
                        break
                    choice = json.loads(payload).get('choices', [])
                    if not choice:
                        continue
                    choice = choice[0]
                    if choice.get('finish_reason') == 'length':
                        raise RuntimeError('这一段译文超出长度限制。')
                    token = choice.get('delta', {}).get('content') or ''
                    if token:
                        translated += token
                        yield translated.strip(), False
        if not translated.strip():
            raise RuntimeError('翻译模型返回空内容。')
        yield translated.strip(), True

    def close(self):
        if self.asr_process and self.asr_process.poll() is None:
            self.asr_process.terminate()
            try:
                self.asr_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.asr_process.kill()
                self.asr_process.wait(timeout=5)
        self.asr_process = None
        if self.asr_log:
            self.asr_log.close()
            self.asr_log = None
        if self.mt_process and self.mt_process.poll() is None:
            self.mt_process.terminate()
            try:
                self.mt_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.mt_process.kill()
                self.mt_process.wait(timeout=5)
        self.mt_process = None
        if self.mt_log:
            self.mt_log.close()
            self.mt_log = None
        self.asr = None
        self.vad = None
        import gc
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass
