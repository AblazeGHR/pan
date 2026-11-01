"""Reusable canonical Job host for tests that need the real ``/api`` routes.

Mirrors the proven fixture in ``tests/test_automation_canonical.py``: a real
``FinalService`` bound by the production ``final_service.bind`` helper, the
canonical router mounted through ``final_api.install``, and an explicit typed
executor so a durable admission stays distinguishable from a provider effect.
Legacy ``/api/jobs/*`` handlers are mounted by the same install call, which is
what a suite asserting the retirement boundary needs.
"""
from types import SimpleNamespace

from fastapi import FastAPI

from packages.automation.service import Service
from packages.jobs import final_api
from packages.jobs.final_runtime.executor import TypedExecutor
from packages.jobs.final_service import bind, definition, object_ref

USER = {'type': 'user', 'id': 'local'}


class RecordingProvider:
    """Session provider that records sends instead of touching a real worker."""

    def __init__(self):
        self.calls, self.targets, self.sources = [], [], []

    async def send_session(self, session_id, text, *, effect_key, source):
        self.calls.append(text)
        self.targets.append(session_id)
        self.sources.append(source)
        return {'status': 'queued', 'messageId': 'message_' + str(len(self.calls))}


def build(root, *, worker=None):
    """Return the canonical app/service/provider plus an owned Action reference."""
    app = FastAPI()
    worker = worker or SimpleNamespace(workers={}, set_running_observer=lambda cb: None)
    automation = Service(root / 'automation', worker, None)
    automation.auth = final_api.CanonicalUserAuth(root / 'automation')
    automation.engine.authorization = automation.auth.permission_allowed
    service = bind(app, automation, root / 'canonical')
    app.state.automation = automation
    from packages.jobs.action_api import router as action_router
    from packages.jobs.api import router as facade_router
    final_api.install(app, action_router, facade_router)
    provider = RecordingProvider()
    service.executor = TypedExecutor(session_provider=provider)
    product = next(item for item in service.actions(actor=USER)['items']
                   if item['id'] == 'product_session_message')
    value = definition('action', product)
    value.update(system=False,
                 retry={'idempotent': True, 'sameKeySupported': True, 'requiresProof': False})
    action = service.publish_action('owned_message', value)
    service.owner.claim(0)
    service.owner.set_state('active')
    automation.events.initialize()
    token = automation.auth.login(automation.auth.key())
    return SimpleNamespace(app=app, automation=automation, service=service, provider=provider,
                           token=token, root=root, action=object_ref('action', action))
