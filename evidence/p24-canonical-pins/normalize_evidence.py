"""Preserve raw pytest output; remove trailing spaces only from review copies."""
import hashlib
import json
from pathlib import Path
import re
import zipfile

root = Path(__file__).resolve().parent
rows = []
if (root / 'raw-output.zip').exists():
    raise FileExistsError('Refusing to overwrite original evidence archive')
with zipfile.ZipFile(root / 'raw-output.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(root.iterdir()):
        if path.suffix not in {'.log', '.xml'}:
            continue
        raw = path.read_bytes()
        archive.writestr(path.name, raw)
        review = re.sub(rb'[ \t]+(?=\r?$)', b'', raw, flags=re.MULTILINE)
        if review != raw:
            path.write_bytes(review)
        rows.append({'path': path.name, 'rawSha256': hashlib.sha256(raw).hexdigest(),
                     'reviewSha256': hashlib.sha256(review).hexdigest(), 'changed': review != raw})
(root / 'artifact-whitespace.json').write_text(json.dumps(rows, indent=2) + '\n', encoding='utf-8')
