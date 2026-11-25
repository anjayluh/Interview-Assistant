"""Use the installed Groq SDK with offline HTTP transport, not permissive SDK mocks."""
import asyncio
import inspect
import json
import stat
from types import SimpleNamespace as NS

import httpx
import pytest
from groq import AsyncGroq
from groq.resources.chat.completions import AsyncCompletions

from app.answers import Answers
from app.config import Settings
from app.profile import InterviewContext
from app.provider_errors import ProviderFailure, groq_failure, save_diagnostic
from tests.live_helpers import live_options
from tests.test_core import make_session
from tests.test_live_pipeline import payload, turn


def context():
    return json.dumps({'professional_context': json.loads(InterviewContext().for_question('Explain caching'))})


def test_installed_sdk_accepts_actual_stream_request_without_unsupported_options():
    async def check():
        requests = []
        content = json.dumps(payload())
        def respond(request):
            requests.append(request)
            body = json.loads(request.content)
            assert request.url.path == '/openai/v1/chat/completions'
            assert 'stream_options' not in body
            assert body['model'] == 'test-stream-model' and body['stream'] is True
            pieces = [content[:30], content[30:]]
            events = []
            for piece in pieces:
                event = {'id':'chatcmpl-offline','object':'chat.completion.chunk','created':1,
                         'model':body['model'],'choices':[{'index':0,'delta':{'content':piece},'finish_reason':None}]}
                events.append('data: ' + json.dumps(event) + '\n\n')
            events.append('data: [DONE]\n\n')
            return httpx.Response(200, headers={'content-type':'text/event-stream'}, text=''.join(events))
        http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        client = AsyncGroq(api_key='offline-placeholder', http_client=http, max_retries=0)
        service = Answers(Settings(model='test-stream-model'), client); service.verified = True
        text = []
        result = await service.generate(context(), 'short', on_text=text.append)
        assert result['text'] == payload()['text'] and text
        assert len(requests) == 1
        assert service.last_request['endpoint'].endswith('/openai/v1/chat/completions')
        await service.close()
    asyncio.run(check())


def test_previous_unsupported_parameter_fails_before_http_with_installed_sdk():
    async def check():
        requests = []
        http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: requests.append(r)))
        client = AsyncGroq(api_key='offline-placeholder', http_client=http, max_retries=0)
        assert 'stream_options' not in inspect.signature(AsyncCompletions.create).parameters
        with pytest.raises(TypeError, match='unexpected keyword argument'):
            await client.chat.completions.create(model='openai/gpt-oss-20b',messages=[],stream=True,
                                                 stream_options={'include_usage': True})
        assert requests == []
        await client.close()
    asyncio.run(check())


@pytest.mark.parametrize('status,category', [(400,'invalid_request'),(401,'authentication'),(402,'billing_required'),
    (403,'access_denied'),(404,'not_found'),(429,'rate_limit'),(500,'provider_error'),(503,'provider_error')])
def test_real_sdk_http_errors_are_classified_without_raw_data_or_retry(status, category, tmp_path):
    async def check():
        requests = []
        secret = 'gsk_SECRET_DO_NOT_STORE'
        private = 'PRIVATE_CV_AND_INTERVIEW_CONTENT'
        def respond(request):
            requests.append(request)
            return httpx.Response(status, headers={'x-request-id':'req_offline_123'}, json={
                'error': {'message': secret+' '+private, 'type':'invalid_request_error',
                          'code':'rate_limit_exceeded' if status==429 else 'invalid_request_error', 'param':'model'}})
        client = AsyncGroq(api_key=secret,http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),max_retries=0)
        service = Answers(Settings(),client);service.verified = True
        with pytest.raises(ProviderFailure) as caught:
            await service.generate(context(), 'short', on_text=lambda value: None)
        diagnostic = caught.value.diagnostic
        assert diagnostic['http_status'] == status and diagnostic['category'] == category
        assert diagnostic['request_id'] == 'req_offline_123'
        assert diagnostic['invalid_parameter'] == 'model'
        assert diagnostic['request']['prompt_utf8_bytes'] > 0
        assert len(requests) == 1
        path = tmp_path/'private-runtime'/'last-groq-error.json'
        save_diagnostic(path,diagnostic)
        saved = path.read_text()
        assert secret not in saved and private not in saved and 'Authorization' not in saved
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert 'free allowance exhausted' not in str(caught.value).lower()
        await service.close()
    asyncio.run(check())


