"""Synthetic upstream protocol owner; no installed cc-switch/profile access."""
import json
from pathlib import Path
import secrets
import threading
import time

from packages.cc_switch_bridge.launch_export import CONTRACT, canonical, mac
from packages.core.terminal.win_pipe import create_file_exclusive_owner_only
from tests.test_provider_launch import export

class Owner:
    def __init__(self,url,credential=True):
        self.url=url;self.credential=credential;self.lock=threading.Lock()
        self.provider='provider-a';self.revision=1;self.epoch=secrets.token_hex(16)
        self.tokens={'provider-a':secrets.token_urlsafe(32)}
        self.calls=[];self.entered=threading.Event();self.release=threading.Event();self.release.set()
        self.mutate=None;self.supported=True;self.after=None

    def switch(self):
        with self.lock:
            self.provider='provider-b';self.revision+=1
            self.tokens['provider-b']=secrets.token_urlsafe(32)

    def __call__(self,args):
        self.calls.append(list(args))
        if args==['--version']:
            return {'contract':'pan-cc-switch-gui/v1',**({'privateLaunchExport':CONTRACT} if self.supported else {})}
        assert args[:4]==['--app','codex','provider','launch-export'] and args[-1]=='--json'
        request_path=Path(args[4]);request=json.loads(request_path.read_text(encoding='utf-8'))
        with self.lock:
            provider=self.provider;revision=self.revision;token=self.tokens[provider]
        self.entered.set();assert self.release.wait(5),'synthetic owner barrier timeout'
        bundle={'contract':CONTRACT,'requestId':request['requestId'],
                'config':export(self.url+'/'+provider)['config'],
                'credential':{'kind':'bearer-env','token':token} if self.credential else {'kind':'none'}}
        now=int(time.time()*1000)
        receipt={'contract':CONTRACT,'requestId':request['requestId'],'receiptId':secrets.token_hex(16),
                 'ownerEpoch':self.epoch,'providerId':provider,'providerRevision':revision,'selectionRevision':revision,
                 'credentialSource':{'kind':'provider-api-key' if self.credential else 'none','revision':revision},
                 'issuedAt':now,'expiresAt':now+30000}
        if self.mutate:self.mutate(receipt,bundle)
        raw=canonical(bundle)
        create_file_exclusive_owner_only(str(request_path.parent/'bundle.json'),raw)
        # A real upstream must recheck all provider/default/credential revisions
        # under the shared mutation coordinator, not only a global current ID.
        with self.lock:
            if self.provider!=provider or self.revision!=revision:raise ValueError('Synthetic export revision conflict')
        receipt['bundleMac']=mac(request['receiptKey'],receipt,raw)
        if self.after:self.after(receipt,request_path)
        return receipt


def install(monkeypatch,adapter,url,credential=True):
    from packages.cc_switch_bridge import provider as module
    from packages.cc_switch_bridge.launch_export import FLAG
    bridge=module.Bridge
    owner=Owner(url,credential)
    monkeypatch.delattr(adapter,'provider_launch_source')
    monkeypatch.setenv(FLAG,'1')
    monkeypatch.setattr(module,'Bridge',lambda directory:bridge(directory,runner=owner))
    return owner
