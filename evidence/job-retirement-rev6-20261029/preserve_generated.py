"""Move this batch's fixture diagnostics under its versioned evidence root.

All inputs were absent or Git-clean at batch start. Preserve newly generated
bytes first, then restore only the tracked evidence those tests overwrote.
No product, profile, process or other worktree is touched.
"""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
changed = subprocess.check_output(['git','diff','--name-only'],cwd=ROOT,text=True).splitlines()
allowed = {'evidence/cp-x2-windows-hidden-20261028/proof/atomic-membership.json',
    'evidence/cp-x2-windows-hidden-20261028/proof/creation-edge-hard-death.json',
    'evidence/p23-retained-safety/public-resume/venv/canonical-delete-unknown-false.json',
    'evidence/p23-retained-safety/public-resume/venv/canonical-delete-unknown-true.json',
    'evidence/p23-retained-safety/public-resume/venv/canonical-late-terminal.json',
    'evidence/quota-native/tcp.json'}
assert set(changed) <= allowed, changed
rows = []
for relative in changed:
    path = ROOT / relative
    assert path.resolve().is_relative_to(ROOT) and path.is_file()
    data = path.read_bytes()
    saved = OUT / 'generated' / relative
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_bytes(data)
    original = subprocess.check_output(['git','show','HEAD:'+relative],cwd=ROOT)
    path.write_bytes(original)
    rows.append({'originalPath':relative,'preservedAt':str(saved.relative_to(ROOT)).replace('\\','/'),
                 'generatedSha256':hashlib.sha256(data).hexdigest(),
                 'restoredGitBytesSha256':hashlib.sha256(original).hexdigest()})
# New diagnostics from existing fixture helpers were not present at start.
new = ROOT / 'evidence/cp-x1-ci'
if new.exists():
    assert new.resolve().is_relative_to(ROOT / 'evidence')
    assert not subprocess.check_output(['git','ls-files','--',str(new)],cwd=ROOT).strip()
    for path in list(new.rglob('*')):
        if path.is_file():
            saved = OUT / 'generated' / path.relative_to(ROOT)
            saved.parent.mkdir(parents=True,exist_ok=True)
            path.replace(saved)
    for path in sorted(new.rglob('*'),key=lambda p:len(p.parts),reverse=True):
        if path.is_dir(): path.rmdir()
    new.rmdir()
(OUT/'generated-artifacts.json').write_text(json.dumps(rows,indent=2)+'\n',encoding='utf-8')
print('Preserved and restored only test-generated tracked artifacts:',len(rows))
