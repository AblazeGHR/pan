"""ProcessOwner adapter for the existing headless emulator's resource protocol.

A POSIX group is not Windows Job containment and is not durable. All destructive
operations delegate to the original strong-birth owner, including dead-leader
refusal. No PID/PGID-only fallback, and close never grants signal authority.
"""
import subprocess
import time
from packages.core.platform.process import OwnershipUnknownError
from packages.core.platform.process_posix import PosixProcessOwner
from packages.core.terminal.contracts import ProcessIdentity


class PosixEmulatorProcess:
    def __init__(self,argv,*,cwd):
        self.owner=PosixProcessOwner()
        self.owned=self.owner.spawn(argv,cwd=cwd,stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.proc=self.owned.popen
        value=self.owned.identity
        self.identity=ProcessIdentity(pid=value.pid,
            native_identity=(value.platform,value.kind,value.birth,value.boot_id or ''))
        self.handle_open=True
        self._cleared=False

    def verified_cleanup(self,proc,pid,identity,*,budget):
        outcome={'root_killed':False,'root_dead':False,'job_terminate_called':False,
            'job_owned':None,'job_verified':False,'remaining':None,'errors':[]}
        if proc is not self.proc or pid!=self.owned.pid or not self.identity.matches(identity):
            outcome['errors'].append('posix-owner-identity-mismatch')
            return outcome
        if self._cleared:
            outcome.update(root_dead=True,job_verified=True,remaining=[])
            return outcome
        try:
            started=time.monotonic()
            # Graceful shutdown ACK may precede retained root exit. Observe the
            # original handle before any possible signal; this grants no rights.
            self.owner.wait(self.owned,timeout=max(0.,min(budget,.1)))
            remaining_budget=max(0.,budget-(time.monotonic()-started))
            result=self.owner.kill_tree(self.owned,timeout=min(remaining_budget,2.5))
            outcome.update(root_killed=self.proc.returncode is not None,
                root_dead=self.proc.returncode is not None,job_terminate_called=True,
                job_owned=list(result.owned),remaining=list(result.remaining),
                job_verified=result.confirmed)
            self._cleared=result.confirmed and outcome['root_dead']
        except Exception as error:
            outcome['errors'].append('posix-cleanup:'+type(error).__name__)
        return outcome

    def close(self):
        if not self._cleared:
            raise OwnershipUnknownError('Emulator root/group/member clearance unconfirmed')
        self.owned.close()
        self.handle_open=False
        return True
