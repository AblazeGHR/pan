"""Synthetic stream provider that starts its configured stdio tool without a shell."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tests.support.fake_stream_cli import main

children = []
try:
    if '--mcp-config' in sys.argv:
        cfg = json.loads(Path(sys.argv[sys.argv.index('--mcp-config') + 1]).read_text(encoding='utf-8'))
        for item in cfg['mcpServers'].values():
            child = subprocess.Popen([item['command'], *item.get('args', [])], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=False,
                                     cwd=os.getcwd())
            children.append(child)
            child.stdin.write(b'{"jsonrpc":"2.0","id":1,"method":"initialize"}\n')
            child.stdin.flush()
            assert json.loads(child.stdout.readline())['id'] == 1
    main()
finally:
    for child in children:
        if child.stdin: child.stdin.close()
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate(); child.wait(timeout=3)
