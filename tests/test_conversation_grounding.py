import asyncio
import json
from app.conversation import conversation_state
from app.grounding import guard_answer
from app.answers import parse_spoken_answer
from tests.test_core import make_session
from tests.test_live_pipeline import turn
from tests.live_helpers import live_options


def transcript(key, role, text, **extra):
    return dict(key=key,role=role,text=text,final=True,**extra)


def test_interviewer_pattern_list_does_not_expand_candidate_factory_experience():
    turns=[transcript('i1','interviewer','Factory, Strategy, Facade and Saga are examples.'),
           transcript('m1','me',"I've used the factory one most.")]
    state=conversation_state(turns)
    assert state['interviewer_introduced_topics']==['Facade','Factory','Saga','Strategy']
    assert [row['topic'] for row in state['candidate_topic_reports']]==['Factory']
    claim='I think I’ve covered the main patterns I use daily—Factory, Strategy, Facade, Saga—and how they’ve helped keep our payment and KYC services modular and test-friendly.'
    result=guard_answer(parse_spoken_answer(claim),{},'Do you have any questions for us?',state)
    assert 'use daily' not in result['text'] and 'Saga' not in result['text']
    assert result['text'].endswith('?')
    assert result['grounding']['removed_sentences']==1


def test_candidate_quote_is_attributed_and_never_becomes_verified_cv_evidence():
    state=conversation_state([transcript('m1','me',"I've used the factory one most.")])
    answer=guard_answer(parse_spoken_answer('[[C1]]'),{},discussion=state)
    assert "I've used the factory one most" in answer['text']
    assert answer['conversation_support'][0]['turn_key']=='m1'
    assert answer['conversation_support'][0]['kind']=='candidate_self_report_not_verified_cv'
    assert not answer['evidence']
    assert 'self-report' in answer['experience']
    general=guard_answer(parse_spoken_answer("I'd use Strategy to swap algorithms. Facade provides a simpler interface."),{},discussion=state)
    assert 'Strategy' in general['text'] and 'Facade' in general['text']
    assert not general['evidence'] and not general['conversation_support']


def test_explicit_uncertainty_and_suspected_echo_preserve_attribution():
    state=conversation_state([
        transcript('i1','interviewer',"I've used Saga and Strategy daily."),
        transcript('m0','me',"I've used Saga and Strategy daily.",duplicate_of='i1'),
        transcript('m1','me',"I haven't used Saga."),
        transcript('m2','me',"I'm not familiar with Facade.")])
    reports={row['topic']:row['status'] for row in state['candidate_topic_reports']}
    assert reports=={'Saga':'uncertain_or_no_experience','Facade':'uncertain_or_no_experience'}
    assert 'Strategy' not in reports


def test_candidate_position_survives_recent_context_window_without_ai_text():
    async def check():
        session,events,answers=make_session()
        await session.start(0,1,'medium',options=live_options())
        session.turn('interviewer',turn('Factory Strategy Facade and Saga are examples.',1))
        session.turn('me',turn("I've used the factory one most.",1))
        for n in range(2,135): session.turn('me',turn('A later unrelated comment '+str(n),n))
        session.turn('interviewer',turn('Do you have any questions for us?',2))
        await session.question_task;await session.answer_task
        context=json.loads(answers.calls[-1][0])
        assert not any('factory one most' in row['text'] for row in context['conversation'])
        state=context['conversation_state']
        assert [row['topic'] for row in state['candidate_topic_reports']]==['Factory']
        assert 'factory one most' in json.dumps(state)
        assert context['relevant_prior_suggestions']==[]
        await session.stop()
    asyncio.run(check())


def test_only_factory_does_not_promote_negated_strategy_and_saga():
    state=conversation_state([transcript('m1','me',"I've only used Factory, not Strategy or Saga.")])
    reports={row['topic']:row['status'] for row in state['candidate_topic_reports']}
    assert reports=={'Factory':'self_reported_familiarity','Strategy':'uncertain_or_no_experience','Saga':'uncertain_or_no_experience'}
    corrected=conversation_state([transcript('m1','me',"I haven't used Saga but I have used Factory.")])
    reports={row['topic']:row['status'] for row in corrected['candidate_topic_reports']}
    assert reports=={'Saga':'uncertain_or_no_experience','Factory':'self_reported_familiarity'}


def test_older_nonpattern_statement_can_be_retrieved_for_current_topic():
    turns=[transcript('m1','me',"I've used Redis for caching.")]
    turns += [transcript('m'+str(n),'me',"I know a later unrelated concept "+str(n)) for n in range(2,20)]
    state=conversation_state(turns,'How would you explain Redis caching?')
    assert any('Redis' in row['text'] for row in state['candidate_statements'].values())
