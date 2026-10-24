"""Owned fixture writes history without starting any provider Worker."""
import os
from fastapi import Request, HTTPException
from packages.web import server
from packages.core import session


@server.app.post('/__test/locker-context')
async def context(data: dict, request: Request):
    if request.headers.get('x-context-fixture') != os.environ['PAN_TEST_CONTEXT_NONCE']:
        raise HTTPException(403)
    owner = session.get(data['owner'])
    if 'managed' in data:
        owner.managed = data['managed']
    for row in data.get('history', []):
        session.append_history(owner, row)
    session.save(owner)
    return {'ids': [row.get('messageId') for row in owner.history]}


import tests.support.job_inbox_http_server  # noqa: E402,F401
