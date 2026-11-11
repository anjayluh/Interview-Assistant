"""Cookie-protected profile operations on the existing local server."""
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .cv import MAX_UPLOAD, extract_cv
from .profile import CVFields, ProfessionalProfile, compare_facts

LOCAL_ORIGINS = ('http://127.0.0.1:8765', 'http://localhost:8765', 'http://testserver')


class DefaultCV(BaseModel):
    id: str | None = Field(max_length=32)


def profile_routes(store, token):
    def authorize(request: Request):
        if request.cookies.get('ia_access') != token or (
            request.method != 'GET' and request.headers.get('origin') not in LOCAL_ORIGINS
        ):
            raise HTTPException(403, 'Open Interview Assistant in its local browser tab.')

    router = APIRouter(prefix='/api/profile', dependencies=[Depends(authorize)])

    def checked(operation, *args):
        try:
            return operation(*args)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @router.get('')
    def summary():
        return store.summary()

    @router.put('')
    def save_profile(profile: ProfessionalProfile):
        return store.save_profile(profile)

    @router.post('/preview')
    async def preview(request: Request, filename: str, cv_id: str | None = None):
        from starlette.concurrency import run_in_threadpool

        content = bytearray()
        async for chunk in request.stream():
            if len(content) + len(chunk) > MAX_UPLOAD:
                raise HTTPException(413, 'Choose a CV up to 5 MiB.')
            content.extend(chunk)
        path = PurePath(filename)
        facts = await run_in_threadpool(checked, extract_cv, bytes(content), path.suffix.lower()[1:])
        return await run_in_threadpool(review, CVFields(name=(path.stem[:150] or 'CV'), facts=facts), cv_id)

    @router.post('/compare')
    def review(fields: CVFields, cv_id: str | None = None):
        previous = checked(store.get_cv, cv_id)['facts'] if cv_id else ''
        profile = store.summary()['profile']
        return {**fields.model_dump(), 'comparison': compare_facts(fields.facts, profile, previous)}

    @router.put('/default')
    def default_cv(selection: DefaultCV):
        return checked(store.set_default, selection.id)

    @router.post('/cvs')
    def create_cv(fields: CVFields):
        return checked(store.save_cv, fields)

    @router.get('/cvs/{cv_id}')
    def get_cv(cv_id: str):
        return checked(store.get_cv, cv_id)

    @router.put('/cvs/{cv_id}')
    def update_cv(cv_id: str, fields: CVFields):
        return checked(store.save_cv, fields, cv_id)

    @router.delete('/cvs/{cv_id}')
    def delete_cv(cv_id: str):
        return checked(store.delete_cv, cv_id)

    return router
