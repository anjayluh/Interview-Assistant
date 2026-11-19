import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest
from starlette.testclient import TestClient

from app.answers import Answers
from app.config import Settings
from app.cost import FreeSessionBudget, MAX_INPUT_TOKENS, MAX_OUTPUT_TOKENS, PRICING_DATE, Quote
from app.main import create_app
from app.profile import InterviewContext
from app.transcription import Transcriber
from tests.live_helpers import ORIGIN, live_options, start_command
from tests.test_core import FakeAnswers, FakeCapture, FakeTranscriber, make_session


def test_free_coverage_admits_nonzero_gross_with_no_exchange_rate_and_zero_payment():
    quote = Quote(live_options(), True, 120, PRICING_DATE)
    quote.require_authorisation()
    data = quote.public()
    assert data['total_ugx'] == data['expected_payment_ugx'] == 0
    assert Decimal(data['total_usd']) > 0 and Decimal(data['groq_gross_usd']) > 0
    assert Decimal(data['assembly_reserved_usd']) > Decimal(data['total_usd'])
    assert data['groq_reserved_tokens'] > quote.options.max_answers * (MAX_INPUT_TOKENS + MAX_OUTPUT_TOKENS)
    assert quote.options.fx_ugx_per_usd is None


@pytest.mark.parametrize('overrides,reason', [
    ({'free': {'assembly_credits_usd': '.0001'}}, 'Insufficient free credits for AssemblyAI'),
    ({'free': {'groq_remaining_tokens': 1}}, 'Insufficient free credits or quota'),
    ({'free': {'groq_remaining_requests': 1}}, 'Insufficient free credits or quota'),
    ({'assembly_billing_checked': False}, 'not confirmed for AssemblyAI'),
    ({'groq_billing_checked': False}, 'not confirmed for Groq'),
    ({'free': {'assembly_no_paid_fallback': False}}, 'zero-billing protection'),
    ({'free': {'groq_no_paid_fallback': False}}, 'cannot guarantee zero billable usage'),
    ({'free': {'assembly_plan': 'billable'}}, 'zero-billing protection'),
    ({'free': {'groq_plan': 'paid'}}, 'cannot guarantee zero billable usage'),
    ({'free': {'groq_plan': 'promotional_billable'}}, 'cannot guarantee zero billable usage'),
    ({'free': {'groq_remaining_tokens': 0}}, 'Groq free allowance exhausted'),
    ({'free': {'assembly_credits_usd': 0}}, 'AssemblyAI free allowance exhausted'),
    ({'free': {'checked_at': (datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()}}, 'last 15 minutes'),
    ({'free': {'exclusive_balances': False}}, 'Reserve these organisation balances'),
    ({'free': {'valid_until': datetime.now(timezone.utc).isoformat()}}, 'entire session'),
    ({'cap_ugx': 10}, 'UGX 0'),
])
def test_free_only_rejections_before_any_provider_or_capture(overrides, reason):
    async def check():
        session, _, answers = make_session()
        answers.verify = AsyncMock()
        session.capture_factory = Mock()
        with pytest.raises(ValueError, match=reason):
            await session.start(0, 1, 'medium', options=live_options(**overrides))
        assert session.state == 'idle' and not session.free_budget
        answers.verify.assert_not_awaited()
        session.capture_factory.assert_not_called()
    asyncio.run(check())


def test_exhausting_groq_session_allowance_stops_both_streams_and_cancels_pending_answers():
    async def check():
        session, _, answers = make_session()
        await session.start(0, 1, 'medium', options=live_options(max_answers=1))
        resources = list(session.captures.values()) + list(session.transcribers.values())
        session.request_answer('Explain idempotency')
        await session.answer_task
        assert session.snapshot()['free_usage']['expected_payment_ugx'] == 0
        assert session.snapshot()['free_usage']['remaining_answers'] == 0
        session.request_answer('Explain retries')
        await session.answer_task
        await session.fatal_task
        assert len(answers.calls) == 1
        assert session.state == 'idle'
        assert all(resource.closed for resource in resources)
    asyncio.run(check())


@pytest.mark.parametrize('status,message', [(402,'billing'), (403,'billing'), (429,'free allowance exhausted')])
def test_provider_billing_or_quota_rejection_is_never_retried(status, message):
    async def check():
        class ProviderFailure(Exception):
            status_code = status
        request = AsyncMock(side_effect=ProviderFailure())
        client = NS(chat=NS(completions=NS(create=request)))
        answers = Answers(Settings(), client)
        answers.verified = True
        context = json.dumps({'professional_context': json.loads(InterviewContext().for_question('Explain retries'))})
        with pytest.raises(ValueError, match=message):
            await answers.generate(context, 'medium')
        assert request.await_count == 1
    asyncio.run(check())


def test_assembly_free_credit_exhaustion_stops_entire_session():
    async def check():
        session, _, _ = make_session()
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        fake_sdk = Mock()
        transcriber = Transcriber('mock', lambda event: None, session.fatal, client=fake_sdk)
        transcriber._error(fake_sdk, NS(code=402, message='Credit balance exhausted'))
        await session.fatal_task
        assert 'free allowance exhausted' in session.reason
        assert all(resource.closed for resource in resources)
        assert session.state == 'idle'
    asyncio.run(check())


def test_time_budget_depletion_stops_transcription_even_before_duration_deadline():
    async def check():
        session, _, _ = make_session()
        await session.start(0, 1, 'medium', options=live_options())
        resources = list(session.captures.values()) + list(session.transcribers.values())
        session.free_budget.assembly_reserved_usd = Decimal('0')
        watcher = next(task for task in session.tasks if task.get_coro().__name__ == 'watch_free_coverage')
        await watcher
        assert session.state == 'idle'
        assert 'free-credit budget exhausted' in session.reason
        assert all(resource.closed for resource in resources)
    asyncio.run(check())


def test_free_quote_no_fx_and_authorised_start_through_normal_websocket():
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
            assert 'fx_ugx_per_usd' in start['live'] and start['live']['fx_ugx_per_usd'] is None
            ws.send_json(start)
            assert ws.receive_json()['type'] == 'clear'
            assert ws.receive_json()['state'] == 'starting'
            snapshot = ws.receive_json()
            assert snapshot['state'] == 'running'
            assert snapshot['free_usage']['expected_payment_ugx'] == 0
            assert len(session.transcribers) == 2
            assert client.post('/api/stop-all', headers=ORIGIN).json()['status'] == 'stopped'
            assert session.state == 'idle'


def test_request_larger_than_free_reservation_is_blocked_before_network():
    async def check():
        request = AsyncMock()
        service = Answers(Settings(), NS(chat=NS(completions=NS(create=request))))
        service.verified = service.free_only = True
        with pytest.raises(ValueError, match='reserved free-token budget'):
            await service.generate('x' * (MAX_INPUT_TOKENS + 1), 'long')
        request.assert_not_awaited()
    asyncio.run(check())


def test_reservations_are_not_refunded_after_failure_or_cancellation():
    pricing = Quote(live_options(max_answers=1), False, 120, PRICING_DATE)
    budget = FreeSessionBudget(pricing)
    before = budget.remaining_tokens
    budget.reserve_answer()
    assert budget.remaining_tokens == before - MAX_INPUT_TOKENS - MAX_OUTPUT_TOKENS
    with pytest.raises(ValueError, match='allowance exhausted'):
        budget.reserve_answer()


def test_optional_gross_currency_reference_is_separate_and_requires_fresh_source():
    from datetime import timedelta
    options = live_options(fx_ugx_per_usd='4000', fx_date=PRICING_DATE, fx_source='Synthetic source')
    data = Quote(options, False, 120, PRICING_DATE).public()
    assert data['gross_transcription_ugx_reference'] == 100
    assert data['expected_payment_ugx'] == 0
    stale = options.model_copy(update={'fx_date': PRICING_DATE - timedelta(days=8)})
    data = Quote(stale, False, 120, PRICING_DATE).public()
    assert data['gross_transcription_ugx_reference'] is None
    assert data['expected_payment_ugx'] == 0


@pytest.mark.parametrize('plan', ['promotional_billable', 'paid'])
def test_groq_paid_or_promotional_balance_cannot_override_no_charge_gate(plan):
    pricing = Quote(live_options(free={'groq_plan': plan}), False, 120, PRICING_DATE)
    with pytest.raises(ValueError, match='cannot guarantee zero billable usage'):
        pricing.require_authorisation()


def test_confirmed_positive_assembly_balance_on_billable_account_is_still_blocked():
    pricing = Quote(live_options(free={'assembly_plan': 'billable', 'assembly_credits_usd': '100'}), False, 120, PRICING_DATE)
    with pytest.raises(ValueError, match='zero-billing protection'):
        pricing.require_authorisation()
