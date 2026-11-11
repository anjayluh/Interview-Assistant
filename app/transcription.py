import asyncio
from assemblyai.streaming.v3 import (
    AsyncStreamingClient, StreamingClientOptions, StreamingParameters, StreamingEvents,
)

KEYTERMS = [
    'Python', 'Django', 'Django REST Framework', 'DRF', 'Flask', 'PostgreSQL',
    'Redis', 'React', 'React Native', 'TypeScript', 'JavaScript', 'Vue.js',
    'Kubernetes', 'AWS', 'Docker', 'idempotency', 'microservices', 'GraphQL',
    'REST API', 'CI/CD', 'KYC', 'fintech', 'webhooks',
]


class Transcriber:
    """One SDK connection per physical source; bounded SDK backlog guard.

    assemblyai 1.8.2 has an internal unbounded queue. This pinned adapter is its
    sole audio producer and refuses to enqueue more than eight frames. It reads
    the private queue size deliberately; compatibility is covered by tests.
    """
    def __init__(self, key, on_turn, on_error, client=None):
        self.client = client or AsyncStreamingClient(StreamingClientOptions(
            api_key=key, connect_timeout=8, max_connection_retries=0, terminate_timeout=1))
        self.on_turn = on_turn
        self.on_error = on_error
        self.ready = asyncio.Event()
        self.failed = False
        self.closing = False
        self.use_keyterms = False
        self.client.on(StreamingEvents.Begin, self._begin)
        self.client.on(StreamingEvents.Turn, self._turn)
        self.client.on(StreamingEvents.Error, self._error)
        self.client.on(StreamingEvents.Termination, self._terminated)

    def _begin(self, client, event):
        self.ready.set()

    def _turn(self, client, event):
        if not self.closing:
            self.on_turn(event)

    def _error(self, client, event):
        self.failed = True
        self.ready.set()
        if not self.closing:
            self.on_error('AssemblyAI connection failed. Check credentials, credits and network; restart manually.')

    def _terminated(self, client, event):
        if not self.closing:
            self._error(client, event)

    async def connect(self):
        await self.client.connect(StreamingParameters(
            sample_rate=16000, encoding='pcm_s16le', speech_model='universal-streaming-english',
            format_turns=True, min_turn_silence=500, max_turn_silence=1400,
            inactivity_timeout=60, customer_support_audio_capture=False, keyterms_prompt=KEYTERMS if self.use_keyterms else None))
        await asyncio.wait_for(self.ready.wait(), 12)
        if self.failed:
            raise RuntimeError('AssemblyAI could not start. Check credentials, account credits and connectivity.')
        if not isinstance(getattr(self.client, '_write_queue', None), asyncio.Queue):
            raise RuntimeError('AssemblyAI SDK queue changed; use the pinned requirements.')

    async def send(self, chunk):
        if self.failed or self.closing:
            raise RuntimeError('Transcription connection is closed.')
        for name in ('_read_task', '_write_task'):
            task = getattr(self.client, name, None)
            if task and task.done():
                if not task.cancelled():
                    task.exception()
                raise RuntimeError('Transcription task stopped unexpectedly. Session stopped; restart manually.')
        if self.client._write_queue.qsize() >= 8:
            raise RuntimeError('Transcription network is too slow; session stopped to keep audio buffers bounded.')
        await self.client.stream(chunk)

    async def close(self):
        self.closing = True
        q = getattr(self.client, '_write_queue', None)
        if q:
            while not q.empty():
                q.get_nowait()  # Drop queued audio so Terminate is sent promptly.
        await self.client.disconnect(terminate=True)
        for name in ('_read_task', '_write_task'):
            task = getattr(self.client, name, None)
            if task and task.done() and not task.cancelled():
                task.exception()
