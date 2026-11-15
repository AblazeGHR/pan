"""Owned trusted stdio fixture for pre-existing transport/terminal regressions."""
import sys
from packages.core.manifest_loader import ManifestConfig, McpServer
from packages.core.session import Session
from packages.plugins.runtime import Runtime
from packages.plugins.ipc import Bridge
from packages.web.machine_ipc import bind


def core_bridge(app, root, sid='caller', caller=None):
    manifest = ManifestConfig(mcp_servers=[McpServer('pan', command=sys.executable,
                                                   args=['-m', 'packages.mcp.server'])])
    caller = caller or Session(id=sid, name='Owned IPC fixture')
    caller.adapter_config['plugin_bindings'] = [{'plugin_id': 'pan', 'mode': 'standalone', 'enabled': True}]
    runtime = Runtime(lambda: manifest, lambda value: caller if value == caller.id else None)
    bind(runtime, app, lambda value: caller if value == caller.id else None)
    return Bridge(runtime, root)
