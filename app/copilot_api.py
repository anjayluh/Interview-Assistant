"""Local written capture and practice, plus an opt-in phone companion."""
import asyncio
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .practice import BANK, MIXED, PracticeTurn, practice
from .profile import InterviewSetup
from .profile_api import LOCAL_ORIGINS

MAX_IMAGE = 8 * 1024 * 1024
LOCAL_HOSTS = {'localhost', '127.0.0.1', 'testserver'}


def recognize(image):
    try:
        with TemporaryDirectory(prefix='interview-ocr-') as temp:
            helper = Path(temp) / 'recognize'
            subprocess.run(
                ['/usr/bin/clang', '-fobjc-arc', str(Path(__file__).with_name('ocr.m')),
                 '-framework', 'Foundation', '-framework', 'Vision', '-framework', 'ImageIO', '-framework', 'CoreGraphics',
                 '-o', str(helper)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=True,
            )
            result = subprocess.run(
                [str(helper)], input=image, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=25, check=True,
            )
    except (OSError, subprocess.SubprocessError):
        raise ValueError('Local OCR failed. Check that macOS Command Line Tools are available or paste the text.') from None
    text = result.stdout.decode('utf-8', errors='replace').strip()
    if not text:
        raise ValueError('No text was recognised. Try a sharper screenshot or paste the question.')
    return text[:6000]


class WrittenQuestion(BaseModel):
    question: str = Field(min_length=1, max_length=6000)


def copilot_routes(store, token, control, phone):
    router = APIRouter()
    setup = InterviewSetup()
    ocr_lock = asyncio.Lock()

    def local(request):
        return (not getattr(request.state, 'phone_listener', False) and request.url.hostname in LOCAL_HOSTS and request.cookies.get('ia_access') == token
                and (request.method == 'GET' or request.headers.get('origin') in LOCAL_ORIGINS))

    def authorize(request):
        if local(request):
            return False
        phone.authorize(request)
        return True

    def require_local(request):
        if not local(request):
            raise HTTPException(403, 'Use the Mac to manage pairing.')

    @router.get('/api/copilot/pair')
    async def pair_status(request: Request):
        require_local(request)
        return phone.status()

    @router.get('/api/copilot/pair/networks')
    async def networks(request: Request):
        require_local(request)
        from .phone import interfaces
        try:
            return await asyncio.to_thread(interfaces)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.get('/api/copilot/pair/qr')
    async def pair_qr(request: Request):
        require_local(request)
        return Response(phone.qr(), media_type='image/svg+xml')

    @router.post('/api/copilot/pair')
    async def pair_phone(request: Request, interview: InterviewSetup, address: str | None = None):
        nonlocal setup
        require_local(request)
        async with phone.lock:
            try:
                await phone.start(address)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            setup = interview
            return phone.create()

    @router.post('/api/copilot/pair/{identifier}/{decision}')
    async def decide(request: Request, identifier: str, decision: str):
        require_local(request)
        if decision not in {'approve', 'reject'}:
            raise HTTPException(400, 'Choose approve or reject.')
        phone.decision(identifier, decision == 'approve')
        return phone.status()

    @router.delete('/api/copilot/pair')
    async def unpair_phone(request: Request):
        nonlocal setup
        require_local(request)
        async with phone.lock:
            await phone.close()
            setup = InterviewSetup()
        return {'status': 'revoked'}

    @router.post('/api/companion/claim')
    async def claim(request: Request):
        if not getattr(request.state, 'phone_listener', False):
            raise HTTPException(403, 'Open the local-network companion URL.')
        # Small bounded fields; never accept arbitrary device metadata or tokens.
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                raise HTTPException(413, 'Pairing request too large.')
        import json
        try:
            data = json.loads(raw)
            value, name = data['code'], data.get('name', 'iPhone Safari')
            if not isinstance(value, str) or not isinstance(name, str) or len(value) > 100 or len(name) > 80:
                raise ValueError()
            value.encode('ascii')
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, 'Enter the pairing code shown on the Mac.') from None
        return phone.claim(value, name, request.client.host)

    @router.get('/api/companion/approval')
    async def approval(request: Request):
        if not getattr(request.state, 'phone_listener', False):
            raise HTTPException(403, 'Open the local-network companion URL.')
        return phone.approval(request.headers.get('authorization', '').removeprefix('Bearer '))

    @router.get('/api/copilot/scenarios')
    async def scenarios(request: Request):
        authorize(request)
        return [MIXED, *BANK]

    @router.post('/api/copilot/practice')
    async def practice_turn(request: Request, turn: PracticeTurn):
        is_phone = authorize(request)
        try:
            context = control.session.interview or await asyncio.to_thread(
                store.interview, setup if is_phone else turn.interview)
            return await asyncio.to_thread(practice, turn, context)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.post('/api/copilot/ocr')
    async def ocr(request: Request):
        authorize(request)
        if ocr_lock.locked():
            raise HTTPException(409, 'An image is already being processed.')
        async with ocr_lock:
            image = bytearray()
            async for chunk in request.stream():
                if len(image) + len(chunk) > MAX_IMAGE:
                    raise HTTPException(413, 'Choose an image up to 8 MiB.')
                image.extend(chunk)
            if not image:
                raise HTTPException(400, 'Choose an image.')
            try:
                return {'text': await asyncio.to_thread(recognize, bytes(image))}
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from None

    @router.get('/api/companion/state')
    async def phone_state(request: Request):
        authorize(request)
        session = control.session
        return {'state': session.state, 'latest': session.latest, 'answers': list(session.suggestions)}

    @router.post('/api/companion/answer')
    async def phone_answer(request: Request, data: WrittenQuestion):
        authorize(request)
        session = control.session
        if session.state != 'running' or session.deadline_reached():
            raise HTTPException(409, 'Authorise a live session on the laptop first.')
        if session.answer_task and not session.answer_task.done():
            raise HTTPException(409, 'The copilot is already drafting a response.')
        if not data.question.strip():
            raise HTTPException(400, 'Enter a written question.')
        session.request_answer(data.question.strip())
        return {'accepted': True}

    return router
