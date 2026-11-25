import asyncio
import stat
from app.main import create_app
from tests.test_core import make_session
from tests.test_live_pipeline import turn
from tests.live_helpers import live_options


def test_stop_retains_review_closes_providers_and_restores_after_restart(tmp_path):
    async def check():
        session, events, _ = make_session()
        path = tmp_path/'review.json'
        session.configure_review(path)
        await session.start(0, 1, 'medium', options=live_options())
        session.turn('interviewer', turn('Tell me about yourself', 1))
        session.turn('me', turn('I work on backend systems.', 1))
        await session.question_task; await session.answer_task
        session.turn('interviewer', turn('And how would you', 2, final=False))
        original = session.companion_state()
        resources = [*session.captures.values(), *session.transcribers.values()]
        marker = len(events)
        await session.stop('STOP ALL completed.')
        assert session.state == 'idle' and all(item.closed for item in resources)
        assert not session.interview and not session.tasks and not session.pending_questions
        assert session.deadline is None and session.draft is None
        assert session.companion_state()['history'] == original['history']
        assert session.companion_state()['answers'] == original['answers']
        assert session.companion_state()['partials'] == original['partials']
        assert not any(event['type'] == 'clear' for event in events[marker:])
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        saved = path.read_text()
        assert 'assembly_key' not in saved and 'groq_key' not in saved
        restored = create_app(review_path=path).state.control.session
        assert restored.state == 'idle' and not restored.captures and not restored.transcribers
        assert restored.companion_state()['history'] == original['history']
        assert restored.companion_state()['answers'] == original['answers']
        assert restored.companion_state()['partials'] == original['partials']
        restored.clear()
        assert not restored.history and not restored.suggestions and not restored.partials
        assert not path.exists()
        await restored.stop()
        assert not path.exists()
    asyncio.run(check())


def test_local_audio_test_does_not_erase_stopped_review():
    async def check():
        session, events, _ = make_session()
        session.history.append({'key':'review-1','role':'interviewer','text':'Previous interview'})
        session.suggestions.append({'text':'Previous answer','question':'Previous interview'})
        # Test cleanup uses the same cleanup path as the 10-second local audio test.
        session.testing = True
        await session.stop('Local audio test finished.')
        assert session.history[0]['text'] == 'Previous interview'
        assert session.suggestions[0]['text'] == 'Previous answer'
        assert not any(item['type'] == 'clear' for item in events)
    asyncio.run(check())
