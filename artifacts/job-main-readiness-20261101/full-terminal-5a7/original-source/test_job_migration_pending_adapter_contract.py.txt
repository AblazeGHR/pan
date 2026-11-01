"""Rev11 contract investigation, synthetic stores only; no runtime activation."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import time

import pytest

from packages.jobs import final_contracts as fc
from packages.jobs.runtime_owner import OwnerFence
from packages.scheduler.creators import CanonicalEventStore, CreatorStore, EventBus
from packages.scheduler.pending_adoption import PendingPublicationAdoption
from packages.scheduler.providers import time_definition
from tests.test_job_final_creators import T0, config, interval

BASE = "9b4506c94ac67a39093961b218b2f01d387d1049"


def json_occurrence(profile, *, classification="never-attempted", dispatch="task:entry:123", text="original"):
    snapshot = {"jobId": "job-json", "taskId": "task-json", "entryId": "entry",
                "dispatchKey": dispatch, "fireAt": T0, "runCount": 0}
    note = {"entryId": "entry", "fireAt": T0, "dispatchKey": dispatch,
            "text": text, "error": "target session is missing"}
    stopped = {"proofKind": "owned-process-tree-v1", "pid": 4321, "birth": "birth-json",
               "treeEnded": True, "sourceDigest": fc.digest(snapshot)}
    stopped["treeDigest"] = fc.digest({"pid": stopped["pid"], "birth": stopped["birth"], "treeEnded": True})
    return {"source": {"profileRoot": str(profile.resolve()), "snapshot": snapshot,
                        "snapshotDigest": fc.digest(snapshot), "noteDigest": fc.digest(note)},
            "note": note, "writerStop": stopped, "classification": classification}


def json_port(profile):
    owner = OwnerFence(profile)
    owner.claim(0)
    owner.set_state("active")
    creators = CreatorStore(profile / "canonical")
    events = CanonicalEventStore(profile / "canonical")
    creators.owner_fence = events.owner_fence = owner
    return owner, creators, events, PendingPublicationAdoption(creators, events, None)


def original_atomic_writer():
    raw = subprocess.check_output(["git", "show", BASE + ":packages/core/background_jobs.py"])
    node, = [n for n in ast.parse(raw).body if isinstance(n, ast.FunctionDef) and n.name == "_atomic_write"]
    assert not node.decorator_list
    module = ast.Module(body=[node], type_ignores=[])
    scope = {"Path": Path, "json": json, "secrets": secrets, "os": os, "time": time}
    exec(compile(ast.fix_missing_locations(module), BASE + ":_atomic_write", "exec"), scope)
    return scope["_atomic_write"]


def test_canonical_claim_does_not_exclude_original_uncooperative_writer(tmp_path):
    profile = tmp_path / "owned-profile"
    profile.mkdir()
    source = profile / "job_owned.json"
    source.write_text('{"text":"before"}', encoding="utf-8")
    owner = OwnerFence(profile)
    owner.claim(0)
    try:
        with owner.writer(canonical=True):
            # This is the exact old atomic writer, targeting only our new file.
            # It does not participate in the runtime owner database at all.
            original_atomic_writer()(source, {"text": "old writer still changed source"})
        assert json.loads(source.read_text())["text"] == "old writer still changed source"
        with pytest.raises(fc.ContractError, match="epoch fence rejected"):
            with OwnerFence(profile).writer():
                pytest.fail("Cooperative writer crossed canonical owner fence")
        assert owner.activation_intent() is None
    finally:
        owner.release()


def test_json_never_attempted_creates_new_pending_occurrence_links_dispatch_and_settles(tmp_path):
    profile = tmp_path / "profile"
    owner, creators, events, port = json_port(profile)
    try:
        occurrence = json_occurrence(profile)
        result = port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-1", actor_digest="a" * 64)
        assert result["firstCreated"] is True and result["originalDispatchKey"] == "task:entry:123"
        item = creators.get(result["creatorId"])
        assert item["state"] == "disabled" and item["pendingAdoption"]["identity"]["firstCreated"]
        assert item["pendingPublication"]["envelope"]["eventId"] == result["eventId"]
        assert item["pendingPublication"]["envelope"]["eventType"] == "scheduler.pending-occurrence.v1"
        assert item["pendingPublication"]["envelope"]["payload"]["originalDispatchKey"] == "task:entry:123"
        bus = EventBus(events, creators.get)
        asyncio.run(bus.publish(item["pendingPublication"]["envelope"], item["pendingPublication"]["fence"]))
        settled = port.settle(result["creatorId"])
        assert settled["publicationState"] == "settled"
        assert creators.get(result["creatorId"])["pendingAdoption"]["publicationSettled"] is True
        assert events.get(result["eventId"])["event"]["payload"]["originalDispatchKey"] == "task:entry:123"
    finally:
        owner.release()


def test_json_repeat_is_idempotent_stale_digest_and_competing_import_reject(tmp_path):
    profile = tmp_path / "profile"
    owner, creators, events, port = json_port(profile)
    try:
        occurrence = json_occurrence(profile)
        first = port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-1", actor_digest="a" * 64)
        repeat = port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-1", actor_digest="a" * 64)
        assert repeat["duplicate"] is True and repeat["eventId"] == first["eventId"]
        changed = json_occurrence(profile, text="changed")
        with pytest.raises(fc.ContractError, match="digest changed"):
            port.adopt_json_occurrence(occurrence=changed, import_key="offline-1", actor_digest="a" * 64)
        with pytest.raises(fc.ContractError, match="another owner"):
            port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-2", actor_digest="b" * 64)
    finally:
        owner.release()


def test_json_crash_after_creator_write_reopens_and_completes_same_pending_adoption(tmp_path):
    profile = tmp_path / "profile"
    owner, creators, events, port = json_port(profile)
    try:
        occurrence = json_occurrence(profile)
        original = port._adopt_event
        calls = {"count": 0}
        def crash_once(item):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("synthetic crash after creator transaction")
            return original(item)
        port._adopt_event = crash_once
        with pytest.raises(RuntimeError, match="synthetic crash"):
            port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-1", actor_digest="a" * 64)
        port._adopt_event = original
        retry = port.adopt_json_occurrence(occurrence=occurrence, import_key="offline-1", actor_digest="a" * 64)
        assert retry["duplicate"] is True and creators.get(retry["creatorId"])["pendingAdoption"]["phase"] == "adopted"
        assert events.get(retry["eventId"]) is None
    finally:
        owner.release()


@pytest.mark.parametrize("classification", ["known-no-send", "accepted", "unknown", "partial"])
def test_json_no_send_and_effect_states_are_preserved_without_publication(tmp_path, classification):
    profile = tmp_path / "profile"
    owner, creators, events, port = json_port(profile)
    try:
        result = port.adopt_json_occurrence(occurrence=json_occurrence(profile, classification=classification),
                                            import_key="offline-1", actor_digest="a" * 64)
        assert result["state"] == "preserved_only" and result["originalDispatchKey"] == "task:entry:123"
        if classification == "known-no-send":
            item = creators.get(result["creatorId"])
            assert item["state"] == "disabled" and item["pendingPublication"] is None
            assert events.stats() == {"events": 0, "pins": 0}
        else:
            assert result["executable"] is False
    finally:
        owner.release()


def test_existing_legacy_lease_excludes_claim_until_trusted_cooperative_pass_exits(tmp_path):
    legacy, canonical = OwnerFence(tmp_path / "owned-profile"), OwnerFence(tmp_path / "owned-profile")
    with legacy.legacy_pass():
        with pytest.raises(fc.ContractError, match="Legacy pass still active"):
            canonical.claim(0)
        assert canonical.token is None and canonical.lock.file is None
    canonical.claim(0)
    try:
        assert canonical.status()["state"] == "activating"
        assert canonical.activation_intent() is None
    finally:
        canonical.release()


def test_existing_creator_document_can_hold_facts_without_new_table_or_publication(tmp_path):
    profile = tmp_path / "owned-profile"
    owner = OwnerFence(profile)
    owner.claim(0)
    creators = CreatorStore(profile / "canonical")
    creators.owner_fence = owner
    try:
        item = creators.create(time_definition("owned schema probe", config([interval()])),
                               request_key="shape-probe", state="disabled", at=T0)
        fact = {"recordType": "shape-probe-only-not-a-transfer", "originalDispatch": "task:entry:123",
                "note": {"entryId": "entry", "dispatchKey": "task:entry:123", "fireAt": T0,
                         "text": " original ", "error": "no target"}}
        with creators.transaction() as db:
            before = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            stored = creators._read_db(db, item["id"])
            # This roundtrip is NOT an adoption or authorized transfer receipt.
            stored["pendingAdoption"] = copy.deepcopy(fact)
            creators._write(db, stored)
            after = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert before == after
        reopened = CreatorStore(profile / "canonical")
        reopened.owner_fence = owner
        current = reopened.get(item["id"])
        assert current["pendingAdoption"] == fact
        assert current["state"] == "disabled" and current["drives"] == []
        assert current["pendingPublication"] is None
        assert not CanonicalEventStore(profile / "canonical").path.exists()
        assert owner.activation_intent() is None
    finally:
        owner.release()


@pytest.mark.parametrize("claimed", ["never-attempted", "known-no-send", "accepted", "unknown", "partial"])
def test_json_note_and_operator_claims_cannot_enter_original_v1_protocol(tmp_path, claimed):
    note = {"entryId": "entry", "dispatchKey": "task:entry:123", "fireAt": T0,
            "text": " original ", "error": "bot_not_connected"}
    source = {"id": "task", "pending": {"event": note, "nextFireAt": None},
              "oldOwnerStopped": True, "deliveryState": claimed}
    original = copy.deepcopy(source)
    port = PendingPublicationAdoption(None, None, None)
    with pytest.raises(fc.ContractError, match="Only original scheduler v1 pending envelopes"):
        port._plan({"snapshot": {"sources": [source]}}, "task", "UTC")
    assert source == original and not list(tmp_path.iterdir())
