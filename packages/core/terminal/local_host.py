"""Non-durable in-process client adapter; no second registry or transport secret."""
from .backend_posix import PosixPtyBackend
from .contracts import OwnershipMode, OwnershipPolicy
from .ownership import build_runtime


class LocalTerminalHost:
    def __init__(self,terminal_id,argv,*,cwd,rows,cols):
        from .emulator import HeadlessEmulator
        from .runner import RunnerEmulatorBridge
        self.rows,self.cols=rows,cols
        self.backend=self.runtime=None
        self.emulator=HeadlessEmulator(rows=rows,cols=cols)
        self.bridge=RunnerEmulatorBridge(self.emulator)
        try:
            self.backend=PosixPtyBackend.spawn(argv,cwd=cwd,rows=rows,cols=cols)
            self.runtime=build_runtime(terminal_id,self.backend,
                ownership=OwnershipPolicy(mode=OwnershipMode.SERVICE,lifecycle_owner='pan-service',
                    tree_guard_kind='posix-process-group',tree_guard=self.backend.guard),
                identity=self.backend.identity,identity_probe=self.backend.probe,
                output_consumer=self.bridge)
            self.runtime.start(rows=rows,cols=cols,gate=self.backend.gate)
        except BaseException as error:
            # Keep the composite owner if any component cannot prove cleanup.
            try:report=self.close(reason='startup-failed')
            except BaseException:
                error.terminal_host=self
            else:
                if not report.ok:error.terminal_host=self
            raise

    def describe(self):
        info=self.runtime.exit
        return {'runner_state':self.runtime.state.value,'host_mode':'local-posix',
            'durable':False,'total_bytes':self.runtime.log.total_bytes,
            'first_retained_seq':self.runtime.log.first_retained_seq,
            'emulator':self.emulator.diagnostics(),
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
        if accepted:
            self.rows,self.cols=rows,cols
            self.bridge.resize(rows,cols)
            engine_confirmed=self.emulator.resize_wait(rows,cols,timeout=1.)
        else:engine_confirmed=False
        return {'status':'ok' if accepted else 'rejected','rows':rows,'cols':cols,
            'describe':{'emulator_resize':{'ok':engine_confirmed}}}

    def snapshot(self,*,timeout_ms=5000):
        from .contracts import Recovery
        snap=self.emulator.snapshot(timeout=max(0.,timeout_ms/1000.))
        diagnostics=self.emulator.diagnostics()
        lag=bool(snap.feed_lag or self.bridge.gap_seen or self.runtime.consumer_failed)
        reset=diagnostics.get('reset_unconfirmed')
        baseline=diagnostics.get('baseline_cursor')
        # Applied cursor only. No producer total, reset or guessed screen parsing.
        valid=(self.emulator.cursors_valid is True and reset is False and not lag
            and snap.recovery in (Recovery.FULL,Recovery.PARTIAL)
            and (baseline is None or int(baseline)<=snap.cursor))
        return {'status':self.runtime.state.value,'serialized_screen':snap.serialized_screen,
            'cursor':snap.cursor,'rows':snap.rows,'cols':snap.cols,
            'fidelity':snap.fidelity.value,'recovery':'degraded' if lag else snap.recovery.value,
            'feed_lag':lag,'note':snap.note,'engine':snap.engine,
            'cursors_valid':valid,'reset_unconfirmed':reset,'diagnostics':diagnostics}

    def close(self,*,reason):
        from types import SimpleNamespace
        if self.runtime is not None:
            runtime_report=self.runtime.close(reason=reason,interrupt=False)
            if not runtime_report.ok:return runtime_report
        elif self.backend is not None:
            try:self.backend.terminate(True);self.backend.close()
            except Exception:return SimpleNamespace(ok=False)
        engine_report=self.emulator.close()
        return SimpleNamespace(ok=engine_report.closed,emulator=engine_report)

    def release_connection(self):
        # Browser disconnect releases only original attachment leases.
        return None


class LocalStartupResource:
    """Retain an original failed-spawn resource without pretending it is attached."""
    def __init__(self,resource,*,backend=None):
        self.resource=resource
        self.backend=backend

    def close(self,*,reason):
        from types import SimpleNamespace
        if self.backend is not None:
            self.backend.terminate(True)
            self.backend.close()
            return SimpleNamespace(ok=True)
        result=self.resource.retry_cleanup(timeout=3.)
        return SimpleNamespace(ok=result.get('closed') is True)
