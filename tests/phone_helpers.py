import ipaddress
from starlette.testclient import TestClient

PHONE_ORIGIN = 'http://192.168.50.10:9123'
PHONE_HEADERS = {'Origin': PHONE_ORIGIN}


def configure_phone(app, monkeypatch):
    manager = app.state.phone

    async def start(address=None):
        manager.origin = PHONE_ORIGIN
        manager.network = ipaddress.ip_network('192.168.50.0/24')
        return []

    manager.origin = PHONE_ORIGIN
    manager.network = ipaddress.ip_network('192.168.50.0/24')
    monkeypatch.setattr(manager, 'start', start)
    async def gateway(scope, receive, send):
        if scope['type'] == 'lifespan':
            await receive(); await send({'type': 'lifespan.startup.complete'})
            await receive(); await send({'type': 'lifespan.shutdown.complete'})
        else:
            await manager.gateway(scope, receive, send)

    return TestClient(gateway, base_url=PHONE_ORIGIN, client=('192.168.50.20', 54321))


def pair(laptop, phone, company='Sample', approve=True):
    invite = laptop.post('/api/copilot/pair', headers={'Origin': 'http://testserver'}, json={'company': company}).json()
    claim = phone.post('/api/companion/claim', headers=PHONE_HEADERS,
                       json={'code': invite['url'].split('#')[1], 'name': 'Test iPhone'}).json()
    assert phone.get('/api/companion/state', headers={'Authorization': 'Bearer ' + claim['secret']}).status_code == 403
    decision = 'approve' if approve else 'reject'
    response = laptop.post(f'/api/copilot/pair/{claim["id"]}/{decision}', headers={'Origin': 'http://testserver'})
    assert response.status_code == 200
    result = phone.get('/api/companion/approval', headers={'Authorization': 'Bearer ' + claim['secret']}).json()
    return {**PHONE_HEADERS, 'Authorization': 'Bearer ' + result.get('token', '')}
