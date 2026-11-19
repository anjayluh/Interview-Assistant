import asyncio
import json
import time
import re
from collections import deque
from .audio import Capture, capture_error
from .answers import Answers
from .questions import QuestionGate, is_question, interviewer_intent, likely_bleed
from .profile import InterviewContext, terms
from .transcription import Transcriber
from .cost import DEFAULT_MINUTES, LiveOptions, quote
from .provider_errors import ProviderFailure, groq_failure

class Session:
    def __init__(self, settings, emit, capture_factory=Capture, transcriber_factory=Transcriber, answers=None):
        self.settings, self._emit = settings, emit
        self.subscribers = set()
        self.metrics = deque(maxlen=80)
        self.metric_once = deque(maxlen=1024)
        self.first_audio_roles = set()
        self.question_task = None
        self.challenge = []
        self.preface = deque(maxlen=120)
        self.preface_overflow = False
        self.challenge_intent = None
        self.challenge_blocked = False
        self.revision = 0
        self.draft = None
        self.answer_enabled = True
        self.answer_status = {'phase': 'idle'}
        self.diagnostic_sink = None
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
        self.limit_minutes = DEFAULT_MINUTES
        self.deadline = None
        self.reason = 'Local-only mode. Live services require authorisation for each session.'
        self.latest = ''
        self.audio_diagnostics = deque(maxlen=24)
        self.testing = False
        self.generation = 0
        self.last_final_at = 0
        self.pending_questions = deque(maxlen=6)

    def emit(self, event):
        event = {**event, 'sent_at': time.time()}
        self._emit(event)
        if event['type'] == 'level':
            return
        for queue in tuple(self.subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # Recover slow phones with one current snapshot, never block capture.
                while not queue.empty(): queue.get_nowait()
                queue.put_nowait({'type': 'companion_snapshot', **self.companion_state()})

    def measure(self, stage, role='', once=False, at=None):
        key = (stage, role, once if isinstance(once, str) else '')
        if once and key in self.metric_once: return
        if once: self.metric_once.append(key)
        now = time.monotonic() if at is None else at
        item = {'stage': stage, 'role': role, 'revision': self.revision,
                'at_ms': round((now - (self.started or now)) * 1000, 1)}
        self.metrics.append(item)
        self.emit({'type': 'latency', **item})

    def companion_state(self):
        return {'state': self.state, 'answer_enabled': self.answer_enabled, 'latest': self.latest, 'answers': [item for item in self.suggestions if item.get('revision') == self.revision],
                'history': list(self.history), 'partials': list(self.partials.values()),
                'draft': self.draft, 'answer_status': self.answer_status, 'metrics': list(self.metrics), 'sent_at': time.time()}

    def diagnose_audio(self, role, stage, message):
        item = {'role': role, 'stage': stage, 'message': message}
        self.audio_diagnostics.append(item)
        self.emit({'type': 'audio_diagnostic', **item})

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
                'audio_diagnostics': list(self.audio_diagnostics),
                'maximum_payment_ugx': 0,
                'active_channels': len(self.opened_at), 'testing': self.testing, 'sources': list(self.captures),
                'model': self.settings.model, 'max_minutes': self.settings.max_minutes,
                'limit_minutes': self.limit_minutes,
                'configured': {'assemblyai': bool(self.settings.assembly_key), 'groq': bool(self.settings.groq_key)},
                'groq_verified': self.answers.verified, 'answer_enabled': self.answer_enabled,
                'answer_status': self.answer_status, 'length': self.length}

    async def start(self, mic, system, length, interview=None, options=None):
        async with self.lock:
            if self.state != 'idle':
                raise ValueError('Stop the current session or audio test first.')
            interview = interview or InterviewContext()
            pricing = quote(options or LiveOptions(), self.settings.max_minutes)
            pricing.require_authorisation()
            if mic == system:
                raise ValueError('Choose different microphone and system audio devices.')
            if not self.settings.assembly_key or not self.settings.groq_key:
                raise ValueError('Add both API keys to .env, then restart using the Desktop launcher.')

            self.answer_enabled = True
            self.answer_status = {'phase': 'idle'}
            self.length = length
            self.generation += 1
            self.clear()
            self.final_keys.clear()
            self.gate = QuestionGate()
            self.interview = interview
            self.start_task = asyncio.current_task()
            self.limit_minutes = pricing.options.minutes
            self.answers.free_only = True
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
            except BaseException as exc:
                if isinstance(exc, ProviderFailure):
                    await self.record_answer_failure(exc)
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
                if role not in self.first_audio_roles:
                    self.first_audio_roles.add(role)
                    self.measure('audio_capture', role, once=True, at=getattr(self.captures[role], 'first_callback_at', None))
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
            message = message.rstrip('.') + '. Live transcription and cloud answers are stopped; local practice, CV tools and phone pairing remain available.'
            self.emit({'type': 'error', 'message': message})
            self.fatal_task = asyncio.create_task(self.stop(message))

    def turn(self, role, event):
        if self.state != 'running':
            return
        if role not in ('me', 'interviewer'): return
        raw_text = event.transcript.strip()
        text = raw_text[:18000 if role == 'interviewer' else 5000]
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
        entry = {'key': key, 'role': role, 'source': 'system_audio' if role == 'interviewer' else 'microphone',
                 'text': text, 'final': final, 'continuation': continuation, 'received_at': time.monotonic()}
        self.measure('first_transcription_fragment', role, once=key)
        others = [*self.history, *self.partials.values()]
        if role == 'me':
            match = next((other for other in reversed(others) if other['role'] == 'interviewer'
                          and likely_bleed(text, other['text'], abs(entry['received_at'] - other.get('received_at', 0)))), None)
            if match:
                entry['duplicate_of'] = match['key']
                self.measure('likely_speaker_bleed', 'me', once=key)
        else:
            for other in others:
                if other['role'] == 'me' and likely_bleed(text, other['text'], abs(entry['received_at'] - other.get('received_at', 0))):
                    other['duplicate_of'] = key
                    self.measure('likely_speaker_bleed', 'me', once=other['key'])
                    self.emit({'type': 'transcript', **other})
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
            if len(raw_text) > 18000:
                self.challenge = [{'key': key, 'text': raw_text[:18001]}]
                self.schedule_question()
                return
            intent = interviewer_intent(text)
            if intent in ('question', 'coding') and self.gate.claim(key, text):
                followup = bool(self.challenge and re.match(r'(?:and |also |what if|how about|but |instead)', text, re.I))
                if not followup:
                    self.challenge_blocked = False
                    self.challenge = list(self.preface)
                    self.preface.clear()
                    if self.preface_overflow:
                        self.challenge_blocked = True
                        self.emit({'type': 'error', 'message': 'Challenge preamble exceeds the live context limit. Submit a shorter complete written problem.'})
                    self.preface_overflow = False
                    self.challenge_intent = interviewer_intent(' '.join(part['text'] for part in self.challenge) + ' ' + text)
                if not self.challenge_blocked:
                    self.challenge.append({'key': key, 'text': text})
                    self.schedule_question()
            elif intent == 'instruction' and not self.challenge:
                if len(self.preface) >= 120 or sum(len(part['text']) + 1 for part in self.preface) + len(text) > 18000:
                    self.preface_overflow = True
                else:
                    self.preface.append({'key': key, 'text': text})
            elif intent == 'instruction' and self.challenge and not self.challenge_blocked:
                self.challenge.append({'key': key, 'text': text})
                self.schedule_question()
            elif (intent == 'statement' and self.challenge_intent == 'coding' and self.challenge and not self.challenge_blocked
                  and re.match(r'(?:example|input|output|the (?:input|output)|for |it |there |n |[\d\[])', text, re.I)):
                # Challenge examples can be declarative; preserve each turn until a new question.
                self.challenge.append({'key': key, 'text': text})
                self.schedule_question()

    def schedule_question(self):
        self.latest = '\n'.join(part['text'] for part in self.challenge)
        if not self.answer_enabled: return
        self.revision += 1
        self.measure('question_finalised', 'interviewer')
        if self.question_task and not self.question_task.done(): self.question_task.cancel()
        self.pending_questions.clear()
        previous = self.answer_task
        if previous and not previous.done(): previous.cancel()
        self.draft = None
        self.emit({'type': 'answer_superseded', 'revision': self.revision})
        if len(self.latest) > 18000 or len(self.challenge) > 120:
            self.challenge_blocked = True
            self.emit({'type': 'error', 'message': 'Challenge exceeds the live context limit. Review and submit a shorter complete written problem.'})
            return
        async def settle():
            await asyncio.sleep(.25)
            if previous: await asyncio.gather(previous, return_exceptions=True)
            if self.state == 'running': self.request_answer(self.latest)
        self.question_task = asyncio.create_task(settle())

    def request_answer(self, question=None):
        if self.state != 'running':
            raise ValueError('Start an authorised live session first.')
        if not self.answer_enabled:
            raise ValueError('Groq suggestions are paused after a failure. Stop, correct the reported cause, then authorise a new session; no automatic retry is made.')
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
            revision = self.revision
            recent = [entry for entry in self.history if not entry.get('duplicate_of')][-8:]
            question_terms = terms(question)
            previous = [item for item in self.suggestions
                        if len(question_terms & terms(item['question'])) >= 2
                        and item['question'] != question][-2:]
            context = json.dumps({
                'question': question[:18000],
                'interviewer_intent': self.challenge_intent or interviewer_intent(question),
                'conversation': [{'speaker': x['role'], 'source': x.get('source'), 'text': x['text'][:250]} for x in recent],
                'relevant_prior_suggestions': [{'question': x['question'][:250], 'text': x['text'][:450]}
                                               for x in previous],
                'professional_context': json.loads((self.interview or InterviewContext()).for_question(question)),
            }, ensure_ascii=False)
            self.measure('answer_generation_start')
            self.answer_status = {'phase': 'generating'}
            self.emit({'type': 'groq', 'status': 'generating'})
            try:
                async with asyncio.timeout_at(self.deadline):
                    def on_token():
                        if revision == self.revision: self.measure('first_generated_token')
                    def on_text(text):
                        if revision != self.revision or self.state != 'running': return
                        if not self.draft: self.measure('first_spoken_answer_text')
                        self.draft = {'type': 'answer_delta', 'text': text, 'revision': revision, 'status': 'Draft · still generating'}
                        self.emit(self.draft)
                    if isinstance(self.answers, Answers):
                        suggestion = await self.answers.generate(context, self.length, on_text=on_text, on_token=on_token)
                    else:
                        suggestion = await self.answers.generate(context, self.length)
                if revision != self.revision: continue
                self.draft = None
                self.measure('answer_complete')
                item = {'type': 'answer', 'question': question, 'revision': revision, **suggestion}
                self.suggestions.append(item)
                self.emit(item)
                self.answer_status = {'phase': 'ready'}
                self.emit({'type': 'groq', 'status': 'ready'})
                self.emit(self.snapshot())
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self.deadline_reached():
                    self.fatal('Maximum session duration reached.')
                    return
                failure = groq_failure(exc, getattr(self.answers, 'last_request', {}))
                await self.record_answer_failure(failure)
                # Preserve strict access/billing/rate-limit shutdown. Other generation
                # failures can pause Groq while independent transcription continues.
                if failure.diagnostic['http_status'] in (401, 402, 403, 429):
                    self.fatal(str(failure))
                else:
                    self.reason = str(failure) + ' Suggested answers are paused; live transcription continues until STOP ALL or the session deadline.'
                    self.emit(self.snapshot())
                    await self.answers.close()
                return

    async def record_answer_failure(self, failure):
        self.answer_enabled = False
        self.pending_questions.clear()
        self.draft = None
        self.answer_status = {'phase': 'failed', 'message': str(failure), 'diagnostic': failure.diagnostic}
        self.emit({'type': 'groq', 'status': 'error', 'message': str(failure), 'diagnostic': failure.diagnostic})
        self.emit({'type': 'error', 'provider': 'groq', 'message': str(failure), 'diagnostic': failure.diagnostic})
        if self.diagnostic_sink:
            try:
                await asyncio.to_thread(self.diagnostic_sink, failure.diagnostic)
            except OSError:
                self.emit({'type': 'error', 'message': 'The safe provider diagnostic could not be saved; it remains visible for this session.'})

    async def test(self, role, device):
        async with self.lock:
            if self.state != 'idle':
                raise ValueError('Stop the current session or audio test first.')
            self.state, self.testing = 'testing', True
            self.reason = f'Testing {role} locally for 10 seconds. No cloud API calls.'
            self.diagnose_audio(role, 'opening', f'Backend received test for device ID {device}; opening input.')
            self.emit(self.snapshot())
            try:
                cap = self.capture_factory(device)
                self.captures[role] = cap
                await self.open_capture(cap)
                name = getattr(cap, 'name', '') or f'device {device}'
                self.diagnose_audio(role, 'opened',
                    f'{name}; {getattr(cap, "rate", "?")} Hz, {getattr(cap, "channels", "?")} input channel(s). Waiting for audio frames.')
                self.tasks.append(asyncio.create_task(self.test_pump(role)))
                self.emit(self.snapshot())
            except asyncio.CancelledError:
                self.diagnose_audio(role, 'cancelled', 'Audio test cancelled while opening the input.')
                await self._cleanup('Audio test cancelled.')
                raise
            except Exception as exc:
                detail = capture_error(exc)
                self.diagnose_audio(role, 'error', detail)
                await self._cleanup('Audio test failed: ' + detail)
                raise ValueError('Audio test failed: ' + detail) from None

    async def test_pump(self, role):
        cap = self.captures[role]
        received = False
        try:
            async with asyncio.timeout(10):
                async for chunk in cap.chunks():
                    if not received:
                        received = True
                        self.diagnose_audio(role, 'receiving', 'Audio frames received. Speak or play sound to check signal level.')
                    self.level(role)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            self.diagnose_audio(role, 'cancelled', 'Audio test stopped before completion.')
            raise
        except Exception as exc:
            self.diagnose_audio(role, 'error', capture_error(exc))
            self.emit({'type': 'error', 'message': 'Audio test failed: ' + capture_error(exc)})
            await self.stop('Audio test failed. See Audio diagnostics.')
            return
        peak = getattr(cap, 'peak_level', cap.level)
        signal = 'Signal detected.' if peak > .001 else 'Frames arrived but the signal was silent or very quiet.'
        if not received:
            signal = 'No audio frames arrived.'
        self.diagnose_audio(role, 'finished',
            f'{signal} Callbacks: {getattr(cap, "callback_count", "?")}; '
            f'frames: {getattr(cap, "frame_count", "?")}; peak RMS: {peak:.5f}.')
        await self.stop('Audio test finished. See Audio diagnostics.')

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
        if self.question_task and self.question_task is not current and not self.question_task.done():
            pending.append(self.question_task)
        if self.answer_task and self.answer_task is not current and not self.answer_task.done():
            pending.append(self.answer_task)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self.answer_task = None
        if self.answer_status.get('phase') != 'failed': self.answer_status = {'phase': 'idle'}
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
        if self.question_task and not self.question_task.done(): self.question_task.cancel()
        self.question_task = None
        self.challenge = []
        self.preface.clear()
        self.preface_overflow = False
        self.challenge_intent = None
        self.draft = None
        self.revision += 1
        self.metrics.clear()
        self.metric_once.clear()
        self.first_audio_roles.clear()
        self.challenge_blocked = False
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
