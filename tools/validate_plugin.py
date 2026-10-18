"""Static plugin check: never starts providers, invokes tools, or writes data."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from packages.core.manifest_loader import load_manifests
from packages.plugins.catalog import Catalog
from packages.plugins.runtime import read_api_document


def inspect(paths):
    resolved = []
    for raw in paths:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            path /= 'manifest.json'
        if not path.is_file():
            raise ValueError(f'Manifest not found: {raw}')
        document = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(document, dict):
            raise ValueError(f'Manifest must be an object: {raw}')
        resolved.append(str(path))
    catalog = Catalog(load_manifests(resolved))
    rows = []
    for plugin in catalog.plugins.values():
        capabilities = None
        if plugin.source['kind'] == 'pan_api':
            capabilities = len(read_api_document(plugin)['capabilities'])
        rows.append({'id': plugin.id, 'source': plugin.source['kind'],
            'modes': list(plugin.supported_modes), 'panels': [p['id'] for p in plugin.panels],
            'capabilities': capabilities,
            'agentCapabilities': list(plugin.agent_capabilities) if plugin.agent_capabilities is not None else None})
    return {'ok': True, 'check': 'static-only', 'plugins': rows}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog='Relative manifest paths use the Pan repository root. No real CLI or network request is executed.')
    parser.add_argument('manifests', nargs='+', help='Manifest JSON files or directories; later declarations override earlier ones')
    parser.add_argument('--json', action='store_true', help='Print machine-readable summary (no commands, credentials or provider output)')
    args = parser.parse_args(argv)
    try:
        result = inspect(args.manifests)
    except Exception as exc:
        # Parser/schema errors can include input values. Do not echo credential-bearing documents.
        result = {'ok': False, 'check': 'static-only', 'error': type(exc).__name__,
                  'message': 'Validation failed. Check JSON syntax, local paths and the plugin/capability schemas.'}
    if args.json:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    elif result['ok']:
        print('Static validation passed (providers were not executed).')
        for row in result['plugins']:
            print(f"  {row['id']}: {row['source']}; modes={','.join(row['modes'])}; panels={','.join(row['panels']) or '-'}")
    else:
        print(f"{result['error']}: {result['message']}", file=sys.stderr)
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
