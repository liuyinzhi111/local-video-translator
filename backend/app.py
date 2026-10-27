from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
import hmac
import json
import logging
import re
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from .engines import LocalEngines
from .settings import ROOT, LANGUAGES, STREAMING_LANGUAGES, access_token, load_config, save_backend
from .streaming import TranslationStream
from .native_streaming import NativeTranslationStream

config = load_config()
token = access_token()
engines = LocalEngines(config)
active = None
load_task = None
shutdown_callback = None
VERSION = '0.2.0'


async def load_engines():
    try:
        await asyncio.to_thread(engines.load)
    except Exception:
        logging.exception('Local model initialization failed')


@asynccontextmanager
async def lifespan(app):
    global load_task
    load_task = asyncio.create_task(load_engines())
    yield
    if active:
        await active['stream'].close()
    if load_task:
        await load_task
    await asyncio.to_thread(engines.close)


app = FastAPI(title='本地视频翻译', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(CORSMiddleware, allow_origin_regex=r'chrome-extension://[a-p]{32}',
                  allow_methods=['GET', 'POST'], allow_headers=['Authorization', 'Content-Type'])


def authorize(request: Request):
    if not hmac.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + token):
        raise HTTPException(401, '连接码无效。请从本地控制台重新复制。')


@app.get('/')
def index():
    return FileResponse(ROOT / 'backend' / 'dashboard.html', headers={
        'Cache-Control': 'no-store',
        'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
    })


@app.get('/dashboard.js')
def dashboard_js():
    return FileResponse(ROOT / 'backend' / 'dashboard.js', media_type='application/javascript')


@app.get('/dashboard.css')
def dashboard_css():
    return FileResponse(ROOT / 'backend' / 'dashboard.css', media_type='text/css')


@app.get('/health')
def health():
    return {'service': 'local-video-translator', 'version': VERSION,
            'phase': engines.phase, 'ready': engines.phase == 'ready'}


@app.get('/api/status')
def status(request: Request):
    authorize(request)
    return {'phase': engines.phase, 'error': engines.error, 'load_seconds': engines.load_seconds,
            'running': active is not None, 'language': active['language'] if active else None,
            'completed_segments': active['stream'].completed if active else 0,
            'asr_backend': config.get('asr_backend', 'qwen'),
            'asr': 'Nemotron 3.5 · 流式' if config.get('asr_backend') == 'nemotron' else 'Qwen3-ASR 1.7B · 兼容',
            'supported_languages': list(STREAMING_LANGUAGES) if config.get('asr_backend') == 'nemotron' else list(LANGUAGES),
            'translation': 'Hy-MT2 1.8B · Q6_K',
            'extension_path': str(ROOT / 'extension'), 'version': VERSION}


@app.post('/api/mode')
async def set_mode(request: Request):
    global load_task
    authorize(request)
    data = await request.json()
    if active or engines.phase == 'unloading' or (load_task and not load_task.done()):
        raise HTTPException(409, '请先停止翻译并等待模型加载完成。')
    selected = data.get('backend')
    if selected not in ('nemotron', 'qwen'):
        raise HTTPException(400, '识别模式无效。')
    if selected == config.get('asr_backend'):
        return {'ok': True}
    engines.phase = 'unloading'
    async def change_mode():
        try:
            await asyncio.to_thread(engines.close)
            save_backend(selected)
            config['asr_backend'] = selected
            engines.phase = 'idle'
            await load_engines()
        except Exception as error:
            engines.phase = 'error'
            engines.error = str(error)
            logging.exception('Could not switch recognition mode')
    load_task = asyncio.create_task(change_mode())
    return {'ok': True}


@app.post('/api/load')
async def load(request: Request):
    global load_task
    authorize(request)
    if active:
        raise HTTPException(409, '请先停止网页翻译。')
    if engines.phase == 'unloading':
        raise HTTPException(409, '正在释放显存，请稍等。')
    if load_task and not load_task.done():
        return {'ok': True}
    load_task = asyncio.create_task(load_engines())
    return {'ok': True}


@app.post('/api/unload')
async def unload(request: Request):
    authorize(request)
    if active or engines.phase == 'unloading' or (load_task and not load_task.done()):
        raise HTTPException(409, '请先停止翻译并等待模型加载完成。')
    engines.phase = 'unloading'
    await asyncio.to_thread(engines.close)
    engines.phase = 'idle'
    return {'ok': True}


@app.post('/api/shutdown')
async def shutdown(request: Request):
    authorize(request)
    if active:
        await active['ws'].close(code=1001, reason='本地服务已关闭')
    if shutdown_callback:
        asyncio.get_running_loop().call_later(0.25, shutdown_callback)
    return {'ok': True}


@app.websocket('/stream')
async def stream_socket(ws: WebSocket):
    global active
    origin = ws.headers.get('origin', '')
    own_origin = f"http://127.0.0.1:{config['port']}"
    if origin != own_origin and not re.fullmatch(r'chrome-extension://[a-p]{32}', origin):
        await ws.close(code=1008)
        return
    await ws.accept()
    stream = None
    try:
        greeting = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not hmac.compare_digest(str(greeting.get('token', '')), token):
            await ws.close(code=1008, reason='连接码无效')
            return
        if engines.phase != 'ready':
            await ws.send_json({'type': 'error', 'message': '本地模型还没就绪，请等待控制台显示就绪。'})
            await ws.close(code=1013)
            return
        if active is not None:
            await ws.send_json({'type': 'error', 'message': '另一个网页正在翻译，请先停止它。'})
            await ws.close(code=1013)
            return
        language = greeting.get('language', 'auto')
        if language != 'auto' and language not in LANGUAGES:
            raise ValueError('原语言设置无效。')
        # No await between occupancy check and claim.
        send_lock = asyncio.Lock()

        async def send(payload):
            async with send_lock:
                await ws.send_json(payload)

        native = config.get('asr_backend') == 'nemotron'
        if native and language != 'auto' and language not in STREAMING_LANGUAGES:
            raise ValueError('当前流式模型不支持该语言，请在本地控制台切换“兼容模式”。')
        stream = NativeTranslationStream(engines, config, send, language) if native else TranslationStream(engines, config, send, language)
        active = {'stream': stream, 'ws': ws, 'language': language}
        if native:
            await stream.start()
        await send({'type': 'ready', 'epoch': stream.epoch, 'sample_rate': 16000})
        while not stream.closed:
            try:
                message = await asyncio.wait_for(ws.receive(), timeout=15)
            except asyncio.TimeoutError:
                await send({'type': 'ping'})
                continue
            if message['type'] == 'websocket.disconnect':
                break
            if message.get('bytes') is not None:
                stream.receive(message['bytes'])
            elif message.get('text') is not None:
                if len(message['text']) > 4096:
                    raise ValueError('控制消息太长。')
                data = json.loads(message['text'])
                kind = data.get('type')
                if kind == 'reset':
                    await stream.reset(str(data.get('reason', '切换视频'))[:100])
                elif kind == 'pause':
                    stream.pause()
                elif kind == 'resume':
                    stream.resume()
                elif kind == 'flush':
                    stream.flush()
                elif kind == 'stop':
                    break
                elif kind == 'pong':
                    pass
                else:
                    raise ValueError('未知控制消息。')
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        with contextlib.suppress(Exception):
            await ws.send_json({'type': 'error', 'message': str(exc)})
    finally:
        if stream:
            await stream.close()
            if active and active['stream'] is stream:
                active = None
        with contextlib.suppress(Exception):
            await ws.close()
