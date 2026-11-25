"""Opt-in local PCM WAV recording. A full recording queue never blocks live audio."""
import asyncio
import os
import shutil
import wave
from pathlib import Path


class ReviewAudio:
    def __init__(self, directory, identifier):
        self.root = Path(directory)
        self.identifier = identifier
        self.queue = asyncio.Queue(maxsize=200)
        self.stats = {'requested':True,'recorded':False,'dropped_chunks':0,'error':False,'format':'mono PCM16 16000Hz','first_chunks_ms':{}}
        self.task = None

    def start(self):
        try:
            self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
            if shutil.disk_usage(self.root).free < 600_000_000: raise OSError('Insufficient local recording space')
        except (OSError, wave.Error):
            self.stats['error'] = True
            return
        self.task = asyncio.create_task(self.write())

    def offer(self, role, chunk, at_ms):
        if not self.task or self.task.done(): return
        self.stats['first_chunks_ms'].setdefault(role,at_ms)
        try: self.queue.put_nowait((role,bytes(chunk)))
        except asyncio.QueueFull: self.stats['dropped_chunks'] += 1

    async def write(self):
        handles, files = {}, {}
        try:
            while True:
                item = await self.queue.get()
                if item is None: break
                role, chunk = item
                if role not in handles:
                    path=self.root/(self.identifier+'-'+role+'.wav')
                    descriptor=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                    files[role]=os.fdopen(descriptor,'wb')
                    handle=handles[role]=wave.open(files[role],'wb')
                    handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000)
                await asyncio.to_thread(handles[role].writeframesraw,chunk)
                self.stats['recorded']=True
        except (OSError, wave.Error):
            self.stats['error']=True
        finally:
            for handle in handles.values():
                try: handle.close()
                except (OSError, wave.Error): self.stats['error']=True
            for handle in files.values(): handle.close()

    async def close(self):
        if not self.task: return
        if not self.task.done():
            sentinel = asyncio.create_task(self.queue.put(None))
            done, _ = await asyncio.wait([sentinel,self.task],return_when=asyncio.FIRST_COMPLETED)
            if self.task in done and not sentinel.done():
                sentinel.cancel()
                await asyncio.gather(sentinel,return_exceptions=True)
        await self.task
