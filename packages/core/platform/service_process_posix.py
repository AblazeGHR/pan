"""Read-only service group evidence, using existing ProcessOwner identities.

A snapshot records observed members before graceful shutdown. Group absence
alone never grants kill authority, and a dead leader never authorizes a signal.
POSIX groups cannot contain arbitrary descendants which deliberately escape.
"""
from __future__ import annotations

import os

from packages.core.platform.process import ProcessIdentity, OwnershipUnknownError


def _original(record):
    from packages.core.platform.process import default_process_owner
    from packages.core.platform.process_posix import _strong_identity
    try:
        identity = ProcessIdentity.from_dict(record["processIdentity"])
    except (KeyError, TypeError, ValueError) as exc:
        raise OwnershipUnknownError("service strong processIdentity unavailable") from exc
    owner = default_process_owner()
    current = owner.identity(os.getpid())
    if (not _strong_identity(identity) or identity.pid != record.get("pid")
            or identity.pgid != identity.pid or current is None
            or current.boot_id != identity.boot_id):
        raise OwnershipUnknownError("service strong identity/group/boot unavailable")
    return owner, identity


def capture(record):
    from packages.core.platform.process_posix import _tree_members, _strong_identity
    owner, identity = _original(record)
    before = owner.check(identity)
    if not before.verified_alive:
        raise OwnershipUnknownError("cannot snapshot an unavailable service leader")
    members = _tree_members(identity.pid, identity.pgid)
    after = owner.check(identity)
    if (not after.verified_alive or after.observed.pgid != identity.pgid
            or not any(member.matches(identity) for member in members)):
        raise OwnershipUnknownError("service leader changed during tree snapshot")
    prior = record.get("processTreeSnapshot")
    if prior:
        if not ProcessIdentity.from_dict(prior["leader"]).matches(identity):
            raise OwnershipUnknownError("prior service tree snapshot identity mismatch")
        previous = tuple(ProcessIdentity.from_dict(m) for m in prior["members"])
        if any(not _strong_identity(m) or m.boot_id != identity.boot_id or m.platform != identity.platform
               or m.pgid != identity.pgid for m in previous):
            raise OwnershipUnknownError("prior service tree member identity mismatch")
        members = (*previous, *members)
    unique = {(m.pid, m.kind, m.birth, m.boot_id): m for m in members}
    return {"leader": identity.as_dict(), "members": [m.as_dict() for m in unique.values()]}


def offline(record):
    from packages.core.platform.process import ProcessStatus
    from packages.core.platform.process_posix import _group_exists, _pid_exists, _strong_identity, PosixProcessOwner
    result = {"confirmed": False, "scope": "original_group_and_observed_members",
              "signalAuthorized": False}
    try:
        owner, identity = _original(record)
        snapshot = record.get("processTreeSnapshot", {})
        if not ProcessIdentity.from_dict(snapshot["leader"]).matches(identity):
            raise OwnershipUnknownError("snapshot is not for original service identity")
        members = tuple(ProcessIdentity.from_dict(m) for m in snapshot["members"])
        if not members or not any(m.matches(identity) for m in members):
            raise OwnershipUnknownError("original leader missing from snapshot")
        if any(not _strong_identity(m) or m.platform != identity.platform
               or m.boot_id != identity.boot_id or m.pgid != identity.pgid for m in members):
            raise OwnershipUnknownError("snapshot member identity/group mismatch")
        check = owner.check(identity)
        # PID absence is read-only exit evidence; a reused PID is never signalled.
        gone = (_pid_exists(identity.pid) is False or
                check.status is ProcessStatus.ALIVE and check.ours is False)
        remaining = PosixProcessOwner._member_remaining(members)
        group_present = _group_exists(identity.pgid)
        result.update(confirmed=gone and not group_present and not remaining,
                      leaderGone=gone, groupPresent=group_present,
                      remaining=list(remaining), originalIdentity=identity.as_dict())
        result["reason"] = "original group/member absence" if result["confirmed"] else "tree exit unconfirmed; no stale PGID signal"
    except (KeyError, TypeError, ValueError, OSError, OwnershipUnknownError) as exc:
        result["reason"] = str(exc)
    return result
