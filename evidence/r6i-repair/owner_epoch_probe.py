"""Reproduce the effect receipt fence at epoch 2, with no source migration.

Run against either checkout through its run_tests_isolated.py. Only the local
SessionProvider test double executes. Exit 1 preserves the observed red gate.
"""
import asyncio
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from packages.jobs.final_api import CanonicalUserAuth
from packages.jobs.final_runtime.executor import TypedExecutor
from packages.jobs.final_service import FinalService, object_ref
from packages.jobs.runtime_owner import OwnerFence
from tests.test_automation_canonical import USER, SessionProvider, job_def


def run(root):
    first = OwnerFence(root)
    first.claim(0)
    first.set_state("active")
    first.release()
    owner = OwnerFence(root)
    owner.claim(1)
    owner.set_state("active")
    provider = SessionProvider()
    service = FinalService(root / "canonical", CanonicalUserAuth(root / "auth"),
        executor=TypedExecutor(session_provider=provider), owner=owner)
    try:
        service._initialize_schema()
        service._install_product_actions()
        action = next(a for a in service.actions(actor=USER)["items"] if a["id"] == "product_session_message")
        job = service.write("job", None, {"definition":job_def(SimpleNamespace(action=object_ref("action", action))),
            "expectedRevision":0, "requestKey":"job"}, actor=USER)["result"]["object"]
        job = service.control("job", job["id"], {"operation":"enable", "expectedRevision":1,
            "expectedStateRevision":0, "requestKey":"enable", "parameters":{}}, actor=USER)["result"]["object"]
        service.create_run(job["id"], {"expectedRevision":1, "requestKey":"run", "input":{}, "cause":{"type":"manual"}}, actor=USER)
        try:
            asyncio.run(service.dispatch_once())
        except Exception as exc:
            attempts = service.final.store.get(job["id"])["finalRuntime"]["attempts"]
            return {"gate":"blocked", "code":getattr(exc,"code",None), "message":str(exc),
                    "runtimeEpoch":owner.token[0], "persistedAttemptEpochs":[a["ownerEpoch"] for a in attempts.values()],
                    "providerCalls":len(provider.calls), "sourceMigrationUsed":False}
        attempts = service.final.store.get(job["id"])["finalRuntime"]["attempts"]
        assert len(provider.calls) == 1 and len(attempts) == 1
        assert next(iter(attempts.values()))["ownerEpoch"] == owner.token[0] == 2
        asyncio.run(service.dispatch_once())
        assert len(provider.calls) == 1
        return {"gate":"passed", "runtimeEpoch":owner.token[0], "persistedAttemptEpochs":[a["ownerEpoch"] for a in attempts.values()], "providerCalls":len(provider.calls), "sourceMigrationUsed":False}
    finally:
        owner.release()


if __name__ == "__main__":
    with TemporaryDirectory(prefix="pan-r6i-epoch-") as directory:
        result = run(Path(directory))
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))
    raise SystemExit(result["gate"] != "passed")
