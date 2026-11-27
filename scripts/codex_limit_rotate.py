#!/usr/bin/env python3
"""Monitor Codex quota failures and optionally recover existing Workers.

Supply explicit ``--api``, ``--ledger`` and ``--bridge-data``. Default mode
only observes; ``--execute`` enables durable records and effects. An existing
user Bearer may be supplied in the PAN_ROTATION_BEARER environment variable;
never put credentials in command arguments. Run periodically with one ledger.
The continuation uses the Session Wake message and is queued after restart
acceptance. Pan gives the accepted restart priority over queue dispatch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener
from urllib.error import HTTPError


PAGE = 100
MAX_PAGES = 1000
MAX_PENDING = 4096
MAX_AGE = 300


class Uncertain(RuntimeError):
    """The available facts do not justify an automatic action."""


class AuthDenied(Uncertain):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _object(value, label):
    if not isinstance(value, dict):
        raise Uncertain(f"{label}: expected object")
    return value


class Pan:
    def __init__(self, base, bearer=None):
        parsed = urlsplit(base)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment):
            raise ValueError("--api must be an HTTP URL")
        if bearer is not None and (not bearer or "\r" in bearer or "\n" in bearer):
            raise ValueError("invalid Bearer configuration")
        self.base = base.rstrip("/")
        self._bearer = bearer

    def request(self, method, path, body=None):
        headers = {"Accept": "application/json"}
        if self._bearer:
            headers["Authorization"] = "Bearer " + self._bearer
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with build_opener(_NoRedirect()).open(request, timeout=10) as response:
                return _object(json.load(response), path)
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise AuthDenied(f"Pan authorization denied ({exc.code})") from None
            raise Uncertain(f"Pan HTTP error ({exc.code})") from None

    def get(self, path):
        return self.request("GET", path)

    def restart(self, sid):
        return self.request("POST", f"/api/sessions/{quote(sid, safe='')}/worker/delayed-restart?existing_worker_only=true", {})

    def restart_state(self, sid):
        return self.get(f"/api/sessions/{quote(sid, safe='')}/worker/delayed-restart").get("delayedRestart")

    def enqueue_continuation(self, sid, key):
        response = self.request("POST", "/api/sessions/wake", {
            "sessionIds": [sid], "source": "automation", "clientMessageId": key,
        })
        results = response.get("results")
        if (response.get("ok") is not True or not isinstance(results, list)
                or len(results) != 1 or not isinstance(results[0], dict)
                or results[0].get("sessionId") != sid):
            raise Uncertain("continuation receipt unconfirmed")
        return results[0]

    def events(self, cursor):
        return self.get(f"/api/automation/events?after={cursor}&limit={PAGE}")

    def session(self, sid):
        # Bounded history still includes the persisted lastResult identity.
        return self.get(f"/api/sessions/{quote(sid, safe='')}?historyLimit=1")

    def inventory(self):
        workers = self.get("/api/list").get("workers")
        sessions = self.get("/api/sessions?summary=1").get("sessions")
        if not isinstance(workers, list) or not isinstance(sessions, list):
            raise Uncertain("invalid inventory")
        summaries = {s.get("id"): s for s in sessions if isinstance(s, dict)}
        result = {}
        for worker in workers:
            if not isinstance(worker, dict):
                raise Uncertain("invalid worker")
            sid = worker.get("sessionId")
            s = summaries.get(sid)
            if (isinstance(sid, str) and isinstance(s, dict) and s.get("adapter") == "codex"
                    and worker.get("status") in {"idle", "running"}):
                if sid in result or not isinstance(worker.get("workerId"), str) or type(worker.get("generation")) is not int:
                    raise Uncertain("ambiguous worker inventory")
                result[sid] = {"workerId": worker["workerId"], "generation": worker["generation"],
                               "status": worker["status"]}
        return result


class PanScriptIPC(Pan):
    """Explicit IPC mode: never fall back to Bearer or network HTTP."""
    def __init__(self, root, profile):
        self.root = Path(root)
        self.profile = profile

    def request(self, method, path, body=None):
        from packages.web.script_auth import load_client
        from packages.plugins.ipc import request as ipc_request
        try:
            locator = json.loads((self.root / 'script-ipc.json').read_text(encoding='utf-8'))
            address = locator['address']
            pid = locator.get('pid')
            if (os.name != 'nt' or type(pid) is not int or pid <= 0 or not isinstance(address, str)
                    or not re.fullmatch(r'\\\\\.\\pipe\\pan-script-' + str(pid) + r'-[0-9a-f]{32}', address)):
                raise ValueError('invalid locator')
            result = ipc_request({'operation': 'script_http', 'session_id': '',
                'profile': self.profile, 'credential': load_client(self.root, self.profile),
                'method': method, 'path': path, 'body': body or {}}, address=address)
        except Exception:
            raise Uncertain('Script IPC request unconfirmed') from None
        result = _object(result, 'Script IPC')
        if result.get('ok') is False and isinstance(result.get('error'), dict) and result['error'].get('code') == 403:
            raise AuthDenied('Script IPC authorization denied')
        return result


def valid_issue(event):
    if not isinstance(event, dict) or event.get("type") != "runtime.issue.v1":
        return None
    p = event.get("payload")
    if not isinstance(p, dict) or (p.get("adapter"), p.get("classification"), p.get("confidence")) != (
            "codex", "codex_quota_exhausted", "structured"):
        return None
    c = p.get("correlation")
    if not isinstance(c, dict) or not isinstance(p.get("issueId"), str) or not p["issueId"] or event.get("id") != p["issueId"]:
        return None
    if any(not isinstance(c.get(k), str) or not c[k] for k in ("sessionId", "workerId", "runtimeEpoch")):
        return None
    if any(type(c.get(k)) is not int or c[k] < 1 for k in ("generation", "executionSeq")):
        return None
    # Ordinary executions omit taskSeq/taskId from runtime.issue; a supplied
    # task identity must still be well formed and match the persisted result.
    if ("taskSeq" in c and (type(c["taskSeq"]) is not int or c["taskSeq"] < 1)
            or "taskId" in c and (not isinstance(c["taskId"], str) or not c["taskId"])):
        return None
    system = event.get("system", {})
    if not isinstance(system, dict) or any(k in system and system[k] != c.get(k) for k in ("sessionId", "workerId", "taskId")):
        return None
    return {"issueId": p["issueId"], "correlation": c}


def correlate(issue, detail):
    """Return matched, pending, or stale; no prose or generic 429 inference."""
    c = issue["correlation"]
    if detail.get("id") != c["sessionId"] or detail.get("adapter") != "codex":
        return "stale"
    last = detail.get("lastResult")
    if not isinstance(last, dict):
        return "pending"
    keys = ("workerId", "generation", "executionSeq", "taskSeq", "taskId")
    if (any(last.get(k) != c.get(k) for k in keys)
            or any(type(last.get(k)) is not int for k in ("generation", "executionSeq"))
            or (last.get("taskSeq") is not None and type(last["taskSeq"]) is not int)):
        # The worker may still be finishing and lastResult can be the previous run.
        if detail.get("workerId") == c["workerId"] and detail.get("workerStatus") == "running":
            return "pending"
        return "stale"
    if last.get("status") != "error" or not isinstance(last.get("terminalKey"), str) or not last["terminalKey"]:
        return "stale"
    diagnostic = last.get("reportDiagnostic")
    terminal_issue = diagnostic.get("runtimeIssue") if isinstance(diagnostic, dict) else None
    if not isinstance(terminal_issue, dict) or terminal_issue.get("issueId") != issue["issueId"]:
        return "stale"
    if terminal_issue.get("correlation") != c:
        return "stale"
    if (not isinstance(last.get("historyEpoch"), str) or not last["historyEpoch"]
            or type(last.get("historyRevision")) is not int
            or detail.get("historyEpoch") != last["historyEpoch"]
            or detail.get("historyRevision") != last["historyRevision"]):
        return "stale"
    return "matched"


def choose(snapshot, now):
    """Return current/alternative/wait/unknown using fresh, complete windows."""
    if not isinstance(snapshot, dict) or snapshot.get("status") != "known":
        return {"decision": "unknown"}
    at = snapshot.get("observedAt")
    if type(at) not in (int, float) or not math.isfinite(at) or not 0 <= now - at <= MAX_AGE:
        return {"decision": "unknown"}
    rows = snapshot.get("accounts")
    if not isinstance(rows, list) or not rows:
        return {"decision": "unknown"}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or row["id"] in seen:
            return {"decision": "unknown"}
        seen.add(row["id"])
        for key in ("fiveHour", "week"):
            w = row.get(key)
            if not isinstance(w, dict):
                return {"decision": "unknown"}
            used, reset = w.get("usedPercent"), w.get("resetsAt")
            if (type(used) not in (int, float) or not math.isfinite(used) or not 0 <= used <= 100
                    or type(reset) not in (int, float) or not math.isfinite(reset) or reset <= now):
                return {"decision": "unknown"}
    current = snapshot.get("currentAccountId")
    if current not in seen:
        return {"decision": "unknown"}
    usable = [r for r in rows if all(r[k]["usedPercent"] < 100 for k in ("fiveHour", "week"))]
    if any(r["id"] == current for r in usable):
        return {"decision": "current", "provider": current}
    if usable:
        usable.sort(key=lambda r: (r["week"]["resetsAt"], r["week"]["usedPercent"], r["fiveHour"]["usedPercent"], r["id"]))
        return {"decision": "switch", "provider": usable[0]["id"], "expected": current}
    # An account is usable only after every exhausted window has reset.
    wake = min(max(r[k]["resetsAt"] for k in ("fiveHour", "week") if r[k]["usedPercent"] >= 100) for r in rows)
    return {"decision": "wait", "until": wake + 300}


class Ledger:
    def __init__(self, path, execute):
        self.path = Path(path)
        self.execute = execute
        if self.path.exists():
            self.data = _object(json.loads(self.path.read_text(encoding="utf-8")), "ledger")
            if self.data.get("version") != 1:
                raise Uncertain("unsupported ledger version")
        else:
            self.data = {"version": 1, "cursor": None, "pending": {}, "seen": {}, "batches": []}

    def save(self):
        if not self.execute:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(self.data, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)


@contextmanager
def ledger_lock(path, execute):
    """Serialize all readers/writers sharing an active ledger across processes."""
    if not execute:
        yield
        return
    path = Path(str(path) + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def page(api, cursor):
    response = api.events(cursor)
    rows, next_cursor = response.get("events"), response.get("nextAfter")
    if not isinstance(rows, list) or len(rows) > PAGE or type(next_cursor) is not int:
        raise Uncertain("invalid event page")
    previous = cursor
    for row in rows:
        if not isinstance(row, dict) or type(row.get("seq")) is not int or row["seq"] <= previous:
            raise Uncertain("invalid event sequence")
        previous = row["seq"]
    if (next_cursor < previous if isinstance(api, PanScriptIPC) else next_cursor != previous):
        raise Uncertain("invalid next cursor")
    return rows, next_cursor


def observe(api, ledger):
    state = ledger.data
    if state["cursor"] is None:
        # Start at the current tip. Never treat retained historical events as new.
        cursor = 0
        for _ in range(MAX_PAGES):
            old_cursor = cursor
            rows, cursor = page(api, cursor)
            if (cursor == old_cursor if isinstance(api, PanScriptIPC) else len(rows) < PAGE):
                state["cursor"] = cursor
                ledger.save()
                return
        raise Uncertain("event tip exceeds bounded scan")
    cursor = state["cursor"]
    for _ in range(MAX_PAGES):
        rows, next_cursor = page(api, cursor)
        at_tip = next_cursor == cursor if isinstance(api, PanScriptIPC) else len(rows) < PAGE
        for row in rows:
            issue = valid_issue(row.get("event"))
            if issue and issue["issueId"] not in state["seen"]:
                state["pending"].setdefault(issue["issueId"], issue)
                if len(state["pending"]) > MAX_PENDING:
                    raise Uncertain("pending issue capacity reached")
        state["cursor"] = cursor = next_cursor
        ledger.save()
        if at_tip:
            break
    else:
        raise Uncertain("event backlog exceeds bounded scan")
    for iid, issue in list(state["pending"].items()):
        detail = api.session(issue["correlation"]["sessionId"])
        outcome = correlate(issue, detail)
        if outcome == "pending":
            continue
        if outcome == "matched":
            last = detail["lastResult"]
            terminal_key = last["terminalKey"]
            # Several issue samples belonging to the same terminal count once.
            seen_terminal = any(v.get("terminalKey") == terminal_key and v.get("sessionId") == detail["id"]
                                and v.get("outcome") != "stale" for v in state["seen"].values())
            batched_terminal = any(t.get("terminalKey") == terminal_key and t.get("targetSessionId") == detail["id"]
                                   for batch in state["batches"] for t in batch.get("targets", []))
            if not seen_terminal and not batched_terminal:
                inventory = api.inventory()
                c = issue["correlation"]
                target = inventory.get(c["sessionId"])
                if not target or target["workerId"] != c["workerId"] or target["generation"] != c["generation"]:
                    outcome = "stale"
                else:
                    candidate = {"trigger": issue, "terminalKey": terminal_key,
                        "targetSessionId": detail["id"], "historyEpoch": last.get("historyEpoch"),
                        "historyRevision": last.get("historyRevision")}
                    active = next((b for b in reversed(state["batches"])
                                   if b["status"] in {"observed_only", "waiting_reset", "restarting"}), None)
                    if active is None:
                        state["batches"].append({"targets": [candidate], "workers": inventory,
                                                 "status": "observed_only"})
                    else:
                        if active["status"] != "restarting":
                            active["workers"].update(inventory)
                        if detail["id"] in active["workers"]:
                            active["targets"].append(candidate)
                        else:
                            outcome = "stale"
        state["seen"][iid] = {"outcome": outcome, "sessionId": detail.get("id"),
                              "terminalKey": (detail.get("lastResult") or {}).get("terminalKey")}
        if len(state["seen"]) > MAX_PENDING:
            raise Uncertain("terminal identity capacity reached")
        del state["pending"][iid]
        ledger.save()


def inspect_quota(ledger, bridge, now=time.time):
    """Sample through the existing read-only bridge; never issue a switch."""
    from packages.automation.quota_provider import sample

    actionable = [b for b in ledger.data["batches"] if b["status"] in {"observed_only", "waiting_reset"}]
    if not actionable:
        return {"decision": "none"}
    waiting = [b for b in actionable if b["status"] == "waiting_reset"]
    if len(waiting) == len(actionable) and all(now() < b["quotaDecision"]["until"] for b in waiting):
        return {"decision": "wait", "until": min(b["quotaDecision"]["until"] for b in waiting)}
    gates = bridge.settings.gates("codex")
    disabled = {pid for pid, value in gates.items() if value}
    _, snapshot = sample(bridge, disabled, now, time.monotonic() + 30)
    decision = choose(snapshot, now())
    for batch in ledger.data["batches"]:
        if batch["status"] in {"observed_only", "waiting_reset"}:
            if batch["status"] == "waiting_reset" and now() < batch["quotaDecision"]["until"]:
                continue
            batch["quotaDecision"] = decision
            batch["quotaCheckedAt"] = now()
            batch["status"] = "waiting_reset" if decision["decision"] == "wait" else "observed_only"
    ledger.save()
    return decision


def _continuation_safe(detail, target):
    last = detail.get("lastResult")
    return (detail.get("id") == target["targetSessionId"] and detail.get("adapter") == "codex"
            and isinstance(last, dict) and last.get("terminalKey") == target["terminalKey"]
            and detail.get("historyEpoch") == target["historyEpoch"]
            and detail.get("historyRevision") == target["historyRevision"]
            and detail.get("queuePaused") is False and detail.get("illegalOffline") is False)


def reconcile(api, bridge, ledger, now=time.time):
    """Advance each persisted effect once; uncertain dispatches are not replayed."""
    if not ledger.execute:
        return
    for batch in ledger.data["batches"]:
        status = batch["status"]
        if status == "switching":
            # There is no queryable command receipt in the provider bridge.
            try:
                current = bridge.read_only("list", {"app": "codex"}).get("currentProviderId")
            except Exception:
                current = None
            batch["switchReadback"] = current
            batch["status"] = "unknown"
            ledger.save()
            continue
        if status == "observed_only":
            decision = batch.get("quotaDecision") or {}
            kind = decision.get("decision")
            if kind not in {"current", "switch"}:
                continue
            checked = batch.get("quotaCheckedAt")
            if type(checked) not in (int, float) or not 0 <= now() - checked <= MAX_AGE:
                batch["quotaDecision"] = {"decision": "unknown"}
                ledger.save()
                continue
            if kind == "switch":
                batch["status"] = "switching"
                batch["switch"] = {"from": decision["expected"], "to": decision["provider"], "status": "dispatching"}
                ledger.save()
                try:
                    response = bridge.invoke("auto_switch", {"app": "codex", "id": decision["provider"],
                                             "expectedCurrentId": decision["expected"]})
                    current = bridge.read_only("list", {"app": "codex"}).get("currentProviderId")
                    if response.get("currentProviderId") != decision["provider"] or current != decision["provider"]:
                        raise Uncertain("provider switch unconfirmed")
                    batch["switch"].update(status="confirmed", readback=current)
                except Exception:
                    batch["switch"]["status"] = "unknown"
                    try:
                        batch["switch"]["readback"] = bridge.read_only("list", {"app": "codex"}).get("currentProviderId")
                    except Exception:
                        pass
                    batch["status"] = "unknown"
                    ledger.save()
                    continue
            else:
                batch["switch"] = {"status": "not_required", "provider": decision["provider"]}
            batch["status"] = "restarting"
            batch["provider"] = decision["provider"]
            ledger.save()
        if batch["status"] != "restarting":
            continue
        try:
            if bridge.read_only("list", {"app": "codex"}).get("currentProviderId") != batch.get("provider"):
                batch["status"] = "unknown"
                ledger.save()
                continue
        except Exception:
            batch["status"] = "unknown"
            ledger.save()
            continue
        for sid, worker in batch["workers"].items():
            record = worker.setdefault("restart", {"status": "ready"})
            if record["status"] == "ready":
                live = api.inventory().get(sid)
                if not live:
                    record["status"] = "skipped_changed"
                    ledger.save()
                    continue
                worker.update(live)
                duplicate = any(other is not batch and any(
                    item.get("workerId") == worker["workerId"] and item.get("generation") == worker["generation"]
                    and item.get("restart", {}).get("status") not in {None, "ready", "skipped_changed"}
                    for item in other.get("workers", {}).values()) for other in ledger.data["batches"])
                if duplicate:
                    record["status"] = "unknown_duplicate"
                    ledger.save()
                    continue
                prior = api.restart_state(sid)
                if isinstance(prior, dict) and (prior.get("status") in {"pending", "restarting"}
                        or (prior.get("workerId"), prior.get("generation")) == (worker["workerId"], worker["generation"])):
                    record["status"] = "unknown"
                    ledger.save()
                    continue
                record["status"] = "dispatching"
                ledger.save()
                try:
                    result = api.restart(sid)
                except AuthDenied:
                    record["status"] = "unknown"
                    ledger.save()
                    raise
                except Exception:
                    record["status"] = "unknown"
                    try:
                        record["observedReceipt"] = api.restart_state(sid)
                    except AuthDenied:
                        ledger.save()
                        raise
                    except Exception:
                        pass
                    ledger.save()
                    continue
                receipt = result.get("delayedRestart")
                if (not isinstance(receipt, dict) or receipt.get("sessionId") != sid
                        or receipt.get("action") != "restart"
                        or not isinstance(receipt.get("workerId"), str) or not receipt["workerId"]
                        or type(receipt.get("generation")) is not int
                        or type(receipt.get("revision")) is not int
                        or receipt.get("status") not in {"pending", "restarting", "completed"}):
                    record["status"] = "unknown"
                else:
                    # Pan captures the current Worker when it accepts the Session request.
                    worker.update(workerId=receipt["workerId"], generation=receipt["generation"])
                    record.update(status="accepted", acceptedReceipt=receipt)
                ledger.save()
            if record["status"] == "dispatching":
                # The response was lost before the original receipt was stored.
                record["observedReceipt"] = api.restart_state(sid)
                record["status"] = "unknown"
                ledger.save()
        for target in batch["targets"]:
            if target.get("continuation") is not None:
                continue
            sid = target["targetSessionId"]
            worker = batch["workers"].get(sid, {})
            restart = worker.get("restart", {})
            if restart.get("status") not in {"accepted", "completed"}:
                continue
            detail = api.session(sid)
            if not _continuation_safe(detail, target):
                target["continuation"] = {"status": "skipped_changed_or_blocked"}
                ledger.save()
                continue
            key = "codex-limit:" + hashlib.sha256((sid + "\0" + target["terminalKey"]).encode()).hexdigest()
            target["continuation"] = {"status": "dispatching", "clientMessageId": key}
            ledger.save()
            try:
                result = api.enqueue_continuation(sid, key)
            except AuthDenied:
                target["continuation"]["status"] = "unknown"
                ledger.save()
                raise
            except Exception:
                target["continuation"]["status"] = "unknown"
                ledger.save()
                continue
            target["continuation"]["status"] = "queued" if result.get("status") == "queued" and isinstance(result.get("queueItemId"), str) and bool(result["queueItemId"]) else "unknown"
            if target["continuation"]["status"] == "queued":
                target["continuation"]["queueItemId"] = result.get("queueItemId")
            ledger.save()
        # A dispatching send is uncertain after a process crash. Never replay it.
        for target in batch["targets"]:
            if (target.get("continuation") or {}).get("status") == "dispatching":
                target["continuation"]["status"] = "unknown"
                ledger.save()
        if all(w.get("restart", {}).get("status") not in {"ready", "dispatching"}
               for w in batch["workers"].values()):
            batch["status"] = "settled"
            ledger.save()


def preflight_ledger(state):
    """Identify historical matches without changing or replaying the ledger."""
    rows = []
    for iid, entry in state['seen'].items():
        if not isinstance(entry, dict) or entry.get('outcome') != 'matched':
            continue
        sid, terminal = entry.get('sessionId'), entry.get('terminalKey')
        found = any(t.get('targetSessionId') == sid and t.get('terminalKey') == terminal
                    for b in state['batches'] for t in b.get('targets', []))
        if not found:
            rows.append({'issueId': iid, 'sessionId': sid, 'terminalKey': terminal,
                         'status': 'manual_reconciliation_required'})
    unresolved = []
    for index, batch in enumerate(state['batches']):
        restart = [w.get('restart', {}).get('status') for w in batch.get('workers', {}).values()]
        continuation = [t.get('continuation', {}).get('status') for t in batch.get('targets', [])
                        if isinstance(t.get('continuation'), dict)]
        if (batch.get('status') in {'unknown', 'switching', 'identity_conflict'}
                or any(s in {'unknown', 'unknown_duplicate', 'dispatching'} for s in restart + continuation)):
            unresolved.append({'batchIndex': index, 'status': batch.get('status'),
                               'restartStatuses': restart, 'continuationStatuses': continuation})
    return {'version': state['version'], 'cursor': state['cursor'],
            'pendingCount': len(state['pending']), 'batchCount': len(state['batches']),
            'pendingIssueIds': list(state['pending']), 'unresolvedBatches': unresolved,
            'historicalMatchesWithoutBatch': rows,
            'instruction': 'No replay or absence of external effects is inferred.'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", help="explicit Pan base URL")
    parser.add_argument("--ledger", required=True, type=Path, help="dedicated durable ledger")
    parser.add_argument("--bridge-data", type=Path, help="cc-switch bridge data directory")
    parser.add_argument('--script-profile', help='named local IPC profile')
    parser.add_argument('--script-data-dir', type=Path, help='Pan data root for script profile and IPC locator')
    parser.add_argument("--preflight-ledger", action="store_true", help="read-only historical reconciliation list; no network or writes")
    parser.add_argument("--execute", action="store_true", help="enable ledger and account/Worker effects")
    args = parser.parse_args(argv)
    if args.preflight_ledger:
        if args.execute:
            parser.error('--preflight-ledger cannot be combined with --execute')
        try:
            if not args.ledger.is_file():
                raise Uncertain('ledger not found')
            print(json.dumps(preflight_ledger(Ledger(args.ledger, False).data), ensure_ascii=False))
            return 0
        except (OSError, ValueError, KeyError, TypeError, Uncertain) as exc:
            print(f'preflight stopped: {type(exc).__name__}', file=sys.stderr)
            return 1
    if not args.bridge_data or (not args.script_profile and not args.api):
        parser.error('--bridge-data and either --api or --script-profile are required')
    if args.script_profile and (not args.script_data_dir or args.api):
        parser.error('--script-profile requires --script-data-dir and excludes --api')
    try:
        with ledger_lock(args.ledger, args.execute):
            ledger = Ledger(args.ledger, args.execute)
            # Do not pass the user's Bearer to the cc-switch child process.
            bearer = os.environ.pop("PAN_ROTATION_BEARER", None)
            if args.script_profile:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                api = PanScriptIPC(args.script_data_dir, args.script_profile)
            else:
                api = Pan(args.api, bearer)
            observe(api, ledger)
            if ledger.data["batches"]:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from packages.cc_switch_bridge.provider import Bridge
                bridge = Bridge(str(args.bridge_data))
                inspect_quota(ledger, bridge)
                reconcile(api, bridge, ledger)
            print(json.dumps({'event': 'scan_complete', 'cursor': ledger.data['cursor'],
                'pendingCount': len(ledger.data['pending']), 'seenCount': len(ledger.data['seen']),
                'batchCount': len(ledger.data['batches']),
                'batchStatuses': [b.get('status', 'unknown') for b in ledger.data['batches']]},
                ensure_ascii=False), flush=True)
    except (OSError, ValueError, Uncertain) as exc:
        print(f"observation stopped: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
