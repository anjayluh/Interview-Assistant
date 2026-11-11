import asyncio
from datetime import date
from io import BytesIO
import json
import stat
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from zipfile import ZipFile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest
from starlette.testclient import TestClient

from app.answers import Answers
from app.config import Settings
from app.main import create_app
from app.profile import InterviewContext, InterviewSetup, ProfileStore, SEED, experience
from app.questions import is_question
from tests.test_core import make_session

ORIGIN = {'origin': 'http://testserver'}
PATH = '/api/profile'


def docx(text):
    result = BytesIO()
    with ZipFile(result, 'w') as archive:
        archive.writestr('word/document.xml', f'''
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>''')
    return result.getvalue()


def pdf(text):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                             NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})
    })
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 10 700 Td ({text}) Tj ET'.encode('ascii'))
    page[NameObject('/Contents')] = writer._add_object(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


@pytest.fixture
def store(tmp_path):
    return ProfileStore(tmp_path / 'private' / 'profile.json')


@pytest.mark.parametrize('extension, document', [('pdf', pdf), ('docx', docx)])
def test_upload_preview_and_correct_before_saving(store, extension, document):
    text = 'Reduced payment request failures by 45% through payment-flow redesign and stronger API error handling.'
    with TestClient(create_app(profile_store=store)) as client:
        assert client.get(PATH).status_code == 403
        client.get('/')
        preview = client.post(PATH + '/preview', params={'filename': f'Targeted.{extension}'},
                              content=document(text), headers=ORIGIN)
        assert preview.status_code == 200, preview.text
        extracted = preview.json()
        assert extracted['facts'] == text
        assert extracted['name'] == 'Targeted'
        assert any(item['kind'] == 'Possible contradiction' and '40%' in item['existing']
                   for item in extracted['comparison'])
        assert not store.path.exists()
        assert client.get(PATH).json()['cvs'] == []
        saved = client.post(PATH + '/cvs', json={'name': 'Reviewed', 'facts': text.replace('45%', '40%')},
                            headers=ORIGIN).json()
        assert client.get(PATH + '/cvs/' + saved['id']).json()['facts'].startswith('Reduced payment request failures by 40%')
        assert client.get(PATH).json()['profile'] == SEED.model_dump()
        assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700


def test_versions_default_and_master_survive_restart(store):
    with TestClient(create_app(profile_store=store)) as client:
        client.get('/')
        one = client.post(PATH + '/cvs', json={'name': 'Backend', 'facts': 'Django payments'},
                          headers=ORIGIN).json()['id']
        two = client.post(PATH + '/cvs', json={'name': 'Frontend', 'facts': 'React Native'},
                          headers=ORIGIN).json()['id']
        assert client.put(PATH + '/default', json={'id': one}, headers=ORIGIN).status_code == 200
        assert client.put(PATH + '/cvs/' + two, json={'name': 'Mobile', 'facts': 'React Native and Redux'},
                          headers=ORIGIN).status_code == 200
        assert client.put(PATH, json={**SEED.model_dump(), 'notes': 'New verified project'},
                          headers=ORIGIN).status_code == 200

    restarted = ProfileStore(store.path)
    assert restarted.summary()['profile']['notes'] == 'New verified project'
    assert restarted.get_cv(one) == {'name': 'Backend', 'facts': 'Django payments'}
    assert restarted.get_cv(two) == {'name': 'Mobile', 'facts': 'React Native and Redux'}
    assert restarted.interview(InterviewSetup()).cv_facts == 'Django payments'
    assert restarted.interview(InterviewSetup(cv_id=two)).cv_facts == 'React Native and Redux'
    assert restarted.interview(InterviewSetup(cv_id='')).cv_facts == ''
    assert restarted.summary()['default_cv'] == one
    with TestClient(create_app(profile_store=restarted)) as client:
        client.get('/')
        assert client.delete(PATH + '/cvs/' + one, headers=ORIGIN).json()['default_cv'] is None
        assert client.get(PATH + '/cvs/' + one).status_code == 400
        assert client.get(PATH + '/cvs/' + two).status_code == 200


def test_upload_bounds_validation_and_origin(store):
    with TestClient(create_app(profile_store=store)) as client:
        client.get('/')
        assert client.post(PATH + '/preview?filename=cv.docx', content=docx('CV')).status_code == 403
        assert client.post(PATH + '/preview?filename=cv.pdf', content=b'bad', headers=ORIGIN).status_code == 400
        assert client.post(PATH + '/preview?filename=cv.pdf', content=b'x' * (5 * 1024 * 1024 + 1),
                           headers=ORIGIN).status_code == 413
        assert client.put(PATH, json={**SEED.model_dump(), 'career_start': '2018-13'},
                          headers=ORIGIN).status_code == 422


def test_experience_uses_one_timeline_and_rolls_over():
    assert experience(SEED.career_start, date(2026, 11, 11)) == {'years': 8, 'months': 1, 'claim': '8+ years'}
    assert experience(SEED.career_start, date(2027, 9, 30))['claim'] == '8+ years'
    assert experience(SEED.career_start, date(2027, 10, 1))['claim'] == '9+ years'
    assert 'October 2018 – September 2019' in SEED.employment
    assert 'January 2019 – April 2021' in SEED.employment


def test_relevant_role_context_without_full_cv():
    context = InterviewContext(setup=InterviewSetup(title='Senior Backend Engineer',
                               description='Django PostgreSQL APIs and reliability'),
                               cv_facts='Django APIs and PostgreSQL\nUnrelated private text\nprivate@example.com')
    selected = json.loads(context.for_question('Tell me about yourself.'))
    assert selected['skills'][0].startswith(('Backend:', 'Data:'))
    assert selected['selected_cv_excerpts'] == ['Django APIs and PostgreSQL']
    assert 'private@example.com' not in json.dumps(selected)
    assert selected['target_role'] == 'Senior Backend Engineer'
    assert any('Numida' in row for row in selected['employment'])


PAYMENT = SEED.achievements.splitlines()[0]
CASES = [
    ('Tell me about yourself.', 'Professional background', 'Direct experience',
     "I'm a senior full-stack engineer and technical lead with 8+ years in software engineering.", 'technical lead'),
    ('Why are you leaving your previous role?', 'Professional background', 'Needs clarification',
     "I'd clarify the actual reason for leaving before answering.", 'actual reason'),
    ('How did you reduce payment request failures?', 'Behavioural', 'Direct experience',
     'I redesigned the payment flow and strengthened API error handling. Payment request failures dropped by 40%.', '40%'),
    ('Explain how you would design an idempotent payment API.', 'API / backend engineering', 'Proposed approach',
     "I'd require an idempotency key, enforce a unique constraint, and reject reuse with a different payload.", 'unique constraint'),
    ('How would you debug slow Django API endpoints?', 'Debugging / troubleshooting', 'Proposed approach',
     "I'd reproduce the slow request, inspect traces and query counts, and compare p95 latency after fixing the bottleneck.", 'p95'),
    ('How would you optimise a PostgreSQL query?', 'Database', 'Proposed approach',
     "I'd inspect EXPLAIN ANALYZE for actual row counts and use indexes selectively based on the plan.", 'EXPLAIN ANALYZE'),
    ('How would you design a scalable notification service?', 'System design', 'Proposed approach',
     "I'd put requests on a queue and let workers send notifications with bounded retries and a dead-letter queue.", 'dead-letter'),
    ('Explain the difference between concurrency and parallelism.', 'Technical concept', 'Not applicable',
     'Concurrency lets tasks make progress in overlapping time. Parallelism executes work simultaneously.', 'simultaneously'),
    ('How would you implement CI/CD for a containerised Django application?', 'Cloud / DevOps', 'Proposed approach',
     "I'd test and scan the image, promote the same immutable digest, and validate migrations before rolling it out.", 'immutable digest'),
    ('Write a Python function to find duplicate values in a list.', 'Coding / algorithm', 'Proposed approach',
     "I'd track seen values in a set and add repeats to a second set.", 'second set'),
    ('How would you investigate duplicate transactions caused by retries?', 'Debugging / troubleshooting', 'Proposed approach',
     "I'd correlate retry traces by transaction and idempotency key, reproduce concurrent retries, and assert a single write.", 'single write'),
]
DUPLICATES_CODE = '''def duplicates(values):
    seen, repeated = set(), set()
    for value in values:
        if value in seen:
            repeated.add(value)
        seen.add(value)
    return repeated'''


@pytest.mark.parametrize('question, category, kind, spoken, required', CASES)
def test_interview_question_contract_with_mocked_groq(question, category, kind, spoken, required):
    """Check prompt grounding and validate representative replies without calling a model."""
    async def check():
        assert is_question(question)
        coding = category == 'Coding / algorithm'
        payment = category == 'Behavioural'
        professional = json.loads(InterviewContext().for_question(question))
        if payment:
            assert PAYMENT in professional['achievements']
        example = PAYMENT if payment else 'Invented production deployment'
        reply = {'category': category, 'experience': kind, 'text': spoken, 'example': example,
                 'code': DUPLICATES_CODE if coding else '', 'language': 'Python' if coding else '',
                 'details': 'O(n) expected time and O(n) space for hashable values.' if coding else ''}
        call = AsyncMock(return_value=NS(choices=[NS(message=NS(content=json.dumps(reply)))]))
        service = Answers(Settings(model='openai/gpt-oss-20b'), NS(
            models=NS(list=AsyncMock(return_value=NS(data=[NS(id='openai/gpt-oss-20b')]))),
            chat=NS(completions=NS(create=call))))
        result = await service.generate(json.dumps({'question': question, 'professional_context': professional}), 'medium')
        request = call.call_args.kwargs
        assert call.await_count == 1
        assert request['response_format']['json_schema']['strict']
        prompt = request['messages'][0]['content']
        assert 'Do not invent employers, dates, reasons for leaving' in prompt
        assert 'job description is never evidence of experience' in prompt
        assert 'working code in the requested language' in prompt
        assert 'logs/metrics/traces' in prompt and 'preserve contribution wording' in prompt.lower()
        assert '115 spoken words' in prompt
        assert json.loads(request['messages'][1]['content'])['question'] == question
        assert (result['category'], result['experience']) == (category, kind)
        assert required in result['text'] and len(result['text'].split()) < 115
        assert result['example'] == (PAYMENT if payment else '')
        if kind == 'Proposed approach':
            assert result['text'].startswith("I'd")
        if coding:
            namespace = {}
            exec(result['code'], namespace)
            assert namespace['duplicates']([1, 2, 1, 1, 3, 2]) == {1, 2}
            assert namespace['duplicates']([]) == set()
            assert 'O(n)' in result['details']
    asyncio.run(check())


def test_session_snapshot_and_privacy_between_interviews():
    async def check():
        session, _, answers = make_session()
        first = InterviewContext(setup=InterviewSetup(title='Private first role', keyterms=False),
                                 cv_facts='Django unique-first-interview-fact')
        await session.start(0, 1, 'medium', first)
        assert all(not trans.use_keyterms for trans in session.transcribers.values())
        event = NS(transcript='How do you use Django?', turn_order=1, end_of_turn=True)
        session.turn('interviewer', event)
        await session.answer_task
        assert 'unique-first-interview-fact' in answers.calls[0][0]
        session.turn('interviewer', NS(transcript='How do you use Django in production?', turn_order=1, end_of_turn=True))
        assert len(session.history) == 1
        assert session.history[0]['original_text'] == 'How do you use Django?'
        assert session.history[0]['text'].endswith('in production?')
        await session.stop()
        assert session.interview is None
        await session.start(0, 1, 'medium', InterviewContext())
        assert all(trans.use_keyterms for trans in session.transcribers.values())
        assert not session.suggestions and not session.history
        session.turn('interviewer', event)
        await session.answer_task
        assert 'unique-first-interview-fact' not in answers.calls[1][0]
        assert 'Private first role' not in answers.calls[1][0]
        assert 'in production?' not in answers.calls[1][0]
        await session.stop()
    asyncio.run(check())


def test_websocket_start_snapshots_selected_cv_and_role(store):
    from app.profile import CVFields
    from tests.test_core import FakeAnswers, FakeCapture, FakeTranscriber

    default_id = store.save_cv(CVFields(name='Default', facts='Django default fact'))['id']
    selected_id = store.save_cv(CVFields(name='Selected', facts='Django selected fact'))['id']
    store.set_default(default_id)
    app = create_app(Settings(assembly_key='mock', groq_key='mock'), profile_store=store)
    session = app.state.control.session
    session.answers = FakeAnswers()
    session.capture_factory = FakeCapture
    session.transcriber_factory = FakeTranscriber
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=interview-test-123456', headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json({'cmd': 'start', 'mic': 0, 'system': 1, 'interview': {
                'cv_id': selected_id, 'title': 'Senior Backend Engineer', 'keyterms': False,
            }})
            assert ws.receive_json()['type'] == 'clear'
            assert ws.receive_json()['state'] == 'starting'
            assert ws.receive_json()['state'] == 'running'
            assert session.length == 'medium'
            assert session.interview.setup.title == 'Senior Backend Engineer'
            assert session.interview.cv_facts == 'Django selected fact'
            store.save_cv(CVFields(name='Edited', facts='Django edited fact'), selected_id)
            assert session.interview.cv_facts == 'Django selected fact'
            assert store.summary()['default_cv'] == default_id
