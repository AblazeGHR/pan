"""Real Pan lifespan in an isolated empty data root; no provider/plugin work."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--root', type=Path, required=True)
parser.add_argument('--port', type=int, required=True)
args = parser.parse_args()
assert 1024 <= args.port <= 65535 and args.port != 8768

from packages.core import config, session
config.CONFIG_FILE = args.root / 'config.json'
session.SESSION_DIR = args.root / 'sessions'
session._cache.clear()
session._all_loaded = False

from packages.web import server
server.DATA_DIR = args.root
server.WORKDIRS_DIR = args.root / 'workdirs'
server.ATTACHMENTS_DIR = args.root / 'attachments'

import uvicorn
host = uvicorn.Server(uvicorn.Config(server.app, host='127.0.0.1', port=args.port,
                                   log_level='warning', access_log=False))
server.app.state.pan_uvicorn_server = host
host.run()
