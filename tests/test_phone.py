"""Mocked LAN/provider tests; these do not claim physical iPhone verification."""
import asyncio
import ipaddress
from unittest.mock import Mock

import pytest
from starlette.testclient import TestClient
from fastapi import HTTPException

from app.main import create_app
from app.phone import Phone, interfaces
from tests.phone_helpers import configure_phone, pair, PHONE_HEADERS, PHONE_ORIGIN
from tests.live_helpers import ORIGIN


def test_qr_uses_local_url_and_requires_mac_approval(monkeypatch):
    app = create_app()
    with TestClient(app) as laptop, configure_phone(app, monkeypatch) as phone:
        laptop.get('/')
        data = laptop.post('/api/copilot/pair', headers=ORIGIN, json={}).json()
        assert data['url'].startswith(PHONE_ORIGIN + '/companion#')
        assert data['expires_in'] in (119, 120) and len(data['code']) == 8
        import segno
        make = Mock(wraps=segno.make)
        monkeypatch.setattr('app.phone.segno.make', make)
        qr = laptop.get('/api/copilot/pair/qr')
        assert qr.status_code == 200 and '<svg' in qr.text
        make.assert_called_once_with(data['url'], micro=False, error='m')
        assert phone.get('/api/copilot/pair/qr').status_code == 403
        claim = phone.post('/api/companion/claim', headers=PHONE_HEADERS, json={'code': data['code']}).json()
        assert app.state.phone.status()['pending']['comparison'] == claim['comparison']
        assert not app.state.phone.status()['url']  # Single use, even before approval.
        assert phone.get('/api/companion/state', headers={'Authorization': 'Bearer '+claim['secret']}).status_code == 403
        assert phone.post('/api/companion/claim', headers=PHONE_HEADERS, json={'code': data['code']}).status_code == 403
        assert laptop.post(f'/api/copilot/pair/{claim["id"]}/reject', headers=ORIGIN).status_code == 200
        result = phone.get('/api/companion/approval', headers={'Authorization': 'Bearer '+claim['secret']}).json()
        assert result == {'decision': 'rejected'}
        assert phone.get('/api/companion/approval', headers={'Authorization': 'Bearer wrong'}).status_code == 403


