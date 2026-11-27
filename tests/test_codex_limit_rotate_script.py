"""Isolated HTTP and fake bridge checks; never contacts an installed provider."""
import copy
import json
import secrets
import threading
import subprocess
import sys
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import codex_limit_rotate as rotate


def issue(i="issue-1", **changes):
    correlation = {"sessionId": "s1", "workerId": "w1", "runtimeEpoch": "e1",
                   "generation": 1, "taskSeq": 2, "executionSeq": 3, "taskId": "t1"}
    payload = {"issueId": i, "adapter": "codex", "classification": "codex_quota_exhausted",
               "confidence": "structured", "correlation": correlation}
    payload.update(changes)
    return {"id": i, "type": "runtime.issue.v1", "payload": payload,
            "system": {"sessionId": "s1", "workerId": "w1", "taskId": "t1"}}


def detail(event):
    c = event["payload"]["correlation"]
    return {"id": "s1", "adapter": "codex", "workerId": "w1", "workerStatus": "idle",
            "historyEpoch": "history1", "historyRevision": 7,
            "queueRevision": 9, "queuePendingCount": 0, "queuePaused": False,
            "illegalOffline": False, "activeTaskId": None,
            "lastResult": {"status": "error", "terminalKey": "term1", "workerId": c["workerId"],
                "generation": c["generation"], "taskSeq": c["taskSeq"], "executionSeq": c["executionSeq"],
                "taskId": c["taskId"], "historyEpoch": "history1", "historyRevision": 7,
                "reportDiagnostic": {"runtimeIssue": copy.deepcopy(event["payload"])}}}


class FilteredIPC(rotate.PanScriptIPC):
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def events(self, cursor):
        self.calls.append(cursor)
        return self.pages[cursor]

    def session(self, sid):
        return detail(issue('new'))

    def inventory(self):
        return {}


def test_filtered_ipc_pagination_existing_and_new_ledger(tmp_path):
    new = issue('new')
    pages = {4696: {'events': [], 'nextAfter': 4796},
             4796: {'events': [{'seq': 4800, 'event': {'type': 'other'}}], 'nextAfter': 4896},
             4896: {'events': [{'seq': 4900, 'event': new}], 'nextAfter': 4900},
             4900: {'events': [], 'nextAfter': 4900}}
    api = FilteredIPC(pages)
    ledger = rotate.Ledger(tmp_path / 'existing.json', True)
    ledger.data['cursor'] = 4696
    rotate.observe(api, ledger)
    assert api.calls == [4696, 4796, 4896, 4900]
    assert ledger.data['cursor'] == 4900 and 'new' in ledger.data['seen']
    fresh = rotate.Ledger(tmp_path / 'new.json', True)
    api = FilteredIPC({0: pages[4696], **pages})
    rotate.observe(api, fresh)
    assert fresh.data['cursor'] == 4900 and not fresh.data['seen'] and not fresh.data['batches']


@pytest.mark.parametrize('response', [
    {'events': [], 'nextAfter': 9},
    {'events': [], 'nextAfter': True},
    {'events': [{'seq': 12}, {'seq': 11}], 'nextAfter': 12},
    {'events': [{'seq': 12}], 'nextAfter': 11},
    {'events': [{'seq': 10}], 'nextAfter': 10},
])
def test_filtered_ipc_rejects_malformed_cursors(response):
    with pytest.raises(rotate.Uncertain):
        rotate.page(FilteredIPC({10: response}), 10)


def test_http_cursor_remains_strict():
    class HTTP:
        def events(self, cursor): return {'events': [], 'nextAfter': cursor + 100}
    with pytest.raises(rotate.Uncertain): rotate.page(HTTP(), 10)


def test_filtered_ipc_bounded_scan_and_observe_only(tmp_path, monkeypatch):
    monkeypatch.setattr(rotate, 'MAX_PAGES', 2)
    path = tmp_path / 'ledger.json'
    original = {'version': 1, 'cursor': 10, 'pending': {}, 'seen': {}, 'batches': []}
    path.write_text(json.dumps(original))
    api = FilteredIPC({10: {'events': [], 'nextAfter': 110}, 110: {'events': [], 'nextAfter': 210}})
    with pytest.raises(rotate.Uncertain, match='backlog'):
        rotate.observe(api, rotate.Ledger(path, False))
    assert json.loads(path.read_text()) == original
    api = FilteredIPC({0: {'events': [], 'nextAfter': 100}, 100: {'events': [], 'nextAfter': 200}})
    fresh = rotate.Ledger(tmp_path / 'fresh.json', True)
    with pytest.raises(rotate.Uncertain, match='tip'):
        rotate.observe(api, fresh)
    assert fresh.data['cursor'] is None and not fresh.path.exists()


