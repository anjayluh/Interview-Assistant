import asyncio
import json
import stat
import wave
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from starlette.testclient import TestClient
from app.grounding import guard_answer, evidence_bank
from app.answers import parse_spoken_answer
from app.questions import interviewer_intent, conversation_move, likely_bleed
from app.review import Review, summary
from app.review_audio import ReviewAudio
from app.main import create_app
from tests.test_core import make_session
from tests.test_live_pipeline import turn
from tests.live_helpers import live_options, ORIGIN
from tests.phone_helpers import configure_phone, pair


PROFILE = {'selected_cv_excerpts':['Contributed to a Twitter importer serving more than 5,000 users.'],
           'achievements':['Reduced payment-request failures by 40% through payment-flow redesign.'],
           'employment':['Technical Lead, Numida Technologies'], 'skills':['Python, Django, PostgreSQL'],
           'recent_profile_notes':[], 'company_research_user_supplied':'Stripe processed 2 million records.',
           'job_description_excerpt':'Azure, MongoDB, Stripe'}


@pytest.mark.parametrize('fabrication',[
    'I tackled a batch job processing 2 million loan applications nightly.',
    'In my last role at Numida, we used an NGINX gateway.',
    'I refactored Stripe and PayPal integrations using Strategy.',
    'This reduced database load by 70%.',
    'We cut integration time from 90 minutes to 12 minutes.',
    "I've used Saga daily at Numida.",
])
def test_real_interview_fabrications_are_not_published_as_personal_history(fabrication):
    answer=guard_answer(parse_spoken_answer(fabrication+" I'd first measure the bottleneck."),PROFILE)
    assert fabrication not in answer['text']
    assert "I'd first measure" in answer['text']
    assert answer['grounding']['removed_sentences'] == 1
    assert not answer['evidence']


def test_immutable_evidence_and_proposed_technical_reasoning_are_distinguished():
    bank=evidence_bank(PROFILE)
    assert 'Stripe' not in json.dumps(bank) and 'Azure' not in json.dumps(bank)
    answer=guard_answer(parse_spoken_answer("A relevant example is [[E1]]. I'd separate workers from the API and measure queue lag."),PROFILE)
    assert 'I contributed to a Twitter importer' in answer['text']
    assert answer['evidence'][0]['text'] == PROFILE['selected_cv_excerpts'][0]
    assert answer['evidence'][0]['id']=='E1'
    assert 'queue lag' in answer['text']
    assert not answer['grounding_warning']
    missing=guard_answer(parse_spoken_answer('[[E999]]'),PROFILE)
    assert 'needs your confirmation' in missing['text'] and not missing['evidence']


@pytest.mark.parametrize('text,expected',[
 ('Okay cool the other thing is apart from payment are there other projects you have been exposed to','question'),
 ('Yes yes so was there another project you could describe outside payment','question'),
 ('Okay while you think about it are you familiar with design patterns','question'),
 ('Factory creates instances have you heard of Strategy or Facade','question'),
 ('Okay design patterns are lower level than architecture do you follow','check_in'),
 ('You get what I mean?','check_in'),
 ('Yes with these experiences it is fine if you have not come across them','statement'),
 ('Oh yeah Factory is one Strategy is another Facade is another you get','check_in'),
 ('My question is not about duplicate user IDs but storing unnecessary data','clarification'),
 ('Okay that is it from me do you have anything else','question'),
 ('Thank you very much for making the time','goodbye'),
])
def test_actual_interview_turn_routing(text,expected):
    assert interviewer_intent(text)==expected


def test_new_nonpayment_question_replaces_old_target_and_checkins_do_not_generate():
    async def check():
        session,events,answers=make_session()
        await session.start(0,1,'medium',options=live_options())
        for n,text in enumerate(['Describe a performance improvement.','Okay cool are there other projects outside payment','While you think are you familiar with design patterns'],1):
            session.turn('interviewer',turn(text,n))
            await session.question_task; await session.answer_task
            assert json.loads(answers.calls[-1][0])['question']==text
            assert json.loads(answers.calls[-1][0])['relevant_prior_suggestions']==[]
        count=len(answers.calls)
        session.turn('interviewer',turn('You get what I mean?',4))
        session.turn('interviewer',turn('I use Factory and Strategy at my company.',5))
        assert len(answers.calls)==count
        session.turn('interviewer',turn('Thank you very much for making the time',6))
        assert session.wrapup and session.state=='running'
        assert len(answers.calls)==count
        assert any(e['type']=='routing' and e['intent']=='check_in' for e in events)
        await session.stop()
    asyncio.run(check())


