from __future__ import annotations

import copy
from pathlib import Path

from scripts import codex_auto_rotate as rotate


def _worker(session_id: str, worker_id: str, status: str, generation: int = 1) -> dict:
    return {
        "sessionId": session_id,
        "workerId": worker_id,
        "status": status,
        "generation": generation,
    }


def _summary(session_id: str, adapter: str, status: str, task_id: str | None = None,
             execution_seq: int | None = None, terminal: list | None = None) -> dict:
    return {
        "id": session_id,
        "adapter": adapter,
        "workerStatus": status,
        "workerId": "worker-" + session_id[-1],
        "workerGeneration": 1,
        "workerTaskId": task_id,
        "workerTaskSeq": 1 if task_id else None,
        "workerExecutionSeq": execution_seq,
        "terminalResults": terminal or [],
    }


def _detail(session_id: str, task_id: str | None, paused: bool = False) -> dict:
    return {
        "id": session_id,
        "activeTaskId": task_id,
        "queuePaused": paused,
        "terminalResults": [],
    }


class FakeApi:
    def __init__(self, workers, summaries, details, delayed=None):
        self.workers = copy.deepcopy(workers)
        self._summaries = copy.deepcopy(summaries)
        self.details = copy.deepcopy(details)
        self.delayed = copy.deepcopy(delayed or {})
        self.calls: list[tuple] = []

    def live_workers(self):
        return copy.deepcopy(self.workers)

    def summaries(self):
        return copy.deepcopy(self._summaries)

    def session(self, session_id):
        return copy.deepcopy(self.details[session_id])

    def pause_queue(self, session_id, paused):
        self.calls.append(("pause", session_id, paused))
        self.details[session_id]["queuePaused"] = paused
        return {"ok": True}

    def send(self, session_id, text, *, force, client_message_id):
        self.calls.append(("send", session_id, text, force, client_message_id))
        return {"status": "queued", "queueItemId": "q_" + session_id}

    def delayed_restart(self, session_id):
        self.calls.append(("delayed_restart", session_id))
        script = self.delayed.get(session_id)
        if script == "offline":
            return {"error": "No live Worker; delayed restart will not start an offline Session"}
        if script == "immediate":
            return {"error": "Worker is error; use Restart to recover the runtime"}
        return {"delayedRestart": {"sessionId": session_id, "status": "pending"}}

    def delayed_state(self, session_id):
        self.calls.append(("delayed_state", session_id))
        script = self.delayed.get(session_id)
        if script == "failed":
            return {"status": "failed", "error": "simulated failure"}
        # Promote the fake worker to a new generation on the first poll.
        for row in self.workers:
            if row["sessionId"] == session_id:
                if row["generation"] == 1:
                    row["generation"] = 2
                    row["status"] = "idle"
                    return {"status": "completed", "replacementWorkerId": row["workerId"], "replacementGeneration": 2}
                return {"status": "completed", "replacementWorkerId": row["workerId"], "replacementGeneration": row["generation"]}
        return {"status": "failed", "error": "worker disappeared"}

    def restart_worker(self, worker_id):
        self.calls.append(("restart_worker", worker_id))
        for row in self.workers:
            if row["workerId"] == worker_id:
                row["generation"] += 1
                row["status"] = "idle"
                return {"workerId": worker_id, "status": "restarted"}
        return {"error": "Worker not found"}


class FakeSwitcher:
    def __init__(self, current="p1"):
        self.current_value = current
        self.calls: list[tuple] = []

    def current(self):
        return self.current_value

    def switch(self, target, expected):
        self.calls.append((target, expected))
        assert expected == self.current_value
        self.current_value = target
        return {"currentProviderId": target}


def _trigger(**overrides):
    value = {
        "currentProviderId": "p1",
        "quotas": [
            {"id": "p1", "quota": {"status": "ok", "available": True, "tiers": [
                {"name": "five_hour", "usedPercent": 96, "resetsAt": "2026-10-25T10:00:00Z"},
                {"name": "seven_day", "usedPercent": 10, "resetsAt": "2026-11-01T00:00:00Z"},
            ]}},
            {"id": "p2", "quota": {"status": "ok", "available": True, "tiers": [
                {"name": "five_hour", "usedPercent": 0, "resetsAt": "2026-10-25T12:00:00Z"},
                {"name": "seven_day", "usedPercent": 15, "resetsAt": "2026-11-01T00:00:00Z"},
            ]}},
        ],
    }
    value.update(overrides)
    return value


