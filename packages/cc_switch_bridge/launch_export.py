"""Private, versioned cc-switch launch export consumed by the existing spawner.

No DB/live-config fallback. Current upstream has no capability; default off.
Secrets exist only in the owner-only bundle and owned process environment.
"""
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import shutil
import stat
import time
import tomllib

CONTRACT='pan-cc-switch-launch/v1'
FLAG='PAN_CODEX_CC_SWITCH_PRIVATE_EXPORT'
MAX_BUNDLE=128*1024


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode('utf-8')

def mac(key,receipt,raw):
    return hmac.new(bytes.fromhex(key),canonical(receipt)+b'\n'+raw,hashlib.sha256).hexdigest()


def read_private(path,root):
    from packages.core.terminal import win_pipe
    from packages.core.terminal.secret_store import SecretStore
    if win_pipe.is_reparse_point(str(path.parent)) or win_pipe.is_reparse_point(str(path)):
        raise ValueError('Export reparse point')
    info=path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1 or info.st_size>MAX_BUNDLE:
        raise ValueError('Invalid private export file')
    SecretStore(root=root).verify_owner_only(path)
    with path.open('rb') as stream:raw=stream.read(MAX_BUNDLE+1)
    if len(raw)>MAX_BUNDLE:raise ValueError('Private export oversized')
    return raw


def decode(raw):
    def unique(pairs):
        result={}
        for key,value in pairs:
            if key in result:raise ValueError('Duplicate private export field')
            result[key]=value
        return result
    return json.loads(raw.decode('utf-8'),object_pairs_hook=unique)


def validate(receipt,raw,request,now):
    """Exact closed grammar. No raw helper error/material is returned to callers."""
    fields={'contract','requestId','receiptId','ownerEpoch','providerId','providerRevision',
            'selectionRevision','credentialSource','issuedAt','expiresAt','bundleMac'}
    if not isinstance(receipt,dict) or set(receipt)!=fields:raise ValueError('Invalid export receipt fields')
    if receipt['contract']!=CONTRACT or receipt['requestId']!=request['requestId']:
        raise ValueError('Export request mismatch')
    for key in ('receiptId','ownerEpoch'):
        if not isinstance(receipt[key],str) or not re.fullmatch('[0-9a-f]{32}',receipt[key]):raise ValueError('Invalid export identity')
    from packages.automation.quota_identity import identifier
    if not identifier(receipt['providerId']):raise ValueError('Invalid export provider')
    for key in ('providerRevision','selectionRevision'):
        if type(receipt[key]) is not int or not 1<=receipt[key]<2**63:raise ValueError('Invalid export revision')
    source=receipt['credentialSource']
    if (not isinstance(source,dict) or set(source)!={'kind','revision'}
            or source['kind'] not in ('none','provider-api-key') or type(source['revision']) is not int
            or not 1<=source['revision']<2**63):raise ValueError('Unsupported credential source')
    issued=receipt['issuedAt'];expires=receipt['expiresAt']
    if (type(issued) is not int or type(expires) is not int
            or not request['createdAt']<=issued<=now+2000 or not now<expires<=issued+30000):
        raise ValueError('Export receipt expired or invalid')
    signature=receipt['bundleMac'];unsigned={k:v for k,v in receipt.items() if k!='bundleMac'}
    if not isinstance(signature,str) or not hmac.compare_digest(signature,mac(request['receiptKey'],unsigned,raw)):
        raise ValueError('Private export authentication failed')
    bundle=decode(raw)
    if (not isinstance(bundle,dict) or set(bundle)!={'contract','requestId','config','credential'}
            or bundle['contract']!=CONTRACT or bundle['requestId']!=request['requestId']
            or not isinstance(bundle['config'],str) or len(bundle['config'].encode('utf-8'))>64*1024):
        raise ValueError('Invalid private bundle')
    credential=bundle['credential']
    if not isinstance(credential,dict):raise ValueError('Invalid export credential')
    if source['kind']=='none':
        if credential!={'kind':'none'}:raise ValueError('Unexpected credential material')
    else:
        token=credential.get('token')
        if (set(credential)!={'kind','token'} or credential['kind']!='bearer-env' or not isinstance(token,str)
                or not 16<=len(token)<=16384 or not re.fullmatch(r'[A-Za-z0-9._~+/\-]+=*',token)):
            raise ValueError('Unsupported export credential')
    return bundle


