"""Authenticated Mac-only review browser and export; no cloud calls."""
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from .profile_api import LOCAL_ORIGINS
from .copilot_api import LOCAL_HOSTS
from .review import summary, valid_id


def review_routes(control, token):
    router = APIRouter()
    def local(request):
        if (getattr(request.state,'phone_listener',False) or request.cookies.get('ia_access') != token
            or request.url.hostname not in LOCAL_HOSTS
            or (request.method != 'GET' and request.headers.get('origin') not in LOCAL_ORIGINS)):
            raise HTTPException(403,'Review files are available only on the Mac.')
    def location(identifier):
        session = control.session
        if not valid_id(identifier) or not session.review_path: raise HTTPException(404,'Review not found.')
        return session.review_path.parent/'reviews'/(identifier+'.json')
    def read(path):
        if not path.is_file(): raise HTTPException(404,'Review not found.')
        if path.stat().st_size > 50_000_000: raise HTTPException(413,'Review too large for the browser. Open its local file.')
        try: return json.loads(path.read_text())
        except (OSError,ValueError): raise HTTPException(400,'Review file could not be read.') from None
    @router.get('/api/reviews')
    async def index(request:Request):
        local(request)
        await control.session.save_review()
        root = control.session.review_path
        if not root: return []
        results=[]
        for path in sorted((root.parent/'reviews').glob('*.json'),key=lambda x:x.stat().st_mtime,reverse=True):
            if not valid_id(path.stem): continue
            record=read(path)
            results.append({'id':path.stem,'created_at':record.get('created_at'), 'metadata':record.get('metadata',{}),
                            'turns':len(record.get('history',[])), 'answers':len(record.get('answers',[]))})
        return results
    @router.get('/api/reviews/{identifier}')
    async def get(request:Request,identifier:str):
        local(request)
        if identifier == control.session.review.data['id']: await control.session.save_review()
        record=read(location(identifier))
        return {**record,'analysis':summary(record)}
    @router.delete('/api/reviews/{identifier}')
    async def delete(request:Request,identifier:str):
        local(request)
        path=location(identifier)
        if identifier == control.session.review.data['id']:
            if control.session.state != 'idle': raise HTTPException(409,'Stop the session before deleting its review.')
            control.session.clear()
        path.unlink(missing_ok=True)
        for role in ('me','interviewer'):
            path.with_name(identifier+'-'+role+'.wav').unlink(missing_ok=True)
        return {'deleted':True}
    @router.get('/api/reviews/{identifier}/audio/{role}')
    async def audio(request:Request,identifier:str,role:str):
        local(request)
        if role not in ('me','interviewer'): raise HTTPException(404,'Audio not found.')
        if control.session.state != 'idle' and identifier == control.session.review.data['id']:
            raise HTTPException(409,'Stop the session before downloading its audio.')
        path=location(identifier).with_name(identifier+'-'+role+'.wav')
        if not path.is_file(): raise HTTPException(404,'No local audio was recorded.')
        return FileResponse(path,media_type='audio/wav',filename=path.name)
    return router