def _happy_api():
    workers = [
        _worker("s1", "worker-1", "running", 1),
        _worker("s2", "worker-2", "idle", 1),
        _worker("s3", "worker-3", "running", 1),
    ]
    summaries = [
        _summary("s1", "codex", "running", "T1", 2),
        _summary("s2", "codex", "idle", None, None),
        _summary("s3", "cbc", "running", "T3", 1),
    ]
    details = {
        "s1": _detail("s1", "T1"),
        "s2": _detail("s2", None),
        "s3": _detail("s3", "T3"),
    }
    return FakeApi(workers, summaries, details)


def test_happy_path_freezes_only_codex_and_resumes_only_running(tmp_path):
    api = _happy_api()
    switcher = FakeSwitcher()
    ledger_path = tmp_path / "ledger.json"
    orchestrator = rotate.Orchestrator(
        api, switcher, trigger=_trigger(), execute=True, timeout=5, poll=0.01,
        ledger_path=ledger_path, sleep=lambda _: None,
    )
    ledger = orchestrator.run()

    assert [row.session_id for row in orchestrator.frozen] == ["s1", "s2"]
    assert switcher.calls == [("p2", "p1")]
    assert ("pause", "s1", True) in api.calls
    assert ("pause", "s2", True) in api.calls
    assert ("pause", "s3", True) not in api.calls
    sends = [call for call in api.calls if call[0] == "send"]
    assert [call[1] for call in sends] == ["s1", "s1"]
    assert sends[0][3] is True
    assert sends[1][3] is False
    assert ledger["phase"] == "done"
    assert ledger["currentProviderId"] == "p1"
    assert ledger["targetProviderId"] == "p2"
    assert all(row["restart"]["status"] == "completed" for row in ledger["frozen"])
    assert [row["resume"]["status"] for row in ledger["frozen"]] == ["queued", "skipped"]
    assert ledger_path.is_file()


def test_dry_run_does_not_write(tmp_path):
    api = _happy_api()
    switcher = FakeSwitcher()
    orchestrator = rotate.Orchestrator(
        api, switcher, trigger=_trigger(), execute=False, timeout=1, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    ledger = orchestrator.run()
    assert switcher.calls == []
    assert not any(call[0] in {"pause", "send", "delayed_restart", "restart_worker"} for call in api.calls)
    assert ledger["mode"] == "dry-run"
    assert all(row["restart"]["status"] == "planned" for row in ledger["frozen"])
    assert [row["resume"]["status"] for row in ledger["frozen"]] == ["planned", "skipped"]


def test_restart_failure_is_recorded_and_not_resumed(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1), _worker("s2", "worker-2", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1), _summary("s2", "codex", "running", "T2", 1)]
    details = {"s1": _detail("s1", "T1"), "s2": _detail("s2", "T2")}
    api = FakeApi(workers, summaries, details, delayed={"s1": "failed"})
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=5, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    ledger = orchestrator.run()
    restarts = {row["session_id"]: row["restart"]["status"] for row in ledger["frozen"]}
    resumes = {row["session_id"]: row["resume"]["status"] for row in ledger["frozen"]}
    assert restarts == {"s1": "failed", "s2": "completed"}
    assert resumes == {"s1": "skipped", "s2": "queued"}


def test_offline_worker_is_skipped(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1)]
    details = {"s1": _detail("s1", "T1")}
    api = FakeApi(workers, summaries, details, delayed={"s1": "offline"})
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=2, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    ledger = orchestrator.run()
    assert ledger["frozen"][0]["restart"]["status"] == "skipped"
    assert ledger["frozen"][0]["resume"]["status"] == "skipped"
    assert ledger["frozen"][0]["resume"]["reason"] == "restart_skipped"
    assert not any(call[0] == "send" and call[3] is False for call in api.calls)


def test_no_candidate_fails_before_pause(tmp_path):
    trigger = _trigger()
    trigger["quotas"][1]["quota"]["tiers"][0]["usedPercent"] = 100
    api = _happy_api()
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=trigger, execute=True, timeout=1, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    try:
        orchestrator.run()
    except rotate.RotationError as exc:
        assert "no eligible candidate" in str(exc)
    else:
        raise AssertionError("expected RotationError")
    assert not any(call[0] in {"pause", "send", "delayed_restart", "restart_worker"} for call in api.calls)


