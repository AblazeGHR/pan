"""Record actual canonical API admission with detached launch disabled.

This is a diagnostic, not a lifecycle successor or a fake success receipt.
No listener, service, provider, or product file is touched.
"""
import asyncio
import gc
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import psutil
from fastapi import HTTPException

from packages.core import lifecycle_action, worker
from packages.jobs import independent_executor
from packages.jobs.independent_executor import ExecutionStore
from packages.web import server
from tests.support.lifecycle_observation import LifecycleObservation


async def probe(root, operation, options, strategy):
    launches, snapshots, gates = [], [], []
    sessions = [SimpleNamespace(id="running-a", last_legal_worker_state="idle",
        execution_operations={"run": {"state": "running", "safeTerminal": False}})]
    def snapshot(**kwargs):
        snapshots.append(True)
        return sessions
    with patch.object(server, "_PROJECT_DIR", root), \
         patch.object(server, "_main_restart_port", lambda: 48321), \
         patch.object(server, "_canonical_lifecycle_pending", {}), \
         patch.object(server, "_session_lifecycle_preferences", lambda config=None: {"exitStrategy": strategy, "startupPreference": "ask"}), \
         patch.object(server.sess, "list_all", snapshot), \
         patch.object(worker, "begin_shutdown", lambda: gates.append(True)), \
         patch.object(independent_executor, "launch_execution", lambda root, eid: launches.append(eid)):
        try:
            payload = {"operation": operation}
            if options is not None:
                payload["options"] = options
            first = await server.api_system_main_lifecycle(payload)
            second = await server.api_system_main_lifecycle(payload)
        except HTTPException as exc:
            return {"operation": operation, "strategy": strategy, "httpStatus": exc.status_code,
                    "detail": exc.detail, "launchCount": len(launches), "snapshotCount": len(snapshots), "gateCount": len(gates)}
        store = ExecutionStore(lifecycle_action.receipt_root(root))
        doc = store.get(first["effectId"])
        persisted = doc["frozenPlan"]["arguments"]["options"]
        observed = LifecycleObservation(root).read(request_id=first["requestId"], root=root,
            port=48321, old_pid=os.getpid(), old_birth=psutil.Process().create_time(),
            operation=operation, options=persisted)
        assert observed == doc and first["effectId"] == second["effectId"]
        return {"operation": operation, "strategy": strategy, "inputOptions": options,
                "accepted": first["accepted"], "sameEffectOnDuplicate": first["effectId"] == second["effectId"],
                "requestId": first["requestId"], "effectId": first["effectId"],
                "storedOptions": persisted, "phase": observed["phase"], "result": observed["result"],
                "launchCount": len(launches), "snapshotCount": len(snapshots), "gateCount": len(gates)}


async def main():
    cases = [("exit", None, "ask"), ("restart", None, "ask"),
             ("exit", {"markRunningSessionsOffline": False}, "offline"),
             ("restart", {"markRunningSessionsOffline": True}, "preserve-running")]
    with tempfile.TemporaryDirectory(prefix="cpx1-lifecycle-observe-") as path:
        root = Path(path)
        rows = []
        for index, case in enumerate(cases):
            case_root = root / str(index)
            case_root.mkdir()
            rows.append(await probe(case_root, *case))
        # ExecutionStore's sqlite connection context commits but does not close;
        # collect those fixture-only connections before TemporaryDirectory exit.
        gc.collect()
    target = Path("evidence/cp-x1-lifecycle-v2-20261029/transition-observation.json")
    target.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
