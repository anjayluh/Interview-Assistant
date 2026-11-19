import asyncio
import json
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.answers import Answers, SCHEMA, spoken_prefix
from app.config import Settings
from app.main import create_app
from tests.live_helpers import live_options
from tests.test_core import make_session


def turn(text, number=1, final=True):
    return NS(transcript=text, turn_order=number, end_of_turn=final)


def payload():
    return {'text': "I'd use a set to detect duplicates.", **{key: '' for key in SCHEMA['required'] if key != 'text'},
            'category': 'Coding / algorithm', 'experience': 'Proposed approach',
            'code': 'def duplicate(xs):\n    return len(set(xs)) != len(xs)', 'details': 'O(n) time, O(n) space.'}


class MockStream:
    def __init__(self, content=None, release=None, fail=None):
        self.content = json.dumps(payload()) if content is None else content
        self.release = release
        self.fail = fail
        self.closed = False
    async def __aiter__(self):
        # The first spoken prefix is delivered before the complete object.
        yield NS(choices=[NS(delta=NS(content=self.content[:36]))])
        await asyncio.sleep(.005)
        if self.release: await self.release.wait()
        if self.fail: raise self.fail
        yield NS(choices=[NS(delta=NS(content=self.content[36:]))])
        yield NS(choices=[], usage=NS(total_tokens=200))
    async def close(self): self.closed = True


def streaming_answers(stream):
    create = AsyncMock(return_value=stream)
    client = NS(chat=NS(completions=NS(create=create)), close=AsyncMock(),
                models=NS(list=AsyncMock(return_value=NS(data=[NS(id='test-stream-model')]))))
    return Answers(Settings(groq_key='mock', model='test-stream-model'), client), create


def test_real_stream_roles_partials_context_and_separate_answer_events():
    async def check():
        session, events, _ = make_session()
        session.answers, request = streaming_answers(MockStream())
        await session.start(0, 1, 'short', options=live_options())
        microphone, system = session.transcribers['me'], session.transcribers['interviewer']
        system.turn(turn('Find duplicates in an array', final=False))
        assert events[-1]['type'] == 'transcript' and not events[-1]['final']
        assert not request.await_count
        microphone.turn(turn('I would start by clarifying the input format.', 1))
        microphone.turn(turn('What size is your engineering team?', 2))
        assert not session.question_task and not session.answer_task
        system.turn(turn('Find duplicates in an array', final=True))
        system.turn(turn('Assume n is at most 100000 and return the first duplicate.', 2))
        await session.question_task
        await session.answer_task
        transcript = [e for e in events if e['type'] == 'transcript']
        assert transcript[0]['role'] == 'interviewer' and transcript[0]['source'] == 'system_audio'
        assert any(e['role'] == 'me' and e['source'] == 'microphone' for e in transcript)
        args = request.call_args.kwargs
        context = json.loads(args['messages'][1]['content'])
        assert 'return the first duplicate' in context['question']
        assert context['interviewer_intent'] == 'coding'
        assert any(x['speaker'] == 'me' for x in context['conversation'])
        assert 'professional_context' in context
        assert args['stream'] and args['response_format'] == {'type': 'json_object'}
        assert args['model'] == 'test-stream-model'
        assert 'Strengthen every response' in args['messages'][0]['content']
        assert request.await_count == 1
        assert any(e['type'] == 'answer_delta' for e in events)
        complete = [e for e in events if e['type'] == 'answer']
        assert len(complete) == 1 and complete[0]['text'] == payload()['text']
        assert not any(e['text'].startswith('Find duplicates') for e in complete)
        assert session.companion_state()['history'] and session.companion_state()['answers']
        await session.stop()
    asyncio.run(check())


@pytest.mark.parametrize('microphone_first', [True, False])
def test_likely_bleed_preserves_source_but_is_hidden_from_context(microphone_first):
    async def check():
        session, events, answers = make_session()
        await session.start(0, 1, 'short', options=live_options())
        question = 'How would you design a reliable payment processing system?'
        roles = ['me', 'interviewer'] if microphone_first else ['interviewer', 'me']
        for role in roles: session.turn(role, turn(question))
        mic = next(x for x in session.history if x['role'] == 'me')
        assert mic['source'] == 'microphone' and mic['duplicate_of'].endswith('interviewer:1')
        assert mic['original_text'] == question
        assert any(e.get('duplicate_of') for e in events if e['type'] == 'transcript')
        await session.question_task
        await session.answer_task
        context = json.loads(answers.calls[0][0])
        assert [x['speaker'] for x in context['conversation']] == ['interviewer']
        await session.stop()
    asyncio.run(check())


