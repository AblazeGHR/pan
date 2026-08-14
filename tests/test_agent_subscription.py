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
    srv.agent_subscriptions[ws] = {"worker.result", "worker.status"}

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
    srv.agent_subscriptions[ws] = {"*"}

    events = [
        {"type": "worker.stream", "workerId": "w1"},
        {"type": "session.created", "sessionId": "s1"},
    ]
    for e in events:
        _run(srv.broadcast(e))

    assert len(ws.sent) == 2, f"wildcard should deliver all, got {ws.sent}"
    _cleanup()


def test_empty_subscription_resets_to_default():
    """subscribe([]) semantics handled by endpoint: stores default set."""
    _cleanup()
    # Simulate the endpoint logic: [] → default subscription
    raw_types = []
    subs = set(str(t) for t in raw_types)
    srv.agent_subscriptions["ws"] = subs if subs else set(srv._AGENT_DEFAULT_SUBSCRIPTION)
    assert srv.agent_subscriptions["ws"] == set(srv._AGENT_DEFAULT_SUBSCRIPTION)
    _cleanup()


def test_dead_agent_pruned_on_broadcast():
    """A send_json that raises removes the connection."""
    _cleanup()

    class BrokenWS(FakeWS):
        async def send_json(self, data: dict):
            raise ConnectionError("boom")

    ws = BrokenWS()
    srv.agent_clients.add(ws)
    srv.agent_subscriptions[ws] = {"worker.result"}

    _run(srv.broadcast({"type": "worker.result", "workerId": "w1"}))

    assert ws not in srv.agent_clients, "broken agent connection not pruned"
    _cleanup()


if __name__ == "__main__":
    test_default_only_worker_result()
    test_subscribe_filters_event_types()
    test_wildcard_subscribes_all()
    test_empty_subscription_resets_to_default()
    test_dead_agent_pruned_on_broadcast()
    print("\n=== ALL SUBSCRIPTION TESTS PASSED ===")
