"""Confirmed origin loss is scoped to a stable private Session/native snapshot."""
import asyncio
import copy
import json
import threading
import sqlite3

import httpx
import pytest

from packages.core import session as sess
from packages.web import server
from packages.core.adapters.codex import sessions as codex
from test_codex_reimport_origins import origin, private_rollout


async def send(**extra):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=server.app, raise_app_exceptions=False),
                                 base_url="http://test") as client:
        r = await client.post("/api/adapters/codex/sessions/import",
                              json={"session_id": "private-thread", **extra})
        return r.status_code, r.json() if r.status_code == 200 else r.text


def setup(tmp_path, monkeypatch):
    matched = origin(tmp_path, source="agent", attachment=False, text="matched body")
    missing = origin(tmp_path, source="user", attachment=False, text="lost native body")
    missing["messageId"] = "pan:" + "2" * 32
    missing["taskId"] = "missing-task"
    path = private_rollout(tmp_path, monkeypatch, [{"role": "user", "content": matched["content"]},
                                                {"role": "assistant", "content": "native reply"}])
    s = sess.create("confirmation-private", adapter="codex", cli_session_id="private-thread",
                    history=[matched, missing])
    s.last_result = {"status": "done", "result": "before"}
    sess.save_full(s)
    return s, path, matched, missing


def check(s):
    status, result = asyncio.run(send())
    assert status == 200
    assert result["errorCode"] == "reimport_origin_conflict"
    assert result["conflict"]["sessionId"] == s.id
    return result["conflict"]


def force(s, conflict):
    return {"force": True, "panSessionId": s.id, "confirmationToken": conflict["confirmationToken"]}


def disk(s):
    return {p: p.read_bytes() for p in [sess._path(s.id), sess._history_path(s.id)]}


def test_conflict_summary_cancel_then_confirm_and_reload(tmp_path, monkeypatch):
    s, path, matched, missing = setup(tmp_path, monkeypatch)
    before = disk(s)
    conflict = check(s)
    assert conflict["count"] == 1
    assert conflict["items"] == [{"position": 2, "messageId": missing["messageId"], "source": "user",
                                   "reason": "missing_or_changed", "preview": "lost native body"}]
    assert conflict["historyRevision"] == s.history_revision
    assert disk(s) == before  # closing the modal entails no mutation/request
    status, result = asyncio.run(send(**force(s, conflict)))
    assert status == 200 and result.get("reimported") is True
    assert [r["content"] for r in s.history] == ["matched body", "native reply"]
    for key in ("messageId", "taskId", "source", "sourceSessionId", "parts", "queueEnvelopes"):
        assert s.history[0][key] == matched[key]
    sess._cache.clear()
    assert sess.get(s.id).history == s.history
    assert path.exists()