def test_coding_preamble_examples_followups_and_formatting_repeats():
    async def check():
        session, _, answers = make_session()
        await session.start(0, 1, 'short', options=live_options())
        session.turn('interviewer', turn('Given an array of integers, input may contain negatives.', 1))
        assert not session.answer_task and not session.question_task
        session.turn('interviewer', turn('Find duplicate values.', 2))
        session.turn('interviewer', turn('For example input is [1, 2, 1] and output is [1].', 3))
        await session.question_task; await session.answer_task
        first = json.loads(answers.calls[0][0])
        assert 'negatives' in first['question'] and '[1, 2, 1]' in first['question']
        session.turn('interviewer', turn('For example, input is [1, 2, 1] and output is [1].', 3))
        assert len(answers.calls) == 1
        session.turn('interviewer', turn('What if the array cannot fit in memory?', 4))
        await session.question_task; await session.answer_task
        second = json.loads(answers.calls[1][0])
        assert 'negatives' in second['question'] and 'cannot fit in memory' in second['question']
        await session.stop()
    asyncio.run(check())


def test_clarification_cancels_obsolete_stream_before_new_generation():
    async def check():
        release = asyncio.Event()
        old_stream = MockStream(release=release)
        session, events, _ = make_session()
        session.answers, request = streaming_answers(old_stream)
        await session.start(0, 1, 'short', options=live_options())
        session.turn('interviewer', turn('Find duplicates in an array.'))
        await session.question_task
        for _ in range(50):
            if session.draft: break
            await asyncio.sleep(.005)
        assert session.draft
        old_task = session.answer_task
        new_stream = MockStream()
        request.return_value = new_stream
        session.turn('interviewer', turn('Also preserve the original input order.', 2))
        assert session.companion_state()['draft'] is None
        await session.question_task; await session.answer_task
        assert old_task.cancelled() and old_stream.closed
        assert request.await_count == 2  # Clarification is a new prompt, never a retry.
        complete = [e for e in events if e['type'] == 'answer']
        assert len(complete) == 1 and 'original input order' in complete[0]['question']
        await session.stop()
    asyncio.run(check())


@pytest.mark.parametrize('failure', ['invalid', 402, 429])
def test_stream_errors_clear_draft_close_stream_and_never_retry(failure):
    async def check():
        class ProviderFailure(Exception): status_code = failure
        stream = MockStream(content='{"text":"Draft"}' if failure == 'invalid' else None,
                            fail=None if failure == 'invalid' else ProviderFailure())
        session, _, _ = make_session()
        session.answers, request = streaming_answers(stream)
        await session.start(0, 1, 'short', options=live_options())
        resources = [*session.captures.values(), *session.transcribers.values()]
        session.request_answer('Find duplicates in an array')
        await session.answer_task
        if session.fatal_task: await session.fatal_task
        if failure == 'invalid':
            assert session.state == 'running' and session.answer_enabled
            await session.stop()
        assert stream.closed and request.await_count == 1
        assert session.state == 'idle' and session.draft is None and not session.suggestions
        assert all(r.closed for r in resources)
        assert session.snapshot()['maximum_payment_ugx'] == 0
    asyncio.run(check())


def test_streamed_spoken_prefix_never_exposes_metadata_or_code():
    assert spoken_prefix('{"category":"Coding","text":"I\\nwould use a set') == 'I\nwould use a set'
    assert spoken_prefix('{"details":"literal \\\"text\\\": \\\"fake\\\"","text":"Actual answer') == 'Actual answer'
    assert spoken_prefix('{"text":"Answer","code":"dangerous-looking code"}') == 'Answer'
    assert spoken_prefix('{"code":"only code"}') == ''


def test_stop_all_cancels_question_settling_before_any_request():
    async def check():
        session, _, answers = make_session()
        await session.start(0, 1, 'short', options=live_options())
        resources = [*session.captures.values(), *session.transcribers.values()]
        session.turn('interviewer', turn('Explain cache invalidation.'))
        pending = session.question_task
        await session.stop('STOP ALL')
        assert pending.cancelled() and not answers.calls
        assert all(r.closed for r in resources)
    asyncio.run(check())


