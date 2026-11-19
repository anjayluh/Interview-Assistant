"""Callbacks only copy into a bounded queue. Conversion runs on the consumer."""
import asyncio
import queue
import time
import numpy as np
import sounddevice as sd
import soxr

RATE = 16000
CHUNK_BYTES = 1600  # 50 ms mono PCM16 at 16 kHz

def devices():
    return [{'id': i, 'name': d['name'], 'channels': d['max_input_channels'],
             'sample_rate': d['default_samplerate']} for i, d in enumerate(sd.query_devices())
            if d['max_input_channels'] > 0]

class Capture:
    def __init__(self, device):
        self.device = device
        self.queue = queue.Queue(maxsize=8)
        self.stream = None
        self.closed = True
        self.overflow = False
        self.last_audio = time.monotonic()
        self.level = 0.0
        self.level_at = 0.0
        self.name = ''
        self.callback_count = 0
        self.frame_count = 0
        self.peak_level = 0.0
        self.pending = bytearray()

    def callback(self, data, frames, timing, status):
        if self.closed:
            return
        self.callback_count += 1
        self.frame_count += frames
        if status:
            self.overflow = True
        try:
            self.queue.put_nowait(bytes(data))
        except queue.Full:
            self.overflow = True

    def open(self):
        info = sd.query_devices(self.device, 'input')
        if info['max_input_channels'] < 1:
            raise ValueError('Selected device has no input channels.')
        self.name = info['name']
        self.rate = int(info['default_samplerate'])
        self.channels = min(2, int(info['max_input_channels']))
        sd.check_input_settings(device=self.device, channels=self.channels, dtype='int16', samplerate=self.rate)
        self.resampler = soxr.ResampleStream(self.rate, RATE, 1, dtype='float32', quality='MQ') if self.rate != RATE else None
        self.closed = False
        try:
            self.stream = sd.RawInputStream(device=self.device, samplerate=self.rate, channels=self.channels,
                                           dtype='int16', blocksize=int(self.rate * .05), callback=self.callback)
            self.stream.start()
        except Exception:
            self.close()
            raise

    def convert(self, raw):
        x = np.frombuffer(raw, dtype='<i2').reshape(-1, self.channels).astype(np.float32)
        x = x.mean(axis=1) / 32768.0
        self.level = float(np.sqrt(np.mean(x * x))) if len(x) else 0
        self.peak_level = max(self.peak_level, self.level)
        if self.resampler:
            x = self.resampler.resample_chunk(x)
        return (np.clip(x, -1, 32767 / 32768) * 32768).astype('<i2').tobytes()

    async def chunks(self):
        # Queue.get runs off the audio callback and event loop. Timeout bounds shutdown.
        while not self.closed:
            try:
                raw = await asyncio.to_thread(self.queue.get, True, .25)
            except queue.Empty:
                if time.monotonic() - self.last_audio > 3:
                    raise RuntimeError('Audio device stopped supplying frames. Check its connection and permissions.')
                continue
            if self.closed:
                break
            self.last_audio = time.monotonic()
            if self.overflow:
                raise RuntimeError('Audio buffer overflow. Session stopped rather than accumulating delayed audio.')
            self.pending.extend(self.convert(raw))
            while len(self.pending) >= CHUNK_BYTES:
                chunk = bytes(self.pending[:CHUNK_BYTES])
                del self.pending[:CHUNK_BYTES]
                yield chunk

    def close(self):
        self.closed = True
        try:
            if self.stream:
                try:
                    self.stream.abort()
                finally:
                    self.stream.close()
        finally:
            self.stream = None
            self.pending.clear()
            self.resampler = None
            while not self.queue.empty():
                try:
                    self.queue.get_nowait()
                except queue.Empty:
                    break


def capture_error(exc):
    """Expose local driver errors without request data or provider credentials."""
    if isinstance(exc, sd.PortAudioError):
        codes = [str(arg) for arg in exc.args if isinstance(arg, int)]
        # PortAudio's first argument describes the failed local device operation.
        message = str(exc.args[0])[:220] if exc.args else 'PortAudio failed.'
        return message + (' (code ' + ', '.join(codes) + ')' if codes else '')
    if isinstance(exc, (ValueError, RuntimeError)):
        return str(exc)[:220]
    return type(exc).__name__ + ' while accessing the audio device.'
