"""Private, session-bound stdio MCP environment broker (Windows only)."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def authorized(data_root, handle, session_id, name):
    from packages.acp.local import StateStore, _read_profile
    from packages.acp.policy import physical_mcp_pin
    from packages.core.config import DEFAULT_PLUGIN_MANIFESTS
    from packages.core.manifest_loader import load_manifests
    from packages.plugins.catalog import Catalog

    store = StateStore(data_root)
    path = store.path(handle)
    if store.win.is_reparse_point(str(path)):
        raise PermissionError('state unavailable')
    store.verifier.verify_owner_only(path)
    state = json.loads(store.protector.unprotect(path.read_bytes()))
    if state.get('sessionId') != session_id or name not in state.get('mcpNames', []):
        raise PermissionError('session unavailable')
    profile, _ = _read_profile(data_root, state.get('principal'))
    fingerprint = hashlib.sha256(json.dumps(profile.mcp_env, ensure_ascii=True,
        separators=(',', ':')).encode()).hexdigest()
    if (state.get('profileMcpEnv') != fingerprint or name not in profile.mcp_names
            or state.get('template') != profile.template
            or state.get('roots') != [str(root) for root in profile.roots]
            or state.get('profileMcp') != [list(pair) for pair in zip(profile.mcp_names, profile.mcp_pins)]):
        raise PermissionError('profile changed')
    index = profile.mcp_names.index(name)
    env = dict(profile.mcp_env[index])
    if not env:
        raise PermissionError('environment unavailable')
    config = json.loads((Path(data_root) / 'config.json').read_text(encoding='utf-8'))
    catalog = Catalog(load_manifests(config.get('plugin_manifests', DEFAULT_PLUGIN_MANIFESTS)))
    if physical_mcp_pin(catalog, [name]) != profile.mcp_pins[index]:
        raise PermissionError('descriptor changed')
    rows = catalog.compile(catalog.legacy_bindings([name]))
    if len(rows) != 1:
        raise PermissionError('descriptor unavailable')
    row = rows[0]
    command, args = row.get('command'), row.get('args', [])
    if (not isinstance(command, str) or not os.path.isabs(command) or not isinstance(args, list)
            or any(not isinstance(arg, str) for arg in args) or row.get('env')
            or row.get('url') or row.get('headers')):
        raise PermissionError('descriptor unavailable')
    return profile, [command, *args], env


def main(data_root, handle, session_id, name):
    if os.name != 'nt':
        raise OSError('private MCP transport unavailable')
    from packages.core.terminal.guard import JobObjectGuard
    from packages.core.terminal.emulator import _resume_process
    profile, command, private_env = authorized(data_root, handle, session_id, name)
    child_env = dict(os.environ)
    child_env.update(private_env)
    guard = JobObjectGuard()
    child = None
    stopped = threading.Event()
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=child_env, shell=False,
            creationflags=0x00000004 | 0x08000000)
        guard.assign(int(child._handle))
        if not guard.is_member(int(child._handle)) or not guard.active_processes():
            raise RuntimeError('owned subprocess unavailable')
        if authorized(data_root, handle, session_id, name) != (profile, command, private_env):
            raise PermissionError('authorization changed during startup')
        _resume_process(int(child._handle))
        def copy(source, target):
            try:
                while True:
                    data = os.read(source.fileno(), 65536)
                    if not data:
                        break
                    target.write(data)
                    target.flush()
            except (OSError, ValueError):
                pass
            finally:
                stopped.set()
        threading.Thread(target=copy, args=(sys.stdin.buffer, child.stdin), daemon=True).start()
        threading.Thread(target=copy, args=(child.stdout, sys.stdout.buffer), daemon=True).start()
        while not stopped.wait(.25) and child.poll() is None:
            try:
                current, current_command, current_env = authorized(data_root, handle, session_id, name)
                if current != profile or current_command != command or current_env != private_env:
                    break
            except Exception:
                break
    finally:
        try:
            if child is not None:
                try:
                    guard.terminate_tree(child.pid, timeout=3)
                finally:
                    if child.poll() is None:
                        child.terminate()
        finally:
            guard.close()


if __name__ == '__main__':
    try:
        if len(sys.argv) != 5:
            raise ValueError('invalid invocation')
        main(*sys.argv[1:])
    except Exception:
        # Never print a descriptor or secret to protocol stdout or logs.
        raise SystemExit(1)
