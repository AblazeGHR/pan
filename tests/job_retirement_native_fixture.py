"""Owned retirement-only fixture with the default product executor and releases."""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.automation.service import Service as Automation
from packages.core import worker
from packages.jobs import final_api as api
from packages.jobs.final_service import bind, object_ref


@pytest.fixture
def h(tmp_path):
    app = FastAPI()
    automation = Automation(tmp_path / 'automation', worker, None)
    automation.auth = api.CanonicalUserAuth(tmp_path / 'automation')
    service = bind(app, automation, tmp_path / 'canonical')
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    api.install(app, action_router, facade_router)
    action = next(row for row in service.actions(actor={'type': 'user', 'id': 'local'})['items']
                  if row['id'] == 'product_session_message')
    token = automation.auth.login(automation.auth.key())
    client = TestClient(app, headers={'Authorization': 'Bearer ' + token})
    try:
        yield SimpleNamespace(app=app, automation=automation, service=service,
            client=client, token=token, root=tmp_path / 'canonical', action=object_ref('action', action))
    finally:
        client.close()
        asyncio.run(service.close())
