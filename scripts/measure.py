"""Measure a real idle server with one connected WebSocket; no API calls."""
import asyncio
import json
from pathlib import Path
import resource
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import httpx
import uvicorn
import websockets
from app.main import create_app

async def main():
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8766, log_level='error', access_log=False))
    # The application origin policy deliberately only permits the production origin.
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done(): raise RuntimeError('Measurement server failed to start')
        await asyncio.sleep(.05)
    async with httpx.AsyncClient() as client:
        await client.get('http://127.0.0.1:8766/')
        cookie = '; '.join(f'{k}={v}' for k,v in client.cookies.items())
        async with websockets.connect('ws://127.0.0.1:8766/ws?client=measurement-1234567890', origin='http://127.0.0.1:8765', additional_headers={'Cookie':cookie}) as ws:
            initial = json.loads(await ws.recv())
            assert initial['state']['active_channels'] == 0
            cpu = time.process_time(); wall = time.monotonic()
            await asyncio.sleep(5)
            duration = time.monotonic()-wall; cpu_used=time.process_time()-cpu
            report={'scenario':'Real Uvicorn server idle with one WebSocket; no audio or cloud calls. Measurement client runs in the same process, so these include its overhead.',
                    'wall_seconds':round(duration,3), 'cpu_seconds':round(cpu_used,5),
                    'cpu_percent_one_core':round(cpu_used/duration*100,3),
                    'peak_resident_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024/1024,2),
                    'platform':sys.platform,
                    'limitations':'Peak RSS includes startup/imports, not current memory. Browser memory excluded. Active capture and paid API load not measured.'}
            print(json.dumps(report,indent=2))
    server.should_exit=True
    await task

if __name__=='__main__': asyncio.run(main())
