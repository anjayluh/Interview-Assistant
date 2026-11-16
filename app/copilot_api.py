"""Local written capture and practice, plus an opt-in phone companion."""
import asyncio
import secrets
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .practice import BANK, MIXED, PracticeTurn, practice
from .profile import InterviewSetup
from .profile_api import LOCAL_ORIGINS

MAX_IMAGE = 8 * 1024 * 1024
LOCAL_HOSTS = {'localhost', '127.0.0.1', 'testserver'}


def phone_hostname(origin):
    if not origin:
        return None
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.hostname in LOCAL_HOSTS
            or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('IA_PHONE_ORIGIN must be an HTTPS origin with no path.')
    return parsed.hostname


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


def copilot_routes(store, token, control, phone_origin=''):
    router = APIRouter()
    phone_host = phone_hostname(phone_origin)
    pair = None
    setup = InterviewSetup()
    ocr_lock = asyncio.Lock()

    def local(request):
        return (request.url.hostname in LOCAL_HOSTS and request.cookies.get('ia_access') == token
                and (request.method == 'GET' or request.headers.get('origin') in LOCAL_ORIGINS))

    def authorize(request):
        if local(request):
            return False
        if (pair and time.monotonic() < pair[1] and request.url.hostname == phone_host
                and (request.method == 'GET' or request.headers.get('origin') == phone_origin)
                and secrets.compare_digest(request.headers.get('authorization', ''), 'Bearer ' + pair[0])):
            return True
        raise HTTPException(403, 'Open the local app or pair the phone from the laptop.')

    @router.post('/api/copilot/pair')
    async def pair_phone(request: Request, interview: InterviewSetup):
        nonlocal pair, setup
        if not local(request) or not phone_origin:
            raise HTTPException(403, 'Phone access requires an optional HTTPS forwarding setup.')
        setup = interview
        pair = (secrets.token_urlsafe(32), time.monotonic() + 7200)
        return {'url': phone_origin + '/companion#' + pair[0]}

    @router.delete('/api/copilot/pair')
    async def unpair_phone(request: Request):
        nonlocal pair, setup
        if not local(request):
            raise HTTPException(403, 'Unpair from the laptop.')
        pair, setup = None, InterviewSetup()
        return {'status': 'unpaired'}

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
