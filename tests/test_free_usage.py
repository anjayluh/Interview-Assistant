import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from app.answers import Answers
from app.config import Settings
from app.cost import LiveOptions, MAX_INPUT_TOKENS, Quote
from app.main import create_app
from app.profile import InterviewContext
from app.transcription import Transcriber
from tests.live_helpers import ORIGIN, live_options, start_command
from tests.test_core import FakeAnswers, FakeCapture, FakeTranscriber, make_session


def test_default_free_session_needs_no_manual_billing_or_price_inputs():
    options = LiveOptions(confirmed=True)
    assert options.model_dump() == {'minutes': 60, 'cap_ugx': 0, 'confirmed': True}
    quote = Quote(options, 120)
    quote.require_authorisation()
    assert quote.public()['blockers'] == []
    assert quote.public()['maximum_payment_ugx'] == 0


def test_removed_fields_from_an_older_tab_cannot_reintroduce_billing_gates():
    options = LiveOptions(
        confirmed=True, fx_ugx_per_usd=None, assembly_billing_checked=False,
        groq_billing_checked=False, free={'checked_at': None, 'assembly_credits_usd': None},
        max_answers=3,
    )
    assert options.model_dump() == live_options().model_dump()
    Quote(options, 120).require_authorisation()


def test_no_arbitrary_answer_reservation_cutoff_during_free_session():
    async def check():
        session, _, answers = make_session()
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        for index in range(5):
            session.request_answer(f'Explain retries, example {index}')
            await session.answer_task
        assert len(answers.calls) == 5
        assert session.state == 'running'
        assert session.snapshot()['maximum_payment_ugx'] == 0
        await session.stop()
        assert all(resource.closed for resource in resources)
    asyncio.run(check())


@pytest.mark.parametrize('status,message', [
    (401, 'API key was rejected'), (402, 'free allowance exhausted'),
    (403, 'denied access'), (429, 'free allowance exhausted or rate limited'),
])
def test_groq_errors_stop_both_streams_without_retry(status, message):
    async def check():
        class ProviderFailure(Exception):
            status_code = status

        request = AsyncMock(side_effect=ProviderFailure())
        client = NS(
            chat=NS(completions=NS(create=request)),
            models=NS(list=AsyncMock(return_value=NS(data=[NS(id='openai/gpt-oss-20b')]))),
            close=AsyncMock(),
        )
        session, _, _ = make_session()
        session.answers = Answers(Settings(), client)
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        session.request_answer('Explain retries')
        await session.answer_task
        await session.fatal_task
        assert request.await_count == 1
        assert message in session.reason
        assert session.state == 'idle'
        assert all(resource.closed for resource in resources)
        client.close.assert_awaited_once()
    asyncio.run(check())


@pytest.mark.parametrize('code,message', [
    (402, 'credit balance exhausted'), (429, 'free quota exceeded'),
])
def test_assembly_credit_or_quota_error_stops_entire_session(code, message):
    async def check():
        session, _, _ = make_session()
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        sdk = NS(on=lambda *args: None)
        transcriber = Transcriber('mock', lambda event: None, session.fatal, client=sdk)
        transcriber._error(sdk, NS(code=code, message=message))
        await session.fatal_task
        assert 'free allowance exhausted' in session.reason
        assert all(resource.closed for resource in resources)
        assert session.state == 'idle'
    asyncio.run(check())


def test_default_websocket_start_with_empty_billing_state():
    app = create_app(Settings(assembly_key='mock', groq_key='mock'))
    session = app.state.control.session
    session.answers = FakeAnswers()
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=free-session-123456', headers=ORIGIN) as ws:
            ws.receive_json()
            start = start_command(client)
            assert start['live'] == {'minutes': 60, 'cap_ugx': 0, 'confirmed': True}
            ws.send_json(start)
            assert ws.receive_json()['type'] == 'clear'
            assert ws.receive_json()['state'] == 'starting'
            snapshot = ws.receive_json()
            assert snapshot['state'] == 'running'
            assert snapshot['maximum_payment_ugx'] == 0
            assert len(session.transcribers) == 2
            assert client.post('/api/stop-all', headers=ORIGIN).json()['status'] == 'stopped'
            assert session.state == 'idle'


