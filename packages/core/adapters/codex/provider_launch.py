"""Fixed, credentialless launch config + native config/read attestation.

Only a trusted in-code export source can supply provider identity. No HTTP/
Session verified flag, global-profile read, or provider-ID inference. Current
Bridge has no export capability, so ordinary production spawns stay unbound.
"""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import threading
import time
import tomllib

DOMAIN='cc-switch-provider:codex'
ENV='PAN_CODEX_FIXED_LAUNCH'


def enabled():return os.environ.get('PAN_CODEX_FIXED_PROVIDER_LAUNCH')=='1'


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def projection(config):
    key=config.get('model_provider');providers=config.get('model_providers')
    if not isinstance(key,str) or not isinstance(providers,dict) or not isinstance(providers.get(key),dict):
        raise ValueError('Fixed provider missing')
    p=providers[key]
    # First supported mode has no credential/keyring/environment fallback. A
    # credentialed official provider needs an upstream private export/attestation.
    allowed={'name','base_url','wire_api','requires_openai_auth'}
    if (set(p)-allowed or p.get('requires_openai_auth') is not False
            or p.get('wire_api')!='responses' or not isinstance(p.get('base_url'),str)):
        raise ValueError('Credentialed or unsupported provider config')
    from urllib.parse import urlsplit
    url=urlsplit(p['base_url'])
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Unsafe provider URL')
    return {'model_provider':key,'provider':{k:p[k] for k in ('base_url','wire_api','requires_openai_auth')}}


def native_projection(config):
    """Accept only observed inert native defaults, never credential fallback.

    Codex 0.163 config/read serializes optional ProviderConfig fields as null
    and capability flags as false. The trusted export still uses projection's
    strict input grammar. New native fields/nonempty auth remain unconfirmed.
    """
    key=config.get('model_provider');providers=config.get('model_providers')
    if not isinstance(key,str) or not isinstance(providers,dict) or not isinstance(providers.get(key),dict):
        raise ValueError('Fixed provider missing')
    p=dict(providers[key])
    nullable={'model_catalog_url','env_key','env_key_instructions','experimental_bearer_token',
              'auth','gateway_oauth','aws','capabilities','query_params','http_headers','env_http_headers',
              'request_max_retries','stream_max_retries','stream_idle_timeout_ms','websocket_connect_timeout_ms'}
    for field in nullable:
        if field in p:
            if p[field] is not None:raise ValueError('Nonempty native provider extension')
            del p[field]
    for field in ('supports_websockets','supports_standalone_web_search'):
        if field in p:
            if p[field] is not False:raise ValueError('Unsupported native provider capability')
            del p[field]
    return projection({'model_provider':key,'model_providers':{key:p}})


