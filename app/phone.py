"""Explicit, private-LAN-only listener and approval-based phone credentials."""
import asyncio
import contextlib
import errno
import io
import ipaddress
import re
import secrets
import socket
import subprocess
import time

import segno
import uvicorn
from fastapi import HTTPException
from starlette.responses import JSONResponse

PRIVATE = tuple(ipaddress.ip_network(x) for x in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16'))
PATHS = {'/companion', '/static/copilot.js', '/static/style.css', '/api/companion/claim',
         '/api/companion/approval', '/api/companion/receipt', '/api/companion/events', '/api/companion/state', '/api/companion/answer',
         '/api/copilot/scenarios', '/api/copilot/practice', '/api/copilot/ocr'}


def interfaces():
    try:
        output = subprocess.check_output(['/sbin/ifconfig'], text=True, timeout=3)
    except (OSError, subprocess.SubprocessError):
        raise ValueError('Cannot read Mac network interfaces. Connect to Wi-Fi and try again.') from None
    # Wi-Fi is not always en0 (USB adapters and migrated Macs can differ).
    wifi = 'en0'
    labels = {}
    try:
        ports = subprocess.check_output(['/usr/sbin/networksetup', '-listallhardwareports'], text=True, timeout=3)
        for label, device in re.findall(r'Hardware Port: ([^\n]+)\nDevice: (en\d+)', ports):
            labels[device] = label
            if label in {'Wi-Fi', 'AirPort'}:
                wifi = device
    except (OSError, subprocess.SubprocessError):
        pass
    found = []
    for block in re.split(r'\n(?=\S)', output):
        name = block.split(':', 1)[0]
        if not re.fullmatch(r'en\d+', name) or 'status: active' not in block:
            continue
        for address, mask in re.findall(r'inet (\d+\.\d+\.\d+\.\d+) netmask (0x[0-9a-f]+)', block):
            ip = ipaddress.ip_address(address)
            if any(ip in net for net in PRIVATE):
                network = ipaddress.ip_network(f'{address}/{ipaddress.IPv4Address(int(mask, 16))}', strict=False)
                found.append({'name': name, 'label': labels.get(name, name), 'address': address, 'network': str(network)})
    if not found:
        raise ValueError('No active private Wi-Fi/Ethernet IPv4 address was found. Connect the Mac and iPhone to the same Wi-Fi; guest isolation may prevent access.')
    return sorted(found, key=lambda x: (x['name'] != wifi, x['name'], x['address']))


class ListenerServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self):
        yield


