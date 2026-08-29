"""Tests for /ws/agent event subscription filtering (Phase B).

Verifies the `broadcast()` filtering logic for agent clients:
- default (no subscribe): only worker.result is delivered
- after subscribe([...]): only listed types delivered
- "*" wildcard: all events delivered
- explicit [] resets to default
- dead agent connections are pruned
"""

import asyncio
import sys
from pathlib import Path

# Make packages importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import packages.web.server as srv


class FakeWS:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed = False

    async def send_json(self, data: dict):
        self.sent.append(data)


def _cleanup():
    srv.agent_clients.clear()
    srv.agent_subscriptions.clear()
    srv.ws_clients.clear()


def _run(coro):
    return asyncio.run(coro)


# ── tests ──

def test_default_only_worker_result():
    """Unsubscribed agent connection receives only worker.result."""
    _cleanup()
    ws = FakeWS()
    srv.agent_clients.add(ws)

    events = [
        {"type": "worker.result", "workerId": "w1"},
        {"type": "worker.stream", "workerId": "w1", "event": {"type": "assistant"}},
        {"type": "worker.status", "workerId": "w1", "status": "running"},
        {"type": "session.created", "sessionId": "s1"},
    ]
    for e in events:
        _run(srv.broadcast(e))

    types = [m.get("type") for m in ws.sent]
    assert types == ["worker.result"], f"expected only worker.result, got {types}"
    _cleanup()


def test_subscribe_filters_event_types():
    """After subscribe, only listed event types are delivered."""
    _cleanup()
    ws = FakeWS()
    srv.agent_clients.add(ws)
    sub = srv.AgentSubscription(event_types={"worker.result", "worker.status"})
    srv.agent_subscriptions[ws] = sub

    events = [
        {"type": "worker.result", "workerId": "w1"},
        {"type": "worker.stream", "workerId": "w1"},
        {"type": "worker.status", "workerId": "w1", "status": "idle"},
    ]
    for e in events:
        _run(srv.broadcast(e))

    types = [m.get("type") for m in ws.sent]
    assert types == ["worker.result", "worker.status"], f"got {types}"
    _cleanup()


def test_wildcard_subscribes_all():
    """'*' in subscription delivers everything."""
    _cleanup()
    ws = FakeWS()
    srv.agent_clients.add(ws)
    sub = srv.AgentSubscription(event_types={"*"})
    srv.agent_subscriptions[ws] = sub

    events = [
        {"type": "worker.stream", "workerId": "w1"},
        {"type": "session.created", "sessionId": "s1"},
    ]
    for e in events:
        _run(srv.broadcast(e))

    assert len(ws.sent) == 2, f"wildcard should deliver all, got {ws.sent}"
    _cleanup()


def test_empty_subscription_resets_to_default():
    """subscribe([]) semantics: empty list resets to default event types."""
    _cleanup()
    sub = srv.AgentSubscription(event_types={"worker.stream"})  # start non-default
    # Simulate endpoint logic: [] → default subscription
    raw_types = []
    types = set(str(t) for t in raw_types)
    sub.event_types = types if types else set(srv._AGENT_DEFAULT_SUBSCRIPTION)
    assert sub.event_types == set(srv._AGENT_DEFAULT_SUBSCRIPTION)
    _cleanup()


def test_session_ids_filter():
    """worker.result filtered to subscribed session ids."""
    _cleanup()
    ws = FakeWS()
    srv.agent_clients.add(ws)
    sub = srv.AgentSubscription(event_types={"worker.result"}, session_ids={"sA"})
    srv.agent_subscriptions[ws] = sub

    events = [
        {"type": "worker.result", "workerId": "w1", "sessionId": "sA", "taskSeq": 1},
        {"type": "worker.result", "workerId": "w1", "sessionId": "sB", "taskSeq": 1},
    ]
    for e in events:
        _run(srv.broadcast(e))

    # only sA delivered
    got_sids = [m.get("sessionId") for m in ws.sent]
    assert got_sids == ["sA"], f"session filter failed, got {got_sids}"
    _cleanup()


def test_consumed_seq_tracked():
    """broadcast records consumed result seq per session."""
    _cleanup()
    ws = FakeWS()
    srv.agent_clients.add(ws)
    sub = srv.AgentSubscription(event_types={"worker.result"})
    srv.agent_subscriptions[ws] = sub

    _run(srv.broadcast({"type": "worker.result", "workerId": "w1", "sessionId": "s1", "taskSeq": 3}))

    assert sub.consumed_seq.get("s1") == 3, f"consumed seq not tracked: {sub.consumed_seq}"
    _cleanup()


