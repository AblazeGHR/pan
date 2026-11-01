"""Bounded, sanitized current-run ownership diagnostics for platform CI."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys

from packages.core.platform.process import default_process_owner
from packages.core.platform.execution_host import supervisor_exit_evidence


def _identity(raw):
    if not isinstance(raw, dict):
        return None
    return {key: raw[key] for key in ('pid', 'kind', 'birth', 'platform', 'boot_id', 'pgid') if key in raw}


def _proof(raw):
    if not isinstance(raw, dict):
        return None
    result = {key: raw[key] for key in ('confirmed', 'rootExited', 'identityVerified',
              'treeTerminated', 'effectStarted') if key in raw}
    tree = raw.get('ownedTree')
    if isinstance(tree, dict):
        result['ownedTree'] = {key: tree[key][:64] if isinstance(tree.get(key), list) else tree[key]
                               for key in ('owned', 'remaining', 'confirmed') if key in tree}
    check = raw.get('identityCheck')
    if isinstance(check, dict):
        result['identityCheck'] = {key: check[key] for key in ('status', 'ours') if key in check}
        result['identityCheck']['expected'] = _identity(check.get('expected'))
        result['identityCheck']['observed'] = _identity(check.get('observed'))
    if 'error' in raw:
        # Do not persist exception strings: they may contain local argv/paths.
        result['errorPresent'] = True
    return result


def save_process_proof(label, *, documents=(), identities=(), checks=None):
    """Called before teardown so a cleanup assertion cannot erase diagnostics."""
    owner = default_process_owner()
    rows = []
    for doc in list(documents)[:4]:
        result = doc.get('result') or {}
        receipt = result.get('receipt') or {}
        cleanup = doc.get('cleanupRequest') or {}
        evidence = doc.get('evidence') or {}
        attempt = doc.get('attempt') or {}
        rows.append({'effectId': doc.get('effectId'), 'phase': doc.get('phase'),
            'attempt': {key: attempt[key] for key in ('attemptId', 'leaseId', 'ownerEpoch', 'generation') if key in attempt},
            'outcome': result.get('outcome'), 'receiptCode': receipt.get('code'),
            'processIdentity': _identity(evidence.get('processIdentity') or receipt.get('processIdentity')),
            'supervisorIdentity': _identity(doc.get('supervisorIdentity')),
            'supervisorExited': supervisor_exit_evidence(doc['supervisorIdentity']).get('confirmed') if doc.get('supervisorIdentity') else None,
            'cleanupRevision': cleanup.get('revision'), 'cleanupStatus': cleanup.get('status'),
            'cleanupRequestProof': _proof(cleanup.get('proof')), 'cleanupProof': _proof(result.get('cleanupProof'))})
    probes = []
    for raw in list(identities)[:16]:
        try:
            check = owner.verify(raw)
            probes.append({'expected': _identity(raw), 'status': check.status.value,
                           'ours': check.ours, 'observed': _identity(check.observed.as_dict()) if check.observed else None})
        except Exception as exc:
            probes.append({'expected': _identity(raw), 'probeErrorType': type(exc).__name__})
    name = ''.join(c if c.isalnum() or c in '-_' else '_' for c in str(label))[:100]
    target = Path('evidence/cp-x1-ci/cp-x2-proof')
    target.mkdir(parents=True, exist_ok=True)
    path = target / (name + '-' + hashlib.sha256(str(label).encode()).hexdigest()[:8] + '.json')
    value = {'platform': sys.platform, 'python': sys.version.split()[0], 'executions': rows, 'probes': probes,
             'checks': {key: value for key, value in (checks or {}).items()
                        if isinstance(value, (bool, int)) or (isinstance(value, list) and not value)}}
    encoded = json.dumps(value, indent=2)
    if len(encoded.encode('utf-8')) > 32768:
        raise AssertionError('proof diagnostic exceeded fixed budget')
    path.write_text(encoded + '\n', encoding='utf-8')
    return path
