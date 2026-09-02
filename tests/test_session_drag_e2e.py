"""Real HTTP + WebSocket E2E for session drag reorder & claim (isolated).

These tests drive the REAL FastAPI app through TestClient (HTTP handlers +
WS broadcast + real on-disk persistence into the conftest-isolated tmp
SESSION_DIR). They do NOT touch 8768/8767 — no external server is started.

Covered (the drag front↔back contract):
- GET /api/sessions returns real persisted sessions in server order;
- POST /api/sessions/order is reflected by GET and broadcast as
  ``session.orderUpdated`` on /ws;
- the order survives a "restart" (cache dropped, reloaded from disk);
- POST /api/claim makes B.managed contain A and A.managedBy = B, persisted;
- an invalid order (unknown id) is rejected with ok:false and nothing changes.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import session as _sess  # noqa: E402
import packages.web.server as srv  # noqa: E402


def _recv_until(ws, want_type, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = ws.receive_json()
        if data.get("type") == want_type:
            return data
    raise AssertionError(f"no {want_type} broadcast within {timeout}s")


def test_order_and_claim_real_http_ws_e2e():
    from fastapi.testclient import TestClient

    ids: list[str] = []
    with TestClient(srv.app) as client:
        # ── 读取真实 sessions：先通过 POST /api/sessions 创建三个 ──
        for name in ("ses_a", "ses_b", "ses_c"):
            r = client.post("/api/sessions", json={"name": name})
            assert r.status_code == 200, r.text
            body = r.json()
            assert "id" in body, body
            ids.append(body["id"])
        a, b, c = ids
        assert len(set(ids)) == 3

        # GET 反映创建顺序（新 session order=None 按 created_at 排末尾）。
        got = client.get("/api/sessions?summary=1").json()["sessions"]
        assert [s["id"] for s in got] == [a, b, c], got

        # ── 拖 A 到 B/C 之间 → POST /api/sessions/order：C,A,B ──
        with client.websocket_connect("/ws") as ws:
            time.sleep(0.15)  # let the server-side handler register this client
            r = client.post("/api/sessions/order", json={"sessionIds": [c, a, b]})
            assert r.status_code == 200
            payload = r.json()
            assert payload["ok"] is True, payload
            assert payload["order"] == [c, a, b], payload

            # WS 广播反映新顺序
            evt = _recv_until(ws, "session.orderUpdated")
            assert evt["order"] == [c, a, b], evt

        # GET /api/sessions 反映新顺序
        got = client.get("/api/sessions?summary=1").json()["sessions"]
        assert [s["id"] for s in got] == [c, a, b], got
        assert [s["order"] for s in got] == [0, 1, 2], got

        # ── 拖 A 到 B 卡片中心 → POST /api/claim（B manage A）──
        r = client.post("/api/claim", json={"managerId": b, "sessionId": a})
        assert r.status_code == 200
        claim = r.json()
        assert claim["ok"] is True, claim
        assert a in claim["managed"], claim

        # GET 完整 session：B.managed 含 A；A.managedBy = B
        b_full = client.get(f"/api/sessions/{b}").json()
        assert a in b_full["managed"], b_full
        a_full = client.get(f"/api/sessions/{a}").json()
        assert a_full["managedBy"] == b, a_full

        # ── 刷新（模拟服务重启：清缓存从磁盘重读）后顺序与管理关系保持 ──
        _sess._cache.clear()
        _sess._all_loaded = False
        got = client.get("/api/sessions?summary=1").json()["sessions"]
        assert [s["id"] for s in got] == [c, a, b], "顺序在重启后未保持"
        b_full = client.get(f"/api/sessions/{b}").json()
        assert a in b_full["managed"], "managed 关系在重启后未保持"
        a_full = client.get(f"/api/sessions/{a}").json()
        assert a_full["managedBy"] == b, "managedBy 在重启后未保持"

        # ── 非法/失败响应：未知 id 被拒绝，顺序不变，不假装成功 ──
        r = client.post("/api/sessions/order", json={"sessionIds": [c, a, "ses_ghost"]})
        assert r.status_code == 200
        err = r.json()
        assert err["ok"] is False, err
        assert err["error"]["code"] == "session_not_found", err
        got = client.get("/api/sessions?summary=1").json()["sessions"]
        assert [s["id"] for s in got] == [c, a, b], "拒绝后顺序不应改变"


def test_invalid_order_bodies_are_rejected_http():
    from fastapi.testclient import TestClient

    with TestClient(srv.app) as client:
        r = client.post("/api/sessions", json={"name": "only_one"})
        sid = r.json()["id"]
        for body in ({}, {"sessionIds": sid}, {"sessionIds": [1, 2]},
                     {"sessionIds": [sid, sid]}):
            resp = client.post("/api/sessions/order", json=body).json()
            assert resp["ok"] is False, body
        got = client.get("/api/sessions?summary=1").json()["sessions"]
        assert [s["id"] for s in got] == [sid]
