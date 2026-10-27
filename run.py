from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.request import Request, urlopen
import webbrowser

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'runtime' / 'python-site'))
os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                  PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
if sys.stdout:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr:
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--server', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    from backend.settings import access_token, load_config
    config = load_config()
    base = f"http://127.0.0.1:{config['port']}"
    url = base + '/#' + access_token()
    # Check only our local service, without system HTTP proxy routing.
    import httpx
    if not args.server:
        try:
            with httpx.Client(trust_env=False, timeout=1.5) as client:
                found = client.get(base + '/health')
                if found.status_code == 200 and found.json().get('service') == 'local-video-translator':
                    if not args.no_browser:
                        webbrowser.open(url)
                    return
        except Exception:
            pass
        (ROOT / 'logs').mkdir(exist_ok=True)
        log = (ROOT / 'logs' / 'backend.log').open('a', encoding='utf-8')
        # Keep the desktop service alive when its launcher or development terminal exits.
        flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP |
                 subprocess.CREATE_BREAKAWAY_FROM_JOB) if os.name == 'nt' else 0
        subprocess.Popen([sys.executable, '-u', str(ROOT / 'run.py'), '--server'], cwd=str(ROOT),
                         stdout=log, stderr=subprocess.STDOUT,
                         creationflags=flags)
        for _ in range(80):
            try:
                with httpx.Client(trust_env=False, timeout=.5) as client:
                    response = client.get(base + '/health')
                    if response.status_code == 200:
                        if not args.no_browser:
                            webbrowser.open(url)
                        return
            except Exception:
                pass
            time.sleep(.25)
        if os.name == 'nt':
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, '本地服务未能启动，请查看项目 logs/backend.log。', '本地视频翻译', 0x10)
        raise SystemExit(1)
    import uvicorn
    from backend import app as backend
    server = uvicorn.Server(uvicorn.Config(backend.app, host='127.0.0.1', port=config['port'],
                                          ws='websockets', ws_max_size=65536,
                                          access_log=False, log_level='info'))
    backend.shutdown_callback = lambda: setattr(server, 'should_exit', True)
    server.run()


if __name__ == '__main__':
    main()