def test_long_speaker_echo_is_flagged_in_both_arrival_orders_without_changing_roles():
    async def check(order):
        session,events,_=make_session()
        await session.start(0,1,'medium',options=live_options())
        mic='the gateway validates the signed token before forwarding the request'
        system='I would put authentication at the gateway where '+mic+' and keep internal services private'
        for role in order:
            session.turn(role,turn(mic if role=='me' else system,1))
        row=next(x for x in session.history if x['role']=='me')
        assert row['duplicate_of'].endswith('interviewer:1')
        assert row['speaker_confidence']=='suspected_echo' and row['original_text']==mic
        assert session.review.snapshot()['history']
        await session.stop()
    for order in [('me','interviewer'),('interviewer','me')]: asyncio.run(check(order))
    assert not likely_bleed('my own different explanation of authentication here','their separate discussion of queue partitioning here',1)


def test_full_review_retains_more_than_ui_buffers_and_has_question_answer_timing_links(tmp_path):
    async def check():
        session,_,_=make_session();session.configure_review(tmp_path/'session-review.json')
        await session.start(0,1,'medium',options=live_options())
        for n in range(150):
            session.turn('me',turn('My candidate response number '+str(n),n))
        for rev in range(100):
            session.emit({'type':'latency','stage':'question_finalised','revision':rev,'at_ms':rev*1000})
            session.emit({'type':'latency','stage':'answer_generation_start','revision':rev,'at_ms':rev*1000+250})
            session.emit({'type':'latency','stage':'answer_complete','revision':rev,'at_ms':rev*1000+1250})
            session.emit({'type':'answer','revision':rev,'question':'Explain '+str(rev),'text':'An answer','turn_keys':[]})
        await session.stop()
        record=json.loads((tmp_path/'session-review.json').read_text())
        assert len(record['history'])==150 and len(record['answers'])==100 and len(record['metrics'])>=300
        assert len(session.history)==120
        assert summary(record)['timings'][0]['recognised_to_complete_ms']==1250
        assert not record['limits_hit']
        archive=tmp_path/'reviews'/(record['id']+'.json')
        assert archive.exists() and stat.S_IMODE(archive.stat().st_mode)==0o600
        await session.start(0,1,'medium',options=live_options())
        assert archive.exists()  # Starting again preserves the previous archive.
        await session.stop()
    asyncio.run(check())


def test_receipts_distinguish_replay_and_unmeasured_one_way_time():
    review=Review();review.capture({'type':'answer','revision':1,'question':'Q','text':'A'})
    review.receipt(1,'phone',2.5,False);review.receipt(1,'phone',99,False)
    review.receipt(1,'phone',4,True);review.receipt(1,'desktop',float('nan'))
    assert len(review.data['receipts'])==2
    assert review.data['receipts'][0]['render_ms']==2.5
    assert 'not one-way' in review.data['receipts'][0]['meaning']
    assert review.data['receipts'][1]['replay']


def test_review_endpoints_are_mac_only_and_delete_is_explicit(tmp_path,monkeypatch):
    app=create_app(review_path=tmp_path/'latest.json')
    s=app.state.control.session
    s.emit({'type':'transcript','key':'1','role':'interviewer','text':'Private question','final':True})
    with TestClient(app) as client, configure_phone(app,monkeypatch) as phone:
        assert client.get('/api/reviews').status_code==403
        client.get('/')
        records=client.get('/api/reviews').json();assert records
        identifier=records[0]['id']
        record=client.get('/api/reviews/'+identifier).json()
        assert record['history'][0]['text']=='Private question'
        headers=pair(client,phone)
        assert phone.get('/api/reviews',headers=headers).status_code==403
        assert client.delete('/api/reviews/'+identifier).status_code==403
        assert client.get('/api/reviews/not-valid').status_code==404
        assert client.delete('/api/reviews/'+identifier,headers=ORIGIN).status_code==200
        assert client.get('/api/reviews/'+identifier).status_code==404


def test_audio_is_opt_in_local_and_recording_overflow_does_not_block_live_capture(tmp_path):
    async def check():
        recording=ReviewAudio(tmp_path,'a'*32);recording.start()
        for _ in range(250): recording.offer('me',b'\0'*1600,50)
        await recording.close()
        assert recording.stats['recorded'] and recording.stats['dropped_chunks']==50
        path=tmp_path/('a'*32+'-me.wav')
        with wave.open(str(path)) as audio:
            assert audio.getframerate()==16000 and audio.getnchannels()==1
            assert audio.getnframes()==200*800
        assert stat.S_IMODE(path.stat().st_mode)==0o600
        session,_,_=make_session()
        session.configure_review(tmp_path/'latest.json')
        await session.start(0,1,'medium',options=live_options())
        assert session.review_audio is None and not session.review.data['metadata']['audio_requested']
        await session.stop()
    asyncio.run(check())


