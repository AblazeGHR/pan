"""Offline explicit-root Group dry-run/apply; no service or effects are started."""
import argparse
import json
from pathlib import Path
import sys
import threading
import sqlite3

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.actions.group_migration import LegacyGroupMigration
from packages.actions.store import atomic
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService

USER = {'type': 'user', 'id': 'local'}


class OfflineAuthorization(CanonicalUserAuth):
    """Reuse the real permission reader, without login/session pruning on open.

    This CLI is a same-OS-user offline administration boundary. It reads no
    credentials and grants no HTTP/IPC callers a migration capability.
    """
    def __init__(self, root):
        self.path = root / 'user.key'
        self.permissions_path = root / 'authorization.json'
        self.sessions_path = root / 'sessions.json'
        self.sessions = {}
        self.lock = threading.RLock()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--canonical-root', type=Path, required=True)
    parser.add_argument('--authorization-root', type=Path, required=True)
    parser.add_argument('--mappings', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--request-key')
    parser.add_argument('--expected-owner-epoch', type=int)
    parser.add_argument('--status-key')
    args = parser.parse_args(argv)
    root = args.canonical_root.resolve()
    source = args.source_root.resolve()
    if (not (root / 'event-creators' / 'creators.sqlite3').is_file()
            or not (root.parent / 'job-runtime-owner.sqlite3').is_file()
            or not args.authorization_root.is_dir() or not source.is_dir()
            or source == root or source.is_relative_to(root) or root.is_relative_to(source)):
        parser.error('Existing, separate explicit source/canonical/authorization roots are required')
    owner_path = root.parent / 'job-runtime-owner.sqlite3'
    with sqlite3.connect(owner_path.as_uri() + '?mode=ro', uri=True) as db:
        if not db.execute('SELECT 1 FROM owner WHERE singleton=1').fetchone():
            parser.error('Initialize the canonical owner separately; dry-run never bootstraps a profile')
    service = FinalService(root, OfflineAuthorization(args.authorization_root.resolve()))
    migration = LegacyGroupMigration(service, source)
    if args.status_key:
        print(json.dumps(migration.status(args.status_key, actor=USER), ensure_ascii=False))
        return 0
    if not args.apply:
        if not args.mappings or not args.output:
            parser.error('Dry-run requires --mappings and --output')
        output = args.output.resolve()
        if (any(output.is_relative_to(protected) for protected in (source, root, args.authorization_root.resolve()))
                or output in {owner_path, root.parent / 'job-runtime-owner.lock', args.mappings.resolve()}):
            parser.error('Plan output must be outside source/canonical/authorization roots and distinct from mappings')
        plan = migration.preview(json.loads(args.mappings.read_text('utf-8')), actor=USER)
        atomic(output, plan)
        print(json.dumps({'mode': 'dry-run', 'groups': len(plan['groups']), 'blocked': plan['blocked'],
                          'planDigest': plan['planDigest']}, ensure_ascii=False))
        return 2 if plan['blocked'] else 0
    if args.plan is None or not args.request_key or args.expected_owner_epoch is None:
        parser.error('Apply requires --plan, --request-key and --expected-owner-epoch')
    # Only the existing owner protocol decides if a stopped profile can reopen.
    service.owner.claim(args.expected_owner_epoch)
    try:
        response = migration.apply(json.loads(args.plan.read_text('utf-8')), args.request_key, actor=USER)
        print(json.dumps(response, ensure_ascii=False))
        return 0
    finally:
        service.owner.release()


if __name__ == '__main__':
    raise SystemExit(main())