def prepare(adapter,session,root):
    """Called off-loop. Source is host code, not adapter_config or caller data."""
    if not enabled():return None
    from packages.automation.quota_policy import Store
    state=Store(Path(root)/'automation').read()
    source=getattr(adapter,'provider_launch_source',None)
    if (not state['host']['enabled'] or not callable(source) or adapter.name!='codex'
            or session.adapter_config.get('native_control')):return None
    if os.name!='nt':return None  # retained FILETIME + existing owner-only ACL
    export=source(session)
    if export is None:return None
    from packages.automation.quota_identity import identifier
    if (not isinstance(export,dict) or set(export)!={'providerId','exportReceiptId','config'}
            or not identifier(export['providerId']) or not identifier(export['exportReceiptId'])
            or export['providerId'] in state['disabledAccountIds']
            or not isinstance(export['config'],str) or len(export['config'].encode('utf-8'))>64*1024):
        raise ValueError('Invalid trusted export')
    config=tomllib.loads(export['config'])
    # No credential snapshot, inherited MCP/tools, includes, scripts or mutable
    # config references. Pan's existing explicit -c model/MCP options still apply.
    if set(config)-{'model_provider','model_providers'}:raise ValueError('Unsupported fixed config')
    pin=projection(config)
    if set(config['model_providers'])!={pin['model_provider']}:raise ValueError('Ambiguous fixed provider')
    launch_id=secrets.token_hex(16);key=secrets.token_hex(32)
    from packages.core.legal_offline import SERVICE_ID
    pin['model_provider']='pan_fixed_'+launch_id  # no merge with mutable global provider definitions
    home=Path(root)/'automation'/'private-launches'/launch_id
    from packages.core.terminal import win_pipe
    if any(win_pipe.is_reparse_point(str(p)) for p in (home.parent.parent,home.parent,home)):
        raise ValueError('Private launch path is a reparse point')
    win_pipe.create_owner_only_directory(str(home))
    try:
        manifest={'launchId':launch_id,'key':key,'pin':pin,'configDigest':hashlib.sha256(export['config'].encode()).hexdigest()}
        win_pipe.create_file_exclusive_owner_only(str(home/'config.toml'),export['config'].encode('utf-8'))
        win_pipe.create_file_exclusive_owner_only(str(home/'launch.json'),json.dumps(manifest).encode('utf-8'))
        from packages.core.terminal.secret_store import SecretStore
        verifier=SecretStore(root=root)
        for p in (home,home/'config.toml',home/'launch.json'):verifier.verify_owner_only(p)
        return Pending(home,export['providerId'],export['exportReceiptId'],manifest,state['epoch'],SERVICE_ID)
    except BaseException:
        shutil.rmtree(home);raise


