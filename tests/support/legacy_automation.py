"""Legacy four-object authorization harness for still-retained Runtime proofs.

``packages.jobs.action_runtime.Runtime`` remains the legacy Job execution host
and keeps its own revocation/attempt barriers, so the tests that drive it
directly still need an ``automation.Service`` for grants, a private IPC bridge
and an owned temp root.  The Service is deliberately *not* started: the
Automation event dispatch loop is retired and requires a canonical owner
(``tests/support/canonical_host.py``); these tests assert the Runtime host only.
"""
from types import SimpleNamespace

from fastapi import FastAPI

from packages.actions.api import install as install_actions
from packages.automation.api import install as install_automation
from packages.automation.authorization import bind_objects
from packages.automation.service import Service
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.jobs.action_api import router
from packages.plugins.ipc import Bridge
from packages.plugins.runtime import Runtime as PluginRuntime
from tests.test_job_action_templates import setup

SESSION_ID = 'ses_authorization_timeout'


async def harness(root, monkeypatch):
    rt, calls = await setup(root)
    svc = Service(root / 'automation', SimpleNamespace(workers={}), None)
    svc.job_runtime = rt
    svc.engine.actions = rt.actions
    rt.authorization = svc.auth.permission_allowed
    app = FastAPI()
    app.state.automation = svc
    server = SimpleNamespace(app=app, sess=SimpleNamespace(list_all=lambda **kw: []))
    monkeypatch.setattr('packages.actions.api.Store', lambda: rt.actions)
    install_automation(server)
    install_actions(server)
    app.include_router(router)
    manifest = ManifestConfig(mcp_servers=[
        McpServer('pan', command='python', args=['-m', 'packages.mcp.server'])])
    session = Session(id=SESSION_ID, name='Authorization', adapter_config={
        'plugin_bindings': [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]})
    runtime = PluginRuntime(lambda: manifest, lambda sid: session if sid == SESSION_ID else None)
    bind_objects(runtime, app)
    bridge = Bridge(runtime, root)
    return SimpleNamespace(rt=rt, calls=calls, svc=svc, app=app, bridge=bridge, session=session)


async def close(h):
    await h.bridge.close()
    await h.svc.engine.close()
    await h.svc.bus.close()
    await h.rt.close()