@pytest.mark.parametrize("change", ["append", "native", "native_nonbody", "wrong_session", "forged", "readonly", "replay"])
def test_old_confirmation_cannot_override_changes(tmp_path, monkeypatch, change):
    s, path, _, _ = setup(tmp_path, monkeypatch)
    conflict = check(s)
    args = force(s, conflict)
    if change == "append":
        sess.append_history(s, {"role": "user", "content": "new Pan message"})
        sess.save(s)
    if change == "native":
        with path.open("a", encoding="utf-8") as f:
            f.write('\n' + json.dumps({"type": "response_item", "payload": {"type": "message", "role": "user",
                  "content": [{"type": "input_text", "text": "new native message"}]}}))
    if change == "native_nonbody":
        with path.open("a", encoding="utf-8") as f:
            f.write('\n' + json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied"}}))
    if change == "wrong_session": args["panSessionId"] = "another-session"
    if change == "forged": args["confirmationToken"] = "invalid"
    if change == "readonly":
        s.readonly_session = True
        sess.save(s)
    if change == "replay": assert asyncio.run(send(**args))[1].get("reimported") is True
    old = copy.deepcopy(s.history)
    before = disk(s)
    status, result = asyncio.run(send(**args))
    assert status == 200 and result.get("ok") is False
    assert result["errorCode"] == ("readonly_session" if change == "readonly" else "reimport_stale")
    assert s.history == old and disk(s) == before
    if change in {"append", "native", "native_nonbody"}:
        assert result["conflict"]["stale"] is True
        assert result["conflict"]["confirmationToken"] != conflict["confirmationToken"]


def test_force_without_confirmation_is_not_a_bypass(tmp_path, monkeypatch):
    s, _, _, _ = setup(tmp_path, monkeypatch)
    before = disk(s)
    assert asyncio.run(send(force=True))[1]["errorCode"] == "reimport_stale"
    assert disk(s) == before


def test_readonly_normal_check_is_also_rejected(tmp_path, monkeypatch):
    s, _, _, _ = setup(tmp_path, monkeypatch)
    s.readonly_session = True
    assert asyncio.run(send())[1]["errorCode"] == "readonly_session"


def test_confirmed_write_failure_restores_memory_and_disk(tmp_path, monkeypatch):
    s, _, _, _ = setup(tmp_path, monkeypatch)
    conflict = check(s)
    old = copy.deepcopy(s.history)
    before = disk(s)
    real_save = sess._save_body
    def fail(*args, **kwargs):
        real_save(*args, **kwargs)
        raise OSError("injected confirmed save failure")
    monkeypatch.setattr(sess, "_save_body", fail)
    assert asyncio.run(send(**force(s, conflict)))[0] == 500
    assert s.history == old and disk(s) == before
    assert s.last_result == {"status": "done", "result": "before"}
    sess._cache.clear()
    assert sess.get(s.id).history == old


@pytest.mark.parametrize("change", ["append", "native", "readonly", "unversioned"])
def test_ticket_revalidates_while_confirmation_waits(tmp_path, monkeypatch, change):
    s, path, _, _ = setup(tmp_path, monkeypatch)
    conflict = check(s)
    entered, release = threading.Event(), threading.Event()
    async def run():
        blocker = asyncio.create_task(sess._persist_async(s.id, lambda: (entered.set(), release.wait(5))))
        assert await asyncio.to_thread(entered.wait, 2)
        request = asyncio.create_task(send(**force(s, conflict)))
        await asyncio.sleep(0.05)
        if change == "append": sess.append_history(s, {"role": "user", "content": "new while waiting"})
        if change == "native":
            with path.open("a", encoding="utf-8") as f: f.write('\n' + json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied"}}))
        if change == "readonly": s.readonly_session = True
        if change == "unversioned": s.history.append({"role": "user", "content": "new direct append"})
        old = copy.deepcopy(s.history)
        release.set()
        await blocker
        status, result = await request
        assert status == 200 and result["errorCode"] == "reimport_stale"
        assert s.history == old
    asyncio.run(run())


@pytest.mark.parametrize("failure", [False, True])
def test_real_cancellation_observes_writer_outcome(tmp_path, monkeypatch, failure):
    s, _, _, _ = setup(tmp_path, monkeypatch)
    conflict = check(s)
    old = copy.deepcopy(s.history)
    before = disk(s)
    entered, release = threading.Event(), threading.Event()
    real_save = sess._save_body
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        real_save(*args, **kwargs)
        if failure: raise OSError("cancelled writer failed after write")
    monkeypatch.setattr(sess, "_save_body", blocked)
    async def run():
        task = asyncio.create_task(server._import_session(server._sessions_provider("codex"), "codex",
                                    {"session_id": "private-thread", **force(s, conflict)}))
        assert await asyncio.to_thread(entered.wait, 2)
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError): await task
    asyncio.run(run())
    if failure: assert s.history == old and disk(s) == before
    else: assert [r["content"] for r in s.history] == ["matched body", "native reply"]
    sess._cache.clear()
    assert sess.get(s.id).history == s.history


def test_native_change_during_save_rolls_back(tmp_path, monkeypatch):
    s, path, _, _ = setup(tmp_path, monkeypatch)
    conflict = check(s)
    old = copy.deepcopy(s.history)
    before = disk(s)
    real_save = sess._save_body
    def mutate_after_write(*args, **kwargs):
        real_save(*args, **kwargs)
        with path.open("a", encoding="utf-8") as f:
            f.write('\n' + json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied"}}))
    monkeypatch.setattr(sess, "_save_body", mutate_after_write)
    result = asyncio.run(send(**force(s, conflict)))[1]
    assert result["errorCode"] == "reimport_stale"
    assert s.history == old and disk(s) == before


def test_summary_is_bounded_with_total_and_positions(tmp_path, monkeypatch):
    s, _, _, missing = setup(tmp_path, monkeypatch)
    for index in range(25):
        row = {**copy.deepcopy(missing), "messageId": f"pan:{index + 100:032x}", "content": "long " * 100}
        sess.append_history(s, row)
    conflict = check(s)
    assert conflict["count"] == 26 and len(conflict["items"]) == 20 and conflict["omittedCount"] == 6
    assert all(len(item["preview"]) <= 160 for item in conflict["items"])
    assert [item["position"] for item in conflict["items"]] == list(range(2, 22))


@pytest.mark.parametrize("reason", ["changed_native_item", "ambiguous_match", "missing_identity"])
def test_summary_explains_match_failure(tmp_path, monkeypatch, reason):
    s, path, _, missing = setup(tmp_path, monkeypatch)
    if reason == "changed_native_item": s.history[1]["nativeItemId"] = "item-0"
    if reason == "missing_identity": s.history[1].pop("messageId")
    if reason == "ambiguous_match":
        with path.open("a", encoding="utf-8") as f:
            for index in range(2):
                f.write('\n' + json.dumps({"type": "response_item", "payload": {
                    "type": "message", "role": "user", "id": f"duplicate-{index}",
                    "content": [{"type": "input_text", "text": missing["content"]}]}}))
    conflict = check(s)
    assert conflict["items"][0]["reason"] == reason


def test_codex_snapshot_reads_private_rollout_bytes(tmp_path, monkeypatch):
    path = tmp_path / "native.jsonl"
    path.write_text(json.dumps({"type": "session_meta", "payload": {"id": "private-thread"}}), encoding="utf-8")
    db = tmp_path / "state.sqlite"
    con = sqlite3.connect(db)
    con.execute("create table threads(id text, rollout_path text)")
    con.execute("insert into threads values (?, ?)", ("private-thread", str(path)))
    con.commit()
    con.close()
    monkeypatch.setattr(codex, "_STATE_DB", db)
    before = codex.get_reimport_snapshot("private-thread")
    with path.open("a", encoding="utf-8") as f:
        f.write('\n' + json.dumps({"type": "event_msg", "payload": {"type": "thread_settings_applied"}}))
    assert codex.get_reimport_snapshot("private-thread") != before
    assert codex.get_reimport_snapshot("missing-thread") is None
