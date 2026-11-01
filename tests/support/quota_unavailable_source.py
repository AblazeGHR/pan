"""Empty owned quota profile; never discovers workstation accounts or CLI."""
from pathlib import Path
from packages.automation.quota_host import ReadSource
from packages.automation.quota_policy import Unavailable
from packages.cc_switch_bridge.provider import Bridge
from packages.core.codex_quota_store import CodexProfile, CodexQuotaStore


def source(root):
    root = Path(root).resolve()
    def unavailable(args):
        raise Unavailable('owned fixture has no account CLI')
    profile = CodexProfile(root / 'empty-codex', None, None, 'owned-empty')
    return ReadSource(root, bridge=Bridge(root / 'bridge', runner=unavailable),
        profile_resolver=lambda: profile,
        store_factory=lambda value: CodexQuotaStore(value, root=root / 'quota'))
