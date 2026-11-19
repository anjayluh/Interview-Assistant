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
            assert body['model'] == 'openai/gpt-oss-20b' and body['stream'] is True
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
        service = Answers(Settings(), client); service.verified = True
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
        clear_index = max(i for i,e in enumerate(events) if e['type']=='clear')
        assert error_index < clear_index
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
