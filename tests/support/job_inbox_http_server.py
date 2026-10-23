"""Owned composition launcher: real Jobs + Inbox; fake CLI, no QQ gateway."""
import os
import sys
from pathlib import Path

from tests.support.startup_trace import arm

arm()

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
data=Path(os.environ['PAN_E2E_DATA_ROOT']).resolve()
assert data.name=='data'
from packages.qq import gateway_plugins
gateway_plugins.ROOT=data.parent
gateway_plugins.MANIFEST=data/'qq_plugins/manifest.json'
gateway_plugins.MANIFEST.parent.mkdir(parents=True,exist_ok=True)
gateway_plugins.MANIFEST.write_text('{"plugins":[]}',encoding='utf-8')
import packages.web.server as server
server._PROJECT_DIR=data.parent

@server.app.get('/__test/job-inbox-identity')
async def identity():
    from packages.core import session,config
    from packages.core.inbox_service import get_inbox
    return {'pid':os.getpid(),'root':str(data),'sessions':str(session.SESSION_DIR),
            'config':str(config.CONFIG_FILE),'inbox':str(get_inbox().path),
            'gatewayManifest':str(gateway_plugins.MANIFEST),'fakeCLI':os.environ['PAN_E2E_FAKE_CLI']}

from tests.support import automation_http_server  # Starts the shared isolated server.
