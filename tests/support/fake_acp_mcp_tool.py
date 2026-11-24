"""Short-lived owned stdio MCP fixture, not a real external tool."""
import json
import os
import subprocess
from pathlib import Path
import sys

marker = Path(sys.argv[1])
if '--private-check' in sys.argv:
    assert os.environ.get('ACP_SYNTHETIC_SECRET') == 'synthetic-only-value'
    assert 'synthetic-only-value' not in repr(sys.argv)
    marker.with_suffix('.pid').write_text(str(os.getpid()), encoding='ascii')
    clean_env = dict(os.environ)
    clean_env.pop('ACP_SYNTHETIC_SECRET', None)
    descendant = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'], env=clean_env)
    marker.with_suffix('.childpid').write_text(str(descendant.pid), encoding='ascii')
if '--no-private-check' in sys.argv:
    assert 'ACP_SYNTHETIC_SECRET' not in os.environ
marker.write_text('started', encoding='ascii')
for line in sys.stdin:
    msg = json.loads(line)
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{}}), flush=True)
marker.write_text('closed', encoding='ascii')
