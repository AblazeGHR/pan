"""Offline JSON Scheduler preview/apply/status through the existing P6 host.

Default is dry-run. Existing initialized roots and stopped canonical ownership
are prerequisites; this command never enrolls/bootstraps a source or starts an
engine, provider, process, Worker, notification sender or scheduler pass.
"""
import argparse
import asyncio
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import FinalService
from packages.scheduler.json_handoff import JsonTaskHandoff
from scripts.migrate_action_groups import OfflineAuthorization

USER = {'type': 'user', 'id': 'local'}


def plain(path):
    return path.is_absolute() and all(not p.is_symlink()
        and not (hasattr(p, 'is_junction') and p.is_junction()) for p in (path, *path.parents))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile-root', type=Path, required=True)
    parser.add_argument('--canonical-root', type=Path, required=True)
    parser.add_argument('--authorization-root', type=Path, required=True)
    parser.add_argument('--mappings', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--expected-plan-digest')
    parser.add_argument('--expected-owner-epoch', type=int)
    parser.add_argument('--request-key')
    parser.add_argument('--status-key')
    args = parser.parse_args(argv)
    roots = (args.profile_root, args.canonical_root, args.authorization_root)
    if any(not plain(root) or not root.is_dir() for root in roots):
        parser.error('Existing absolute plain profile/canonical/authorization roots required')
    profile, canonical, auth = (root.resolve() for root in roots)
    if canonical.parent != profile or not (canonical / 'event-creators' / 'creators.sqlite3').is_file():
        parser.error('Existing canonical store must belong to this profile; initialize separately')
    if not (auth / 'authorization.json').is_file():
        parser.error('Existing authorization document required; do not initialize grants during migration')
    owner_path = profile / 'job-runtime-owner.sqlite3'
    if not owner_path.is_file() or not plain(owner_path):
        parser.error('Existing cooperative profile owner required; never enroll an unknown source')
    with closing(sqlite3.connect(owner_path.as_uri() + '?mode=ro', uri=True)) as db:
        owner = db.execute('SELECT epoch,state FROM owner WHERE singleton=1').fetchone()
    if owner is None:
        parser.error('Initialize/stop the profile separately; dry-run never bootstraps ownership')
    if args.apply and (owner[1] == 'active' or args.expected_owner_epoch != owner[0]):
        parser.error('Stopped original owner and exact current owner epoch required for apply')
    if not args.apply and not args.status_key:
        if args.mappings is None or args.output is None or not plain(args.mappings) or not args.mappings.is_file():
            parser.error('Dry-run requires absolute --mappings and --output')
        output = args.output
        if not plain(output) or output.exists() or output.resolve().is_relative_to(profile) or output.resolve().is_relative_to(auth):
            parser.error('New plan output must be outside profile/authorization and distinct from existing files')
    if args.apply:
        if not args.plan or not plain(args.plan) or not args.plan.is_file() or not args.request_key or not args.expected_plan_digest:
            parser.error('Apply requires explicit --plan, --expected-plan-digest, --request-key and --expected-owner-epoch')
        plan = fc.loads(args.plan.read_text(encoding='utf-8-sig'))
        if fc.digest(plan) != args.expected_plan_digest:
            parser.error('Reviewed plan digest changed')
    service = FinalService(canonical, OfflineAuthorization(auth))
    try:
        if args.status_key:
            try:
                result = service.operation_for_key('create', args.status_key, actor=USER)
            except KeyError:
                print(json.dumps({'status': 'unknown', 'ownerEpoch': owner[0], 'ownerState': owner[1],
                                  'message': 'No canonical receipt; query original handoff intent, never replay effects'}))
                return 3
            print(json.dumps(result, ensure_ascii=False))
            return 0
        port = JsonTaskHandoff(service)
        if args.apply:
            print(json.dumps(port.apply(plan, args.request_key, actor=USER), ensure_ascii=False))
            return 0
        mappings = fc.loads(args.mappings.read_text(encoding='utf-8-sig'))
        if not isinstance(mappings, list) or not mappings:
            parser.error('Explicit nonempty mapping list required')
        plan = port.preview(mappings, actor=USER)
        # Exclusive output creation preserves previous review evidence.
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(fc.canonical_json(plan) + '\n')
        print(json.dumps({'planDigest': fc.digest(plan), 'eligible': len(plan['entries']),
                          'blocked': plan['blocked'], 'plan': str(args.output)}))
        return 1 if plan['blocked'] else 0
    except (fc.ContractError, OSError, ValueError, KeyError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'blocked', 'message': str(exc), 'unknownReplay': False}), file=sys.stderr)
        return 1
    finally:
        asyncio.run(service.close())


if __name__ == '__main__':
    raise SystemExit(main())
