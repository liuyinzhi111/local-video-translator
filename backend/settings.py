from __future__ import annotations

import json
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LANGUAGES = json.loads((ROOT / 'extension' / 'languages.json').read_text(encoding='utf-8'))
STREAMING_LANGUAGES = {
    'en': 'en-US', 'es': 'es-ES', 'it': 'it-IT', 'fr': 'fr-FR', 'de': 'de-DE',
    'pt': 'pt-BR', 'ja': 'ja-JP', 'ko': 'ko-KR', 'ru': 'ru-RU', 'ar': 'ar-AR',
    'hi': 'hi-IN', 'vi': 'vi-VN', 'tr': 'tr-TR', 'nl': 'nl-NL', 'pl': 'pl-PL',
    'cs': 'cs-CZ', 'zh': 'zh-CN',
}


def load_config() -> dict:
    config = json.loads((ROOT / 'config.json').read_text(encoding='utf-8-sig'))
    for key in ('asr_model', 'translation_model', 'translation_runtime',
                'streaming_asr_model', 'streaming_asr_runtime'):
        if key not in config:
            continue
        value = Path(config[key])
        config[key] = str(value if value.is_absolute() else ROOT / value)
    if config['host'] != '127.0.0.1':
        raise ValueError('此程序只允许绑定 127.0.0.1。')
    return config


def save_backend(backend: str):
    if backend not in ('nemotron', 'qwen'):
        raise ValueError('识别模式无效。')
    path = ROOT / 'config.json'
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    data['asr_backend'] = backend
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def access_token() -> str:
    path = ROOT / 'runtime' / 'access.json'
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))['token']
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'token': token}), encoding='utf-8')
    return token
