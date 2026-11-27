"""Owned script-API fixture; synthetic event writes stay in the actual owner."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from fastapi import HTTPException
from packages.web import server


@server.app.post('/__test/script-api-events')
async def append_owned_fixture_events(body: dict):
    events = body.get('events')
    if set(body) != {'events'} or not isinstance(events, list) or len(events) > 256:
        raise HTTPException(400, 'Invalid owned fixture events')
    service = server.app.state.automation
    for event in events:
        if not isinstance(event, dict) or not str(event.get('id', '')).startswith('fixture-'):
            raise HTTPException(400, 'Only synthetic fixture events accepted')
        await service.engine.io(service.events.append, event, [])
    return {'appended': len(events)}


# Reuse its actual isolated lifecycle, owner fence and global auth middleware.
import tests.support.automation_http_server