class Pending:
    def __init__(self,home,provider,reference,manifest,epoch,runtime_epoch):
        self.home=home;self.provider=provider;self.reference=reference;self.manifest=manifest
        self.epoch=epoch;self.guard=None;self.native_handle=None;self.wrapper_handle=None;self.receipt=None;self.owner=None
        self._close_lock=threading.Lock()
        self.runtime_epoch=runtime_epoch

    def environment(self):
        env=dict(os.environ)
        for key in list(env):
            if key.startswith(('OPENAI_','AZURE_OPENAI_')) or key==ENV:env.pop(key)
        env[ENV]=str(self.home/'launch.json')
        return env

    def attach(self,process):
        from packages.core.terminal.guard import JobObjectGuard
        self.guard=JobObjectGuard()
        native=process._transport.get_extra_info('subprocess')
        self.guard.assign(int(native._handle))

    async def accept(self,process):
        from packages.automation.quota_host import retained_process
        from packages.core.terminal import identity
        attaching=asyncio.create_task(asyncio.to_thread(self.attach,process))
        try:await asyncio.shield(attaching)
        except asyncio.CancelledError:
            await attaching
            raise
        hello=json.loads(await asyncio.wait_for(process.stdout.readline(),8))
        signature=hello.pop('signature',None)
        if (hello.get('type')!='provider.launch.bootstrap' or hello.get('launchId')!=self.manifest['launchId']
                or not isinstance(signature,str) or not hmac.compare_digest(signature,
                    hmac.new(bytes.fromhex(self.manifest['key']),json.dumps(hello,sort_keys=True).encode(),hashlib.sha256).hexdigest())):
            raise RuntimeError('Bootstrap rejected')
        wrapper=hello['wrapperProcess']
        # Windows venv Python can be a launcher with a second interpreter PID.
        # Verify and enroll the authenticated interpreter before allowing children.
        import psutil
        if wrapper['pid']!=process.pid and process.pid not in [p.pid for p in psutil.Process(wrapper['pid']).parents()]:
            raise RuntimeError('Bootstrap is not an owned descendant')
        handle=identity.kernel32().OpenProcess(0x100000|0x1000|0x100|0x1,False,wrapper['pid'])
        try:
            proof=identity.probe_handle(handle,wrapper['pid'])
            if proof.status.value!='alive' or str(proof.identity.created_at_filetime)!=wrapper['birthToken']:
                raise RuntimeError('Bootstrap identity changed')
            if not self.guard.is_member(int(handle)):self.guard.assign(int(handle))
            self.wrapper_handle=handle;handle=None
        finally:
            if handle:identity.close_handle_checked(handle)
        process.stdin.write((self.manifest['launchId']+'\n').encode());await process.stdin.drain()
        for expected in ('provider.launch.started','provider.launch.verified'):
            line=await asyncio.wait_for(process.stdout.readline(),8)
            if len(line)>16*1024:raise RuntimeError('Launch receipt oversized')
            row=json.loads(line)
            signature=row.pop('signature',None)
            if (row.get('type')!=expected or row.get('launchId')!=self.manifest['launchId']
                    or row.get('wrapperPid')!=wrapper['pid']
                    or not isinstance(signature,str) or not hmac.compare_digest(signature,
                        hmac.new(bytes.fromhex(self.manifest['key']),json.dumps(row,sort_keys=True).encode(),hashlib.sha256).hexdigest())):
                raise RuntimeError('Launch receipt rejected')
            native=row.get('nativeProcess')
            if not isinstance(native,dict) or type(native.get('pid')) is not int:raise RuntimeError('Native identity missing')
            if expected.endswith('started'):started_native=native
            elif native!=started_native:raise RuntimeError('Native generation changed during proof')
            if self.native_handle is None:self.native_handle=identity.open_process_for_probe(native['pid'])
            proof=identity.probe_handle(self.native_handle,native['pid'])
            if (proof.status.value!='alive' or str(proof.identity.created_at_filetime)!=native.get('birthToken')):
                raise RuntimeError('Native identity changed')
            if not self.guard.is_member(self.native_handle):raise RuntimeError('Native process is outside launch owner')
            if expected.endswith('verified'):
                if row.get('projectionDigest')!=digest(self.manifest['pin']):raise RuntimeError('Native config mismatch')
                from packages.automation.quota_identity import identifier
                if row.get('modelProvider')!=self.manifest['pin']['model_provider'] or not identifier(row.get('nativeThreadId')):
                    raise RuntimeError('Native thread provider mismatch')
                owner=retained_process(process)
                if owner is None:raise RuntimeError('Wrapper identity unknown')
                self.receipt={'version':1,'identityDomain':DOMAIN,'providerId':self.provider,
                    'runtimeEpoch':self.runtime_epoch,
                    'launchId':self.manifest['launchId'],'exportReceiptId':self.reference,
                    'configDigest':self.manifest['configDigest'],'projectionDigest':row['projectionDigest'],
                    'processIdentity':owner,'wrapperProcessIdentity':wrapper,
                    'nativeProcessIdentity':native,'nativeThreadId':row['nativeThreadId'],'verifiedAt':time.time()}
        self.manifest.clear()  # secret capability remains only in private file until cleanup

    def close(self):
        with self._close_lock:self._close()

    def _close(self):
        from packages.core.terminal.identity import close_handle_checked
        if self.guard:
            _,remaining=self.guard.terminate_tree(None,timeout=2)
            if remaining:raise RuntimeError('Fixed launch cleanup unconfirmed')
            if not self.guard.close():raise RuntimeError('Launch Job close unconfirmed')
            self.guard=None
        if self.native_handle:
            if not close_handle_checked(self.native_handle):raise RuntimeError('Native handle close unconfirmed')
            self.native_handle=None
        if self.wrapper_handle:
            if not close_handle_checked(self.wrapper_handle):raise RuntimeError('Wrapper handle close unconfirmed')
            self.wrapper_handle=None
        if self.home.exists():shutil.rmtree(self.home)


def bootstrap():
    """Trusted wrapper only, before any child spawn or stdin reader thread."""
    path=os.environ.get(ENV)
    if not path:return
    m=json.loads(Path(path).read_text(encoding='utf-8'))
    from packages.core.terminal.win_pipe import current_process_identity
    own=current_process_identity()
    hello={'type':'provider.launch.bootstrap','launchId':m['launchId'],
           'wrapperProcess':{'pid':os.getpid(),'birthToken':str(own.created_at_filetime)}}
    hello['signature']=hmac.new(bytes.fromhex(m['key']),json.dumps(hello,sort_keys=True).encode(),hashlib.sha256).hexdigest()
    print(json.dumps(hello),flush=True)
    if sys.stdin.readline().strip()!=m['launchId']:raise RuntimeError('Launch start rejected')