def test_stale_trigger_fails_before_pause(tmp_path):
    trigger = _trigger(currentProviderId="p0")
    api = _happy_api()
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=trigger, execute=True, timeout=1, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    try:
        orchestrator.run()
    except rotate.RotationError as exc:
        assert "trigger current provider" in str(exc)
    else:
        raise AssertionError("expected RotationError")
    assert not any(call[0] in {"pause", "send", "delayed_restart", "restart_worker"} for call in api.calls)


def test_immediate_restart_path(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1)]
    details = {"s1": _detail("s1", "T1")}
    api = FakeApi(workers, summaries, details, delayed={"s1": "immediate"})
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=2, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    ledger = orchestrator.run()
    assert ledger["frozen"][0]["restart"]["kind"] == "immediate"
    assert ledger["frozen"][0]["restart"]["status"] == "completed"
    assert ledger["frozen"][0]["resume"]["status"] == "queued"


class FakeReadBridge:
    def __init__(self, five_used: float):
        self.five_used = five_used

    def read_only(self, operation, arguments):
        if operation == "list":
            return {
                "currentProviderId": "p1",
                "providers": [
                    {"id": "p1", "category": "official"},
                    {"id": "p2", "category": "official"},
                ],
            }
        provider_id = arguments["id"]
        return {
            "id": provider_id,
            "app": "codex",
            "quota": {
                "status": "ok",
                "available": True,
                "tiers": [
                    {"name": "five_hour", "usedPercent": self.five_used if provider_id == "p1" else 0,
                     "resetsAt": "2026-10-25T12:00:00Z"},
                    {"name": "seven_day", "usedPercent": 10,
                     "resetsAt": "2026-11-01T00:00:00Z"},
                ],
            },
        }


def test_snapshot_triggers_only_at_threshold():
    at_threshold = rotate.BridgeSwitcher(Path("."), Path("."), bridge=FakeReadBridge(95))
    below = rotate.BridgeSwitcher(Path("."), Path("."), bridge=FakeReadBridge(94.9))
    assert at_threshold.snapshot(95)["currentProviderId"] == "p1"
    assert below.snapshot(95) is None


class AlwaysPendingApi(FakeApi):
    def delayed_state(self, session_id):
        self.calls.append(("delayed_state", session_id))
        return {"status": "pending"}


def test_restart_timeout_forces_immediate_restart_and_resumes(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1)]
    details = {"s1": _detail("s1", "T1")}
    api = AlwaysPendingApi(workers, summaries, details)
    now = [0.0]
    def clock():
        now[0] += 1.0
        return now[0]
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=0.01, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None, clock=clock,
    )
    ledger = orchestrator.run()
    restart = ledger["frozen"][0]["restart"]
    assert restart["status"] == "completed"
    assert restart.get("forced") is True
    assert ledger["frozen"][0]["resume"]["status"] == "queued"


class FailingRestartApi(AlwaysPendingApi):
    def restart_worker(self, worker_id):
        self.calls.append(("restart_worker", worker_id))
        return {"error": "restart refused"}


def test_restart_timeout_is_recorded_when_force_also_fails(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1)]
    details = {"s1": _detail("s1", "T1")}
    api = FailingRestartApi(workers, summaries, details)
    now = [0.0]
    def clock():
        now[0] += 1.0
        return now[0]
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=0.01, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None, clock=clock,
    )
    ledger = orchestrator.run()
    assert ledger["frozen"][0]["restart"]["status"] == "timeout"
    assert ledger["frozen"][0]["resume"]["status"] == "skipped"


def test_completed_task_is_not_resumed(tmp_path):
    workers = [_worker("s1", "worker-1", "running", 1)]
    summaries = [_summary("s1", "codex", "running", "T1", 1, terminal=[{"taskId": "T1"}])]
    details = {"s1": _detail("s1", "T1")}
    api = FakeApi(workers, summaries, details)
    orchestrator = rotate.Orchestrator(
        api, FakeSwitcher(), trigger=_trigger(), execute=True, timeout=2, poll=0.01,
        ledger_path=tmp_path / "ledger.json", sleep=lambda _: None,
    )
    ledger = orchestrator.run()
    assert ledger["frozen"][0]["resume"]["status"] == "skipped"
    assert ledger["frozen"][0]["resume"]["reason"] == "task already terminal"
