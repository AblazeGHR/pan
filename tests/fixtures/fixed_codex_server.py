"""Own app-server fixture: loads CODEX_HOME, contacts only owned loopback URL."""
import json
import os
from pathlib import Path
import sys
import tomllib
import urllib.request
from urllib.parse import urlsplit

path=Path(os.environ['CODEX_HOME'])/'config.toml'
config=tomllib.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
for index,arg in enumerate(sys.argv):
    if arg=='-c':
        key,value=sys.argv[index+1].split('=',1);parts=key.split('.');target=config
        for p in parts[:-1]:target=target.setdefault(p,{})
        target[parts[-1]]=json.loads(value)
for line in sys.stdin:
    req=json.loads(line);method=req.get('method');key=req.get('id')
    if key is None:continue
    if method=='config/read':
        provider=config['model_providers'][config['model_provider']];url=provider['base_url']
        assert urlsplit(url).hostname=='127.0.0.1'
        with urllib.request.urlopen(url,timeout=3) as response:assert response.read()==b'owned-provider'
        value={'config':config}
    elif method in ('thread/start','thread/resume'):
        assert req['params']['modelProvider']==config['model_provider']
        value={'thread':{'id':req['params'].get('threadId') or 'owned-thread'},
               'modelProvider':'ignored-pin' if os.environ.get('PAN_TEST_FIXED_WRONG_THREAD_PROVIDER') else config['model_provider']}
    elif method=='turn/start':value={'turn':{'id':'owned-turn','status':'inProgress'}}
    else:value={}
    print(json.dumps({'id':key,'result':value}),flush=True)
    if method=='turn/start':
        print(json.dumps({'method':'turn/completed','params':{'threadId':'owned-thread','turn':{'id':'owned-turn','status':'completed'}}}),flush=True)