def test_large_requests_still_block_before_network():
    async def check():
        request = AsyncMock()
        service = Answers(Settings(), NS(chat=NS(completions=NS(create=request))))
        service.verified = service.free_only = True
        with pytest.raises(ValueError, match='per-request context limit'):
            await service.generate('x' * (MAX_INPUT_TOKENS + 1), 'long')
        request.assert_not_awaited()
    asyncio.run(check())


def test_stream_start_preserves_provider_exhaustion_message():
    async def check():
        from assemblyai.streaming.v3 import StreamingEvents
        class SDK:
            def __init__(self):
                self.handlers = {}
            def on(self, event, handler):
                self.handlers[event] = handler
            async def connect(self, params):
                self.handlers[StreamingEvents.Error](self, NS(code=402, message='Credit balance is negative'))
            async def disconnect(self, terminate):
                assert terminate

        errors = []
        transcriber = Transcriber('mock', lambda event: None, errors.append, client=SDK())
        with pytest.raises(ValueError, match='free allowance exhausted'):
            await transcriber.connect()
        await transcriber.close()
        assert len(errors) == 1
    asyncio.run(check())


@pytest.mark.parametrize('minutes', [5, 60, 120])
def test_supported_durations_set_backend_deadline_and_zero_payment(minutes):
    async def check():
        session, _, _ = make_session()
        before = asyncio.get_running_loop().time()
        await session.start(0, 1, 'medium', options=live_options(minutes=minutes))
        resources = list(session.captures.values()) + list(session.transcribers.values())
        assert minutes * 60 <= session.deadline - before < minutes * 60 + 1
        assert session.limit_minutes == minutes
        assert session.snapshot()['maximum_payment_ugx'] == 0
        await session.stop()
        assert all(resource.closed for resource in resources)
    asyncio.run(check())


@pytest.mark.parametrize('minutes', [4, 121, 1440])
def test_duration_outside_five_to_120_is_rejected_before_provider_use(minutes):
    with pytest.raises(ValueError):
        LiveOptions(minutes=minutes, confirmed=True)


def test_duration_defaults_match_interface_and_server_ceiling(monkeypatch):
    from app.config import ROOT, configured_maximum
    import re
    html = (ROOT / 'app/static/index.html').read_text()
    field = re.search(r'<input id="live-minutes"[^>]+>', html)[0]
    assert 'min="5"' in field and 'max="120"' in field and 'value="60"' in field
    assert LiveOptions().minutes == 60
    monkeypatch.delenv('MAX_SESSION_MINUTES', raising=False)
    assert configured_maximum() == 120
    monkeypatch.setenv('MAX_SESSION_MINUTES', '1440')
    assert configured_maximum() == 0  # Invalid configuration fails closed.
    assert Quote(live_options(minutes=120), 120).public()['blockers'] == []
    assert Quote(live_options(minutes=120, cap_ugx=1), 120).public()['blockers'] == [
        'Maximum user expenditure must remain UGX 0.']


def test_local_functionality_remains_available_after_provider_exhaustion():
    app = create_app(Settings(assembly_key='mock', groq_key='mock'))
    session = app.state.control.session
    session.answers = FakeAnswers()
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    session.answers.generate = AsyncMock(side_effect=ValueError('Groq free allowance exhausted.'))
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=local-after-failure-123', headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json(start_command(client, options=live_options(minutes=120)))
            assert ws.receive_json()['type'] == 'clear'
            assert ws.receive_json()['state'] == 'starting'
            assert ws.receive_json()['state'] == 'running'
            resources = list(session.captures.values()) + list(session.transcribers.values())
            ws.send_json({'cmd': 'answer', 'question': 'How do you prioritise?'})
            for _ in range(20):
                event = ws.receive_json()
                if event.get('state') == 'idle':
                    break
            else:
                pytest.fail('Provider failure did not clean up the live session')
            assert all(item.closed for item in resources)
            assert 'local practice, CV tools and phone pairing remain available' in session.reason
            session.answers.generate.assert_awaited_once()
            assert client.get('/api/profile').status_code == 200
            assert client.get('/api/copilot/pair').status_code == 200
            assert client.post('/api/copilot/practice', headers=ORIGIN,
                               json={'scenario': 'Company and business'}).status_code == 200
