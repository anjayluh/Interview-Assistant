import asyncio

import pytest
import sounddevice as sd
from starlette.testclient import TestClient

from app.audio import Capture
from app.main import create_app
from tests.test_core import FakeCapture, make_session
from tests.live_helpers import ORIGIN


def test_microphone_test_acknowledged_before_open_and_driver_error_preserved():
    app = create_app()
    opened = []
    class FailedMicrophone(FakeCapture):
        def open(self):
            opened.append(self.device)
            raise sd.PortAudioError('Error opening input stream: Device unavailable', -9985)

    session = app.state.control.session
    session.capture_factory = FailedMicrophone
    with TestClient(app) as client:
        client.get('/')
        with client.websocket_connect('/ws?client=audio-debug-12345678', headers=ORIGIN) as ws:
            ws.receive_json()
            ws.send_json({'cmd': 'test', 'role': 'me', 'device': 7})
            first = ws.receive_json()
            assert first['type'] == 'audio_diagnostic' and first['role'] == 'me'
            assert first['stage'] == 'opening'
            events = [first]
            while True:
                event = ws.receive_json()
                events.append(event)
                if event['type'] == 'error':
                    break
            assert opened == [7]
            assert any(event.get('stage') == 'error' and '-9985' in event['message'] for event in events)
            assert session.state == 'idle'
            assert session.snapshot()['audio_diagnostics'][-1]['stage'] == 'error'
            assert not session.answers.verified


def test_microphone_frames_reported_on_correct_source():
    async def check():
        session, events, _ = make_session()
        await session.test('me', 3)
        await asyncio.sleep(.03)
        stages = [event['stage'] for event in events if event['type'] == 'audio_diagnostic']
        assert stages[:3] == ['opening', 'opened', 'receiving']
        assert all(event['role'] == 'me' for event in events
                   if event['type'] in ('audio_diagnostic', 'level'))
        await session.stop()
        assert len(session.audio_diagnostics) <= 24
    asyncio.run(check())


def test_audio_callback_counts_frames_without_storing_extra_audio():
    capture = Capture(0)
    capture.closed = False
    capture.callback(b'\0' * 1600, 800, None, False)
    assert capture.callback_count == 1 and capture.frame_count == 800
    assert capture.queue.qsize() == 1
    capture.close()
