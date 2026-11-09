"""Owned HTTP assembly of the canonical runtime; never a product executor."""
import argparse
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import sys


class ConsumingProvider:
    """Consume only fixture targets and persist each actual consumption before receipt."""
    def __init__(self, root):
        self.root = root
        self.calls = []

    async def send_session(self, session_id, text, *, effect_key, source, **kwargs):
        if session_id not in ('owned-a', 'owned-b', 'owned-unknown'):
            return {'status': 'rejected', 'code': 'not_owned'}
        row = dict(sessionId=session_id, text=text, effectKey=dict(effect_key), source=source)
        with (self.root / 'consumed.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row) + '\n')
        self.calls.append(row)
        # Consumption happened, but its acknowledgement was lost.
        if session_id == 'owned-unknown':
            return {'status': 'unknown'}
        return {'status': 'succeeded', 'messageId': f'owned-{len(self.calls)}', 'consumption': 'confirmed'}


def app_for(root):
    from fastapi import FastAPI
    from packages.automation.service import Service
    from packages.jobs.final_api import CanonicalUserAuth, install
    from packages.jobs.final_service import bind
    from packages.jobs.action_api import router as actions
    from packages.jobs.api import router as jobs
    from types import SimpleNamespace
    root = Path(root).resolve()
    if not (root / '.jobs-owned-harness').is_file():
        raise ValueError('Explicit owned harness marker required')
    from packages.core import session, config, background_jobs
    from packages.core.platform import data_paths
    data_paths.DEFAULT_ROOT = background_jobs.DEFAULT_ROOT = root / 'background_jobs'
    os.environ['PAN_BACKGROUND_JOBS_DIR'] = os.environ['PAN_SCHEDULER_DIR'] = str(root / 'background_jobs')
    session.SESSION_DIR = root / 'sessions'
    config.CONFIG_FILE = root / 'config.json'
    config.CONFIG_FILE.write_text('{}', encoding='utf-8')
    for sid in ('owned-a', 'owned-b', 'owned-unknown'):
        session.save(session.Session(id=sid, name=sid, queue_paused=True))
    app = FastAPI()
    automation = Service(root / 'automation', SimpleNamespace(workers={}), None)
    automation.auth = CanonicalUserAuth(root / 'automation')
    service = bind(app, automation, root / 'canonical')
    provider = ConsumingProvider(root)
    # Keep the original default TypedExecutor identity: process dispatch must
    # continue selecting FinalService._external_adapter / ProcessOwner.
    service.executor.session_provider = provider
    app.state.automation = automation
    app.state.owned = SimpleNamespace(service=service, provider=provider, auth=automation.auth)
    install(app, actions, jobs)
    scripts = {}
    for name, tail in [('quick', ''), ('long', 'time.sleep(300)')]:
        script = root / (name + '.py')
        script.write_text("import os,time\nos.write(1,b'owned stdout\\n');os.write(2,b'owned stderr\\n')\n" + tail + '\n', encoding='utf-8')
        scripts[name] = [sys.executable, str(script)]

    @asynccontextmanager
    async def lifespan(app):
        await service.start()
        try:
            yield
        finally:
            # Cleanup only execution identities recorded inside this owned root.
            from packages.jobs.independent_executor import ExecutionStore, ExecutionHost
            store = ExecutionStore(service.root / 'executions')
            with store.connect() as db:
                documents = [json.loads(row[0]) for row in db.execute('SELECT document FROM executions')]
            try:
                for doc in documents:
                    if doc.get('result') is None:
                        ExecutionHost(store).cleanup_execution(doc['effectId'], doc['attempt'])
            finally:
                await service.close()
    app.router.lifespan_context = lifespan

    @app.get('/test/ready')
    def ready():
        from packages.jobs.final_service import object_ref
        refs = {a['id']: object_ref('action', a) for a in service.actions(actor={'type': 'user', 'id': 'local'})['items']
                if a['id'] in ('product_process_cleanup_v1', 'product_session_broadcast_v2')}
        return {'ready': True, 'token': automation.auth.login(automation.auth.key()),
                'actionRefs': refs, 'scripts': scripts, 'cwd': str(root), 'targets': ['owned-a', 'owned-b', 'owned-unknown'],
                'consumed': provider.calls, 'owner': service.owner.status(), 'diagnostics': service.diagnostics}
    return app


def main():
    import uvicorn
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', required=True, type=Path)
    args = parser.parse_args()
    if not args.root.is_absolute() or args.root.exists():
        raise SystemExit('A new absolute owned root is required')
    args.root.mkdir(parents=True)
    (args.root / '.jobs-owned-harness').touch()
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    if sock.getsockname()[1] in (8767, 8768):
        raise SystemExit('Protected port')
    print(json.dumps({'url': f'http://127.0.0.1:{sock.getsockname()[1]}', 'root': str(args.root)}), flush=True)
    uvicorn.Server(uvicorn.Config(app_for(args.root), log_level='warning')).run(sockets=[sock])


if __name__ == '__main__':
    main()
