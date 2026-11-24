"""Short-lived owned stdio MCP fixture, not a real external tool."""
import json
from pathlib import Path
import sys

marker = Path(sys.argv[1])
marker.write_text('started', encoding='ascii')
for line in sys.stdin:
    msg = json.loads(line)
    print(json.dumps({'jsonrpc':'2.0','id':msg['id'],'result':{}}), flush=True)
marker.write_text('closed', encoding='ascii')