@pytest.fixture
def service():
    state = {"events": [], "details": {}, "workers": [
        {"sessionId": "s1", "workerId": "w1", "generation": 1, "status": "idle"},
        {"sessionId": "s2", "workerId": "w2", "generation": 2, "status": "running"},
        {"sessionId": "s3", "workerId": "w3", "generation": 1, "status": "running"}],
        "sessions": [{"id": "s1", "adapter": "codex"}, {"id": "s2", "adapter": "codex"},
                     {"id": "s3", "adapter": "cbc"}], "posts": [], "receipts": {},
        "auth": None, "auth_status": 401, "reject_posts": None, "revision": 0}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            # Drain the request body before returning rejection, so Windows
            # does not reset the socket before the client receives the status.
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
            if not self.authorized():
                return
            if state["reject_posts"] is not None:
                self.send_error(state["reject_posts"])
                return
            parsed = urlsplit(self.path)
            state["posts"].append((parsed.path, body))
            if parsed.path == "/api/sessions/wake":
                sid, = body["sessionIds"]
                assert sid in state["receipts"]
                value = {"ok": True, "results": [
                    {"status": "queued", "sessionId": sid, "queueItemId": "fixture-q-" + sid},
                ]}
            elif parsed.path.endswith("/worker/delayed-restart"):
                assert parse_qs(parsed.query).get("existing_worker_only") == ["true"]
                sid = parsed.path.split("/")[3]
                worker = next(w for w in state["workers"] if w["sessionId"] == sid)
                state["revision"] += 1
                value = {"delayedRestart": {"sessionId": sid, "workerId": worker["workerId"],
                    "generation": worker["generation"], "action": "restart", "status": "pending",
                    "revision": state["revision"]}}
                state["receipts"][sid] = value["delayedRestart"]
            else:
                self.send_error(404)
                return
            self.reply(value)

        def authorized(self):
            if state["auth"] is not None and self.headers.get("Authorization") != "Bearer " + state["auth"]:
                self.send_error(state["auth_status"])
                return False
            return True

        def reply(self, value):
            data = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self.authorized():
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/api/automation/events":
                after = int(parse_qs(parsed.query)["after"][0])
                rows = [row for row in state["events"] if row["seq"] > after][:100]
                value = {"events": rows, "nextAfter": rows[-1]["seq"] if rows else after}
            elif parsed.path == "/api/list":
                value = {"workers": state["workers"]}
            elif parsed.path == "/api/sessions":
                value = {"sessions": state["sessions"]}
            elif parsed.path.endswith("/worker/delayed-restart"):
                value = {"delayedRestart": state["receipts"].get(parsed.path.split("/")[3])}
            else:
                value = state["details"][parsed.path.rsplit("/", 1)[-1]]
            self.reply(value)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield rotate.Pan(f"http://127.0.0.1:{server.server_port}"), state
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_cursor_terminal_inventory_and_process_recovery(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    old = issue("old")
    state["events"].append({"seq": 1, "event": old})
    rotate.observe(api, ledger)
    assert ledger.data["cursor"] == 1 and not ledger.data["batches"]
    event = issue()
    state["events"].append({"seq": 2, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)
    batch, = ledger.data["batches"]
    assert set(batch["workers"]) == {"s1", "s2"}
    assert batch["workers"]["s2"]["status"] == "running"
    assert batch["targets"][0]["terminalKey"] == "term1"
    recovered = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, recovered)
    assert len(recovered.data["batches"]) == 1
    assert not state["posts"]


def test_stale_sample_does_not_suppress_terminal_match(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / 'ledger.json', True)
    rotate.observe(api, ledger)
    transient, terminal = issue('transient'), issue('terminal')
    state['events'] = [{'seq': 1, 'event': transient}, {'seq': 2, 'event': terminal}]
    state['details']['s1'] = detail(terminal)
    rotate.observe(api, ledger)
    assert ledger.data['seen']['transient']['outcome'] == 'stale'
    assert ledger.data['seen']['terminal']['outcome'] == 'matched'
    assert len(ledger.data['batches']) == 1
    assert not state['posts']


def test_legacy_match_without_batch_stays_unreplayed_and_reported(service, tmp_path, capsys):
    api, state = service
    path = tmp_path / 'ledger.json'
    original = {'version': 1, 'cursor': 10, 'pending': {}, 'batches': [], 'seen': {
        'old-stale': {'outcome': 'stale', 'sessionId': 's1', 'terminalKey': 'term1'},
        'old-match': {'outcome': 'matched', 'sessionId': 's1', 'terminalKey': 'term1'}}}
    path.write_text(json.dumps(original), encoding='utf-8')
    before = path.read_bytes()
    assert rotate.main(['--ledger', str(path), '--preflight-ledger']) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['historicalMatchesWithoutBatch'][0]['issueId'] == 'old-match'
    assert path.read_bytes() == before
    event = issue('new-sample')
    state['events'] = [{'seq': 11, 'event': event}]
    state['details']['s1'] = detail(event)
    ledger = rotate.Ledger(path, True)
    rotate.observe(api, ledger)
    assert not ledger.data['batches'] and ledger.data['cursor'] == 11
    assert not state['posts']


def test_existing_unknown_batch_still_deduplicates(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / 'ledger.json', True)
    rotate.observe(api, ledger)
    ledger.data['batches'].append({'status': 'unknown', 'workers': {},
        'targets': [{'targetSessionId': 's1', 'terminalKey': 'term1'}]})
    ledger.data['seen']['old'] = {'outcome': 'stale', 'sessionId': 's1', 'terminalKey': 'term1'}
    event = issue('terminal')
    state['events'] = [{'seq': 1, 'event': event}]
    state['details']['s1'] = detail(event)
    rotate.observe(api, ledger)
    assert len(ledger.data['batches']) == 1
    assert ledger.data['batches'][0]['status'] == 'unknown' and not state['posts']
    assert rotate.preflight_ledger(ledger.data)['unresolvedBatches'][0]['batchIndex'] == 0


def test_first_scan_emits_sanitized_health(service, tmp_path):
    api, state = service
    script = rotate.__file__
    result = subprocess.run([sys.executable, '-B', script, '--api', api.base,
        '--ledger', str(tmp_path / 'ledger.json'), '--bridge-data', str(tmp_path / 'bridge')],
        capture_output=True, text=True, timeout=15)
    assert result.returncode == 0
    health = json.loads(result.stdout)
    assert health['event'] == 'scan_complete' and health['cursor'] == 0
    assert health['pendingCount'] == health['batchCount'] == 0
    assert not (tmp_path / 'ledger.json').exists() and not state['posts']


@pytest.mark.skipif(os.name != 'nt', reason='candidate launcher targets Windows PowerShell')
def test_candidate_launcher_first_scan_and_single_owner(service, tmp_path):
    api, state = service
    launcher = 'D:/project/Pan/.workflow/codex-rotation-incident-20261123/run-live.candidate.ps1'
    command = ['powershell.exe', '-NoProfile', '-File', launcher, '-Python', sys.executable,
               '-Script', rotate.__file__, '-Api', api.base, '-Ledger', str(tmp_path / 'ledger.json'),
               '-Bridge', str(tmp_path / 'bridge'), '-OwnerLock', str(tmp_path / 'owner.lock'),
               '-IntervalSeconds', '30']
    output = tmp_path / 'watch.log'
    with output.open('w', encoding='utf-8') as stream:
        proc = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if output.exists() and 'scan_complete' in output.read_text(encoding='utf-8', errors='replace'):
                    break
                if proc.poll() is not None:
                    pytest.fail('candidate watcher exited before first scan')
                time.sleep(.05)
            else:
                pytest.fail('candidate watcher did not report the first scan')
            other = subprocess.run(command, capture_output=True, text=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            assert other.returncode != 0
            assert 'owner lock' in other.stderr
            assert not state['posts']
        finally:
            proc.terminate()
            proc.wait(timeout=10)


def test_nonterminal_then_terminal_and_conflicts(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = {"id": "s1", "adapter": "codex", "workerStatus": "running",
                               "workerId": "w1", "lastResult": None}
    rotate.observe(api, ledger)
    assert "issue-1" in ledger.data["pending"]
    state["details"]["s1"] = detail(event)
    state["details"]["s1"]["lastResult"]["executionSeq"] = 99
    rotate.observe(api, ledger)
    assert ledger.data["seen"]["issue-1"]["outcome"] == "stale"
    assert not ledger.data["batches"]


def test_duplicate_terminal_is_checked_once(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)
    # A different issue ID may belong to the same persisted terminal run.
    second = issue("issue-2")
    state["events"].append({"seq": 2, "event": second})
    state["details"]["s1"] = detail(second)
    rotate.observe(api, ledger)
    assert len(ledger.data["batches"]) == 1
    assert len(ledger.data["batches"][0]["targets"]) == 1
    assert len(ledger.data["seen"]) == 2


def test_concurrent_limit_targets_remain_in_same_batch(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    first = issue()
    second = issue("issue-2")
    second["payload"]["correlation"].update(sessionId="s2", workerId="w2", generation=2)
    second["system"].update(sessionId="s2", workerId="w2")
    second_detail = detail(second)
    second_detail.update(id="s2", workerId="w2")
    second_detail["lastResult"]["terminalKey"] = "term2"
    state["details"] = {"s1": detail(first), "s2": second_detail}
    state["events"] = [{"seq": 1, "event": first}, {"seq": 2, "event": second}]
    rotate.observe(api, ledger)
    batch, = ledger.data["batches"]
    assert {target["targetSessionId"] for target in batch["targets"]} == {"s1", "s2"}
    assert len(batch["workers"]) == 2
    bridge = RecoveryBridge()
    rotate.inspect_quota(ledger, bridge, now=lambda: bridge.at)
    run_reconcile(api, bridge, ledger)
    assert bridge.switches == [("a", "b")]
    assert [p for p, _ in state["posts"][:2]] == ["/api/sessions/s1/worker/delayed-restart", "/api/sessions/s2/worker/delayed-restart"]
    assert [body["sessionIds"] for p, body in state["posts"][2:] if p == "/api/sessions/wake"] == [["s1"], ["s2"]]
    run_reconcile(api, bridge, ledger)
    assert len(state["posts"]) == 4 and len(bridge.switches) == 1


def test_real_runtime_issue_mapping_can_be_correlated():
    from packages.core.runtime_issues import map_runtime_issue
    mapped = map_runtime_issue({"type": "error", "error": {"code": "usage_limit_reached"}},
        adapter="codex", metadata={"sessionId": "s1", "workerId": "w1", "runtimeEpoch": "e1",
                                    "generation": 1, "taskSeq": 2, "executionSeq": 3, "taskId": "t1"})
    event = mapped["event"]
    assert rotate.correlate(rotate.valid_issue(event), detail(event)) == "matched"


def ordinary_issue(i='ordinary'):
    from packages.core.runtime_issues import map_runtime_issue
    return map_runtime_issue({'type':'error', 'error':{'code':'usage_limit_reached'}},
        adapter='codex', metadata={'sessionId':'s1', 'workerId':'w1', 'generation':1,
            'executionSeq':3, 'runtimeEpoch':'e1', 'threadId':'thread', 'turnId':'turn'})['event']


def ordinary_detail(event):
    c = event['payload']['correlation']
    return {'id':'s1', 'adapter':'codex', 'workerId':'w1', 'workerStatus':'idle',
        'historyEpoch':'history1', 'historyRevision':7, 'queuePaused':False, 'illegalOffline':False,
        'lastResult':{'status':'error', 'terminalKey':'term1', 'workerId':c['workerId'],
            'generation':c['generation'], 'executionSeq':c['executionSeq'], 'taskSeq':None, 'taskId':None,
            'historyEpoch':'history1', 'historyRevision':7,
            'reportDiagnostic':{'runtimeIssue':copy.deepcopy(event['payload'])}}}


def test_ordinary_no_task_sequence_batches_only_new_matching_terminal(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / 'ledger.json', True)
    event = ordinary_issue()
    state['events'] = [{'seq':1, 'event':event}]
    rotate.observe(api, ledger)
    assert ledger.data['cursor'] == 1 and not ledger.data['batches']
    event2 = copy.deepcopy(event)
    event2['id'] = event2['payload']['issueId'] = 'new-issue'
    state['events'].append({'seq':2, 'event':event2})
    state['details']['s1'] = ordinary_detail(event2)
    rotate.observe(api, ledger)
    assert ledger.data['seen']['new-issue']['outcome'] == 'matched'
    assert len(ledger.data['batches']) == 1
    assert len(ledger.data['batches'][0]['targets']) == 1
    rotate.observe(api, rotate.Ledger(tmp_path / 'ledger.json', True))
    assert len(state['posts']) == 0


def test_ordinary_projected_event_and_duplicate_terminal(service, tmp_path):
    from packages.web.script_auth import project_collection
    api, state = service
    ledger = rotate.Ledger(tmp_path / 'ledger.json', True)
    rotate.observe(api, ledger)
    first = ordinary_issue()
    first['id'] = first['payload']['issueId'] = 'ordinary-first'
    second = copy.deepcopy(first)
    second['id'] = second['payload']['issueId'] = 'ordinary-second'
    profile = {'sessions':frozenset({'s1'})}
    rows = project_collection(profile, '/api/automation/events?after=0&limit=100', {
        'events':[{'seq':1, 'event':first}, {'seq':2, 'event':second}], 'nextAfter':2})['events']
    assert len(rows) == 2 and all(rotate.valid_issue(row['event']) for row in rows)
    state['events'], state['details']['s1'] = rows[:1], ordinary_detail(first)
    rotate.observe(api, ledger)
    state['events'], state['details']['s1'] = rows, ordinary_detail(second)
    rotate.observe(api, ledger)
    assert len(ledger.data['batches']) == 1
    assert len(ledger.data['batches'][0]['targets']) == 1
    assert ledger.data['cursor'] == 2 and len(ledger.data['seen']) == 2


@pytest.mark.parametrize('field,value', [
    ('workerId','other'), ('generation',2), ('executionSeq',4), ('taskSeq',2), ('taskId','other'),
])
def test_ordinary_terminal_conflicting_identity_is_stale(field, value):
    event = ordinary_issue()
    candidate = rotate.valid_issue(event)
    assert candidate is not None
    result = ordinary_detail(event)
    result['lastResult'][field] = value
    assert rotate.correlate(candidate, result) == 'stale'


def test_ordinary_epoch_and_optional_task_fields_fail_closed():
    event = ordinary_issue()
    candidate = rotate.valid_issue(event)
    assert candidate is not None
    result = ordinary_detail(event)
    del result['lastResult']['taskSeq']
    del result['lastResult']['taskId']
    assert rotate.correlate(candidate, result) == 'matched'
    result['lastResult']['reportDiagnostic']['runtimeIssue']['correlation']['runtimeEpoch'] = 'different'
    assert rotate.correlate(candidate, result) == 'stale'
    for field, invalid in [('taskSeq', None), ('taskSeq', True), ('taskSeq', 0), ('taskId', ''), ('taskId', 4)]:
        bad = copy.deepcopy(event)
        bad['payload']['correlation'][field] = invalid
        assert rotate.valid_issue(bad) is None


@pytest.mark.parametrize("changes", [
    {"adapter": "cbc"}, {"confidence": "adapter_text"},
    {"classification": "codex_capacity"}, {"classification": "provider_rate_limited"},
])
def test_only_structured_codex_quota(changes):
    assert rotate.valid_issue(issue(**changes)) is None


def test_terminal_diagnostic_and_system_identity_must_match():
    event = issue()
    candidate = rotate.valid_issue(event)
    good = detail(event)
    assert rotate.correlate(candidate, good) == "matched"
    good["lastResult"]["reportDiagnostic"]["runtimeIssue"]["issueId"] = "other"
    assert rotate.correlate(candidate, good) == "stale"
    event["system"]["workerId"] = "conflict"
    assert rotate.valid_issue(event) is None


def test_new_history_invalidates_late_failure():
    event = issue()
    value = detail(event)
    value["historyRevision"] += 1
    assert rotate.correlate(rotate.valid_issue(event), value) == "stale"


def row(pid, five, week, freset=1200, wreset=1400):
    return {"id": pid, "fiveHour": {"usedPercent": five, "resetsAt": freset},
            "week": {"usedPercent": week, "resetsAt": wreset}}


def test_quota_decisions_and_reset_grace():
    snap = {"status": "known", "observedAt": 1000, "currentAccountId": "a",
            "accounts": [row("a", 10, 30), row("b", 5, 1)]}
    assert rotate.choose(snap, 1000) == {"decision": "current", "provider": "a"}
    snap["accounts"][0]["fiveHour"]["usedPercent"] = 100
    assert rotate.choose(snap, 1000) == {"decision": "switch", "provider": "b", "expected": "a"}
    snap["accounts"][1]["fiveHour"]["usedPercent"] = 100
    assert rotate.choose(snap, 1000) == {"decision": "wait", "until": 1500}
    assert rotate.choose(snap, 1301) == {"decision": "unknown"}


def test_dry_run_does_not_create_ledger(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", False)
    rotate.observe(api, ledger)
    assert not ledger.path.exists() and not state["posts"]


def test_fake_bridge_disabled_and_wait_then_resample(service, tmp_path):
    api, state = service
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)

    class Settings:
        def gates(self, app):
            assert app == "codex"
            return {"b": True}

    class Bridge:
        settings = Settings()
        calls = []

        def read_only(self, op, args, *, deadline):
            self.calls.append((op, dict(args)))
            if op == "list":
                return {"currentProviderId": "a", "providers": [
                    {"id": "a", "category": "official"}, {"id": "b", "category": "official"}]}
            assert args["id"] == "a"
            return {"id": "a", "app": "codex", "quota": {"status": "ok", "available": True,
                "queriedAt": 1000000, "tiers": [
                    {"name": "five_hour", "usedPercent": 100, "resetsAt": 1200},
                    {"name": "seven_day", "usedPercent": 20, "resetsAt": 1400}]}}

    bridge = Bridge()
    assert rotate.inspect_quota(ledger, bridge, now=lambda: 1000) == {"decision": "wait", "until": 1500}
    assert ledger.data["batches"][0]["status"] == "waiting_reset"
    count = len(bridge.calls)
    rotate.inspect_quota(ledger, bridge, now=lambda: 1200)
    assert len(bridge.calls) == count
    # At wake time the original observation is stale and cannot be reused.
    assert rotate.inspect_quota(ledger, bridge, now=lambda: 1500) == {"decision": "unknown"}
    assert not state["posts"]


def test_actual_delayed_restart_endpoint_only_exposes_latest_intent(monkeypatch):
    """Two same-generation intents: the first cannot be queried after replacement."""
    import asyncio
    from types import SimpleNamespace
    from packages.core import worker
    from packages.web import server

    async def run():
        w = SimpleNamespace(session_id="s", worker_id="w", generation=4, status="running", pending_restart=False)
        monkeypatch.setattr(worker, "workers", {"w": w})
        monkeypatch.setattr(worker, "_delayed_restarts", {})
        monkeypatch.setattr(worker, "_get_session_shallow", lambda sid: object())
        monkeypatch.setattr(worker, "find_alive_worker_by_session", lambda sid: w)
        monkeypatch.setattr(worker, "find_worker_by_session", lambda sid: w)
        monkeypatch.setattr(server, "_summary_session_get", lambda sid: object())
        monkeypatch.setattr(server.legal_offline, "is_illegal", lambda s: False)
        first = (await server.api_delayed_restart("s", existing_worker_only=True))["delayedRestart"]
        worker._set_restart_intent(worker._delayed_restarts["s"], "failed", "synthetic failure")
        second = (await server.api_delayed_restart("s", existing_worker_only=True))["delayedRestart"]
        readback = (await server.api_delayed_restart_state("s"))["delayedRestart"]
        assert first["workerId"] == second["workerId"] == "w"
        assert first["generation"] == second["generation"] == 4
        assert first["revision"] != second["revision"]
        assert readback == second
        assert "requestId" not in first and "requestId" not in readback
        await asyncio.sleep(0)

    asyncio.run(run())


class RecoveryBridge:
    def __init__(self, current_used=100, alternative_used=20):
        self.current = "a"
        self.usage = {"a": current_used, "b": alternative_used}
        self.switches = []
        self.settings = self
        self.at = 1000

    def gates(self, app):
        return {}

    def read_only(self, op, args, *, deadline=None):
        if op == "list":
            return {"currentProviderId": self.current, "providers": [
                {"id": "a", "category": "official"}, {"id": "b", "category": "official"}]}
        pid = args["id"]
        return {"id": pid, "app": "codex", "quota": {"status": "ok", "available": True,
            "queriedAt": self.at * 1000, "tiers": [
                {"name": "five_hour", "usedPercent": self.usage[pid], "resetsAt": self.at + 200},
                {"name": "seven_day", "usedPercent": 20, "resetsAt": self.at + 400}]}}

    def invoke(self, op, args):
        assert op == "auto_switch" and args["expectedCurrentId"] == self.current
        self.switches.append((self.current, args["id"]))
        self.current = args["id"]
        return {"currentProviderId": self.current}


def run_reconcile(api, bridge, ledger):
    rotate.reconcile(api, bridge, ledger, now=lambda: bridge.at)


def begin_recovery(api, state, path, bridge):
    ledger = rotate.Ledger(path, True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)
    rotate.inspect_quota(ledger, bridge, now=lambda: 1000)
    run_reconcile(api, bridge, ledger)
    return ledger


def test_http_recovery_switch_then_restart_and_enqueue_once(service, tmp_path):
    api, state = service
    secret = secrets.token_urlsafe(24)
    state["auth"] = secret
    api = rotate.Pan(api.base, secret)
    bridge = RecoveryBridge()
    path = tmp_path / "ledger.json"
    ledger = begin_recovery(api, state, path, bridge)
    assert bridge.switches == [("a", "b")]
    assert [p for p, _ in state["posts"]] == [
        "/api/sessions/s1/worker/delayed-restart",
        "/api/sessions/s2/worker/delayed-restart",
        "/api/sessions/wake",
    ]
    assert all(r["status"] == "pending" for r in state["receipts"].values())
    wake = state["posts"][-1][1]
    assert wake["sessionIds"] == ["s1"] and wake["source"] == "automation"
    assert wake["clientMessageId"].startswith("codex-limit:")
    target, = ledger.data["batches"][0]["targets"]
    assert target["continuation"]["status"] == "queued"
    assert secret not in path.read_text(encoding="utf-8")
    run_reconcile(api, bridge, rotate.Ledger(path, True))
    assert len(state["posts"]) == 3 and bridge.switches == [("a", "b")]


@pytest.mark.parametrize("change,value", [
    ("historyRevision", 8), ("illegalOffline", True), ("queuePaused", True),
])
def test_continuation_skips_changed_or_blocked_session(service, tmp_path, change, value):
    api, state = service
    bridge = RecoveryBridge(10, 10)

    class ChangeAfterRestart:
        def __getattr__(self, name):
            return getattr(api, name)

        def restart(self, sid):
            response = api.restart(sid)
            if sid == "s1":
                state["details"][sid][change] = value
            return response

    ledger = begin_recovery(ChangeAfterRestart(), state, tmp_path / "ledger.json", bridge)
    assert not bridge.switches
    assert not any(path == "/api/sessions/wake" for path, _ in state["posts"])
    target, = ledger.data["batches"][0]["targets"]
    assert target["continuation"]["status"] == "skipped_changed_or_blocked"


def test_all_exhausted_waits_then_refreshes_before_restarts(service, tmp_path):
    api, state = service
    bridge = RecoveryBridge(100, 100)
    ledger = begin_recovery(api, state, tmp_path / "ledger.json", bridge)
    batch, = ledger.data["batches"]
    assert batch["status"] == "waiting_reset" and batch["quotaDecision"]["until"] == 1500
    assert not state["posts"] and not bridge.switches
    rotate.inspect_quota(ledger, bridge, now=lambda: 1499)
    run_reconcile(api, bridge, ledger)
    assert not state["posts"]
    bridge.at = 1500
    bridge.usage["a"] = 5
    rotate.inspect_quota(ledger, bridge, now=lambda: 1500)
    run_reconcile(api, bridge, ledger)
    assert len(state["posts"]) == 3 and not bridge.switches


def test_restart_response_loss_queries_receipt_without_replay(service, tmp_path):
    api, state = service
    bridge = RecoveryBridge(10, 10)

    class LoseRestart:
        def __getattr__(self, name):
            return getattr(api, name)

        def restart(self, sid):
            api.restart(sid)
            raise OSError("synthetic response loss")

    path = tmp_path / "ledger.json"
    ledger = begin_recovery(LoseRestart(), state, path, bridge)
    for worker in ledger.data["batches"][0]["workers"].values():
        assert worker["restart"]["status"] == "unknown"
        assert worker["restart"]["observedReceipt"]["status"] == "pending"
    run_reconcile(LoseRestart(), bridge, rotate.Ledger(path, True))
    assert len(state["posts"]) == 2


def test_send_response_loss_is_never_replayed(service, tmp_path):
    api, state = service
    bridge = RecoveryBridge(10, 10)
    path = tmp_path / "ledger.json"
    class LoseSend:
        def __getattr__(self, name):
            return getattr(api, name)

        def enqueue_continuation(self, sid, key):
            api.enqueue_continuation(sid, key)
            raise OSError("synthetic response loss")

    ledger = begin_recovery(LoseSend(), state, path, bridge)
    assert ledger.data["batches"][0]["targets"][0]["continuation"]["status"] == "unknown"
    run_reconcile(api, bridge, rotate.Ledger(path, True))
    assert len([p for p, _ in state["posts"] if p == "/api/sessions/wake"]) == 1


def test_restart_accepts_current_worker_for_same_session(service, tmp_path):
    api, state = service
    bridge = RecoveryBridge(10, 10)

    class RaceRestart:
        def __getattr__(self, name):
            return getattr(api, name)

        def restart(self, sid):
            if sid == "s1":
                state["workers"][0].update(workerId="unexpected", generation=2)
            return api.restart(sid)

    ledger = begin_recovery(RaceRestart(), state, tmp_path / "ledger.json", bridge)
    batch, = ledger.data["batches"]
    receipt = batch["workers"]["s1"]["restart"]["acceptedReceipt"]
    assert receipt["workerId"] == "unexpected" and receipt["generation"] == 2
    assert batch["targets"][0]["continuation"]["status"] == "queued"


def test_expired_quota_decision_does_not_dispatch(service, tmp_path):
    api, state = service
    bridge = RecoveryBridge()
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)
    rotate.inspect_quota(ledger, bridge, now=lambda: 1000)
    rotate.reconcile(api, bridge, ledger, now=lambda: 1301)
    assert ledger.data["batches"][0]["quotaDecision"]["decision"] == "unknown"
    assert not state["posts"] and not bridge.switches


@pytest.mark.parametrize("status", [401, 403])
def test_bearer_authorization_stops_on_denial(service, tmp_path, status):
    api, state = service
    secret = secrets.token_urlsafe(24)
    state["auth"] = secret
    state["auth_status"] = status
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    with pytest.raises(rotate.AuthDenied):
        rotate.observe(api, ledger)
    assert not ledger.path.exists() and not state["posts"]
    authenticated = rotate.Pan(api.base, secret)
    rotate.observe(authenticated, ledger)
    assert secret not in ledger.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("status", [401, 403])
def test_bearer_denied_at_restart_stops_later_effects(service, tmp_path, status):
    api, state = service
    secret = secrets.token_urlsafe(24)
    state["auth"] = secret
    api = rotate.Pan(api.base, secret)
    bridge = RecoveryBridge(10, 10)
    ledger = rotate.Ledger(tmp_path / "ledger.json", True)
    rotate.observe(api, ledger)
    event = issue()
    state["events"].append({"seq": 1, "event": event})
    state["details"]["s1"] = detail(event)
    rotate.observe(api, ledger)
    assert rotate.inspect_quota(ledger, bridge, now=lambda: 1000) == {"decision": "current", "provider": "a"}
    state["reject_posts"] = status
    with pytest.raises(rotate.AuthDenied):
        run_reconcile(api, bridge, ledger)
    assert not state["posts"]
    workers = ledger.data["batches"][0]["workers"]
    assert workers["s1"]["restart"]["status"] == "unknown"
    assert "restart" not in workers["s2"]
    assert secret not in ledger.path.read_text(encoding="utf-8")