@pytest.mark.parametrize('error,category', [(httpx.ReadTimeout('private-secret'),'timeout'),
    (httpx.ConnectError('private-secret'),'network'),(TypeError("unexpected keyword argument 'stream_options'"),'application_error')])
def test_timeout_network_and_local_exception_have_no_invented_http_status(error,category):
    failure = groq_failure(error, {'model':'openai/gpt-oss-20b'})
    assert failure.diagnostic['http_status'] is None
    assert failure.diagnostic['category'] == category
    assert 'private-secret' not in json.dumps(failure.diagnostic)


def test_failure_status_survives_cleanup_and_reconnect_snapshot():
    async def check():
        from unittest.mock import AsyncMock
        class Rejected(Exception): status_code = 429
        session, events, answers = make_session()
        answers.generate = AsyncMock(side_effect=Rejected())
        await session.start(0,1,'short',options=live_options())
        session.request_answer('Explain retries')
        await session.answer_task;await session.fatal_task
        assert session.state == 'idle'
        assert session.companion_state()['answer_status']['phase'] == 'failed'
        assert session.companion_state()['answer_status']['diagnostic']['http_status'] == 429
        types = [e['type'] for e in events]
        error_index = next(i for i,e in enumerate(events) if e['type']=='groq' and e.get('status')=='error')
        assert error_index > 0
        assert not any(e['type'] == 'clear' for e in events[error_index:])
        assert session.snapshot()['answer_status']['phase'] == 'failed'
    asyncio.run(check())


def test_failed_answers_do_not_retry_on_later_interviewer_questions():
    async def check():
        from unittest.mock import AsyncMock
        session, _, answers = make_session()
        answers.generate = AsyncMock(side_effect=RuntimeError('private payload'))
        await session.start(0,1,'short',options=live_options())
        session.request_answer('Explain caching')
        await session.answer_task
        assert session.state == 'running' and not session.answer_enabled
        session.turn('interviewer',turn('How would you test this?',2))
        await asyncio.sleep(.3)
        assert answers.generate.await_count == 1
        assert session.latest == 'How would you test this?'
        assert session.answer_status['phase'] == 'failed'
        with pytest.raises(ValueError,match='paused'):
            session.request_answer('Manual retry is blocked until a new authorised session')
        await session.stop()
    asyncio.run(check())


def test_actual_sdk_stream_json_validation_error_is_not_an_application_bug():
    async def check():
        count = 0
        def respond(request):
            nonlocal count
            count += 1
            event = {'error': {'message': 'PRIVATE generated content', 'code': 'json_validate_failed', 'type': 'invalid_request_error'}}
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  text='data: ' + json.dumps(event) + '\n\n')
        client = AsyncGroq(api_key='mock', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), max_retries=0)
        service = Answers(Settings(model='test-stream-model'), client); service.verified = True
        with pytest.raises(ProviderFailure) as caught:
            await service.generate(context(), 'medium', on_text=lambda x: None)
        assert caught.value.diagnostic['category'] == 'invalid_response'
        assert caught.value.diagnostic['provider_error_code'] == 'json_validate_failed'
        assert caught.value.diagnostic['exception_type'] == 'APIError'
        assert caught.value.diagnostic['http_status'] is None
        assert 'PRIVATE' not in json.dumps(caught.value.diagnostic)
        assert count == 1
        await service.close()
    asyncio.run(check())


