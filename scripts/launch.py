"""Single process launcher. No credentials or transcript are logged."""
import fcntl
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
RUNTIME = ROOT / '.runtime'
URL = 'http://127.0.0.1:8765'

def health():
    try:
        with urllib.request.urlopen(URL + '/health', timeout=.5) as response:
            return json.load(response).get('app') == 'interview-assistant'
    except (OSError, ValueError):
        return False

def browser():
    if '--no-browser' in sys.argv:
        return
    result = subprocess.run(['/usr/bin/open', '-a', 'Brave Browser', URL], capture_output=True)
    if result.returncode:
        print('Brave could not open. Open this address yourself: ' + URL)

def acquire_or_existing(lock, timeout=15):
    """A stopped process can release its lock after its listening socket closes."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if health():
                return False
            if time.monotonic() >= deadline:
                raise SystemExit('The previous app is still shutting down after 15 seconds. No second backend was started.')
            time.sleep(.1)


def wait_for_shutdown(timeout=15):
    deadline = time.monotonic() + timeout
    with (RUNTIME / 'backend.lock').open('a') as probe:
        while True:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(probe, fcntl.LOCK_UN)
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise SystemExit('Shutdown has not finished after 15 seconds. The app has not reported itself stopped.')
                time.sleep(.1)


def stop():
    try:
        token = (RUNTIME / 'stop-token').read_text().strip()
        request = urllib.request.Request(URL + '/api/shutdown', data=b'', headers={'X-IA-Shutdown': token}, method='POST')
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise OSError()
        wait_for_shutdown()
        print('Interview Assistant has stopped. Audio sessions and the backend are closed.')
    except (OSError, ValueError):
        print('No reachable Interview Assistant to stop. If its Terminal is still open, press Control-C there.')

def main():
    if '--stop' in sys.argv:
        stop()
        return
    os.umask(0o077)
    RUNTIME.mkdir(exist_ok=True, mode=0o700)
    lock = (RUNTIME / 'backend.lock').open('w')
    if not acquire_or_existing(lock):
        browser()
        print('The existing Interview Assistant is already running.')
        lock.close()
        return
    token = secrets.token_urlsafe(32)
    os.environ['IA_SHUTDOWN_TOKEN'] = token
    token_file = RUNTIME / 'stop-token'
    token_file.write_text(token)
    token_file.chmod(0o600)
    import logging
    for name in ['assemblyai', 'groq', 'httpx', 'httpcore']:
        logging.getLogger(name).setLevel(logging.CRITICAL)
    import uvicorn
    from app.main import app
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765,
                                          workers=1, access_log=False, log_level='warning', timeout_graceful_shutdown=5))
    app.state.shutdown_callback = lambda: setattr(server, 'should_exit', True)
    def open_when_ready():
        for _ in range(80):
            if server.started:
                browser()
                return
            if server.should_exit:
                return
            time.sleep(.1)
    opener = threading.Thread(target=open_when_ready, daemon=True)
    opener.start()
    print('Interview Assistant: ' + URL)
    print('Keep this Terminal open. Use STOP ALL in the app to end live streaming; Control-C or the Stop launcher exits the backend.')
    print('No audio or paid session starts automatically.')
    try:
        server.run()
    finally:
        token_file.unlink(missing_ok=True)
        lock.close()

if __name__ == '__main__':
    main()
