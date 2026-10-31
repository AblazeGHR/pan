"""Summarize completed immutable gates without replacing preserved raw reds."""
import json
from pathlib import Path
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
records = json.loads((HERE / 'commands.json').read_text(encoding='utf-8'))['checks']
assert len(records) == 9
assert all(r['exit'] == 0 for r in records), 'Report failures explicitly before candidate delivery'
backend = [r for r in records if r.get('junit')]
totals = {k: sum(r['counts'][k] for r in backend)
          for k in ('tests', 'passed', 'failures', 'errors', 'skipped')}
matrix = json.loads((HERE / 'current43-matrix.json').read_text(encoding='utf-8'))
assert len(matrix['executed']) == totals['tests'], 'Do not double count repeated backend nodes'
assert all(n['status'] == 'passed' for n in matrix['executed'].values())
browser = ET.parse(HERE / 'browser.xml')
observations = []
for node in browser.iter('system-out'):
    for line in (node.text or '').splitlines():
        if line.startswith('R25_BROWSER='):
            observations.append(json.loads(line.removeprefix('R25_BROWSER=')))
assert len(observations) == 1
(HERE / 'browser-observation.json').write_text(json.dumps(observations[0], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
lines = ['| Gate | Actual result | Exit | Seconds |', '| --- | --- | ---: | ---: |']
for r in sorted(records, key=lambda c: c['gate']):
    c = r.get('counts')
    result = (f"{c['passed']} passed / {c['failures']} failed / {c['errors']} error / {c['skipped']} skip"
              if c else 'TypeScript + Vite production build completed')
    lines.append(f"| `{r['gate']}` | {result} | {r['exit']} | {r['elapsedSeconds']:.2f} |")
summary = '\n'.join(lines) + f"\n\nBackend: **{totals['passed']} distinct exact nodes passed**, " \
    f"{totals['failures']} failed / {totals['errors']} error / {totals['skipped']} skip. " \
    'React counts are separate from backend nodes; development reruns are excluded. ' \
    f"Fresh Chromium version: `{observations[0]['chromium']}`. " \
    'Every command, interpreter, raw output hash and immutable source SHA is in `commands.json`.\n\n' \
    'Owned HTTP/WS listeners, pooled clients, browser and crash children were closed/waited by fixture finalizers. ' \
    'The scoped post-gate process observation is `resource-cleanup.json`; no real service was stopped. ' \
    'Owned dependency archive/store are retained as reusable build data.\n\n'
path = ROOT / 'docs/reports/JOB_RETIRE_REV25_QQ_PENDING_WS_20261031.md'
text = path.read_text(encoding='utf-8')
marker = 'Final gate counts and cleanup are filled after completion. '
assert marker in text
path.write_text(text.replace(marker, summary), encoding='utf-8')
print(json.dumps({'backend': totals, 'react': next(r['counts'] for r in records if r['gate'] == 'react'),
                  'chromium': observations[0]['chromium']}))
