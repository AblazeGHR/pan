"""Scheduler historical projection, without the retired store/runtime.

Original migration rows win over mutable legacy snapshots. No execution or
status inference occurs; opaque records remain inspectable evidence only.
"""
import base64
from datetime import datetime
import hashlib
import json
from pathlib import Path

from packages.jobs.migration_history import Archive

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / 'data' / 'background_jobs'


def data_root():
    from packages.core.platform.data_paths import job_root
    return job_root(DEFAULT_ROOT)


def _epoch(value):
    return datetime.fromtimestamp(value).replace(microsecond=0).isoformat() if isinstance(value, (int, float)) else value


def project(source, original_id):
    if not isinstance(source, dict):
        return {'id': original_id, 'status': 'unknown'}
    out = dict(source)
    out['id'] = original_id
    out.setdefault('runCount', 0)
    if isinstance(source.get('schedule'), list):
        entry = next(iter(source['schedule']), {})
        if not isinstance(entry, dict):
            raise ValueError('Archived schedule entry is not an object')
        out['schedule'] = {k: v for k, v in entry.items() if k not in {
            'id', 'enabled', 'nextFireAt', 'lastFireAt', 'misfirePolicy', 'graceSec'}}
    for key in ('createdAt', 'updatedAt'):
        if key in out:
            out[key] = _epoch(out[key])
    return out


class History:
    def __init__(self, canonical_root=None):
        self.root = data_root()
        self.archive = Archive(canonical_root or self.root.parent / 'job-final')

    def tasks(self, include_disabled=True):
        records = {}
        for kind, doc in self.archive.documents():
            if kind == 'migration_receipt':
                continue
            identity = doc['originalId']
            if identity in records:
                raise ValueError('Ambiguous original history identity')
            try:
                source = self.archive.source(doc)
            except (ValueError, UnicodeError):
                if kind != 'migration_unrecognized':
                    raise
                # Integrity failure is never downgraded to opaque evidence.
                raw = base64.b64decode(doc['rawBytesBase64'], validate=True)
                if 'sha256:' + hashlib.sha256(raw).hexdigest() != doc['sourceSha256']:
                    raise ValueError('Historical source bytes/hash mismatch')
                source = None
            task = project(source, identity)
            task['history'] = {**doc, 'source': source if isinstance(source, dict) else {}, 'recordType': 'historicalEvidenceOnly', 'executable': False}
            task['executable'] = False
            records[identity] = task
        # Inspect original files directly, never background_jobs.list_jobs or
        # scheduler.store (those may migrate, normalize or initialize storage).
        for path in sorted((self.root / 'jobs').glob('*.json')):
            raw = path.read_bytes()
            source = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(source, dict):
                raise ValueError('Archived task is not an object')
            if source.get('kind') != 'scheduled-task':
                continue
            identity = source.get('taskId') or source.get('jobId')
            if not identity:
                raise ValueError('Archived task has no original identity')
            if identity in records or any(t['history'].get('source', {}).get('taskId') == identity for t in records.values()):
                continue
            task = project(source, identity)
            task['history'] = {'originalId': identity, 'source': source, 'sourceSha256':
                'sha256:' + hashlib.sha256(raw).hexdigest(), 'rawBytesBase64': base64.b64encode(raw).decode(),
                'recordType': 'historicalEvidenceOnly', 'executable': False}
            task['executable'] = False
            records[identity] = task
        return sorted((t for t in records.values() if include_disabled or t.get('enabled')),
                      key=lambda t: (str(t.get('createdAt', t.get('created_at')) or ''), t['id']))

    def get(self, identity):
        matches = [t for t in self.tasks() if identity in {t['id'],
            t['history'].get('source', {}).get('taskId'), t['history'].get('source', {}).get('jobId')}]
        if len(matches) > 1:
            raise ValueError('Ambiguous original history identity')
        return matches[0] if matches else None

    def runs(self, task):
        source = task['history'].get('source', {})
        aliases = {value for value in (task['id'], source.get('taskId'), source.get('jobId')) if value}
        path = self.root / 'runs.jsonl'
        if not path.exists():
            return []
        rows = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
        if any(not isinstance(r, dict) for r in rows):
            raise ValueError('Archived run is not an object')
        return [r for r in reversed(rows) if r.get('task_id', r.get('taskId')) in aliases]

    def evidence(self, task):
        doc = task['history']
        raw = self.archive.log(doc)
        key = doc.get('source', {}).get('terminalEventId')
        receipts = [r for kind, r in self.archive.documents() if kind == 'migration_receipt' and r.get('originalKey') == key]
        if len(receipts) > 1:
            raise ValueError('Ambiguous original receipt')
        return {'log': None if raw is None else {'bytesBase64': base64.b64encode(raw).decode(),
                'sha256': hashlib.sha256(raw).hexdigest()}, 'receipt': receipts[0] if receipts else None}
