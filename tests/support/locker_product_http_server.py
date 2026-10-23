"""Owned runtime fixture; core auth/Card/Locker APIs stay entirely production."""
import asyncio
import os
from fastapi import Request, HTTPException
from packages.web import server
from packages.interactions.locker_messages import accept_locker_message
from packages.interactions.api import failure
from packages.interactions.contracts import InteractionError

gate = {'armed': False, 'captured': False, 'release': None}


def fixture_identity(request):
    if request.headers.get('x-locker-fixture') != os.environ['PAN_TEST_LOCKER_NONCE']:
        raise HTTPException(403)


@server.app.post('/__test/locker/accept')
async def accept(data: dict, request: Request):
    fixture_identity(request)
    try:
        return {'message': accept_locker_message(data['sourceSessionId'], data['recipientId'], data['message'])}
    except InteractionError as exc:
        raise failure(exc) from exc


@server.app.post('/__test/locker/gate/{operation}')
async def gate_operation(operation: str, request: Request):
    fixture_identity(request)
    if operation == 'arm':
        gate.update(armed=True, captured=False, release=asyncio.Event())
    elif operation == 'release' and gate['release']:
        gate['release'].set()
    return {'captured': gate['captured']}


class LateResponse:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get('type') == 'http' and scope.get('method') == 'GET' and scope.get('path') == '/api/user-locker-messages' and gate['armed']:
            gate['armed'] = False
            captured = []
            async def capture(message):
                captured.append(message)
            await self.app(scope, receive, capture)
            gate['captured'] = True
            await asyncio.wait_for(gate['release'].wait(), timeout=20)
            for message in captured:
                await send(message)
        else:
            await self.app(scope, receive, send)


server.app.add_middleware(LateResponse)
# Reuse the real automation/queue lifecycle and fakeCLI transport. No parallel
# lifecycle, principal override, mocked core APIs or notification consumer.
import tests.support.job_inbox_http_server  # noqa: E402,F401
