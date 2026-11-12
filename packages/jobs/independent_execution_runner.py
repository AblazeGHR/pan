"""Fixed canonical one-shot runner. Does not import the R4 legacy runner."""
from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import threading
import time
from contextlib import ExitStack

from packages.actions.typed_executor import EffectReceipt, TypedExecutor
from packages.core.platform.execution_host import supervisor_identity
from packages.jobs.independent_executor import ExecutionConflict, ExecutionStore, MAX_MONITORING_OUTPUT_BYTES


_MAX_COMPLETION_BYTES = MAX_MONITORING_OUTPUT_BYTES  # Old command provider OUTPUT_LIMIT.


class _CompletionOutput:
    """Drain stdout without retaining more than the old provider bound.

    The original log still receives stdout; stderr never enters this proof
    buffer. No file path or process-supplied identity participates in proof.
    """

    def __init__(self, log):
        read_fd, write_fd = os.pipe()
        self.writer = os.fdopen(write_fd, "wb", buffering=0)
        self.reader = os.fdopen(read_fd, "rb", buffering=0)
        self.log = log
        self.content = bytearray()
        self.size = 0
        self.error = None
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        try:
            with self.reader:
                while chunk := self.reader.read(65536):
                    self.size += len(chunk)
                    self.content.extend(chunk[:max(0, _MAX_COMPLETION_BYTES - len(self.content))])
                    self.log.write(chunk)
        except Exception as exc:
            self.error = type(exc).__name__

    def close_writer(self):
        self.writer.close()

    def finish(self):
        self.close_writer()
        self.thread.join(5)
        if self.thread.is_alive() or self.error:
            raise OSError("original stdout drain unconfirmed")
        return bytes(self.content), self.size


def _monitoring_completion(result, stream, *, attempt, effect_key, completion):
    """Bind the old structured completion contract to the original Attempt.

    stdout is captured separately from the canonical stderr/log stream. The
    runner owns the binding; process output cannot choose another Attempt or
    effect identity. A missing or malformed signal is a terminal rejected
    result, while unknown/partial results remain unknown/partial for lookup.
    """
    if not isinstance(completion, dict) or completion.get("contract") != "monitoring":
        return result
    try:
        raw, size = stream.finish()
        if (size > _MAX_COMPLETION_BYTES and result.get("outcome") in {"succeeded", "rejected"}
                and result.get("cleanupProof", {}).get("confirmed") is True):
            result = copy.deepcopy(result)
            result["outcome"] = "rejected"
            result.setdefault("receipt", {}).update(code="monitoring_completion_invalid",
                message="completion stdout exceeds bounded proof size")
            result["cleanupProof"]["reason"] = "monitoring-output-limit"
            return result
        if result.get("outcome") != "succeeded":
            return result
        if size > _MAX_COMPLETION_BYTES:
            raise ValueError("completion stdout exceeds bounded proof size")
        text = raw.decode("utf-8")
        structured = json.loads(text)
        signal = structured.get("panJobCompletion") if isinstance(structured, dict) else None
        expected = completion.get("parameters", {}).get("signalType")
        if (not isinstance(signal, dict) or set(signal) != {"type", "completed"}
                or signal.get("completed") is not True or signal.get("type") != expected):
            raise ValueError("monitoring completion signal does not match frozen contract")
    except (OSError, UnicodeDecodeError, ValueError, RecursionError) as exc:
        result = copy.deepcopy(result)
        if result.get("outcome") not in {"unknown", "partial"}:
            result["outcome"] = "unknown" if isinstance(exc, OSError) else "rejected"
        result.setdefault("receipt", {}).update(code="monitoring_completion_invalid",
                                                message=str(exc) or type(exc).__name__)
        return result
    proof = {
        "contract": "monitoring",
        "signal": signal,
        "attempt": {k: attempt[k] for k in ("attemptId", "leaseId", "runId", "stepId", "ownerEpoch", "generation")},
        "effectKey": copy.deepcopy(dict(effect_key)),
        "source": "stdout",
        "encoding": "utf-8",
        "bytes": len(raw),
    }
    result.setdefault("receipt", {})["completionProof"] = proof
    result["receipt"]["structuredContent"] = structured
    return result


async def execute_claimed(store, doc, *, executor=None):
    identity, attempt, claim = doc["effectId"], doc["attempt"], doc["claim"]
    resources = ExitStack()
    capture = executor is None and doc["frozenPlan"].get("operation") in {"process", "background-process", "command"}
    completion = doc["frozenPlan"].get("completion")
    completion_stream = None
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
                from packages.jobs.process_logs import open_log
                # Trusted original execution record chooses the path. argv,
                # frozen arguments and caller-provided paths never select logs.
                log = resources.enter_context(open_log(store.root, doc, write=True))
                if isinstance(completion, dict) and completion.get("contract") == "monitoring":
                    completion_stream = _CompletionOutput(log)

                class RecordingOwner:
                    cleanup_tree = None

                    def __getattr__(self, name):
                        return getattr(owner, name)

                    def spawn(self, argv, **kwargs):
                        env = {name: value for name, value in os.environ.items()
                               if not name.upper().startswith("PAN_") and name.upper() != "PYTHONPATH"}
                        # Ordinary executions keep the merged direct file sink.
                        # Monitoring drains only stdout into the bounded proof
                        # buffer and also appends it to this original log.
                        import subprocess
                        kwargs.update(stdout=completion_stream.writer if completion_stream else log,
                                      stderr=log if completion_stream else subprocess.STDOUT)
                        try:
                            return owner.spawn(argv, env=env, **kwargs)
                        finally:
                            if completion_stream:
                                completion_stream.close_writer()

                    def recoverable_identity(self, handle):
                        record = owner.recoverable_identity(handle)
                        launch = getattr(handle, "window_launch", {})
                        if launch.get("hiddenWindowFailure"):
                            log.write(("Hidden window setup failed; launching visible: "
                                       + launch["hiddenWindowFailure"] + "\n").encode("utf-8", "replace"))
                            log.flush()
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
                            output_stop = completion_stream is not None and (
                                completion_stream.size > _MAX_COMPLETION_BYTES or completion_stream.error is not None)
                            if request.get("status") != "requested" and (waited.exited or time.monotonic() >= deadline or output_stop):
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
            if capture and "logRef" in doc:
                result.setdefault("receipt", {})["logRef"] = doc["logRef"]
            if completion_stream is not None:
                result = _monitoring_completion(result, completion_stream,
                                                attempt=attempt, effect_key=doc["effectKey"], completion=completion)
        return store.finish(identity, attempt, claim, result)
    except ExecutionConflict:
        # A pre-effect cancellation may win the phase transaction. A stale
        # worker is never allowed to overwrite a newer fence or receipt.
        return store.get(identity)
    except Exception as exc:
        result = EffectReceipt(doc["effectKey"], "unknown", {
            "code": "supervisor_error", "message": str(exc), "phase": "uncertain"},
            {"confirmed": False}).as_dict()
        if capture and "logRef" in doc:
            result["receipt"]["logRef"] = doc["logRef"]
        return store.finish(identity, attempt, claim, result)
    finally:
        try:
            if completion_stream:
                completion_stream.close_writer()
                completion_stream.thread.join(5)
        finally:
            resources.close()


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
