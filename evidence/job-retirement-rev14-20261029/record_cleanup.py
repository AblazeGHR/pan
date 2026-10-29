"""Read-only resource observations; deliberately performs no deletion."""
import hashlib
import json
from pathlib import Path
import socket

out = Path(__file__).resolve().parent
root = out.parents[1]
proof = root / 'evidence/cp-x1-ci/cp-x2-proof'
files = [{'path': p.relative_to(root).as_posix(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
          'bytes': p.stat().st_size} for p in sorted(proof.glob('*.json'))]
with socket.socket() as sock:
    sock.settimeout(1)
    listening = sock.connect_ex(('127.0.0.1', 57386)) == 0
assert not listening, 'Owned browser port remains reachable'
link = root / 'packages/web/node_modules'
result = {
    'ownedBrowserPort': 57386, 'tcpConnectableAfterTests': listening,
    'browserAndServer': 'Test finally closes Chromium, joins uvicorn thread and closes socket; assertions passed',
    'coldServices': 'Test finally closes each cold FinalService',
    'processHosts': 'Original fixture cleanup; real process diagnostics retained below',
    'dependencyJunctionRemaining': str(link.resolve()),
    'cleanupObstacle': 'Automatic approval review rejected combined cleanup and separate Remove-Item junction: blocked by policy; no workaround attempted',
    'noPackagesInstalled': True,
    'preservedGeneratedEvidence': files,
    'outsideScopeOperations': 'No existing services, real data, deployment, main/practical, push or remote CI'
}
(out / 'cleanup.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
print(json.dumps({'ownedPortReachable': listening, 'preservedDiagnosticFiles': len(files),
                  'diagnosticBytes': sum(f['bytes'] for f in files)}))
