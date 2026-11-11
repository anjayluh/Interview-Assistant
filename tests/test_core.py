import asyncio
from tests.live_helpers import live_options
import json
import queue
import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import numpy as np
import pytest
from app.questions import is_question, QuestionGate
from app.audio import Capture, CHUNK_BYTES
from app.config import Settings
from app.answers import Answers
from app.session import Session

@pytest.mark.parametrize('text', ['How would you design payments?', 'Tell me about KYC', 'Explain Redis caching', 'Can you describe a trade-off', 'What did you choose'])
def test_questions(text):
    assert is_question(text)

@pytest.mark.parametrize('text', ['Thank you for joining', 'Okay', 'I will introduce the team', 'We use Python here'])
def test_non_questions(text):
    assert not is_question(text)

def test_dedup_and_bounds():
    gate = QuestionGate()
    assert gate.claim('1', 'How do payments work?', now=0)
    assert not gate.claim('1', 'How do payments work?', now=100)
    assert not gate.claim('2', 'HOW do payments work', now=30)
    assert gate.claim('3', 'How do payments work?', now=100)
    for i in range(1000):
        gate.claim(str(i+10), f'question {i}', now=i+200)
    assert len(gate.turns) == 400 and len(gate.recent) == 50

def test_callback_bounded_and_network_free():
    cap = Capture(0); cap.closed = False
    # There is no API client reference on the capture callback path.
    start = time.perf_counter()
    for _ in range(10000):
        cap.callback(b'\0' * CHUNK_BYTES, 800, None, False)
    assert cap.queue.qsize() == 8 and cap.overflow
    assert time.perf_counter() - start < 1
    cap.close()
    assert cap.queue.empty()

@pytest.mark.parametrize('rate', [16000, 44100, 48000])
def test_conversion(rate):
    import soxr
    cap = Capture(0); cap.rate = rate; cap.channels = 2
    cap.resampler = soxr.ResampleStream(rate, 16000, 1, dtype='float32', quality='MQ') if rate != 16000 else None
    t = np.arange(rate) / rate
    signal = (.5 * np.sin(2 * np.pi * 440 * t) * 32767).astype('<i2')
    raw = np.column_stack([signal, signal]).astype('<i2').tobytes()
    out = cap.convert(raw)
    samples = np.frombuffer(out, '<i2')
    assert 15000 < len(samples) <= 16000
    assert .34 < cap.level < .37
    assert max(samples) <= 16400

class FakeCapture:
    all = []
    def __init__(self, device):
        self.device=device; self.queue=queue.Queue(); self.closed=False; self.overflow=False
        self.level=.1; self.level_at=0; self.__class__.all.append(self)
    def open(self): pass
    def close(self): self.closed=True
    async def chunks(self):
        while not self.closed:
            await asyncio.sleep(.01)
            yield b'\0' * CHUNK_BYTES

class FakeTranscriber:
    all = []
    def __init__(self, key, turn, error):
        self.closed=False; self.turn=turn; self.error=error; self.sent=0
        self.__class__.all.append(self)
    async def connect(self): pass
    async def send(self, chunk): self.sent+=1
    async def close(self): self.closed=True

class FakeAnswers:
    def __init__(self): self.verified=False; self.calls=[]
    async def verify(self): self.verified=True
    async def generate(self, context, length):
        self.calls.append((context,length))
        await asyncio.sleep(.01)
        return {'text': 'I would use an idempotency key.'}
    async def close(self): self.verified=False

def make_session(max_minutes=120):
    events=[]; answers=FakeAnswers()
    settings=Settings(assembly_key='fake',groq_key='fake', max_minutes=max_minutes)
    return Session(settings,events.append,FakeCapture,FakeTranscriber,answers), events, answers

def test_turns_only_final_interviewer_and_cleanup():
    async def check():
        session, events, answers = make_session()
        await session.start(0,1,'short', options=live_options())
        captures=list(session.captures.values()); trans=list(session.transcribers.values())
        event=NS(transcript='How do you implement payments?',turn_order=1,end_of_turn=False)
        session.turn('interviewer',event)
        session.turn('me',NS(transcript='What are the requirements?',turn_order=1,end_of_turn=True))
        await asyncio.sleep(.02); assert not answers.calls
        event.end_of_turn=True
        session.turn('interviewer',event)
        session.turn('interviewer',event)  # formatting repeat
        session.turn('interviewer',NS(transcript=event.transcript,turn_order=2,end_of_turn=True))
        await asyncio.sleep(.04)
        assert len(answers.calls)==1
        assert all(t.sent for t in trans)
        await session.stop()
        assert all(c.closed for c in captures) and all(t.closed for t in trans)
        assert session.state=='idle' and not session.tasks and not session.opened_at
        assert session.channel_seconds>0
        await session.start(0,1,'medium', options=live_options())
        session.turn('interviewer',event)
        await asyncio.sleep(.03)
        assert len(answers.calls)==2  # reused SDK turn ID belongs to new session
        await session.stop()
    asyncio.run(check())

