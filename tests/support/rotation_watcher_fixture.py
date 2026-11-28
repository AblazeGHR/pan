"""Run the real watcher main with a strictly local synthetic provider bridge."""
import json
import sys
import time
from pathlib import Path
from packages.cc_switch_bridge import provider
from scripts import codex_limit_rotate

class FakeBridge:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = self
    def gates(self, app):
        assert app == 'codex'
        return {}
    def read_only(self, operation, args, *, deadline=None):
        state = self.root / 'current.json'
        current = json.loads(state.read_text()) if state.exists() else 'synthetic-a'
        if operation == 'list':
            return {'currentProviderId':current, 'providers':[
                {'id':'synthetic-a','category':'official'}, {'id':'synthetic-b','category':'official'}]}
        assert operation == 'quota'
        return {'id':args['id'], 'app':'codex', 'quota':{'status':'ok', 'available':True,
            'queriedAt':int(time.time()*1000), 'tiers':[
                {'name':'five_hour','usedPercent':100 if args['id']=='synthetic-a' else 20,'resetsAt':time.time()+200},
                {'name':'seven_day','usedPercent':20,'resetsAt':time.time()+400}]}}
    def invoke(self, operation, args):
        assert operation == 'auto_switch' and args['expectedCurrentId']=='synthetic-a'
        path = self.root / 'current.json'
        assert not path.exists() or json.loads(path.read_text()) == 'synthetic-a'
        path.write_text(json.dumps(args['id']))
        with (self.root / 'switches.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'from':'synthetic-a','to':args['id']})+'\n')
        return {'currentProviderId':args['id']}

provider.Bridge = FakeBridge
raise SystemExit(codex_limit_rotate.main())