def test_expiry_invalid_tokens_and_rate_limit(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr('app.phone.time.monotonic', lambda: clock[0])
    manager = Phone(); manager.origin = PHONE_ORIGIN
    invitation = manager.create()
    clock[0] += 121
    with pytest.raises(HTTPException) as exc:
        manager.claim(invitation['code'], 'Phone', '192.168.50.20')
    assert exc.value.status_code == 403
    assert manager.status()['url'] == ''
    with pytest.raises(HTTPException):
        manager.qr()
    for _ in range(4):
        with pytest.raises(HTTPException):
            manager.claim('invalid', 'Phone', '192.168.50.20')
    with pytest.raises(HTTPException) as exc:
        manager.claim('invalid', 'Phone', '192.168.50.20')
    assert exc.value.status_code == 429
    clock[0] += 61
    invitation = manager.create()
    request = manager.claim(invitation['code'], 'Phone', '192.168.50.20')
    clock[0] += 121
    with pytest.raises(HTTPException):
        manager.decision(request['id'], True)


def test_device_expiry_reconnection_and_revocation(monkeypatch):
    app = create_app()
    with TestClient(app) as laptop, configure_phone(app, monkeypatch) as phone:
        laptop.get('/')
        headers = pair(laptop, phone)
        assert phone.get('/api/companion/state', headers=headers).status_code == 200
        manager = app.state.phone
        assert manager.status()['device']['connected']
        manager.device['seen'] -= 20
        assert not manager.status()['device']['connected']
        # Same credential reconnects and gets the current shared state.
        app.state.control.session.latest = 'New question during disconnection'
        state = phone.get('/api/companion/state', headers=headers).json()
        assert state['latest'] == 'New question during disconnection'
        assert manager.status()['device']['connected']
        manager.device['expires'] = 0
        assert phone.get('/api/companion/state', headers=headers).status_code == 403
        headers = pair(laptop, phone)
        assert laptop.delete('/api/copilot/pair', headers=ORIGIN).status_code == 200
        assert phone.get('/api/companion/state', headers=headers).status_code == 403
        assert not manager.device and not manager.origin


def test_no_public_exposure_host_origin_or_management_access(monkeypatch):
    app = create_app()
    with TestClient(app) as laptop, configure_phone(app, monkeypatch) as phone:
        laptop.get('/')
        headers = pair(laptop, phone)
        for path in ('/', '/api/profile', '/health', '/api/devices', '/api/copilot/pair'):
            assert phone.get(path, headers=headers).status_code == 403
        for path in ('/api/live-quote', '/api/stop-all', '/api/shutdown'):
            assert phone.post(path, headers=headers, json={}).status_code == 403
        assert phone.get('/api/companion/state', headers={**headers, 'Host': 'evil.example'}).status_code == 403
        assert phone.post('/api/companion/answer', headers={**headers, 'Origin': 'https://evil.example'}, json={'question': 'x'}).status_code == 403
        phone._transport.client = ('203.0.113.5', 12345)
        assert phone.get('/companion', headers={'X-Forwarded-For': '192.168.50.20'}).status_code == 403
        phone._transport.client = ('192.168.60.20', 12345)
        assert phone.get('/companion').status_code == 403


def test_interface_discovery_and_unavailable_addresses(monkeypatch):
    output = '''lo0: flags=UP
 inet 127.0.0.1 netmask 0xff000000
 status: active
en0: flags=UP
 inet 192.168.1.10 netmask 0xffffff00
 status: active
en5: flags=UP
 inet 10.0.0.10 netmask 0xffffff00
 status: active
en6: flags=UP
 inet 172.16.0.10 netmask 0xffffff00
 status: inactive
en7: flags=UP
 inet 8.8.8.8 netmask 0xffffff00
 status: active
utun0: flags=UP
 inet 10.10.10.10 netmask 0xffffff00
 status: active
'''
    monkeypatch.setattr('app.phone.subprocess.check_output', lambda *a, **k: output)
    found = interfaces()
    assert [x['address'] for x in found] == ['192.168.1.10', '10.0.0.10']
    assert found[0]['network'] == '192.168.1.0/24'
    with pytest.raises(ValueError, match='no longer available'):
        asyncio.run(Phone().start('192.168.99.1'))
    monkeypatch.setattr('app.phone.subprocess.check_output', lambda *a, **k: '')
    with pytest.raises(ValueError, match='No active private'):
        interfaces()


def test_listener_binds_selected_private_ip_and_reports_failure(monkeypatch):
    choice = {'name': 'en5', 'address': '10.0.0.10', 'network': '10.0.0.0/24'}
    monkeypatch.setattr('app.phone.interfaces', lambda: [choice])
    sock = Mock(); sock.getsockname.return_value = ('10.0.0.10', 9123)
    class Server:
        def __init__(self, config):
            assert config.proxy_headers is False and config.lifespan == 'off' and config.interface == 'asgi3'
            self.started = False
            self.should_exit = False
        async def serve(self, sockets):
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(.001)
    monkeypatch.setattr('app.phone.ListenerServer', Server)
    async def check():
        monkeypatch.setattr('app.phone.socket.socket', lambda *a: sock)
        manager = Phone()
        await manager.start('10.0.0.10')
        assert manager.origin == 'http://10.0.0.10:9123'
        sock.bind.assert_called_once_with(('10.0.0.10', 0))
        await manager.close()
        sock.close.assert_called_once()
        sock.bind.side_effect = OSError('unavailable')
        with pytest.raises(ValueError, match='Could not open'):
            await manager.start('10.0.0.10')
        assert not manager.origin
    asyncio.run(check())


def test_wifi_preferred_over_other_active_interfaces(monkeypatch):
    config = """en0: flags=UP
 inet 10.1.1.2 netmask 0xffffff00
 status: active
en4: flags=UP
 inet 192.168.2.2 netmask 0xffffff00
 status: active
"""
    def read(command, **kwargs):
        return config if command[0] == '/sbin/ifconfig' else 'Hardware Port: Wi-Fi\nDevice: en4\n'
    monkeypatch.setattr('app.phone.subprocess.check_output', read)
    found = interfaces()
    assert found[0]['name'] == 'en4' and found[0]['label'] == 'Wi-Fi'