class Phone:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.origin = ''
        self.network = None
        self.server = self.task = self.sock = None
        self.invite = self.pending = self.device = None
        self.failures = []
        self.app = None

    async def start(self, address=None):
        choices = await asyncio.to_thread(interfaces)
        selected = next((x for x in choices if x['address'] == address), None) if address else choices[0]
        if not selected:
            raise ValueError('The selected network address is no longer available. Refresh and select an active interface.')
        if self.origin and self.origin.split('//')[1].split(':')[0] == selected['address']:
            return choices
        await self.close()
        sock = None
        try:
            sock = self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((selected['address'], 0))
            sock.listen(16)
            sock.setblocking(False)
            self.origin = f'http://{selected["address"]}:{sock.getsockname()[1]}'
            self.network = ipaddress.ip_network(selected['network'])
            self.server = ListenerServer(uvicorn.Config(self.gateway, interface='asgi3', lifespan='off', access_log=False,
                                                       proxy_headers=False, log_level='critical'))
            self.task = asyncio.create_task(self.server.serve(sockets=[sock]))
            for _ in range(100):
                if self.server.started:
                    return choices
                if self.task.done():
                    break
                await asyncio.sleep(.02)
            raise OSError('listener did not start')
        except Exception as exc:
            await self.close()
            reason = {
                errno.EADDRNOTAVAIL: 'The selected address is no longer assigned to this Mac. Refresh network addresses.',
                errno.EACCES: 'macOS denied permission to open the local listener. Check local-network permissions.',
                errno.EPERM: 'macOS denied permission to open the local listener. Check local-network permissions.',
                errno.ENETDOWN: 'The selected network interface is down. Reconnect Wi-Fi.',
            }.get(getattr(exc, 'errno', None), 'The listener could not start on the selected address. Check the Mac firewall and Wi-Fi connection.')
            raise ValueError('Could not open the companion. ' + reason) from exc

    async def gateway(self, scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
            return
        if scope['type'] != 'http':
            return
        peer = scope.get('client', ('', 0))[0]
        headers = dict(scope['headers'])
        try:
            ip = ipaddress.ip_address(peer)
            allowed = self.network is not None and ip in self.network and any(ip in net for net in PRIVATE)
        except ValueError:
            allowed = False
        if (not self.origin or not allowed or scope['path'] not in PATHS
                or headers.get(b'host', b'').decode() != self.origin.split('//')[1]
                or (scope['method'] != 'GET' and headers.get(b'origin', b'').decode() != self.origin)):
            await JSONResponse({'detail': 'Companion access is restricted to this local network.'}, status_code=403)(scope, receive, send)
            return
        scope.setdefault('state', {})['phone_listener'] = True
        await self.app(scope, receive, send)

    async def close(self):
        self.revoke()
        server, task, sock = self.server, self.task, self.sock
        self.server = self.task = self.sock = None
        self.origin = ''
        self.network = None
        try:
            if server:
                server.should_exit = True
            if task:
                try:
                    await asyncio.wait_for(task, 3)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        finally:
            if sock:
                sock.close()
            if server:
                for listener in getattr(server, 'servers', []):
                    listener.close()

    def revoke(self):
        self.invite = self.pending = self.device = None

    def create(self):
        self.revoke()
        self.invite = {'token': secrets.token_urlsafe(32), 'code': ''.join(secrets.choice('ABCDEFGHJKLMNPQRSTUVWXYZ23456789') for _ in range(8)), 'expires': time.monotonic() + 120}
        return self.status()

    def status(self):
        now = time.monotonic()
        invite = self.invite if self.invite and self.invite['expires'] > now else None
        pending = self.pending if self.pending and self.pending['expires'] > now and self.pending['decision'] == 'waiting' else None
        device = self.device if self.device and self.device['expires'] > now else None
        return {'url': self.origin + '/companion#' + invite['token'] if invite else '',
                'entry_url': self.origin + '/companion' if self.origin else '',
                'code': invite['code'] if invite else '', 'expires_in': max(0, int(invite['expires'] - now)) if invite else 0,
                'pending': {k: pending[k] for k in ('id', 'name', 'peer', 'comparison')} if pending else None,
                'device': {'name': device['name'], 'peer': device['peer'], 'connected': now - device['seen'] < 10,
                           'expires_in': int(device['expires'] - now)} if device else None}

    def qr(self):
        url = self.status()['url']
        if not url:
            raise HTTPException(410, 'Pairing expired. Click Pair Phone again.')
        out = io.BytesIO()
        segno.make(url, micro=False, error='m').save(out, kind='svg', scale=5)
        return out.getvalue()

    def claim(self, value, name, peer):
        now = time.monotonic()
        self.failures = [t for t in self.failures if now - t < 60]
        if len(self.failures) >= 5:
            raise HTTPException(429, 'Too many pairing attempts. Wait one minute.')
        invite = self.invite
        if (not invite or now >= invite['expires'] or not
                (secrets.compare_digest(value, invite['token']) or secrets.compare_digest(value.upper(), invite['code']))):
            self.failures.append(now)
            raise HTTPException(403, 'Invalid or expired pairing code. Click Pair Phone on the Mac again.')
        self.invite = None
        self.pending = {'id': secrets.token_hex(12), 'secret': secrets.token_urlsafe(32), 'name': name,
                        'peer': peer, 'comparison': f'{secrets.randbelow(1000000):06}',
                        'expires': now + 120, 'decision': 'waiting'}
        return {k: self.pending[k] for k in ('id', 'secret', 'comparison')}

    def decision(self, identifier, approve):
        p = self.pending
        if not p or p['id'] != identifier or p['expires'] <= time.monotonic() or p['decision'] != 'waiting':
            raise HTTPException(410, 'Approval request expired.')
        p['decision'] = 'approved' if approve else 'rejected'
        if approve:
            self.device = {'token': secrets.token_urlsafe(32), 'name': p['name'], 'peer': p['peer'],
                           'expires': time.monotonic() + 7200, 'seen': 0}

    def approval(self, secret):
        p = self.pending
        if not p or time.monotonic() >= p['expires'] or not secrets.compare_digest(secret, p['secret']):
            raise HTTPException(403, 'Approval expired. Pair again on the Mac.')
        result = {'decision': p['decision']}
        if p['decision'] == 'approved' and self.device:
            result['token'] = self.device['token']
        return result

    def authorize(self, request):
        d = self.device
        if (not getattr(request.state, 'phone_listener', False) or not d
                or time.monotonic() >= d['expires'] or not secrets.compare_digest(
                    request.headers.get('authorization', ''), 'Bearer ' + d['token'])):
            raise HTTPException(403, 'Phone access expired or was revoked. Pair again on the Mac.')
        d['seen'] = time.monotonic()