def test_timeout_and_fatal():
    async def check():
        session,_,_=make_session()
        await session.start(0,1,'short', options=live_options())
        await session.watchdog(asyncio.get_running_loop().time())
        assert session.state=='idle' and 'Maximum' in session.reason
        await session.start(0,1,'short', options=live_options())
        session.fatal('Network dropped')
        await asyncio.sleep(.02)
        assert session.state=='idle' and session.reason=='Network dropped'
    asyncio.run(check())

def test_failed_second_connection_cleans_both():
    async def check():
        session,_,_=make_session()
        made=[]
        class Failing(FakeTranscriber):
            async def connect(self):
                made.append(self)
                if len(made)==2: raise RuntimeError('failure')
        session.transcriber_factory=Failing
        with pytest.raises(RuntimeError): await session.start(0,1,'short', options=live_options())
        assert len(made)==2 and all(t.closed for t in made)
        assert session.state=='idle' and not session.captures
    asyncio.run(check())

def test_stop_during_start():
    async def check():
        session,_,_=make_session()
        async def slow_verify(): await asyncio.sleep(20)
        session.answers.verify=slow_verify
        task=asyncio.create_task(session.start(0,1,'short', options=live_options()))
        await asyncio.sleep(.01)
        await asyncio.wait_for(session.stop(), .5)
        assert task.cancelled() and session.state=='idle'
    asyncio.run(check())

def test_audio_test_is_local_and_stop_closes():
    async def check():
        session,events,answers=make_session()
        await session.test('me',0)
        cap=session.captures['me']
        await asyncio.sleep(.03)
        assert any(e['type']=='level' for e in events)
        assert not session.transcribers and not answers.verified
        await session.stop()
        assert cap.closed
    asyncio.run(check())

def test_context_and_history_bounded_clear_cancels():
    async def check():
        session,_,answers=make_session()
        session.state='running'
        for i in range(500):
            session.turn('me', NS(transcript='x'*7000,turn_order=i,end_of_turn=True))
        assert len(session.history)==120
        assert len(session.history[0]['text'])==5000
        session.latest='Explain caching'
        session.request_answer()
        await asyncio.sleep(.02)
        assert len(answers.calls[0][0])<20000
        session.request_answer()
        session.clear()
        await asyncio.sleep(.02)
        assert not session.history and not session.suggestions and not session.latest
        await session.stop()
    asyncio.run(check())

def test_groq_model_validation_and_request():
    async def check():
        settings=Settings(groq_key='fake',model='test-model')
        client=NS(models=NS(list=AsyncMock(return_value=NS(data=[NS(id='test-model')]))),
                  chat=NS(completions=NS(create=AsyncMock(return_value=NS(choices=[NS(message=NS(content=json.dumps({
                      'text':'Answer', 'category':'Technical concept', 'experience':'Proposed approach',
                      'example':'', 'code':'', 'language':'', 'details':'',
                  })))])))),
                  close=AsyncMock())
        service=Answers(settings,client)
        from app.profile import InterviewContext
        context = json.dumps({'professional_context': json.loads(InterviewContext().for_question('Explain caching'))})
        assert (await service.generate(context,'short'))['text']=='Answer'
        args=client.chat.completions.create.call_args.kwargs
        assert args['model']=='test-model' and args['max_completion_tokens']==900
        assert 'Do not invent' in args['messages'][0]['content']
        settings.model='missing'
        with pytest.raises(ValueError): await service.verify()
        assert client.chat.completions.create.call_count==1
        await service.close()
    asyncio.run(check())

def test_statement_with_question_word_is_not_question():
    assert not is_question('I understand how payment gateways work')
    assert is_question('So how would you design this')

def test_cancel_open_waits_before_hardware_cleanup():
    async def check():
        session, _, _ = make_session()
        import threading
        entered=threading.Event(); released=threading.Event()
        class SlowCapture(FakeCapture):
            def open(self):
                entered.set()
                released.wait(.5)
                self.opened=True
            def close(self):
                assert self.opened
                super().close()
        session.capture_factory=SlowCapture
        start=asyncio.create_task(session.start(0,1,'short', options=live_options()))
        await asyncio.to_thread(entered.wait,.5)
        stop=asyncio.create_task(session.stop())
        await asyncio.sleep(.01)
        assert not stop.done()
        released.set()
        await asyncio.wait_for(stop,1)
        assert start.cancelled() and session.state=='idle'
    asyncio.run(check())
