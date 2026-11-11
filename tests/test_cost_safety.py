import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.cost import COST_UNKNOWN, LiveOptions, PRICING_DATE, Quote
from app.main import create_app
from app.profile import SEED, ProfileStore
from app.transcription import Transcriber
from tests.live_helpers import ORIGIN, live_options, start_command
from tests.test_core import FakeAnswers, FakeCapture, FakeTranscriber, make_session
from tests.test_profile import docx, pdf

WS = '/ws?client=safety-tests-12345678'


def test_offline_pricing_and_zero_cap():
    assert LiveOptions().minutes == 5
    assert LiveOptions().cap_ugx == 0
    zero = Quote(live_options(cap_ugx=0), False, 120, PRICING_DATE)
    assert zero.public()['total_ugx'] == 100
    assert zero.public()['total_usd'] == '0.0250'
    assert zero.public()['hourly_usd'] == '0.30'
    with pytest.raises(ValueError, match='exceeds your UGX 0'):
        zero.require_authorisation()
    with_keyterms = Quote(live_options(cap_ugx=127), True, 120, PRICING_DATE)
    assert with_keyterms.total_ugx == 127
    assert with_keyterms.public()['hourly_usd'] == '0.38'
    with_keyterms.require_authorisation()
    with pytest.raises(ValueError, match='configured maximum'):
        Quote(live_options(minutes=6), False, 5, PRICING_DATE).require_authorisation()


@pytest.mark.parametrize('options,today', [
    (live_options(fx_ugx_per_usd=None), PRICING_DATE),
    (live_options(fx_source=''), PRICING_DATE),
    (live_options(fx_date=PRICING_DATE - timedelta(days=8)), PRICING_DATE),
    (live_options(fx_date=PRICING_DATE + timedelta(days=1)), PRICING_DATE),
    (live_options(fx_date=PRICING_DATE + timedelta(days=31)), PRICING_DATE + timedelta(days=31)),
])
def test_unknown_or_stale_prices_fail_closed(options, today):
    quote = Quote(options, False, 120, today)
    assert quote.public()['total_ugx'] is None
    with pytest.raises(ValueError, match=COST_UNKNOWN):
        quote.require_authorisation()


@pytest.mark.parametrize('options, message', [
    (live_options(confirmed=False), 'Explicit authorisation'),
    (live_options(assembly_billing_checked=False), 'Billing'),
    (live_options(groq_billing_checked=False), 'Billing'),
    (live_options(fx_ugx_per_usd=None), COST_UNKNOWN),
    (live_options(cap_ugx=0), 'exceeds your UGX 0'),
])
def test_blockers_precede_every_capture_and_provider_call(options, message):
    async def check():
        session, _, answers = make_session()
        answers.verify = AsyncMock()
        session.capture_factory = Mock()
        session.transcriber_factory = Mock()
        with pytest.raises(ValueError, match=message):
            await session.start(0, 1, 'medium', options=options)
        answers.verify.assert_not_awaited()
        session.capture_factory.assert_not_called()
        session.transcriber_factory.assert_not_called()
        assert session.state == 'idle'
    asyncio.run(check())


def test_startup_and_cv_workflows_never_construct_provider_clients(tmp_path, monkeypatch):
    groq = Mock(side_effect=AssertionError('Groq must remain local-only'))
    assembly = Mock(side_effect=AssertionError('AssemblyAI must remain local-only'))
    monkeypatch.setattr('app.answers.AsyncGroq', groq)
    monkeypatch.setattr('app.transcription.AsyncStreamingClient', assembly)
    app = create_app(Settings(assembly_key='fake', groq_key='fake'),
                     profile_store=ProfileStore(tmp_path / 'profile.json'))
    with TestClient(app) as client:
        assert client.get('/').status_code == 200
        assert client.get('/api/profile').status_code == 200
        for ext, fixture in [('pdf', pdf), ('docx', docx)]:
            preview = client.post('/api/profile/preview', params={'filename': 'CV.' + ext},
                                  content=fixture('Django engineering'), headers=ORIGIN)
            assert preview.status_code == 200
            data = {'name': 'CV ' + ext, 'facts': preview.json()['facts']}
            saved = client.post('/api/profile/cvs', json=data, headers=ORIGIN).json()
            assert client.post('/api/profile/compare', json=data, headers=ORIGIN).status_code == 200
            assert client.put('/api/profile/default', json={'id': saved['id']}, headers=ORIGIN).status_code == 200
            assert client.put('/api/profile/cvs/' + saved['id'], json=data, headers=ORIGIN).status_code == 200
        assert client.put('/api/profile', json=SEED.model_dump(), headers=ORIGIN).status_code == 200
        with client.websocket_connect(WS, headers=ORIGIN) as ws:
            assert ws.receive_json()['state']['state'] == 'idle'
            ws.send_json({'cmd': 'start', 'mic': 0, 'system': 1})
            assert 'Explicit authorisation' in ws.receive_json()['message']
    groq.assert_not_called()
    assembly.assert_not_called()


def mocked_app():
    app = create_app(Settings(assembly_key='fake', groq_key='fake'))
    session = app.state.control.session
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    session.answers = FakeAnswers()
    return app, session


def receive_running(ws):
    assert ws.receive_json()['type'] == 'clear'
    assert ws.receive_json()['state'] == 'starting'
    assert ws.receive_json()['state'] == 'running'