def prepare(session,root,state):
    from .provider import Bridge
    from packages.core.adapters.codex import provider_launch as launch
    from packages.core.terminal import win_pipe
    from packages.core.terminal.secret_store import SecretStore
    from packages.automation.quota_policy import Store
    from packages.core.legal_offline import SERVICE_ID
    root=Path(root).resolve()
    bridge=Bridge(root/'plugins'/'cc-switch-bridge')
    if not bridge.private_launch_supported():return None
    launch_id=secrets.token_hex(16);key=secrets.token_hex(32)
    home=root/'automation'/'private-launches'/launch_id
    if any(win_pipe.is_reparse_point(str(p)) for p in (home.parent.parent,home.parent,home)):
        raise ValueError('Private export path is a reparse point')
    win_pipe.create_owner_only_directory(str(home))
    try:
        request={'contract':CONTRACT,'requestId':launch_id,'receiptKey':secrets.token_hex(32),
                 'app':'codex','selection':{'kind':'current'},'createdAt':int(time.time()*1000)}
        win_pipe.create_file_exclusive_owner_only(str(home/'request.json'),canonical(request))
        receipt=bridge.private_launch_export(home/'request.json')
        raw=read_private(home/'bundle.json',root)
        bundle=validate(receipt,raw,request,int(time.time()*1000))
        current=Store(root/'automation').read()
        if current['epoch']!=state['epoch'] or not current['host']['enabled'] or receipt['providerId'] in current['disabledAccountIds']:
            raise ValueError('Export host admission changed')
        config=tomllib.loads(bundle['config'])
        if set(config)!={'model_provider','model_providers'}:raise ValueError('Unsupported export configuration')
        pin=launch.projection(config)
        if set(config['model_providers'])!={pin['model_provider']}:raise ValueError('Ambiguous export configuration')
        pin['model_provider']='pan_fixed_'+launch_id
        if bundle['credential']['kind']=='bearer-env':
            from urllib.parse import urlsplit
            target=urlsplit(pin['provider']['base_url'])
            if target.scheme!='https' and target.hostname not in ('127.0.0.1','::1'):
                raise ValueError('Insecure credential endpoint')
            pin['provider']['env_key']='PAN_CODEX_LAUNCH_TOKEN_'+launch_id.upper()
        config_raw=bundle['config'].encode('utf-8')
        manifest={'launchId':launch_id,'key':key,'pin':pin,'configDigest':hashlib.sha256(config_raw).hexdigest(),
                  'privateBundleDigest':hashlib.sha256(raw).hexdigest(),
                  'exportAudit':{k:receipt[k] for k in ('contract','ownerEpoch','providerRevision','selectionRevision','credentialSource','issuedAt','expiresAt')}}
        win_pipe.create_file_exclusive_owner_only(str(home/'config.toml'),config_raw)
        win_pipe.create_file_exclusive_owner_only(str(home/'launch.json'),canonical(manifest))
        verifier=SecretStore(root=root)
        for p in (home,home/'request.json',home/'bundle.json',home/'config.toml',home/'launch.json'):verifier.verify_owner_only(p)
        pending=launch.Pending(home,receipt['providerId'],receipt['receiptId'],manifest,state['epoch'],SERVICE_ID)
        return pending
    except BaseException:
        # The protocol requires synchronous owner retirement, including on IPC
        # timeout; an owner may never recreate this path after cancellation.
        # Delete only this allocated child; never follow a returned path.
        if home.parent.resolve()!=(root/'automation'/'private-launches').resolve():raise RuntimeError('Private cleanup path escaped')
        shutil.rmtree(home)
        raise


def credential(home,manifest):
    """Recheck the private snapshot before safe environment-only handoff."""
    raw=read_private(Path(home)/'bundle.json',Path(home).parents[2])
    if hashlib.sha256(raw).hexdigest()!=manifest['privateBundleDigest']:raise ValueError('Private bundle changed')
    return decode(raw)['credential']
