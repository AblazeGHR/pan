"""Non-durable in-process client adapter; no second registry or transport secret."""
from .backend_posix import PosixPtyBackend
from .contracts import OwnershipMode, OwnershipPolicy
from .ownership import build_runtime


class LocalTerminalHost:
    def __init__(self,terminal_id,argv,*,cwd,rows,cols):
        self.backend=PosixPtyBackend.spawn(argv,cwd=cwd,rows=rows,cols=cols)
        self.rows,self.cols=rows,cols
        self.runtime=build_runtime(terminal_id,self.backend,
            ownership=OwnershipPolicy(mode=OwnershipMode.SERVICE,lifecycle_owner='pan-service',
                tree_guard_kind='posix-process-group',tree_guard=self.backend.guard),
            identity=self.backend.identity,identity_probe=self.backend.probe)
        try:self.runtime.start(rows=rows,cols=cols,gate=self.backend.gate)
        except BaseException as error:
            error.terminal_host=self
            raise

    def describe(self):
        info=self.runtime.exit
        return {'runner_state':self.runtime.state.value,'host_mode':'local-posix',
            'durable':False,'total_bytes':self.runtime.log.total_bytes,
            'first_retained_seq':self.runtime.log.first_retained_seq,
            'exit':{'seen':info.process_exit_seen,'code':info.code,
                'reader_done':info.reader_done,'output_complete':info.output_complete}}

    def read(self,cursor=0,*,max_bytes=None):
        page=self.runtime.read_from(cursor,max_bytes=max_bytes)
        data=b''.join(chunk.data for chunk in page.chunks)
        return {'data':data,'seq':page.first_seq,'size':len(data),
            'next_cursor':page.next_cursor,'total_bytes':self.runtime.log.total_bytes,
            'first_retained_seq':self.runtime.log.first_retained_seq,
            'gap':page.gap,'truncated':page.truncated,
            'status':self.runtime.state.value,'describe':self.describe()}

    def input(self,data):
        count=self.runtime.write(data)
        return {'ok':count==len(data),'status':'ok' if count==len(data) else 'partial','size':count}

    def resize(self,rows,cols):
        accepted=self.runtime.resize(rows,cols)
        if accepted:self.rows,self.cols=rows,cols
        return {'status':'ok' if accepted else 'rejected','rows':rows,'cols':cols,
            'describe':{'emulator_resize':None}}

    def snapshot(self,*,timeout_ms=5000):
        return {'status':'unavailable','serialized_screen':None,'cursor':None,
            'rows':self.rows,'cols':self.cols,'fidelity':'unavailable','recovery':'degraded',
            'feed_lag':False,'note':'local-host-emulator-unavailable','engine':'',
            'cursors_valid':False,'reset_unconfirmed':None,
            'diagnostics':{'reasons':['emulator-host-unavailable']}}

    def close(self,*,reason):
        return self.runtime.close(reason=reason,interrupt=False)

    def release_connection(self):
        # Browser disconnect releases only original attachment leases.
        return None
