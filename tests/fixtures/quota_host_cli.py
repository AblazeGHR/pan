"""Deterministic cc-switch read fixture. No Pan import, auth or network access."""
import json
import os
from pathlib import Path
import sys
import time

args=sys.argv[1:]
path=Path(os.environ['PAN_TEST_QUOTA_HOST_TRACE'])
with path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(args)+'\n')
now=float(os.environ.get('PAN_TEST_QUOTA_HOST_NOW',int(time.time())))
if args==['auth','status','--json']:
    value={'default_account_id':'managed-a','accounts':[
        {'id':'managed-a','login':'private-label','access_token':'fixture-secret-must-not-escape'},
        {'id':'managed-b','refresh_token':'fixture-refresh-must-not-escape'}]}
elif args==['--app','codex','provider','list','--json']:
    value={'currentProviderId':'provider-a','providers':[
        {'id':'provider-a','category':'official','apiUrl':'https://secret@invalid.test'},
        {'id':'provider-b','category':'official'},{'id':'script-c','category':'third_party'}]}
elif args[:4]==['--app','codex','provider','quota'] and args[-1]=='--json' and args[4] in ('provider-a','provider-b'):
    value={'app':'codex','providerId':args[4],'status':'ok','available':True,'queriedAt':now*1000,
           'result':{'type':'subscription','quota':{'success':True,'tiers':[
               {'name':'five_hour','utilization':25,'resetsAt':now+1800},
               {'name':'seven_day','utilization':70,'resetsAt':now+86400}]}}}
else:raise SystemExit(17)
print(json.dumps(value))
