"""Real stdio provider: persist an owned effect, then exit before replying."""
import json
import os
from pathlib import Path
import sys

state = Path(sys.argv[1])
with state.with_suffix('.starts').open('a', encoding='utf-8') as log:
    log.write(f'{os.getpid()}\n')
for line in sys.stdin:
    request = json.loads(line)
    if request['method'] == 'provider.initialize':
        print(json.dumps({'id': request['id'], 'result': {'protocol_version': 'pan-capability/v1'}}), flush=True)
    else:
        with state.open('a', encoding='utf-8') as log:
            log.write(json.dumps(request['params']) + '\n')
            log.flush()
            os.fsync(log.fileno())
        os._exit(23)