def options():
    """Provider-only immutable CLI overrides; never replace native CODEX_HOME."""
    path=os.environ.get(ENV)
    if not path:return []
    m=json.loads(Path(path).read_text(encoding='utf-8'));pin=m['pin'];alias=pin['model_provider']
    pairs={'model_provider':alias,**{f'model_providers.{alias}.{key}':value for key,value in pin['provider'].items()},
           f'model_providers.{alias}.name':'Pan fixed provider'}
    return [arg for key,value in pairs.items() for arg in ('-c',key+'='+json.dumps(value))]


def attest(app,emit):
    path=os.environ.get(ENV)
    if not path:return
    m=json.loads(Path(path).read_text(encoding='utf-8'))
    config=Path(path).parent/'config.toml'
    if hashlib.sha256(config.read_bytes()).hexdigest()!=m['configDigest']:raise RuntimeError('Launch file changed')
    from packages.automation.quota_host import retained_process
    native=retained_process(app.process)
    if native is None:raise RuntimeError('Native retained identity missing')
    def send(kind,**fields):
        row={'type':kind,'launchId':m['launchId'],'wrapperPid':os.getpid(),'nativeProcess':native,**fields}
        row['signature']=hmac.new(bytes.fromhex(m['key']),json.dumps(row,sort_keys=True).encode(),hashlib.sha256).hexdigest()
        emit(row)
    send('provider.launch.started')
    reply=app._wait_response(app._request('config/read',{'includeLayers':False}))
    actual=native_projection(reply['result']['config'])
    if actual!=m['pin']:raise RuntimeError('Native fixed configuration not confirmed')
    app._fixed_launch_proof=(send,digest(actual))


def thread_params(app,params):
    if os.environ.get(ENV):params['modelProvider']=app.config['model_provider']


def attest_thread(app,result):
    if not os.environ.get(ENV):return
    pin=app.config['model_provider']
    if (result.get('modelProvider')!=pin or (result.get('thread') or {}).get('id')!=app.thread_id):
        raise RuntimeError('Thread did not accept fixed provider')
    if app._initial_thread_id and app.thread_id!=app._initial_thread_id:
        raise RuntimeError('Resumed thread identity changed')
    send,projection_digest=app._fixed_launch_proof
    send('provider.launch.verified',projectionDigest=projection_digest,modelProvider=pin,nativeThreadId=app.thread_id)


def candidate(w,session,epoch,process_identity):
    pending=getattr(w.process,'_provider_launch',None)
    binding=getattr(w,'_provider_launch_binding',None)
    if not pending or not binding or pending.owner is not w:return None
    operation=session.execution_operations.get(binding['launchId'],{})
    record=operation.get('providerLaunchBinding')
    if (operation.get('kind')!='provider-launch' or operation.get('state')!='completed' or operation.get('safeTerminal') is not True
            or operation.get('serviceId')!=epoch or operation.get('token')!=w.offline_token
            or record!=binding or binding['runtimeEpoch']!=epoch or binding['sessionId']!=w.session_id
            or binding['workerId']!=w.worker_id or binding['generation']!=w.generation
            or binding['processIdentity']!=process_identity):return None
    from packages.core.terminal.identity import probe_handle
    native=binding['nativeProcessIdentity'];proof=probe_handle(pending.native_handle,native['pid'])
    if proof.status.value!='alive' or str(proof.identity.created_at_filetime)!=native['birthToken']:return None
    wrapper=binding['wrapperProcessIdentity'];proof=probe_handle(pending.wrapper_handle,wrapper['pid'])
    if proof.status.value!='alive' or str(proof.identity.created_at_filetime)!=wrapper['birthToken']:return None
    return binding['providerId']
