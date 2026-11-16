import json
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient

from app.answers import CATEGORIES, RULES, parse_answer
from app.main import create_app
from app.practice import BANK, MIXED, PracticeTurn, practice
from app.profile import InterviewContext, InterviewSetup
from app.questions import is_question
from tests.live_helpers import ORIGIN


@pytest.mark.parametrize('question', [
    "I'm curious how you would prioritise the roadmap",
    "We would like to hear how you handle feedback",
    "Why",
    "Thanks for that. Do you have any questions for us",
    "Tell us about a conflict, and how did it end?",
    "What salary are you looking for, and when could you start?",
])
def test_spoken_intents(question):
    assert is_question(question)


def test_full_interview_prompt_contract():
    for category in ('Company and role', 'Career motivation', 'Operations and strategy',
                     'People management', 'Salary and logistics', 'Closing questions'):
        assert category in CATEGORIES
    for instruction in ('Never invent company facts', 'never invent salary', 'direct reports',
                        'secondary competencies', 'test cases'):
        assert instruction in RULES
    ctx = InterviewContext(setup=InterviewSetup(research='Company fact. Source: supplied annual report.'))
    assert 'supplied annual report' in json.loads(ctx.for_question('What do you know about us?'))[
        'company_research_user_supplied']
    answer = parse_answer(json.dumps({
        'category': 'Operations and strategy', 'text': 'I would weigh customer impact.',
        'experience': 'Proposed approach', 'example': '', 'code': '', 'language': '', 'details': '',
        'competency': 'Prioritisation', 'secondary_competencies': 'Stakeholder management',
        'talking_points': 'Impact and constraints', 'follow_ups': 'What if priorities change?',
    }))
    assert answer['follow_ups'] == 'What if priorities change?'


def test_all_practice_scenarios_and_adaptive_mixed_progression():
    context = InterviewContext(setup=InterviewSetup(title='Technical Lead', company='Sample'))
    for scenario in [*BANK, MIXED]:
        first = practice(PracticeTurn(scenario=scenario), context)
        assert first['prompt'] and first['criteria']
    short = practice(PracticeTurn(scenario=MIXED, answer='I ask for feedback.'), context)
    assert short['phase'] == 1 and 'specific action' in short['prompt']
    visited = short['visited']
    state = short
    for _ in range(len(BANK) * 2):
        state = practice(PracticeTurn(
            scenario=MIXED, index=state['index'], phase=state['phase'],
            stage=state['stage'], visited=visited,
            answer='I would diagnose PostgreSQL queries and compare observed timings.',
        ), context)
        if state.get('done'):
            break
        visited = state['visited']
    assert state['done'] and len(visited) == len(BANK)


def test_local_ocr_and_practice_do_not_touch_providers(monkeypatch):
    recognize = Mock(return_value='Write a function to count duplicates.')
    monkeypatch.setattr('app.copilot_api.recognize', recognize)
    provider = Mock(side_effect=AssertionError('Provider must not be used'))
    monkeypatch.setattr('app.answers.AsyncGroq', provider)
    monkeypatch.setattr('app.transcription.AsyncStreamingClient', provider)
    with TestClient(create_app(phone_origin='')) as client:
        assert client.get('/api/copilot/scenarios').status_code == 403
        client.get('/')
        assert MIXED in client.get('/api/copilot/scenarios').json()
        assert client.post('/api/copilot/practice', headers=ORIGIN,
                           json={'scenario': 'Company and business',
                                 'interview': {'company': 'Sample'}}).status_code == 200
        image = client.post('/api/copilot/ocr', headers=ORIGIN, content=b'mock image')
        assert image.json()['text'] == recognize.return_value
        assert client.post('/api/companion/answer', headers=ORIGIN,
                           json={'question': 'Test'}).status_code == 409
    provider.assert_not_called()


def test_phone_scope_pair_and_revoke():
    app = create_app(phone_origin='https://phone.example')
    with TestClient(app) as laptop, TestClient(app, base_url='https://phone.example') as phone:
        laptop.get('/')
        assert phone.get('/').status_code == 403
        assert phone.get('/companion').status_code == 200
        assert phone.get('/api/companion/state').status_code == 403
        pair = laptop.post('/api/copilot/pair', headers=ORIGIN, json={'company': 'Sample'}).json()
        headers = {'Authorization': 'Bearer ' + pair['url'].split('#')[1], 'Origin': 'https://phone.example'}
        assert phone.get('/api/companion/state', headers=headers).json()['state'] == 'idle'
        assert phone.get('/api/profile', headers=headers).status_code == 403
        assert phone.post('/api/live-quote', headers=headers, json={}).status_code == 403
        assert phone.post('/api/companion/answer', headers=headers, json={'question': 'Test'}).status_code == 409
        response = phone.post('/api/copilot/practice', headers=headers, json={'scenario': 'Company and business'})
        assert 'Sample' in response.json()['prompt']
        laptop.delete('/api/copilot/pair', headers=ORIGIN)
        assert phone.get('/api/companion/state', headers=headers).status_code == 403


def test_phone_written_question_uses_laptop_session_without_starting_streams():
    from tests.live_helpers import start_command
    from tests.test_core import FakeAnswers, FakeCapture, FakeTranscriber

    app = create_app(phone_origin='https://phone.example')
    session = app.state.control.session
    session.settings.assembly_key = session.settings.groq_key = 'fake'
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    session.answers = FakeAnswers()
    with TestClient(app) as laptop, TestClient(app, base_url='https://phone.example') as phone:
        laptop.get('/')
        url = laptop.post('/api/copilot/pair', headers=ORIGIN, json={}).json()['url']
        headers = {'Authorization': 'Bearer ' + url.split('#')[1], 'Origin': 'https://phone.example'}
        with laptop.websocket_connect('/ws?client=copilot-test-123456', headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json(start_command(laptop, interview={'company': 'Sample', 'research': 'Supplied annual report'}))
            assert ws.receive_json()['type'] == 'clear'
            assert ws.receive_json()['state'] == 'starting'
            assert ws.receive_json()['state'] == 'running'
            streams = list(session.transcribers.values())
            prompt = 'Write a Python function to find duplicates.'
            response = phone.post('/api/companion/answer', headers=headers, json={'question': prompt})
            assert response.json() == {'accepted': True}
            while (event := ws.receive_json())['type'] != 'answer':
                pass
            assert event['question'] == prompt
            assert list(session.transcribers.values()) == streams
            context = json.loads(session.answers.calls[0][0])
            assert context['professional_context']['company_research_user_supplied'] == 'Supplied annual report'
            assert phone.get('/api/companion/state', headers=headers).json()['answers'][0]['question'] == prompt
