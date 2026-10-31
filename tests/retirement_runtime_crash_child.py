"""Owned hard-exit harness: real committed runtime fences and native queues."""
import asyncio
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys

from packages.core import session
from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_service import FinalService
from packages.actions.typed_executor import WorkerAssignProvider

cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
from packages.core import config, background_jobs, session_error_log
from packages.scheduler import store as scheduler_store
from packages.core.platform import data_paths
from packages.core.terminal import registry
owned = Path(cfg['sessions']).parent
config.CONFIG_FILE = owned / 'config.json'
background_jobs.DEFAULT_ROOT = scheduler_store.DEFAULT_ROOT = data_paths.DEFAULT_ROOT = owned / 'background_jobs'
registry.DEFAULT_REGISTRY_ROOT = owned / 'terminals'
session_error_log._path_override = owned / 'logs/session-errors.jsonl'
session.SESSION_DIR = Path(cfg['sessions'])
session._cache.clear()
session._all_loaded = False
session.list_all(load_history=False)  # production startup metadata bootstrap


def crash():
    Path(cfg['barrier']).write_text(cfg['phase'], encoding='utf-8')
    os._exit(71)


class CrashService(FinalService):
    boundary = False

    def _save(self, db, kind, object_id, value):
        FinalService._save(db, kind, object_id, value)
        if kind != 'job' or object_id != cfg['job']:
            return
        reserved = value.get('finalRuntime', {}).get('plans', {}).get(cfg['run'], {}).get('runtimeDelivery', {}).get('send', {})
        children = reserved.get('children', [])
        expected = {'before-reservation': 'never-attempted', 'before-invoke': 'sending', 'after-receipt': 'accepted'}.get(cfg['phase'])
        if expected and children and children[0]['status'] == expected:
            self.boundary = True

    @contextmanager
    def transaction(self, *, readonly=False):
        with super().transaction(readonly=readonly) as db:
            yield db
        if not readonly and self.boundary:
            crash()  # AFTER actual SQLite commit, never a synthetic snapshot


class LostAfterEnqueue(WorkerAssignProvider):
    async def assign(self, *args, **kwargs):
        result = await super().assign(*args, **kwargs)
        assert result['status'] in {'pending', 'queued'}
        crash()


service = CrashService(Path(cfg['root']), CanonicalUserAuth(Path(cfg['auth'])))
status = service.owner.status()
service.owner.claim(status['epoch'] if status else 0)
if cfg['phase'] == 'after-enqueue':
    service.executor.assign_provider = LostAfterEnqueue()
asyncio.run(service.dispatch_once())
raise AssertionError('The requested real crash boundary was not reached: ' + json.dumps(service.final.get(cfg['job'])))
