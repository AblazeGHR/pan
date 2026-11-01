"""Retained pin safety at its actual writer; original red node stays intact."""
from packages.core import background_jobs as jobs
from packages.jobs import api
from packages.core.platform.data_paths import job_root
from tests.test_jobs_api import client, isolated  # noqa: F401


def test_retained_pin_persistence_failure_keeps_disk_and_emits_nothing(client, monkeypatch):
    from packages.jobs.final_runtime import coordinator
    root = job_root()
    jobs._create({"jobId": "job_pin", "status": "running"}, registry_root=root)
    assert client.put("/api/jobs/job_pin/pin", json={"pinned": True}).json()["ok"]
    before = (root / "pins.json").read_bytes()
    emitted = []
    monkeypatch.setitem(api._state, "broadcast", emitted.append)
    def fail(*args, **kwargs):
        raise OSError("isolated metadata disk full")
    monkeypatch.setattr(coordinator, "atomic_write", fail)
    response = client.put("/api/jobs/job_pin/pin", json={"pinned": False}).json()
    assert response["error"]["code"] == "persistence_failed"
    assert (root / "pins.json").read_bytes() == before
    assert emitted == []
    assert client.get("/api/jobs/job_pin").json()["job"]["pinned"] is True