def test_grounding_does_not_allow_hypothetical_clause_to_launder_a_past_claim():
    result=guard_answer(parse_spoken_answer("If helpful, we've reduced database load by 70%. I'd measure queue lag."),PROFILE)
    assert '70%' not in result['text'] and "I'd measure" in result['text']


def test_full_review_download_preserves_metrics_and_phone_receipts_require_pairing(tmp_path,monkeypatch):
    app=create_app(review_path=tmp_path/'latest.json');s=app.state.control.session
    s.emit({'type':'answer','revision':7,'question':'Explain a queue','text':'A proposed answer'})
    s.emit({'type':'latency','revision':7,'stage':'question_finalised','at_ms':0})
    s.emit({'type':'latency','revision':7,'stage':'answer_complete','at_ms':1250})
    with TestClient(app) as client,configure_phone(app,monkeypatch) as phone:
        client.get('/')
        assert phone.post('/api/companion/receipt',json={'revision':7,'render_ms':3}).status_code==403
        headers=pair(client,phone)
        response=phone.post('/api/companion/receipt',headers=headers,json={'revision':7,'render_ms':3,'replay':False})
        assert response.status_code==200
        records=client.get('/api/reviews').json()
        record=client.get('/api/reviews/'+records[0]['id']).json()
        assert record['analysis']['timings'][0]['recognised_to_complete_ms']==1250
        assert record['analysis']['delivery_coverage'][0]['devices']['phone']=='new-answer-receipt'
        assert record['analysis']['delivery_coverage'][0]['devices']['desktop']=='unmeasured'
        assert 'spoken vocal delivery; local audio was off' in record['analysis']['unmeasured']


def test_audio_setup_failure_does_not_create_a_recording_task(tmp_path,monkeypatch):
    monkeypatch.setattr('app.review_audio.shutil.disk_usage',lambda path:NS(free=0))
    audio=ReviewAudio(tmp_path,'b'*32);audio.start()
    assert audio.task is None and audio.stats['error']
    assert not list(tmp_path.glob('*.wav'))


def test_explicit_audio_option_records_and_stop_all_closes_both_sources(tmp_path):
    async def check():
        session,_,_=make_session();session.configure_review(tmp_path/'review.json')
        await session.start(0,1,'medium',options=live_options(),record_audio=True)
        assert session.review_audio
        for role in ('me','interviewer'):
            session.review_audio.offer(role,b'\0'*1600,0)
        resources=[*session.captures.values(),*session.transcribers.values()]
        identifier=session.review.data['id']
        await session.stop()
        assert all(x.closed for x in resources)
        assert session.review.data['metadata']['audio_recorded']
        for role in ('me','interviewer'):
            path=tmp_path/'reviews'/(identifier+'-'+role+'.wav')
            with wave.open(str(path)) as stream: assert stream.getnframes()==800
        session.clear()
        assert not list((tmp_path/'reviews').glob('*.wav'))
    asyncio.run(check())


def test_render_receipt_command_from_controlling_socket(tmp_path):
    app=create_app(review_path=tmp_path/'latest.json')
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=receipt-client-123456',headers=ORIGIN) as ws:
            ws.receive_json()
            app.state.control.session.emit({'type':'answer','revision':1,'question':'Q','text':'A'})
            assert ws.receive_json()['type']=='answer'
            ws.send_json({'cmd':'rendered','revision':1,'render_ms':4,'replay':False})
            # A subsequent command provides an ordering barrier for receipt handling.
            ws.send_json({'cmd':'length','length':'short'})
            assert ws.receive_json()['type']=='state'
            assert app.state.control.session.review.data['receipts'][0]['device']=='desktop'


def test_outside_payments_retrieves_nonpayment_cv_evidence():
    from app.profile import InterviewContext,InterviewSetup
    cv='Reduced payment failures by 40%.\nDeveloped a Twitter importer using Python for 5,000 users.\nImplemented a CRM for farmers and cooperatives.\nLed frontend delivery of a national KYC system.'
    context=InterviewContext(setup=InterviewSetup(title='Senior Backend Engineer'),cv_facts=cv)
    for prompt in ['Describe a project outside payments','Are there other projects not related to payment','Have you worked on projects outside payment']:
        facts=json.loads(context.for_question(prompt))
        selected=' '.join(facts['selected_cv_excerpts'])
        assert 'Twitter importer' in selected or 'CRM' in selected or 'national KYC' in selected
        assert 'Reduced payment failures' not in selected


def test_closing_fallback_is_a_question_not_a_recycled_cv_story():
    result=guard_answer(parse_spoken_answer("I've used Saga daily at Numida."),PROFILE,'Do you have any questions for us?')
    assert result['text'].endswith('?')
    assert 'three months' in result['text'] and not result['evidence']
