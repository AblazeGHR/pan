"""Export a nonexecuting D6 manifest offline; never import it as a Job graph."""
import argparse
import hashlib
import json
from pathlib import Path

from .inventory import build_inventory
from .apply import outside_root, _atomic_write


def build(inventory):
    return {'schemaVersion': 1, 'records': [
        {'id': item['migrationId'], 'originalId': str(item.get('id') or ''),
         'sourceFile': item['sourceFile'],
         'sha256': item['originalDigest'].removeprefix('sha256:'),
         'revision': 1, 'reasonCodes': item['reasonCodes'], 'executable': False}
        for item in inventory['instances'] if item['classificationDetail'] != 'simple']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--allow-real-profile', action='store_true')
    args = parser.parse_args()
    output = outside_root(args.root.resolve(strict=True), args.out)
    manifest = build(build_inventory(args.root, allow_real_profile=args.allow_real_profile))
    raw = json.dumps(manifest, ensure_ascii=True, indent=2).encode('utf-8')
    _atomic_write(output, raw)
    print(json.dumps({'records': len(manifest['records']), 'sha256': hashlib.sha256(raw).hexdigest(), 'executable': False}))
