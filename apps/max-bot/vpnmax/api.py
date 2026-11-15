import ssl

import httpx


class RemoteError(Exception):
    """No URL, credential or upstream response body in printable errors."""

    # The panel validates auth, scope and input before touching a node, so these
    # statuses prove nothing was applied; transport failures and 5xx prove nothing.
    DEFINITE = (400, 401, 403, 404, 422)

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def definite(self):
        return self.status in self.DEFINITE

    @property
    def auth(self):
        return self.status in (401, 403)


class Panel:
    def __init__(self, base_url, key, protocols, transport=None):
        self.protocols = protocols
        self.http = httpx.AsyncClient(base_url=base_url.rstrip('/') + '/',
                                      headers={'X-API-Key': key}, timeout=90,
                                      transport=transport, follow_redirects=False)

    def path(self, sid, suffix=''):
        proto = self.protocols[sid]
        family = '/awg3' if proto == 'awg3' else ''
        return f'api/v1/servers/{sid}{family}/clients' + suffix

    async def request(self, method, path, **kwargs):
        try:
            result = await self.http.request(method, path, **kwargs)
            if not result.is_success:
                raise RemoteError(f'Panel HTTP {result.status_code}', result.status_code)
            return result.json() if result.content else None
        except httpx.HTTPError:
            raise RemoteError('Panel transport failure') from None

    async def clients(self, sid):
        return await self.request('GET', self.path(sid))

    async def servers(self):
        return await self.request('GET', 'api/v1/servers')

    async def issue(self, sid, name):
        # Deliberately no retry: POST is not idempotent upstream.
        return await self.request('POST', self.path(sid), json={'name': name})

    async def config(self, row):
        return await self.request('GET', self.path(row['server_id'], '/config'),
                                  params={'public_key': row['public_key']})

    async def mutate(self, action, row):
        method = 'DELETE' if action == 'revoke' else 'POST'
        suffix = '' if action == 'revoke' else '/' + action
        await self.request(method, self.path(row['server_id'], suffix),
                           params={'public_key': row['public_key']})


class MaxAPI:
    def __init__(self, token, transport=None, ca_file=None, api_socket=None):
        trust = ssl.create_default_context()
        if ca_file:
            trust.load_verify_locations(cafile=ca_file)
        if api_socket:
            if transport is not None:
                raise ValueError('Specify either transport or MAX API socket')
            # Raw TCP relay over SSH; TLS/SNI/hostname verification remain here.
            transport = httpx.AsyncHTTPTransport(verify=trust, uds=api_socket)
        self.http = httpx.AsyncClient(base_url='https://platform-api2.max.ru/',
                                     headers={'Authorization': token}, timeout=20,
                                     transport=transport, follow_redirects=False, verify=trust)

    async def send(self, uid, text, buttons=None, forward=None, media=None):
        body = {'text': text}
        attachments = list(media or [])
        if buttons:
            attachments.append({'type': 'inline_keyboard', 'payload': {'buttons': buttons}})
        if attachments:
            body['attachments'] = attachments
        if forward:
            body['link'] = {'type': 'forward', 'mid': forward}
        try:
            r = await self.http.post('messages', params={'user_id': uid, 'disable_link_preview': 'true'}, json=body)
            if not r.is_success:
                raise RemoteError(f'MAX HTTP {r.status_code}')
        except httpx.HTTPError:
            raise RemoteError('MAX transport failure') from None

    async def updates(self, marker=None, wait=30):
        """Long polling. Works only while the bot has no webhook subscription."""
        params = {'limit': 100, 'timeout': wait, 'types': 'message_created,message_callback,bot_started'}
        if marker is not None:
            params['marker'] = marker
        try:
            r = await self.http.get('updates', params=params, timeout=wait + 15)
            if not r.is_success:
                raise RemoteError(f'MAX updates HTTP {r.status_code}')
            data = r.json()
            return data.get('updates') or [], data.get('marker')
        except (httpx.HTTPError, ValueError):
            raise RemoteError('MAX updates transport failure') from None

    async def acknowledge(self, callback_id):
        try:
            r = await self.http.post('answers', params={'callback_id': callback_id},
                                     json={'notification': 'Запрос обрабатывается'})
            if not r.is_success:
                raise RemoteError(f'MAX callback HTTP {r.status_code}')
        except httpx.HTTPError:
            raise RemoteError('MAX callback transport failure') from None
