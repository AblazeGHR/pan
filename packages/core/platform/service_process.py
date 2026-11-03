"""Thin service process boundary; lifecycle policy and receipts stay in the host.

POSIX uses the existing ProcessOwner, including its strong birth identity and
fail-closed recovery. This is not a service manager or an automatic restart loop.
"""
from __future__ import annotations

import os
import subprocess


def spawn(argv, *, cwd, env, stdin, stdout, stderr, creationflags=0):
    if os.name == "nt":
        return subprocess.Popen(argv, cwd=cwd, env=env, stdin=stdin,
                                stdout=stdout, stderr=stderr, close_fds=True,
                                creationflags=creationflags)
    from packages.core.platform.process import default_process_owner
    owner = default_process_owner()
    owned = owner.spawn(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                        stdout=stdout, stderr=stderr)
    # Keep the original retained handle alive while this launcher exists;
    # later supervisors recover through the same persisted identity contract.
    process = owned.popen
    process.pan_owned_process = owned
    try:
        process.pan_process_identity = owner.recoverable_identity(owned)
    except BaseException:
        # Only this original retained handle authorizes spawn-abort cleanup.
        # Never reconstruct authority from a PID whose identity was not saved.
        cleanup = owner.kill_tree(owned, timeout=5)
        if not cleanup.confirmed:
            from packages.core.platform.process import OwnershipUnknownError
            raise OwnershipUnknownError(f"service spawn identity failed; cleanup unconfirmed: {cleanup}")
        raise
    return process


def capture(record):
    from packages.core.platform.service_process_posix import capture as observe
    return observe(record)


def offline(record):
    from packages.core.platform.service_process_posix import offline as observe
    return observe(record)


def terminate(record):
    """Return a proved result; missing/weak/stale identity grants no signal."""
    from packages.core.platform.process import default_process_owner
    identity = record.get("processIdentity")
    if not isinstance(identity, dict) or identity.get("pid") != record.get("pid"):
        return {"stopped": False, "fallback": False,
                "reason": "strong service process identity unavailable"}
    result = default_process_owner().recover(identity, terminate=True)
    stopped = (result.verified and not result.remaining
               and result.action in {"terminated", "already_dead"})
    return {"stopped": stopped, "fallback": False, "reason": result.detail,
            "processOwnerReceipt": result.as_dict()}
