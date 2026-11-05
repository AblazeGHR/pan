"""Shared command primitive. Hosts own identity, durable intent and lifecycle.

There is no admission loop, retry policy, scheduler or execution database here.
The boundary context must fence *each* spawn; checkpoint must persist before
the next command. A failed checkpoint never permits the host to replay effects.
"""
from __future__ import annotations

import asyncio
import codecs
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import math
import os
from pathlib import Path
import subprocess
import threading
import time
from typing import Callable, Mapping

from packages.core.platform.process import default_process_owner
from packages.jobs import final_contracts as fc

OPERATIONS = frozenset({"process.command", "process.shell"})
OUTPUT_LIMIT = 512 * 1024


def argument_defaults(schema, arguments):
    values = copy.deepcopy(arguments)
    for key, spec in schema.get("properties", {}).items():
        if key not in values and isinstance(spec, dict) and "default" in spec:
            values[key] = copy.deepcopy(spec["default"])
    return values


def now():
    return datetime.now(timezone.utc).isoformat()


@dataclass
class CommandContext:
    identity: Mapping
    boundary: Callable
    checkpoint: Callable


class BoundedStream:
    """Drain all child bytes concurrently while retaining a bounded prefix."""
    def __init__(self, limit=OUTPUT_LIMIT):
        read, write = os.pipe()
        self.reader = os.fdopen(read, "rb", buffering=0)
        self.writer = os.fdopen(write, "wb", buffering=0)
        self.content = bytearray()
        self.limit = limit
        self.size = 0
        self.error = None
        self.thread = threading.Thread(target=self._drain, daemon=True)
        self.thread.start()

    def _drain(self):
        try:
            with self.reader:
                while chunk := self.reader.read(65536):
                    self.size += len(chunk)
                    self.content.extend(chunk[:max(0, self.limit - len(self.content))])
        except Exception as exc:
            self.error = str(exc)

    def finish(self):
        self.writer.close()
        self.thread.join(5)
        if self.thread.is_alive() or self.error:
            raise OSError("Command output drain unconfirmed")
        # Drop an incomplete UTF-8 suffix at the byte boundary.
        truncated = self.size > self.limit
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        return decoder.decode(bytes(self.content), final=not truncated), truncated


def prepare(operation, config, arguments):
    """Keep shell strings opaque; only explicit argv elements bind parameters."""
    if operation not in OPERATIONS or not isinstance(arguments, dict):
        raise ValueError("Unsupported command descriptor")
    descriptor = {**copy.deepcopy(config), **copy.deepcopy(arguments)}
    commands = descriptor.pop("commands", None)
    if commands is None:
        commands = [{}]
    from packages.jobs.chains import MAX_STEPS
    if not isinstance(commands, list) or not 1 <= len(commands) <= MAX_STEPS or any(not isinstance(c, dict) for c in commands):
        raise ValueError("commands must contain 1..32 descriptors, as in existing serial chains")
    result = []
    for command in commands:
        item = {**descriptor, **command}
        cwd = item.get("cwd")
        if not isinstance(cwd, str) or not Path(cwd).is_absolute() or not Path(cwd).is_dir():
            raise ValueError("cwd must be an existing absolute directory")
        timeout = item.get("timeout", item.get("timeout_sec", 30))
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        hide = item.get("hideWindow", True)
        env = item.get("env", {})
        if type(hide) is not bool or not isinstance(env, dict) or any(
                not isinstance(k, str) or not k or "=" in k or "\x00" in k
                or not isinstance(v, str) or "\x00" in v for k, v in env.items()):
            raise ValueError("Invalid hideWindow or environment")
        external = item.get("externalScript")
        resource = None
        if external is not None:
            if not isinstance(external, dict) or not isinstance(external.get("path"), str):
                raise ValueError("externalScript.path required")
            path = Path(external["path"])
            path = (Path(cwd) / path).resolve() if not path.is_absolute() else path
            if not path.is_file():
                raise ValueError("externalScript.path must be an existing file relative to cwd or absolute")
            resource = {"path": str(path), "declaredPath": external["path"], "mutable": True,
                        "declaredDigest": external.get("contentDigest")}
        if "command" in item:
            if "argv" in item:
                raise ValueError("command and argv are mutually exclusive")
            from packages.core.background_jobs import validate_shell_action
            from packages.core.platform.worker_runtime import shell_command_argv
            command_text, _ = validate_shell_action(item["command"], cwd)
            interpreter = item.get("interpreter", "system-default")
            if interpreter == "system-default":
                argv = shell_command_argv(command_text)
            elif isinstance(interpreter, list) and interpreter:
                argv = interpreter + [command_text]
            else:
                raise ValueError("shell interpreter must be system-default or an explicit argv prefix")
        elif external is not None and "argv" not in item:
            interpreter = item.get("interpreter")
            prefix = [interpreter] if isinstance(interpreter, str) else interpreter
            if not isinstance(prefix, list) or not prefix or not isinstance(item.get("args", []), list):
                raise ValueError("externalScript requires an explicit interpreter argv")
            argv = prefix + [str(path)] + item.get("args", [])
        else:
            argv = item.get("argv")
            if argv is None and isinstance(item.get("executable"), str) and isinstance(item.get("args", []), list):
                argv = [item["executable"], *item.get("args", [])]
        if (not isinstance(argv, list) or not argv or not argv[0]
                or any(not isinstance(a, str) or "\x00" in a for a in argv)):
            raise ValueError("argv must be a nonempty string array")
        argv = [str(arguments[a[1:-1]]) if a.startswith("{") and a.endswith("}") and a[1:-1] in arguments
                else a for a in argv]
        result.append({"argv": argv, "cwd": cwd, "timeout": float(timeout),
                       "hideWindow": hide, "env": {**os.environ, **env}, "externalScript": resource})
    return result


