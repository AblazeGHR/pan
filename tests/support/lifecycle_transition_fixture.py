"""Read-only canonical projection for unchanged legacy observation assertions.

The test subprocess uses a fixed non-service port and suppresses detached
launch. It does not simulate supervisor completion or write a legacy registry.
"""
import asyncio
import json

import pytest

from packages.core import background_jobs, lifecycle_action
from packages.jobs import independent_executor
from packages.web import server
from tests.support.lifecycle_observation import LifecycleObservation, ReadOnlyExecutionStore


@pytest.fixture(autouse=True)
def canonical_observation(monkeypatch):
    monkeypatch.setattr(server, "_canonical_lifecycle_pending", {})
    monkeypatch.setattr(server, "_lifecycle_operation_lock", asyncio.Lock())
    monkeypatch.setattr(server, "_main_restart_port", lambda: 48321)
    monkeypatch.setattr(independent_executor, "launch_execution", lambda root, effect_id: None)
    def lookup(request_id, registry_root=None):
        try:
            store = ReadOnlyExecutionStore(lifecycle_action.receipt_root(server._PROJECT_DIR))
            with store.connect() as db:
                docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM executions")]
        except FileNotFoundError:
            return None
        for doc in docs:
            args = doc["frozenPlan"]["arguments"]
            if args["requestId"] == request_id:
                persisted = LifecycleObservation(server._PROJECT_DIR).read(
                    request_id=request_id, root=server._PROJECT_DIR, port=args["port"],
                    old_pid=args["oldPid"], old_birth=args["oldPidCreatedAt"],
                    operation=doc["frozenPlan"]["operation"].removeprefix("service."),
                    options=args["options"])
                return {"options": persisted["frozenPlan"]["arguments"]["options"]}
        return None
    monkeypatch.setattr(background_jobs, "find_service_job", lookup)
