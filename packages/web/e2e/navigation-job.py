"""Start/read Pan durable Jobs in a disposable registry, without a live Pan service."""
import json
import os
from pathlib import Path
import sys
import tempfile
sys.stdout.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
if sys.argv[1] == 'start':
    runtime = Path(tempfile.mkdtemp(prefix='pan-navigation-job-'))
    os.environ['PAN_BACKGROUND_JOBS_DIR'] = str(runtime / 'jobs')
    from packages.core import config, session
    config.CONFIG_FILE = runtime / 'config.json'
    config.CONFIG_FILE.write_text(json.dumps({'python': sys.executable, 'plugin_manifests': []}), encoding='utf-8')
    session.SESSION_DIR = runtime / 'sessions'
    target = session.create('Isolated navigation validation', adapter='codex', workdir=str(runtime))
    from packages.core import background_jobs
    job = background_jobs.start(target.id, sys.argv[2:], str(ROOT), registry_root=runtime / 'jobs', label='navigation-validation')
    print(json.dumps({'runtime': str(runtime), 'job': job}, ensure_ascii=False))
else:
    os.environ['PAN_BACKGROUND_JOBS_DIR'] = sys.argv[2]
    from packages.core import background_jobs
    job = background_jobs.get(sys.argv[3])
    print(json.dumps(job, ensure_ascii=False))
    if job:
        print(Path(job['logPath']).read_text(encoding='utf-8', errors='replace')[-16000:])