def test_single_use_quote_and_stop_all_cancels_generation():
    app, session = mocked_app()
    import threading
    generating = threading.Event()
    cancelled = threading.Event()

    async def generate(*args):
        generating.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    session.answers.generate = generate
    with TestClient(app) as client:
        client.get('/')
        assert client.post('/api/stop-all').status_code == 403
        with client.websocket_connect(WS, headers=ORIGIN) as ws:
            ws.receive_json()
            start = start_command(client)
            tampered = {**start, 'live': {**start['live'], 'minutes': 6}}
            ws.send_json(tampered)
            assert 'fresh estimate' in ws.receive_json()['message']
            assert ws.receive_json()['state'] == 'idle'
            ws.send_json(start)
            receive_running(ws)
            captures = list(session.captures.values())
            streams = list(session.transcribers.values())
            session.latest = 'How would you design this?'
            ws.send_json({'cmd': 'answer'})
            assert generating.wait(1)
            assert client.post('/api/stop-all', headers=ORIGIN).json()['status'] == 'stopped'
            assert cancelled.is_set()
            assert session.state == 'idle' and not session.history and not session.latest
            assert not session.interview and not session.pending_questions and not session.answers.verified
            assert all(x.closed for x in captures + streams)
            ws.send_json(start)
            for _ in range(20):
                event = ws.receive_json()
                if event['type'] == 'error':
                    assert 'fresh estimate' in event['message']
                    break
            else:
                pytest.fail('Replayed quote was not rejected')


def test_five_minute_deadline_advances_backend_without_browser_timer():
    async def check():
        session, _, _ = make_session()
        loop = asyncio.get_running_loop()
        clock = loop.time
        before = clock()
        await session.start(0, 1, 'medium', options=live_options())
        assert 300 <= session.deadline - before <= 301
        captures = list(session.captures.values())
        streams = list(session.transcribers.values())
        await asyncio.sleep(.02)
        loop.time = lambda: clock() + 301
        try:
            for _ in range(40):
                if session.state == 'idle':
                    break
                await asyncio.sleep(.01)
            assert session.state == 'idle'
            assert 'duration' in session.reason
            assert all(x.closed for x in captures + streams)
        finally:
            loop.time = clock
            await session.stop()
    asyncio.run(check())


def test_heartbeat_expiry_cleans_up_without_disconnect(monkeypatch):
    monkeypatch.setattr('app.main.HEARTBEAT_TIMEOUT', .10)
    app, session = mocked_app()
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect(WS, headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json(start_command(client))
            receive_running(ws)
            resources = list(session.captures.values()) + list(session.transcribers.values())
            with pytest.raises(WebSocketDisconnect):
                while True:
                    ws.receive_json()
            assert all(item.closed for item in resources)
            assert session.state == 'idle'


def test_failed_assemblyai_handshake_attempted_once(monkeypatch):
    async def check():
        from assemblyai.streaming.v3 import AsyncStreamingClient
        module = __import__(AsyncStreamingClient.__module__, fromlist=['x'])
        socket = AsyncMock(side_effect=OSError('Synthetic connection failure'))
        monkeypatch.setattr(module, 'websocket_connect_async', socket)
        errors = []
        transcriber = Transcriber('mock-key', lambda event: None, errors.append)
        with pytest.raises(RuntimeError, match='could not start'):
            await transcriber.connect()
        await transcriber.close()
        assert socket.await_count == 1
        assert len(errors) == 1
    asyncio.run(check())


def test_groq_has_no_sdk_retries_and_failure_ends_session(monkeypatch):
    async def check():
        from app.answers import Answers

        client = Mock()
        factory = Mock(return_value=client)
        monkeypatch.setattr('app.answers.AsyncGroq', factory)
        assert Answers(Settings(groq_key='fake')).get_client() is client
        assert factory.call_args.kwargs['max_retries'] == 0

        session, _, answers = make_session()
        answers.generate = AsyncMock(side_effect=RuntimeError('Synthetic provider failure'))
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        session.request_answer('How would you design a queue?')
        session.request_answer('How would you test a queue?')
        await session.answer_task
        await session.fatal_task
        assert answers.generate.await_count == 1
        assert all(item.closed for item in resources)
        assert session.state == 'idle'
    asyncio.run(check())


def test_deadline_includes_provider_startup():
    async def check():
        session, _, _ = make_session()
        loop = asyncio.get_running_loop()
        clock = loop.time
        opened = asyncio.Event()

        class SlowConnection(FakeTranscriber):
            async def connect(self):
                opened.set()
                await asyncio.Future()

        session.transcriber_factory = SlowConnection
        start = asyncio.create_task(session.start(0, 1, 'medium', options=live_options()))
        await asyncio.wait_for(opened.wait(), 1)
        captures = list(session.captures.values())
        streams = list(session.transcribers.values())
        assert len(streams) == 1
        loop.time = lambda: clock() + 301
        try:
            with pytest.raises(ValueError, match='duration limit during startup'):
                await asyncio.wait_for(start, 1)
            assert session.state == 'idle'
            assert all(resource.closed for resource in captures + streams)
        finally:
            loop.time = clock
    asyncio.run(check())


def test_capture_buffers_clear_even_if_driver_abort_fails():
    from app.audio import Capture

    capture = Capture(0)
    stream = Mock()
    stream.abort.side_effect = RuntimeError('Synthetic driver error')
    capture.stream = stream
    capture.pending.extend(b'sample')
    capture.queue.put_nowait(b'sample')
    with pytest.raises(RuntimeError, match='Synthetic driver error'):
        capture.close()
    stream.close.assert_called_once()
    assert capture.closed and capture.stream is None and capture.queue.empty() and not capture.pending
