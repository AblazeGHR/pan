"""Fixed canonical one-shot runner. Does not import the R4 legacy runner."""
from __future__ import annotations

import argparse
import asyncio
import os
import time

from packages.actions.typed_executor import EffectReceipt, TypedExecutor
from packages.core.platform.execution_host import supervisor_identity
from packages.jobs.independent_executor import ExecutionConflict, ExecutionStore


async def execute_claimed(store, doc, *, executor=None):
    identity, attempt, claim = doc["effectId"], doc["attempt"], doc["claim"]
    try:
        if doc["frozenPlan"]["operation"] in {"service.restart", "service.exit"}:
            from packages.core.main_lifecycle import run_canonical_supervisor
            # This worker reads only a sealed local admission. The public
            # independent endpoint cannot admit a lifecycle operation.
            result = await asyncio.to_thread(run_canonical_supervisor, store, doc)
        else:
            store.phase(identity, attempt, claim, "executing")
            if executor is None:
                from packages.core.platform.process import default_process_owner
                owner = default_process_owner()

                class RecordingOwner:
                    cleanup_tree = None

                    def __getattr__(self, name):
                        return getattr(owner, name)

                    def spawn(self, argv, **kwargs):
                        env = {name: value for name, value in os.environ.items()
                               if not name.upper().startswith("PAN_") and name.upper() != "PYTHONPATH"}
                        return owner.spawn(argv, env=env, **kwargs)

                    def recoverable_identity(self, handle):
                        record = owner.recoverable_identity(handle)
                        store.phase(identity, attempt, claim, "running", processIdentity=record)
                        return record

                    def wait(self, handle, *, timeout):
                        deadline = time.monotonic() + timeout
                        while True:
                            request = store.get(identity).get("cleanupRequest", {})
                            if request.get("status") == "unknown":
                                # Even if the root subsequently exits, keep
                                # the original tree handle for explicit recheck.
                                # Normal wait/finally must not retry a partially
                                # observed destructive cleanup automatically.
                                time.sleep(.05)
                                continue
                            waited = owner.wait(handle, timeout=min(.05, max(0, deadline - time.monotonic())))
                            if request.get("status") != "requested" and (waited.exited or time.monotonic() >= deadline):
                                return waited
                            if request.get("status") != "requested":
                                continue
                            proof = {"confirmed": False, "rootExited": False}
                            try:
                                record = owner.recoverable_identity(handle)
                                check = owner.verify(record)
                                proof["identityCheck"] = check.as_dict()
                                if check.ours is not True or check.status.value not in {"alive", "dead"}:
                                    raise RuntimeError("original process identity is unknown or reused")
                                tree = owner.kill_tree(handle, timeout=5)
                                proof["ownedTree"] = tree.as_dict()
                                root = owner.wait(handle, timeout=5)
                                proof.update(rootExited=root.exited,
                                             confirmed=root.exited and tree.confirmed is True)
                                if proof["confirmed"]:
                                    self.cleanup_tree = tree
                            except Exception as exc:
                                proof["error"] = str(exc)
                            store.finish_cleanup(identity, attempt, claim, request["revision"], proof)

                    def kill_tree(self, handle, *, timeout):
                        tree = self.cleanup_tree or owner.kill_tree(handle, timeout=timeout)
                        store.phase(identity, attempt, claim, "cleaning", treeCleanupProof=tree.as_dict())
                        return tree

                executor = TypedExecutor(process_owner=RecordingOwner(), process_timeout=86400)
            result = await executor.execute(attempt=attempt, effect_key=doc["effectKey"], frozen_plan=doc["frozenPlan"])
            tree_proof = store.get(identity).get("evidence", {}).get("treeCleanupProof")
            if tree_proof is not None:
                result.setdefault("cleanupProof", {})["ownedTree"] = tree_proof
        return store.finish(identity, attempt, claim, result)
    except ExecutionConflict:
        # A pre-effect cancellation may win the phase transaction. A stale
        # worker is never allowed to overwrite a newer fence or receipt.
        return store.get(identity)
    except Exception as exc:
        result = EffectReceipt(doc["effectKey"], "unknown", {
            "code": "supervisor_error", "message": str(exc), "phase": "uncertain"},
            {"confirmed": False}).as_dict()
        return store.finish(identity, attempt, claim, result)


def run(root, identity, bootstrap_receipt=None):
    store = ExecutionStore(root)
    doc = store.get(identity)
    try:
        doc = store.claim(identity, doc["attempt"], supervisor_identity(), bootstrap_receipt=bootstrap_receipt)
    except ExecutionConflict:
        return 1
    asyncio.run(execute_claimed(store, doc))
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--effect-id", required=True)
    parser.add_argument("--bootstrap-receipt")
    parser.add_argument("--bootstrap-nonce")
    args = parser.parse_args()
    receipt = None
    if args.bootstrap_receipt is not None:
        from packages.core.platform.execution_host import read_bootstrap_receipt
        try:
            receipt = read_bootstrap_receipt(args.bootstrap_receipt, args.bootstrap_nonce)
        except Exception:
            store = ExecutionStore(args.root)
            store.launch_failed(args.effect_id, store.get(args.effect_id)["attempt"])
            return 1
    return run(args.root, args.effect_id, receipt)


if __name__ == "__main__":
    raise SystemExit(main())
