"""Navigation fixture additions on the real isolated FastAPI application."""
import importlib.util
from pathlib import Path
import sys
import os
spec = importlib.util.spec_from_file_location('nav_fixture', Path(os.environ.get('PAN_NAV_CHECKOUT', Path(__file__).resolve().parents[3])) / 'packages/web/e2e/server.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
original_seed = fixture._seed_sessions
def seed():
    original_seed()
    fixture.session_store.create('Dense Navigation', adapter='cbc', workdir=str(fixture.WORKDIR))
    server = sys.modules['packages.web.server']
    from fastapi import Body
    @server.app.get('/__e2e/identity')
    async def identity():
        import os, psutil
        return {'pid': os.getpid(), 'processCreatedAt': psutil.Process().create_time(), 'port': fixture.PORT}
    @server.app.post('/__e2e/replace-history')
    async def replace_history(payload: dict = Body(...)):
        session = fixture.session_store.get(payload['sessionId'])
        fixture.session_store.replace_history(session, payload['messages'])
        fixture.session_store.save_full(session)
        await server.broadcast({'type': 'session.updated', 'sessionId': session.id})
        return {'historyEpoch': session.history_epoch, 'historyRevision': session.history_revision}
fixture._seed_sessions = seed
fixture.main()