def test_dead_agent_pruned_on_broadcast():
    """A send_json that raises removes the connection."""
    _cleanup()

    class BrokenWS(FakeWS):
        async def send_json(self, data: dict):
            raise ConnectionError("boom")

    ws = BrokenWS()
    srv.agent_clients.add(ws)
    sub = srv.AgentSubscription(event_types={"worker.result"})
    srv.agent_subscriptions[ws] = sub

    _run(srv.broadcast({"type": "worker.result", "workerId": "w1"}))

    assert ws not in srv.agent_clients, "broken agent connection not pruned"
    _cleanup()


def test_dashboard_replays_live_native_interactions():
    """Dashboard reconnect restores only prompts owned by live workers."""
    _cleanup()
    ws = FakeWS()

    class LiveProcess:
        returncode = None

    class FakeWorker:
        worker_id = "worker-1"
        session_id = "ses-1"
        process = LiveProcess()

    original_list = srv.worker.list_workers
    original_events = srv.worker.pending_interaction_events
    original_status = srv.worker.native_status_event
    original_usage = srv.worker.native_usage_event
    try:
        srv.worker.list_workers = lambda: [FakeWorker()]
        srv.worker.native_status_event = lambda w: {
            "type": "codex.thread_status",
            "native_status": {"type": "active"},
        }
        srv.worker.native_usage_event = lambda w: {
            "type": "codex.token_usage",
            "token_usage": {"last": {"totalTokens": 150}},
        }
        srv.worker.pending_interaction_events = lambda w: [{
            "type": "approval.request", "request_id": 3,
            "method": "item/commandExecution/requestApproval", "params": {},
        }]
        _run(srv._replay_pending_interactions(ws))
    finally:
        srv.worker.list_workers = original_list
        srv.worker.pending_interaction_events = original_events
        srv.worker.native_status_event = original_status
        srv.worker.native_usage_event = original_usage

    assert ws.sent == [
        {
            "type": "worker.stream", "workerId": "worker-1", "sessionId": "ses-1",
            "event": {
                "type": "codex.thread_status",
                "native_status": {"type": "active"},
            },
            "replayed": True,
        },
        {
            "type": "worker.stream", "workerId": "worker-1", "sessionId": "ses-1",
            "event": {
                "type": "codex.token_usage",
                "token_usage": {"last": {"totalTokens": 150}},
            },
            "replayed": True,
        },
        {
            "type": "worker.stream", "workerId": "worker-1", "sessionId": "ses-1",
            "event": {
                "type": "approval.request", "request_id": 3,
                "method": "item/commandExecution/requestApproval", "params": {},
            },
            "replayed": True,
        },
    ]
    _cleanup()


def test_dashboard_replay_can_filter_sessions():
    """A dashboard may request only selected sessions."""
    _cleanup()
    ws = FakeWS()

    class LiveProcess:
        returncode = None

    class FakeWorker:
        process = LiveProcess()

        def __init__(self, worker_id: str, session_id: str):
            self.worker_id = worker_id
            self.session_id = session_id

    workers = [FakeWorker("worker-a", "ses-a"), FakeWorker("worker-b", "ses-b")]
    original_list = srv.worker.list_workers
    original_events = srv.worker.pending_interaction_events
    original_status = srv.worker.native_status_event
    try:
        srv.worker.list_workers = lambda: workers
        srv.worker.native_status_event = lambda w: None
        srv.worker.pending_interaction_events = lambda w: [{
            "type": "codex.user_input", "request_id": w.session_id,
            "method": "item/tool/requestUserInput", "params": {},
        }]
        _run(srv._replay_pending_interactions(ws, ["ses-b"]))
    finally:
        srv.worker.list_workers = original_list
        srv.worker.pending_interaction_events = original_events
        srv.worker.native_status_event = original_status

    assert [m["sessionId"] for m in ws.sent] == ["ses-b"]
    _cleanup()


if __name__ == "__main__":
    test_default_only_worker_result()
    test_subscribe_filters_event_types()
    test_wildcard_subscribes_all()
    test_empty_subscription_resets_to_default()
    test_session_ids_filter()
    test_consumed_seq_tracked()
    test_dead_agent_pruned_on_broadcast()
    print("\n=== ALL SUBSCRIPTION TESTS PASSED ===")
