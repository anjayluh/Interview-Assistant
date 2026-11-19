import asyncio
import json
from unittest.mock import AsyncMock
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from app.config import Settings
from app.main import create_app
from app.transcription import Transcriber
from tests.test_core import FakeCapture, FakeTranscriber, FakeAnswers
from tests.live_helpers import start_command

ORIGIN={'origin':'http://testserver'}
CLIENT='client-1234567890123456'

def test_http_websocket_and_no_automatic_capture():
    app=create_app(Settings(assembly_key='',groq_key=''))
    with TestClient(app) as client:
        assert client.get('/api/devices').status_code==403
        response=client.get('/')
        assert response.status_code==200
        assert 'frame-ancestors' in response.headers['content-security-policy']
        assert 'HttpOnly' in response.headers['set-cookie']
        assert client.get('/health').json()['app']=='interview-assistant'
        assert client.get('/static/app.js').status_code==200
        with client.websocket_connect('/ws?client='+CLIENT, headers=ORIGIN) as ws:
            initial=ws.receive_json()
            assert initial['state']['state']=='idle'
            assert initial['state']['active_channels']==0
            ws.send_json({'cmd':'start','mic':0,'system':1})
            assert ws.receive_json()['type']=='error'
            assert ws.receive_json()['state']=='idle'
            ws.send_json({'cmd':'answer'})
            assert ws.receive_json()['type']=='error'
            ws.send_json({'cmd':'clear'})
            assert ws.receive_json()['type']=='clear'
        assert client.post('/api/shutdown').status_code==403

def test_reject_cross_origin():
    with TestClient(create_app()) as client:
        client.get('/')
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect('/ws?client='+CLIENT,headers={'origin':'http://evil.example'}): pass
        assert client.get('/health',headers={'host':'evil.example'}).status_code==400

def test_controller_disconnect_stops_both_streams():
    import time
    app=create_app(Settings(assembly_key='fake',groq_key='fake'))
    session=app.state.control.session
    session.capture_factory=FakeCapture; session.transcriber_factory=FakeTranscriber; session.answers=FakeAnswers()
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client='+CLIENT,headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json(start_command(client))
            assert ws.receive_json()['type']=='clear'
            assert ws.receive_json()['state']=='starting'
            assert ws.receive_json()['state']=='running'
            captures = list(session.captures.values())
            streams = list(session.transcribers.values())
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect('/ws?client=another-client-123456',headers=ORIGIN): pass
        deadline = time.monotonic() + 1
        while session.state != 'idle' and time.monotonic() < deadline:
            time.sleep(.01)
        with client.websocket_connect('/ws?client='+CLIENT,headers=ORIGIN) as ws:
            snapshot = ws.receive_json()
            assert snapshot['state']['state']=='idle'
            assert not snapshot['history']
        assert all(x.closed for x in captures + streams)


def test_real_sdk_protocol_with_fake_network(monkeypatch):
    from assemblyai.streaming.v3 import AsyncStreamingClient
    import inspect
    module=__import__(AsyncStreamingClient.__module__,fromlist=['x'])
    async def check():
        received=asyncio.Queue(); sent=[]
        await received.put(json.dumps({'type':'Begin','id':'fake-session','expires_at':9999999999}))
        class Socket:
            async def recv(self): return await received.get()
            async def send(self,data):
                sent.append(data)
                if isinstance(data,str) and json.loads(data).get('type')=='Terminate':
                    await received.put(json.dumps({'type':'Termination','audio_duration_seconds':1,'session_duration_seconds':1}))
            async def close(self): pass
        connect=AsyncMock(return_value=Socket())
        monkeypatch.setattr(module,'websocket_connect_async',connect)
        errors=[]
        t=Transcriber('fake',lambda e:None,errors.append)
        t.use_keyterms = True
        await t.connect()
        assert 'speech_model=universal-streaming-english' in connect.call_args.args[0]
        assert 'sample_rate=16000' in connect.call_args.args[0]
        from urllib.parse import parse_qs, urlparse
        query = parse_qs(urlparse(connect.call_args.args[0]).query)
        assert 'Django' in json.loads(query['keyterms_prompt'][0])
        await t.send(b'\0'*1600)
        await asyncio.sleep(.02)
        assert b'\0'*1600 in sent
        await t.close()
        assert any(isinstance(x,str) and json.loads(x).get('type')=='Terminate' for x in sent)
        assert not errors and t.client._read_task.done() and t.client._write_task.done()
    asyncio.run(check())

def test_sdk_queue_guard():
    async def check():
        t=Transcriber('fake',lambda e:None,lambda e:None)
        t.client._write_queue=asyncio.Queue()
        for _ in range(8): t.client._write_queue.put_nowait(b'x')
        with pytest.raises(RuntimeError,match='too slow'): await t.send(b'x')
        assert t.client._write_queue.qsize()==8
        await t.client._client.aclose()
    asyncio.run(check())


def test_server_heartbeat_keeps_background_tab_live_without_browser_timer(monkeypatch):
    import time
    monkeypatch.setattr('app.main.HEARTBEAT_TIMEOUT', .15)
    app = create_app(Settings(assembly_key='fake', groq_key='fake'))
    session = app.state.control.session
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    session.answers = FakeAnswers()
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=' + CLIENT, headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json(start_command(client))
            async def audio_levels():
                for _ in range(200):
                    session.emit({'type': 'level', 'role': 'me', 'value': .1})
                    await asyncio.sleep(.003)
            levels = client.portal.start_task_soon(audio_levels)
            end = time.monotonic() + .5
            replies = 0
            while time.monotonic() < end:
                event = ws.receive_json()
                if event['type'] == 'heartbeat':
                    ws.send_json({'cmd': 'heartbeat'})
                    replies += 1
            levels.cancel()
            assert replies >= 3
            assert session.state == 'running'
            assert len(session.captures) == 2
            assert session.limit_minutes == 60
        assert session.state == 'idle'


def test_idle_disconnect_does_not_overwrite_stop_reason_or_clear_again(monkeypatch):
    monkeypatch.setattr('app.main.HEARTBEAT_TIMEOUT', .05)
    app = create_app()
    session = app.state.control.session
    session.reason = 'STOP ALL completed. Local-only mode.'
    with TestClient(app) as client:
        client.get('/')
        for _ in range(2):
            with client.websocket_connect('/ws?client=' + CLIENT, headers=ORIGIN) as ws:
                initial = ws.receive_json()
                assert initial['state']['reason'] == session.reason
                for _ in range(6):
                    assert ws.receive_json()['type'] == 'heartbeat'
            assert session.reason == 'STOP ALL completed. Local-only mode.'
            assert session.companion_state()['reason'] == session.reason
