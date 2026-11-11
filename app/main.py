import asyncio
import anyio
import contextlib
import secrets
import re
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Settings, ROOT
from .audio import devices
from .session import Session, safe_error
from .profile import InterviewSetup, ProfileStore
from .profile_api import LOCAL_ORIGINS, profile_routes

class Controller:
    def __init__(self, settings):
        self.settings = settings
        self.socket = None
        self.owner = None
        self.queue = None
        self.grace = None
        self.operations = set()
        self.session = Session(settings, self.emit)

    def emit(self, event):
        if self.queue:
            try:
                self.queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stuck UI must not leave billable audio running indefinitely.
                if self.socket:
                    asyncio.create_task(self.socket.close(code=1013))
                self.queue = None

    async def run(self, action):
        try:
            await action
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            self.emit({'type': 'error', 'message': safe_error(exc, 'Operation failed. Check audio device permissions, API configuration and network.')})
            self.emit(self.session.snapshot())

    def launch(self, action):
        task = asyncio.create_task(self.run(action))
        self.operations.add(task)
        task.add_done_callback(self.operations.discard)

    async def expire(self):
        await asyncio.sleep(self.settings.grace_seconds)
        await self.session.stop('Browser disconnected beyond the recovery period.')
        self.session.clear()
        self.owner = None

    async def close(self):
        if self.grace:
            self.grace.cancel()
        await self.session.stop('Backend shutting down.')
        for t in list(self.operations):
            t.cancel()
        await asyncio.gather(*self.operations, return_exceptions=True)
        await self.session.answers.close()


def create_app(settings=None, profile_store=None):
    settings = settings or Settings()
    control = Controller(settings)
    token = secrets.token_urlsafe(32)
    store = profile_store or ProfileStore(ROOT / '.runtime/professional-profile.json')

    def start_interview(data):
        try:
            setup = InterviewSetup.model_validate(data.get('interview', {}))
        except ValueError:
            raise ValueError('Check the interview context fields and their length limits.') from None
        interview = store.interview(setup)
        return control.session.start(int(data['mic']), int(data['system']), valid_length(data), interview)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await control.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.control = control
    app.include_router(profile_routes(store, token))
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', 'testserver'])

    @app.middleware('http')
    async def headers(request: Request, call_next):
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        return response

    @app.get('/')
    async def index():
        response = FileResponse(ROOT / 'app/static/index.html')
        response.set_cookie('ia_access', token, httponly=True, samesite='strict')
        return response

    @app.get('/health')
    async def health():
        return {'app': 'interview-assistant', 'status': 'ok'}

    @app.get('/api/devices')
    async def audio_devices(request: Request):
        if request.cookies.get('ia_access') != token:
            return JSONResponse({'error': 'Open the application first.'}, status_code=403)
        try:
            found = await asyncio.to_thread(devices)
            return {'devices': found, 'error': '' if found else 'No audio inputs are visible. Check macOS microphone permissions and restart outside the restricted development environment.'}
        except Exception:
            return {'devices': [], 'error': 'Audio devices could not be enumerated. Check macOS permissions and restart.'}

    @app.websocket('/ws')
    async def websocket(ws: WebSocket):
        origin = ws.headers.get('origin', '')
        client_id = ws.query_params.get('client', '')
        if (ws.cookies.get('ia_access') != token or origin not in
            LOCAL_ORIGINS or
            not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', client_id)):
            await ws.close(code=1008)
            return
        if control.socket or (control.owner and control.owner != client_id):
            await ws.close(code=4001, reason='Another tab controls this session. Use that tab or wait for its recovery timeout.')
            return
        await ws.accept()
        if control.grace:
            control.grace.cancel()
        control.owner, control.socket = client_id, ws
        q = control.queue = asyncio.Queue(maxsize=128)
        # One bounded snapshot handles reconnects without replaying an unbounded event backlog.
        await ws.send_json({'type': 'snapshot', 'state': control.session.snapshot(),
                            'history': list(control.session.history),
                            'partials': list(control.session.partials.values()),
                            'answers': list(control.session.suggestions)})

        async def send_events():
            while True:
                await ws.send_json(await q.get())

        async def receive_commands():
            while True:
                message = await ws.receive_text()
                if len(message.encode('utf-8')) > 65536:
                    await ws.close(code=1009)
                    return
                import json
                try:
                    data = json.loads(message)
                    cmd = data['cmd']
                    if cmd in ('start', 'test') and (control.operations or control.session.state != 'idle'):
                        raise ValueError('A session operation is already in progress. Stop it before starting another.')
                    if cmd == 'start':
                        control.launch(start_interview(data))
                    elif cmd == 'stop':
                        await control.session.stop()
                    elif cmd == 'test':
                        role = data['role']
                        if role not in ('me', 'interviewer'):
                            raise ValueError('Unknown source.')
                        control.launch(control.session.test(role, int(data['device'])))
                    elif cmd == 'length':
                        control.session.length = valid_length(data)
                        control.emit(control.session.snapshot())
                    elif cmd == 'clear':
                        control.session.clear()
                    elif cmd == 'answer':
                        if control.session.state != 'running':
                            raise ValueError('Start a session before requesting an answer.')
                        if control.session.answer_task and not control.session.answer_task.done():
                            raise ValueError('An answer is already being generated.')
                        control.session.request_answer()
                    else:
                        raise ValueError('Unknown action.')
                except (KeyError, TypeError, ValueError) as exc:
                    control.emit({'type': 'error', 'message': safe_error(exc, 'Invalid command.')})

        sender = asyncio.create_task(send_events())
        receiver = asyncio.create_task(receive_commands())
        try:
            done, pending = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                with contextlib.suppress(WebSocketDisconnect, RuntimeError):
                    t.result()
        except asyncio.CancelledError:
            pass
        finally:
            sender.cancel()
            receiver.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(sender, receiver, return_exceptions=True)
            if control.socket is ws:
                control.socket, control.queue = None, None
                control.grace = asyncio.create_task(control.expire())

    @app.post('/api/shutdown')
    async def shutdown(request: Request):
        import os
        expected = os.getenv('IA_SHUTDOWN_TOKEN', '')
        supplied = request.headers.get('x-ia-shutdown', '')
        if not expected or not secrets.compare_digest(expected, supplied):
            return JSONResponse({'error': 'Forbidden'}, status_code=403)
        callback = getattr(app.state, 'shutdown_callback', None)
        if callback:
            callback()
        return {'status': 'stopping'}

    app.mount('/static', StaticFiles(directory=ROOT / 'app/static'), name='static')
    return app


def valid_length(data):
    length = data.get('length', 'medium')
    if length not in ('short', 'medium', 'long'):
        raise ValueError('Choose Brief, Standard or Detailed answers.')
    return length

app = create_app()