async def execute(commands, context: CommandContext, *, process_owner=None):
    """Serial commands; every non-success blocks successors, without rollback."""
    owner = process_owner or default_process_owner()
    receipts = []
    for ordinal, command in enumerate(commands):
        row = {"ordinal": ordinal, "status": "uncertain", "exitCode": None,
               "stdout": "", "stderr": "", "stdoutTruncated": False,
               "stderrTruncated": False, "startedAt": now(), "cleanupProof": {"confirmed": False}}
        if command.get("externalScript"):
            row["externalScript"] = copy.deepcopy(command["externalScript"])
        handle = None
        out, err = BoundedStream(), BoundedStream()
        cancelled = False
        try:
            # No await between the host owner/auth check and spawn.
            with context.boundary():
                if command.get("externalScript"):
                    with Path(command["externalScript"]["path"]).open("rb") as script:
                        row["externalScript"]["observedDigest"] = hashlib.file_digest(script, "sha256").hexdigest()
                handle = owner.spawn(command["argv"], cwd=command["cwd"], env=command["env"],
                    hide_window=command["hideWindow"], stdin=subprocess.DEVNULL,
                    stdout=out.writer, stderr=err.writer)
            row["processIdentity"] = owner.recoverable_identity(handle)
            deadline = time.monotonic() + command["timeout"]
            reason = None
            while True:
                with context.boundary():
                    pass
                waited = owner.wait(handle, timeout=0)
                if waited.exited:
                    row["exitCode"] = waited.exit_code
                    break
                if time.monotonic() >= deadline:
                    reason = "timeout"
                    break
                await asyncio.sleep(0.02)
            if reason:
                ended = owner.terminate(handle, grace=2)
                row["exitCode"] = ended.exit_code
                row["code"] = reason
            tree = owner.kill_tree(handle, timeout=2)
            row["cleanupProof"] = {"confirmed": tree.confirmed is True,
                                    "ownedTree": tree.as_dict()}
            row["status"] = ("uncertain" if not tree.confirmed else
                             "failed" if reason or row["exitCode"] != 0 else "completed")
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            # This cancels the execution task (including a host shutdown
            # deadline), not the TCP peer. PanServer keeps the request running
            # after peer disconnect; owned TCP tests cover that distinction.
            cancelled = isinstance(exc, asyncio.CancelledError)
            row["code"] = "cancelled" if cancelled else getattr(exc, "code", "command_interrupted")
            row["error"] = str(exc)
            if handle is not None:
                try:
                    ended = owner.terminate(handle, grace=2)
                    tree = owner.kill_tree(handle, timeout=2)
                    row["exitCode"] = ended.exit_code
                    row["cleanupProof"] = {"confirmed": ended.exited and tree.confirmed,
                                            "ownedTree": tree.as_dict()}
                    if row["cleanupProof"]["confirmed"]:
                        row["status"] = "cancelled" if cancelled else "failed"
                except Exception as cleanup_error:
                    row["cleanupError"] = str(cleanup_error)
            else:
                # A spawn exception may follow actual creation. Never claim
                # rejected/clean merely because no handle reached the caller.
                row["status"] = "uncertain"
        finally:
            if handle is not None:
                handle.close()
            for name, stream in (("stdout", out), ("stderr", err)):
                try:
                    row[name], row[name + "Truncated"] = stream.finish()
                except Exception as exc:
                    row.update(status="uncertain", outputError=str(exc))
            row["finishedAt"] = now()
        receipts.append(row)
        context.checkpoint(copy.deepcopy(receipts))
        if row["status"] != "completed" or cancelled:
            break
    last = receipts[-1]["status"]
    outcome = "succeeded" if last == "completed" else "unknown" if last == "uncertain" else "rejected"
    if outcome == "rejected" and any(row["status"] == "completed" for row in receipts[:-1]):
        outcome = "partial"  # Earlier effects cannot be safely repeated as a new Attempt.
        last = "partial"
    return {"outcome": outcome, "receipt": {"commands": receipts, "status": last},
            "cleanupProof": receipts[-1]["cleanupProof"]}


def product_definition(operation):
    """Named successors; execution still requires explicit publication/enable."""
    return {"name": operation, "inputSchema": {"type": "object"}, "outputSchema": {"type": "object"},
        "providerRef": {"id": "pan.command", "releaseDigest": fc.digest("pan.command.v1"), "generation": 1},
        "execution": {"operation": operation, "config": {}}, "effectClass": "write_local",
        "requiredCapabilities": ["processOwner"], "completionContracts": [{"id": "process-exit", "schema": {"type": "object"},
        "terminalProofSchema": {"type": "object"}}], "defaultCompletion": "process-exit",
        "cancel": {"supported": True}, "reconcile": {"supported": False},
        "retry": {"idempotent": False, "sameKeySupported": True, "requiresProof": True}, "system": True}
