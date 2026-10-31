"""Preserve this gate's generated timing before restoring historical evidence."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
rel = 'evidence/job-recovery-r4/default-detached.json'
path = ROOT / rel
before = subprocess.check_output(['git', 'show', 'HEAD:' + rel], cwd=ROOT)
after = path.read_bytes()
a, b = json.loads(before), json.loads(after)
assert {k: v for k, v in a.items() if k != 'originalStableElapsed'} == {
    k: v for k, v in b.items() if k != 'originalStableElapsed'}
assert not (HERE / 'recovery-generated-default-detached.json').exists()
(HERE / 'recovery-generated-default-detached.json').write_bytes(after)
path.write_bytes(before)
(HERE / 'generated-evidence-preservation.json').write_text(json.dumps({
    'sourceSha': 'f5f05ca5be51e3dbcd1bc4b4cd5b29ebe0ab1597',
    'testProducer': 'tests/test_job_recovery_stable_reset.py:422',
    'originalPath': rel, 'onlyChangedField': 'originalStableElapsed',
    'historicalEvidenceRestoredAfterPreservingNewBytes': True,
    'originalSha256': hashlib.sha256(before).hexdigest(),
    'generatedSha256': hashlib.sha256(after).hexdigest(),
}, indent=2) + '\n', encoding='utf-8')
