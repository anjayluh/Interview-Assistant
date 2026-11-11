import asyncio
import json
import time
from collections import deque
from .audio import Capture
from .answers import Answers
from .questions import QuestionGate, is_question
from .profile import InterviewContext, terms
from .transcription import Transcriber
from .cost import DEFAULT_MINUTES, LiveOptions, quote

class Session:
    def __init__(self, settings, emit, capture_factory=Capture, transcriber_factory=Transcriber, answers=None):
        self.settings, self.emit = settings, emit
        self.capture_factory, self.transcriber_factory = capture_factory, transcriber_factory
        self.answers = answers or Answers(settings)
        self.lock = asyncio.Lock()
        self.answer_task = None
        self.start_task = None
        self.fatal_task = None
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
        self.length = 'medium'
        self.interview = None
        self.usage_rate = 0.0
        self.limit_minutes = DEFAULT_MINUTES
        self.deadline = None
        self.reason = 'Local-only mode. Live services require authorisation for each session.'
        self.latest = ''
        self.testing = False
        self.generation = 0
        self.last_final_at = 0
        self.pending_questions = deque(maxlen=6)

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
                'estimate': usage / 3600 * self.usage_rate,
                'active_channels': len(self.opened_at), 'testing': self.testing, 'sources': list(self.captures),
                'model': self.settings.model, 'max_minutes': self.settings.max_minutes,
                'limit_minutes': self.limit_minutes, 'rate_usd': self.usage_rate,
                'configured': {'assemblyai': bool(self.settings.assembly_key), 'groq': bool(self.settings.groq_key)},
                'groq_verified': self.answers.verified, 'length': self.length}

    async def start(self, mic, system, length, interview=None, options=None):
        async with self.lock:
            if self.state != 'idle':
                raise ValueError('Stop the current session or audio test first.')
            interview = interview or InterviewContext()
            pricing = quote(options or LiveOptions(), interview.setup.keyterms, self.settings.max_minutes)
            pricing.require_authorisation()
            if mic == system:
                raise ValueError('Choose different microphone and system audio devices.')
            if not self.settings.assembly_key or not self.settings.groq_key:
                raise ValueError('Add both API keys to .env, then restart using the Desktop launcher.')

            self.length = length
            self.generation += 1
            self.clear()
            self.final_keys.clear()
            self.gate = QuestionGate()
            self.interview = interview
            self.start_task = asyncio.current_task()
            self.limit_minutes = pricing.options.minutes
            self.usage_rate = float(pricing.channel_rate)
            self.started = time.monotonic()
            self.deadline = asyncio.get_running_loop().time() + self.limit_minutes * 60
            self.elapsed = self.channel_seconds = 0
            self.state = 'starting'
            self.reason = 'Checking Groq model and opening audio sources…'
            self.emit(self.snapshot())
            try:
                async with asyncio.timeout_at(self.deadline):
                    await self._open_live(mic, system, interview.setup.keyterms)
                self.state = 'running'
                self.reason = 'Listening'
                for role in self.captures:
                    self.tasks.append(asyncio.create_task(self.pump(role)))
                self.tasks.append(asyncio.create_task(self.watchdog(self.deadline)))
                self.emit(self.snapshot())
            except TimeoutError:
                await self._cleanup('Maximum session duration reached during startup.')
                raise ValueError('The live session reached its duration limit during startup.') from None
            except BaseException:
                await self._cleanup('Live session ended during startup.')
                raise
            finally:
                self.start_task = None

    async def _open_live(self, mic, system, use_keyterms):
        await self.answers.verify()
        for role, device in [('me', mic), ('interviewer', system)]:
            capture = self.capture_factory(device)
            self.captures[role] = capture
            await self.open_capture(capture)
        for role in self.captures:
            if self.deadline_reached():
                raise TimeoutError
            trans = self.transcriber_factory(self.settings.assembly_key,
                lambda event, source=role: self.turn(source, event), self.fatal)
            trans.use_keyterms = use_keyterms
            self.transcribers[role] = trans
            self.opened_at[role] = time.monotonic()
            await trans.connect()
        for capture in self.captures.values():
            capture.overflow = False
            while not capture.queue.empty():
                capture.queue.get_nowait()

    async def pump(self, role):
        try:
            async for chunk in self.captures[role].chunks():
                if self.state == 'running' and not self.deadline_reached():
                    await self.transcribers[role].send(chunk)
                self.level(role)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.fatal('An audio source or transcription connection failed. Start a new authorised session to try again.')

    def level(self, role):
        cap = self.captures[role]
        now = time.monotonic()
        if now - cap.level_at > .15:
            cap.level_at = now
            self.emit({'type': 'level', 'role': role, 'value': min(1, cap.level * 5)})

    def deadline_reached(self):
        return self.deadline is not None and asyncio.get_running_loop().time() >= self.deadline

    async def watchdog(self, deadline):
        await asyncio.sleep(max(0, deadline - asyncio.get_running_loop().time()))
        await self.stop('Maximum session duration reached.')

    def fatal(self, message):
        if self.state in ('running', 'starting') and (not self.fatal_task or self.fatal_task.done()):
            self.emit({'type': 'error', 'message': message})
            self.fatal_task = asyncio.create_task(self.stop(message))

    def turn(self, role, event):
        if self.state != 'running':
            return
        text = event.transcript.strip()[:5000]
        if not text:
            return
        key = f'{self.generation}:{role}:{event.turn_order}'
        final = bool(event.end_of_turn)
        if not final and key in self.final_keys:
            return
        previous = self.history[-1] if self.history else {}
        repeated = next((entry for entry in reversed(self.history) if entry['key'] == key), None) if key in self.final_keys else None
        continuation = repeated['continuation'] if repeated else (
            previous.get('role') == role and time.monotonic() - self.last_final_at < 3
            and (role == 'me' or not (is_question(text) or is_question(previous['text'])))
        )
        entry = {'key': key, 'role': role, 'text': text, 'final': final, 'continuation': continuation}
        self.emit({'type': 'transcript', **entry})
        if not final:
            self.partials[role] = entry
            return
        self.partials.pop(role, None)
        # Formatted final events repeat a turn: update UI, never trigger another answer.
        if key in self.final_keys:
            if repeated:
                repeated['text'] = text
            return
        self.final_keys.append(key)
        self.last_final_at = time.monotonic()
        self.history.append({**entry, 'original_text': text})
        if role == 'interviewer':
            self.latest = text
            if is_question(text) and self.gate.claim(key, text):
                self.request_answer(text)

    def request_answer(self, question=None):
        if self.state != 'running':
            raise ValueError('Start an authorised live session first.')
        question = question or self.latest
        if not question:
            raise ValueError('No completed interviewer speech yet.')
        # Keep a short queue so distinct questions survive a slow generation.
        self.pending_questions.append(question)
        if not self.answer_task or self.answer_task.done():
            self.answer_task = asyncio.create_task(self.answer_loop())

    async def answer_loop(self):
        while self.pending_questions and self.state == 'running' and not self.deadline_reached():
            question = self.pending_questions.popleft()
            recent = list(self.history)[-8:]
            question_terms = terms(question)
            previous = [item for item in self.suggestions
                        if len(question_terms & terms(item['question'])) >= 2
                        and item['question'] != question][-2:]
            context = json.dumps({
                'question': question[:2000],
                'conversation': [{'speaker': x['role'], 'text': x['text'][:250]} for x in recent],
                'relevant_prior_suggestions': [{'question': x['question'][:250], 'text': x['text'][:450]}
                                               for x in previous],
                'professional_context': json.loads((self.interview or InterviewContext()).for_question(question)),
            }, ensure_ascii=False)
            self.emit({'type': 'groq', 'status': 'generating'})
            try:
                async with asyncio.timeout_at(self.deadline):
                    suggestion = await self.answers.generate(context, self.length)
                item = {'type': 'answer', 'question': question, **suggestion}
                self.suggestions.append(item)
                self.emit(item)
                self.emit({'type': 'groq', 'status': 'ready'})
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.fatal(safe_error(exc, 'Groq request failed. Check model access, credits and network before starting a new session.'))
                return

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
        if self.state == 'starting' and self.start_task and self.start_task is not asyncio.current_task():
            self.start_task.cancel()
            await asyncio.gather(self.start_task, return_exceptions=True)
        async with self.lock:
            await self._cleanup(reason)

    async def _cleanup(self, reason):
        if self.state in ('starting', 'running') and self.started:
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
        self.answer_task = None
        self.clear()
        self.final_keys.clear()
        self.gate = QuestionGate()
        self.interview = None
        self.deadline = None
        self.started = None
        # Stop local capture and terminate both provider streams concurrently.
        results = await asyncio.gather(
            *(asyncio.to_thread(cap.close) for cap in self.captures.values()),
            *(trans.close() for trans in self.transcribers.values()),
            self.answers.close(),
            return_exceptions=True,
        )
        now = time.monotonic()
        self.channel_seconds += sum(now - t for t in self.opened_at.values())
        self.opened_at.clear()
        self.transcribers.clear()
        self.captures.clear()
        self.tasks.clear()
        self.testing = False
        self.state = 'idle'
        if any(isinstance(x, BaseException) for x in results):
            self.emit({'type': 'error', 'message': 'Cleanup reported an error. Check provider dashboards and audio devices before restarting.'})
        self.emit(self.snapshot())

    def clear(self):
        if self.answer_task and not self.answer_task.done():
            self.answer_task.cancel()
        self.pending_questions.clear()
        self.history.clear()
        self.last_final_at = 0
        self.suggestions.clear()
        self.partials.clear()
        self.latest = ''
        self.emit({'type': 'clear'})


def safe_error(exc, fallback):
    # Do not display SDK errors, which may include request data or credentials.
    return str(exc) if isinstance(exc, ValueError) else fallback
