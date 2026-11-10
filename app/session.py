import asyncio
import time
from collections import deque
from .audio import Capture
from .answers import Answers
from .questions import QuestionGate, is_question
from .transcription import Transcriber

class Session:
    def __init__(self, settings, emit, capture_factory=Capture, transcriber_factory=Transcriber, answers=None):
        self.settings, self.emit = settings, emit
        self.capture_factory, self.transcriber_factory = capture_factory, transcriber_factory
        self.answers = answers or Answers(settings)
        self.lock = asyncio.Lock()
        self.answer_task = None
        self.start_task = None
        self.tasks = []
        self.captures = {}
        self.transcribers = {}
        self.history = deque(maxlen=120)
        self.suggestions = deque(maxlen=12)
        self.partials = {}
        self.final_keys = deque(maxlen=400)
        self.gate = QuestionGate()
        self.state = 'idle'
        self.started = None
        self.elapsed = 0
        self.channel_seconds = 0
        self.opened_at = {}
        self.length = 'short'
        self.reason = 'Ready. Streaming starts only when you press Start.'
        self.latest = ''
        self.testing = False
        self.generation = 0
        self.pending_question = None

    async def open_capture(self, cap):
        task = asyncio.create_task(asyncio.to_thread(cap.open))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise

    def snapshot(self):
        now = time.monotonic()
        duration = now - self.started if self.started and self.state == 'running' else self.elapsed
        usage = self.channel_seconds + sum(now - t for t in self.opened_at.values())
        return {'type': 'state', 'state': self.state, 'reason': self.reason,
                'duration': duration, 'channel_seconds': usage,
                'estimate': usage / 3600 * self.settings.hourly_rate,
                'active_channels': len(self.opened_at), 'testing': self.testing, 'sources': list(self.captures),
                'model': self.settings.model, 'max_minutes': self.settings.max_minutes,
                'configured': {'assemblyai': bool(self.settings.assembly_key), 'groq': bool(self.settings.groq_key)},
                'groq_verified': self.answers.verified, 'length': self.length}

    async def start(self, mic, system, length):
        async with self.lock:
            if self.state != 'idle':
                raise ValueError('Stop the current session or audio test first.')
            if mic == system:
                raise ValueError('Choose different microphone and system audio devices.')
            if not self.settings.assembly_key or not self.settings.groq_key:
                raise ValueError('Add both API keys to .env, then restart using the Desktop launcher.')
            if not (0 < self.settings.max_minutes <= 1440):
                raise ValueError('MAX_SESSION_MINUTES must be between 0 and 1440.')
            self.length = length
            self.generation += 1
            self.latest = ''
            self.start_task = asyncio.current_task()
            self.state = 'starting'
            self.reason = 'Checking Groq model and opening audio sources…'
            self.emit(self.snapshot())
            try:
                await self.answers.verify()  # Non-generating model-list check, before any paid session.
                for role, dev in [('me', mic), ('interviewer', system)]:
                    cap = self.capture_factory(dev)
                    self.captures[role] = cap
                    await self.open_capture(cap)
                # Open captures first for permissions/format validation, then drain stale startup frames.
                for cap in self.captures.values():
                    cap.overflow = False
                    while not cap.queue.empty():
                        cap.queue.get_nowait()
                self.started = time.monotonic()
                self.elapsed = self.channel_seconds = 0
                self.gate = QuestionGate()
                self.final_keys.clear()
                for role in ('me', 'interviewer'):
                    trans = self.transcriber_factory(self.settings.assembly_key,
                        lambda event, r=role: self.turn(r, event), self.fatal)
                    self.transcribers[role] = trans
                    # Count connection setup conservatively in estimate.
                    self.opened_at[role] = time.monotonic()
                    await trans.connect()
                for cap in self.captures.values():
                    cap.overflow = False
                    while not cap.queue.empty():
                        cap.queue.get_nowait()
                self.state = 'running'
                self.reason = 'Listening'
                for role in self.captures:
                    self.tasks.append(asyncio.create_task(self.pump(role)))
                self.tasks.append(asyncio.create_task(self.watchdog()))
                self.emit(self.snapshot())
            except BaseException:
                await self._cleanup('Session could not start. Check the error and try again.')
                raise
            finally:
                self.start_task = None

    async def pump(self, role):
        try:
            async for chunk in self.captures[role].chunks():
                if self.state == 'running':
                    await self.transcribers[role].send(chunk)
                self.level(role)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.fatal(str(exc))

    def level(self, role):
        cap = self.captures[role]
        now = time.monotonic()
        if now - cap.level_at > .15:
            cap.level_at = now
            self.emit({'type': 'level', 'role': role, 'value': min(1, cap.level * 5)})

    async def watchdog(self):
        await asyncio.sleep(self.settings.max_minutes * 60)
        await self.stop('Maximum session duration reached.')

    def fatal(self, message):
        self.emit({'type': 'error', 'message': message})
        if self.state in ('running', 'starting'):
            asyncio.create_task(self.stop(message))

    def turn(self, role, event):
        if self.state not in ('starting', 'running'):
            return
        text = event.transcript.strip()[:5000]
        if not text:
            return
        key = f'{self.generation}:{role}:{event.turn_order}'
        final = bool(event.end_of_turn)
        self.emit({'type': 'transcript', 'key': key, 'role': role, 'text': text, 'final': final})
        if not final:
            self.partials[role] = {'key': key, 'role': role, 'text': text, 'final': False}
            return
        self.partials.pop(role, None)
        # Formatted final events repeat a turn: update UI, never trigger another answer.
        if key in self.final_keys:
            return
        self.final_keys.append(key)
        self.history.append({'key': key, 'role': role, 'text': text, 'final': True})
        if role == 'interviewer':
            self.latest = text
            if is_question(text) and self.gate.claim(key, text):
                self.request_answer(text)

    def request_answer(self, question=None):
        question = question or self.latest
        if not question:
            raise ValueError('No completed interviewer speech yet.')
        # Coalesce pending questions; retain only the latest, no request backlog.
        self.pending_question = question
        if not self.answer_task or self.answer_task.done():
            self.answer_task = asyncio.create_task(self.answer_loop())

    async def answer_loop(self):
        while self.pending_question:
            question = self.pending_question
            self.pending_question = None
            context = '\n'.join(f"{x['role']}: {x['text']}" for x in list(self.history)[-16:])[-14000:]
            context += '\nLatest interviewer question to answer: ' + question
            self.emit({'type': 'groq', 'status': 'generating'})
            try:
                text = await self.answers.generate(context, self.length)
                item = {'type': 'answer', 'question': question, 'text': text}
                self.suggestions.append(item)
                self.emit(item)
                self.emit({'type': 'groq', 'status': 'ready'})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.emit({'type': 'error', 'message': safe_error(exc, 'Groq request failed. Check model access, credits and network; retry manually.')})
                self.emit({'type': 'groq', 'status': 'error'})

    async def test(self, role, device):
        async with self.lock:
            if self.state != 'idle':
                raise ValueError('Stop the current session or audio test first.')
            self.state, self.testing = 'testing', True
            self.reason = f'Testing {role} locally for 10 seconds. No cloud API calls.'
            cap = self.capture_factory(device)
            self.captures[role] = cap
            try:
                await self.open_capture(cap)
                self.tasks.append(asyncio.create_task(self.test_pump(role)))
                self.emit(self.snapshot())
            except BaseException:
                await self._cleanup('Audio test failed.')
                raise

    async def test_pump(self, role):
        try:
            async with asyncio.timeout(10):
                async for chunk in self.captures[role].chunks():
                    self.level(role)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            raise
        except Exception:
            self.emit({'type': 'error', 'message': 'Audio test failed. Check device access and macOS permissions.'})
        await self.stop('Audio test finished.')

    async def stop(self, reason='Stopped.'):
        # Cancel startup before waiting for its lock, including a connection in progress.
        if self.start_task and self.start_task is not asyncio.current_task():
            self.start_task.cancel()
            await asyncio.gather(self.start_task, return_exceptions=True)
        async with self.lock:
            await self._cleanup(reason)

    async def _cleanup(self, reason):
        if self.state == 'running' and self.started:
            self.elapsed = time.monotonic() - self.started
        self.state = 'stopping'
        self.reason = reason
        self.emit(self.snapshot())
        current = asyncio.current_task()
        pending = [t for t in self.tasks if t is not current and not t.done()]
        if self.answer_task and self.answer_task is not current and not self.answer_task.done():
            pending.append(self.answer_task)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        # Send termination for both channels concurrently before hardware teardown.
        results = await asyncio.gather(*(t.close() for t in self.transcribers.values()), return_exceptions=True)
        now = time.monotonic()
        self.channel_seconds += sum(now - t for t in self.opened_at.values())
        self.opened_at.clear()
        for cap in self.captures.values():
            try:
                await asyncio.to_thread(cap.close)
            except Exception:
                self.emit({'type': 'error', 'message': 'Audio device cleanup reported an error.'})
        self.transcribers.clear()
        self.captures.clear()
        self.tasks.clear()
        self.partials.clear()
        self.pending_question = None
        self.testing = False
        self.state = 'idle'
        if any(isinstance(x, BaseException) for x in results):
            self.emit({'type': 'error', 'message': 'Cloud termination reported an error. Check AssemblyAI usage dashboard before restarting.'})
        self.emit(self.snapshot())

    def clear(self):
        if self.answer_task and not self.answer_task.done():
            self.answer_task.cancel()
        self.pending_question = None
        self.history.clear()
        self.suggestions.clear()
        self.partials.clear()
        self.latest = ''
        self.emit({'type': 'clear'})


def safe_error(exc, fallback):
    # Do not display SDK errors, which may include request data or credentials.
    return str(exc) if isinstance(exc, ValueError) else fallback
