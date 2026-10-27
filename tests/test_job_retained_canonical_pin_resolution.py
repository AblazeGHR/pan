"""Independent canonical pin holds through real HTTP/owner/SQLite paths."""
import copy

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.final_service import definition
from packages.jobs.final_runtime.coordinator import PinMetadataUnavailable, FinalRuntime
from tests.test_job_retained_canonical_pins import (  # noqa: F401
    h, native, create, update_pin, disable, reopen, assert_delete_blocked, notice_rows,
)


@pytest.mark.parametrize("order", ["config-first", "metadata-first"])
@pytest.mark.parametrize("release", ["config-first", "metadata-first"])
@pytest.mark.parametrize("operation", ["delete", "gc"])
def test_independent_holds_require_both_releases(native, order, release, operation):
    h = native
    item = create(h)
    jid = item["id"]
    if order == "metadata-first":
        h.service.final.set_pin(jid, True, expected_revision=0)
    item = update_pin(h, item, True).json()["result"]["object"]
    if order == "config-first":
        h.service.final.set_pin(jid, True, expected_revision=0)
    item = disable(h, item)
    reopen(h)
    assert_delete_blocked(h, jid, "pinned")
    if release == "metadata-first":
        receipt = h.service.final.set_pin(jid, False, expected_revision=1)
        assert receipt == {"revision": 2, "pinned": True, "metadataPinned": False}
        assert h.service.final.metadata(jid) == receipt
        assert h.client.get("/api/jobs/" + jid).json()["retention"]["pinned"] is True
    else:
        item = update_pin(h, item, False, "release-config").json()["result"]["object"]
        assert h.service.final.metadata(jid)["pinned"] is True
    reopen(h)
    assert_delete_blocked(h, jid, "pinned")
    if release == "metadata-first":
        item = update_pin(h, item, False, "release-config").json()["result"]["object"]
    else:
        assert h.service.final.set_pin(jid, False, expected_revision=1) == {"revision": 2, "pinned": False}
    reopen(h)
    assert h.service.final.metadata(jid) == {"revision": 2, "pinned": False}
    assert notice_rows(h, jid) == {} and h.provider.calls == []
    if operation == "delete":
        assert h.service.final.delete(jid, expected_revision=item["configRevision"])["deleted"]
    else:
        assert h.service.final.retention(retention_days=1, at=4102444800)["deleted"] == [jid]
    assert h.service.final.history(jid)["runs"] == []


def test_unrelated_http_edit_and_stale_cas_never_clear_other_hold(native):
    h = native
    original = create(h)
    jid = original["id"]
    h.service.final.set_pin(jid, True, expected_revision=0)
    edited = definition("job", original)
    edited["name"] = "Unrelated name edit"
    response = h.client.put("/api/jobs/" + jid, json={"definition": edited,
        "expectedRevision": original["configRevision"], "requestKey": "rename"})
    assert response.status_code == 200, response.text
    item = response.json()["result"]["object"]
    assert item["retention"]["pinned"] is False
    assert h.service.final.metadata(jid)["pinned"] is True
    item = update_pin(h, item, True, "config-hold").json()["result"]["object"]
    disk = h.service.creators.path.read_bytes()
    stale = update_pin(h, original, False, "stale-config")
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    with pytest.raises(fc.ContractError) as error:
        h.service.final.set_pin(jid, False, expected_revision=0)
    assert error.value.code == "revision_conflict"
    assert h.service.creators.path.read_bytes() == disk
    reopen(h)
    saved = h.service.final.store.get(jid)
    assert saved["config"]["retention"]["pinned"] is True
    assert saved["runtimeMetadata"]["pinned"] is True
    disable(h, item)
    assert_delete_blocked(h, jid, "pinned")


CORRUPTION = [
    ("config", None), ("config", []),
    ("retention", None), ("retention", []),
    ("config-pin", None), ("config-pin", "unknown"), ("config-pin", 1),
    ("metadata", None), ("metadata", []),
    ("metadata-pin", None), ("metadata-pin", "unknown"), ("metadata-pin", 0),
]


@pytest.mark.parametrize("field,value", CORRUPTION)
@pytest.mark.parametrize("other_hold", [False, True])
def test_corrupt_source_is_never_masked_by_other_hold(native, field, value, other_hold):
    h = native
    item = disable(h, create(h))
    jid = item["id"]
    def corrupt(job):
        job["runtimeMetadata"] = {"pinned": other_hold, "revision": 0}
        job["config"]["retention"]["pinned"] = other_hold
        if field == "config":
            job["config"] = copy.deepcopy(value)
        elif field == "retention":
            job["config"]["retention"] = copy.deepcopy(value)
        elif field == "config-pin":
            job["config"]["retention"]["pinned"] = value
        elif field == "metadata":
            job["runtimeMetadata"] = copy.deepcopy(value)
        else:
            job["runtimeMetadata"]["pinned"] = value
    h.service.final.store.mutate(jid, corrupt)
    reopen(h)
    with pytest.raises(PinMetadataUnavailable):
        h.service.final.metadata(jid)
    assert_delete_blocked(h, jid, "pin_metadata_unknown")
    # Unknown config cannot be concealed by a successful metadata unpin.
    if field in {"config", "retention", "config-pin", "metadata"}:
        before = h.service.creators.path.read_bytes()
        with pytest.raises(PinMetadataUnavailable):
            h.service.final.set_pin(jid, False, expected_revision=0)
        assert h.service.creators.path.read_bytes() == before


@pytest.mark.parametrize("path", ["http-config", "runtime-metadata"])
def test_failed_release_preserves_both_holds_on_disk_and_reopen(native, monkeypatch, path):
    h = native
    item = create(h)
    jid = item["id"]
    item = update_pin(h, item, True).json()["result"]["object"]
    h.service.final.set_pin(jid, True, expected_revision=0)
    item = disable(h, item)
    before = h.service.creators.path.read_bytes()
    save = h.service._save
    def fail(db, kind, identity, value):
        save(db, kind, identity, value)
        if kind == "job" and identity == jid:
            raise OSError("owned release write failure after SQL")
    with monkeypatch.context() as patch:
        patch.setattr(h.service, "_save", fail)
        if path == "http-config":
            response = update_pin(h, item, False, "failed-release")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "storage_unavailable"
        else:
            with pytest.raises(OSError, match="release write failure"):
                h.service.final.set_pin(jid, False, expected_revision=1)
    assert h.service.creators.path.read_bytes() == before
    reopen(h)
    saved = h.service.final.store.get(jid)
    assert saved["config"]["retention"]["pinned"] is True
    assert saved["runtimeMetadata"] == {"pinned": True, "revision": 1}
    assert h.service.final.metadata(jid)["pinned"] is True
    assert notice_rows(h, jid) == {} and h.provider.calls == []
    assert h.client.get("/api/operations", params={"operation": "update", "requestKey": "failed-release"}).status_code == 404
    assert_delete_blocked(h, jid, "pinned")


@pytest.mark.parametrize("job", [{}, {"config": {}}, {"config": {"retention": {}}},
    {"runtimeMetadata": {}}, {"runtimeMetadata": {"pinned": False}}])
def test_missing_compatible_sources_default_to_false(job):
    assert FinalRuntime.deletion_blockers(job) == []