def test_phone_event_stream_disconnect_reconnect_does_not_mutate_session():
    async def check():
        app = create_app()
        session = app.state.control.session
        manager = app.state.phone
        manager.device = {'token': 'mock-device', 'seen': 0, 'expires': float('inf'), 'name': 'Mock', 'peer': '192.168.1.2'}
        request = Request({'type': 'http', 'method': 'GET', 'path': '/api/companion/events',
                           'headers': [(b'authorization', b'Bearer mock-device')],
                           'state': {'phone_listener': True}})
        from app.copilot_api import copilot_routes
        route = next(r.endpoint for r in copilot_routes(None, 'local', app.state.control, manager).routes if r.path == '/api/companion/events')
        session.state = 'running'
        session.latest = 'Shared question'
        session.history.append({'key': 'i1', 'role': 'interviewer', 'source': 'system_audio', 'text': 'Shared question', 'final': True})
        response = await route(request)
        reader = response.body_iterator
        snapshot = json.loads(await anext(reader))
        assert snapshot['history'][0]['role'] == 'interviewer'
        session.emit({'type': 'answer_delta', 'text': 'AI only', 'revision': 1})
        assert json.loads(await anext(reader))['type'] == 'answer_delta'
        await reader.aclose()
        assert not session.subscribers and session.state == 'running' and session.latest == 'Shared question'
        response = await route(request)
        reader = response.body_iterator
        assert json.loads(await anext(reader))['latest'] == 'Shared question'
        manager.revoke()
        session.emit({'type': 'transcript', 'role': 'me', 'text': 'Private'})
        assert json.loads(await anext(reader))['type'] == 'revoked'
        await reader.aclose()
        assert not session.subscribers
    asyncio.run(check())


def test_stop_all_closes_inflight_provider_stream_and_draft():
    async def check():
        stream = MockStream(release=asyncio.Event())
        session, _, _ = make_session()
        session.answers, request = streaming_answers(stream)
        await session.start(0, 1, 'short', options=live_options())
        resources = [*session.captures.values(), *session.transcribers.values()]
        session.request_answer('Find duplicates in an array')
        for _ in range(50):
            if session.draft: break
            await asyncio.sleep(.005)
        assert session.draft
        task = session.answer_task
        await session.stop('STOP ALL')
        assert task.cancelled() and stream.closed and session.draft is None
        assert all(resource.closed for resource in resources)
        assert request.await_count == 1 and not session.pending_questions
    asyncio.run(check())


def test_overlong_challenge_blocks_instead_of_generating_from_truncated_context():
    async def check():
        session, events, answers = make_session()
        await session.start(0, 1, 'short', options=live_options())
        session.turn('interviewer', turn('Write a function. ' + 'constraint ' * 2000))
        assert session.challenge_blocked and not session.question_task and not answers.calls
        assert any(e['type'] == 'error' and 'context limit' in e['message'] for e in events)
        await session.stop()
    asyncio.run(check())


def test_completed_answer_survives_next_question_and_phone_reconnect():
    async def check():
        session, events, _ = make_session()
        await session.start(0, 1, 'short', options=live_options())
        system = session.transcribers['interviewer']
        system.turn(turn('How do you handle feedback?', 1))
        await session.question_task
        await session.answer_task
        first = list(session.suggestions)
        assert first
        system.turn(turn('Why should we hire you?', 2))
        assert session.revision != first[0]['revision']
        assert session.companion_state()['answers'] == first
        await session.question_task
        await session.answer_task
        assert len(session.companion_state()['answers']) == 2
        await session.stop()
    asyncio.run(check())


def test_spoken_introduction_generates_answer_for_desktop_and_phone_once():
    async def check():
        session, events, _ = make_session()
        await session.start(0, 1, 'short', options=live_options())
        prompt = 'Share a brief summary about yourself and your experience'
        session.transcribers['me'].turn(turn(prompt, 1))
        assert session.question_task is None
        session.transcribers['interviewer'].turn(turn(prompt, 1, final=False))
        assert session.question_task is None
        session.transcribers['interviewer'].turn(turn(prompt, 1))
        await session.question_task
        await session.answer_task
        answers = [event for event in events if event['type'] == 'answer']
        assert len(answers) == 1 and answers[0]['question'] == prompt
        assert answers[0]['text']
        assert session.companion_state()['answers'] == [{key: value for key, value in answer.items() if key != 'sent_at'} for answer in answers]
        session.transcribers['interviewer'].turn(turn(prompt + '.', 1))
        await asyncio.sleep(.3)
        assert len([event for event in events if event['type'] == 'answer']) == 1
        await session.stop()
    asyncio.run(check())
