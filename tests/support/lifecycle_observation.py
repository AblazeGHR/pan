"""Read existing canonical receipts without initializing or modifying their store."""
from pathlib import Path
import sqlite3
from contextlib import contextmanager

from packages.core import lifecycle_action
from packages.jobs.final_contracts import digest
from packages.jobs.independent_executor import ExecutionHost, ExecutionStore, effect_id


class ReadOnlyExecutionStore(ExecutionStore):
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / "executions.sqlite3"
        if not self.path.is_file():
            raise FileNotFoundError(self.path)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10,
                             isolation_level=None)
        try:
            db.execute("PRAGMA query_only=ON")
            yield db
        finally:
            db.close()


class LifecycleObservation:
    def __init__(self, root):
        self.host = ExecutionHost(ReadOnlyExecutionStore(lifecycle_action.receipt_root(root)))

    def read(self, *, request_id, root, port, old_pid, old_birth, operation, options):
        job, identity, plan = lifecycle_action.protected_plan(
            root=root, port=port, old_pid=old_pid, old_pid_created_at=old_birth,
            operation=operation, options=options, request_id=request_id)
        eid = effect_id(identity["effectKey"])
        doc = self.host.get_execution(eid)
        if (doc["effectId"] != eid or doc["jobId"] != job
                or doc["effectKey"] != identity["effectKey"]
                or doc["attempt"] != identity["attempt"]
                or doc["frozenPlan"] != plan or doc["planDigest"] != digest(plan)):
            raise ValueError("canonical lifecycle identity or frozen plan mismatch")
        return doc
