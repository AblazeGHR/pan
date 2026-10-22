"""Deterministic cc-switch read fixture. No Pan import, auth or network access."""
import json
import os
from pathlib import Path
import sys

args=sys.argv[1:]
path=Path(os.environ['PAN_TEST_QUOTA_HOST_TRACE'])
with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(args)+'\n')
if args!=['auth','status','--json']:
    raise SystemExit(17)
print(json.dumps({'default_account_id':'managed-a','accounts':[
    {'id':'managed-a','login':'private-label','access_token':'fixture-secret-must-not-escape'},
    {'id':'managed-b','refresh_token':'fixture-refresh-must-not-escape'}]}))