@pytest.mark.parametrize('minutes', [15, 60, 120])
def test_live_text_gpt_oss_recovers_on_next_question_and_keeps_configured_deadline(minutes):
    async def check():
        session, events, _ = make_session()
        requests = []
        def respond(request):
            if request.method == 'GET':
                return httpx.Response(200, json={'data': [{'id': 'openai/gpt-oss-20b'}]})
            body = json.loads(request.content)
            requests.append(body)
            assert not body.get('stream', False)
            assert body['response_format']['type'] == 'text'
            assert body['include_reasoning'] is False
            assert body['model'] == 'openai/gpt-oss-20b'
            assert body['service_tier'] == 'on_demand'
            if len(requests) == 2:
                return httpx.Response(400, json={'error': {'code': 'json_validate_failed', 'type': 'invalid_request_error', 'message': 'PRIVATE'}})
            return httpx.Response(200, json={'id':'offline', 'object':'chat.completion', 'created':1,
                'model':body['model'], 'choices':[{'index':0,'message':{'role':'assistant','content':payload()['text']},'finish_reason':'stop'}]})
        sdk = AsyncGroq(api_key='mock', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), max_retries=0)
        session.answers = Answers(Settings(model='openai/gpt-oss-20b'), sdk)
        options = live_options(); options.minutes = minutes
        await session.start(0, 1, 'medium', options=options)
        deadline = session.deadline
        loop = asyncio.get_running_loop(); clock = loop.time; advance = 0
        loop.time = lambda: clock() + advance
        try:
            for number, minute in enumerate([1, 7, 8, minutes - 1], 1):
                advance = minute * 60
                session.request_answer('Explain caching strategy ' + str(number))
                await session.answer_task
                assert session.state == 'running' and session.answer_enabled
                assert session.deadline == deadline and session.limit_minutes == minutes
                assert session.snapshot()['maximum_payment_ugx'] == 0
                if number == 2:
                    assert session.answer_status['phase'] == 'failed'
                    assert session.answer_status['diagnostic']['category'] == 'invalid_response'
                else:
                    assert session.answer_status['phase'] == 'ready'
            assert len(requests) == 4  # One request per distinct question; no retry of failed question.
            assert len(session.suggestions) == 3
            assert not session.draft
            resources = [*session.captures.values(), *session.transcribers.values()]
            advance = minutes * 60 + 1
            for _ in range(40):
                if session.state == 'idle': break
                await asyncio.sleep(.01)
            assert session.state == 'idle'
            assert all(x.closed for x in resources)
        finally:
            loop.time = clock
            await session.stop()
    asyncio.run(check())


@pytest.mark.parametrize('content', [
    "I'd start with the business constraints and measure the bottleneck.",
    'I would use a set.\n```python\ndef first_duplicate(xs):\n    seen = set()\n    for x in xs:\n        if x in seen: return x\n        seen.add(x)\n```\nThis takes O(n) time and O(n) space.',
])
def test_actual_sdk_live_spoken_answer_needs_no_json_and_keeps_code(content):
    async def check():
        requests = []
        def respond(request):
            body = json.loads(request.content); requests.append(body)
            assert body['response_format'] == {'type': 'text'}
            assert 'json_schema' not in json.dumps(body)
            assert not body.get('stream')
            assert body['include_reasoning'] is False
            assert body['max_completion_tokens'] == 1300
            return httpx.Response(200, json={'id':'offline', 'object':'chat.completion', 'created':1,
                'model':body['model'], 'choices':[{'index':0,'message':{'role':'assistant','content':content,
                'reasoning':'PRIVATE reasoning must never appear'},'finish_reason':'stop'}]})
        sdk = AsyncGroq(api_key='mock', http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)), max_retries=0)
        service = Answers(Settings(), sdk); service.verified = True
        result = await service.generate(context(), 'medium', on_text=lambda x: None)
        assert result['text'] and 'PRIVATE' not in json.dumps(result)
        if '```' in content:
            assert 'def first_duplicate' in result['code'] and result['language'] == 'python'
            assert 'O(n)' in result['text'] and 'def first_duplicate' not in result['text']
        assert result['example'] == '' and result['experience'] == 'General recommendation'
        assert len(requests) == 1
        await service.close()
    asyncio.run(check())


def test_formatting_failure_does_not_clear_transcripts_or_previous_answers():
    async def check():
        from unittest.mock import AsyncMock
        session, events, answers = make_session()
        await session.start(0,1,'medium',options=live_options())
        session.turn('interviewer', turn('How would you design a queue?',1))
        await session.question_task; await session.answer_task
        history = list(session.history); suggestions = list(session.suggestions)
        answers.generate = AsyncMock(side_effect=ProviderFailure('Invalid output', {'category':'invalid_response','http_status':400}))
        session.turn('interviewer', turn('How would you test a queue?',2))
        await session.question_task; await session.answer_task
        assert session.state == 'running' and session.answer_enabled
        assert len(session.history) == len(history) + 1
        assert list(session.suggestions) == suggestions
        assert session.companion_state()['history'] and session.companion_state()['answers']
        assert not any(event['type'] == 'clear' for event in events[events.index(next(e for e in events if e['type']=='answer')) + 1:])
        await session.stop()
    asyncio.run(check())


@pytest.mark.parametrize('content', ['', '<think>private reasoning</think>Answer', '{"text":"JSON"}', 'Code: ```python\nunfinished'])
def test_live_spoken_parser_rejects_empty_reasoning_json_and_unfinished_code(content):
    from app.answers import parse_spoken_answer
    with pytest.raises(ValueError): parse_spoken_answer(content)
