"""Rev9 QQ mapping: owned synthetic sources, no QQ transport or accounts."""
import base64
import copy
import json

import pytest

from packages.jobs import final_contracts as fc
from scripts.job_final_migration import apply as migration
from scripts.job_final_migration.inventory import build_inventory
from scripts.job_final_migration.mapping import Mapper
from tests.test_job_final_migration import _scheduled_job, _write_json
from tests.test_job_migration_runtime_contract import runtime_context
from tests.support.job_migration_readiness_rehearsal import current_actions


def source_for(root, bot="654321"):
    source = _scheduled_job()
    args = {"targetType": "group", "targetId": "123456"}
    if bot != "omitted":
        args["botUin"] = bot
    source.update(targetSessionId=None, action={"api": "send_qq", "args": args},
                  text="  original QQ\n原文  ")
    path = root / "background_jobs/jobs/job_qq.json"
    _write_json(path, source)
    return path, source


@pytest.mark.parametrize("bot", ["654321", None, "omitted"])
def test_qq_runtime_exact_release_bindings_and_null_default(tmp_path, bot):
    root = tmp_path / "source"
    path, source = source_for(root, bot)
    original = path.read_bytes()
    context = runtime_context(root)
    inv = build_inventory(root, runtime_delivery=True)
    item, = inv["instances"]
    assert item["classification"] == "simple-script-migratable"
    target = tmp_path / "bundle.json"
    migration.migrate(root, target, context, apply=True,
                      expected_digest=migration.plan_digest(inv, context))
    bundle = migration.read_bundle(target)
    entry, = bundle["entries"]
    job = next(r["object"] for r in bundle["canonical"] if r["kind"] == "job")
    for row in bundle["canonical"]:
        fc.validate_object(row["kind"], row["object"])
    step, = job["steps"]
    assert step["actionRef"] == context["actions"]["qq.send"]["ref"]
    assert step["actionRef"]["id"] == "product_channel_runtime_v1"
    assert step["actionRef"]["digest"] == "82674431dfd366b74e7278c682a51d0ad966239ed98c683ddffe6173a703a255"
    fields = ["targetType", "targetId", "botUin", "text"]
    assert step["arguments"] == {k: {"from": "input", "path": "delivery." + k} for k in fields}
    assert job["arguments"]["delivery"] == {"targetType": "group", "targetId": "123456",
        "botUin": None if bot in (None, "omitted") else bot, "text": source["text"]}
    assert job["deliveryRuntime"]["inputFields"] == ["delivery." + k for k in fields]
    assert job["deliveryRuntime"]["phase"] == "before-reservation"
    assert entry["runtimeMapping"]["receiptOwner"] == "delivery-manager"
    assert entry["runtimeMapping"]["acceptanceAdopted"] is False
    assert entry["runtimeMapping"]["sourceEffectReplayed"] is False
    assert base64.b64decode(bundle["sourceBackups"][path.relative_to(root).as_posix()]["bytes"]) == original
    assert migration.migrate(root, target, context, apply=True,
        expected_digest=migration.plan_digest(inv, context))["targetWrites"] == 0
    assert path.read_bytes() == original


@pytest.mark.parametrize("fault", ["old-release", "phase", "fields", "provider", "pin"])
def test_qq_runtime_bad_contract_rejected_before_target(tmp_path, fault):
    root = tmp_path / "source"
    path, _ = source_for(root)
    original = path.read_bytes()
    context = runtime_context(root)
    binding = context["actions"]["qq.send"]
    if fault == "old-release":
        context["actions"]["qq.send"] = current_actions()["qq.send"]
    else:
        action = binding["object"]
        if fault == "phase":
            action["runtimeBeforeAcceptance"]["phase"] = "before-queue-acceptance"
        elif fault == "fields":
            action["runtimeBeforeAcceptance"]["fields"] = ["targetId", "text"]
        elif fault == "provider":
            action["providerRef"]["generation"] = 1
        if fault != "pin":
            definition = {k: v for k, v in action.items()
                          if k in fc.schema_artifacts()["action_definition.schema.json"]["properties"]}
            binding["ref"]["digest"] = fc.digest(definition)
        else:
            binding["ref"]["digest"] = "0" * 64
    with pytest.raises(ValueError):
        migration.migrate(root, tmp_path / "absent/bundle.json", context)
    assert not (tmp_path / "absent").exists() and path.read_bytes() == original


def test_qq_default_retains_old_literal_release(tmp_path):
    root = tmp_path / "source"
    _, source = source_for(root)
    context = runtime_context(root)
    context.pop("runtimeDelivery")
    context["actions"]["qq.send"] = current_actions()["qq.send"]
    mapper = Mapper(context)
    item, = build_inventory(root)["instances"]
    entry = mapper.map(item, source)
    job = next(r["object"] for r in mapper.objects if r["kind"] == "job")
    assert "deliveryRuntime" not in job and "runtimeMapping" not in entry
    assert job["steps"][0]["actionRef"]["id"] == "product_channel"
    assert job["steps"][0]["arguments"] == {k: {"from": "literal", "value": v}
        for k, v in {**source["action"]["args"], "text": source["text"]}.items()}


@pytest.mark.parametrize("bot", [None, "omitted"])
def test_qq_default_does_not_relax_persisted_bot_guard(tmp_path, bot):
    root = tmp_path / "source"
    path, _ = source_for(root, bot)
    original = path.read_bytes()
    item, = build_inventory(root)["instances"]
    assert item["classification"] == "incompatible-preserved"
    assert "provider_unavailable" in item["reasonCodes"] and item["targetIds"] == []
    assert path.read_bytes() == original


def test_qq_public_cli_explicit_flag_apply_replay_and_stale(tmp_path, capsys):
    from scripts.job_final_migration.cli import main
    root = tmp_path / "source"
    path, _ = source_for(root, None)
    original = path.read_bytes()
    context = runtime_context(root)
    context_path = tmp_path / "context.json"
    context_path.write_text(json.dumps(context), encoding="utf-8")
    target = tmp_path / "bundle.json"
    digest = migration.plan_digest(build_inventory(root, runtime_delivery=True), context)
    args = ["--root", str(root), "--target", str(target), "--context", str(context_path),
            "--runtime-delivery", "--apply", "--expected-digest", digest, "--out", "-"]
    assert main(args) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["targetWrites"] > 0 and first["runtimeActivated"] is False
    assert main(args) == 0
    replay = json.loads(capsys.readouterr().out)
    assert replay["targetWrites"] == 0 and replay["idempotentReplay"] is True
    before = target.read_bytes()
    assert main([value for value in args if value != "--runtime-delivery"]) == 2
    assert "must match reviewed context opt-in" in capsys.readouterr().err
    assert target.read_bytes() == before and path.read_bytes() == original
    source = json.loads(original)
    source["text"] = "changed source after review"
    _write_json(path, source)
    changed = path.read_bytes()
    assert main(args) == 2
    assert "plan_stale" in capsys.readouterr().err
    assert target.read_bytes() == before and path.read_bytes() == changed


@pytest.mark.parametrize("state", ["pending", "accepted", "unknown", "partial"])
def test_qq_existing_delivery_identity_preserved_not_imported(tmp_path, state):
    root = tmp_path / "source"
    path, source = source_for(root)
    receipt = {"dispatchKey": "original-dispatch", "fireAt": "2026-10-01T00:00:00Z",
               "text": " original pending body ", "error": {"code": "original-error"}, "outcome": state}
    source["undeliveredFires" if state == "pending" else "lastDelivery"] = [receipt] if state == "pending" else receipt
    _write_json(path, source)
    original = path.read_bytes()
    item, = build_inventory(root, runtime_delivery=True)["instances"]
    assert item["classification"] == "incompatible-preserved" and item["targetIds"] == []
    assert ("pending_notice" if state == "pending" else "uncertain_effect") in item["reasonCodes"]
    from tests.test_job_final_migration import _background_job
    _write_json(root / "background_jobs/jobs/job_process.json", _background_job(root))
    context = runtime_context(root)
    with pytest.raises(ValueError, match="non-simple"):
        Mapper(context).map(item, source)
    context["partialPolicy"] = {"version": 1, "mode": "preserve-incompatible"}
    target = tmp_path / "partial-bundle.json"
    migration.migrate(root, target, context, apply=True, preserve_incompatible=True,
        expected_digest=migration.plan_digest(build_inventory(root, runtime_delivery=True), context))
    bundle = migration.read_bundle(target)
    assert len(bundle["entries"]) == 1 and bundle["entries"][0]["type"] == "MIG-BG"
    preserved, = bundle["preserved"]
    assert preserved["targetIds"] == [] and preserved["executable"] is False
    assert preserved["migrationId"] == item["migrationId"]
    saved = base64.b64decode(bundle["sourceBackups"][path.relative_to(root).as_posix()]["bytes"])
    assert saved == original
    assert json.loads(saved)["undeliveredFires" if state == "pending" else "lastDelivery"] == ([receipt] if state == "pending" else receipt)
    assert path.read_bytes() == original


@pytest.mark.parametrize("bot", ["", "bad-bot"])
def test_qq_invalid_bot_not_reinterpreted_as_default(tmp_path, bot):
    root = tmp_path / "source"
    path, _ = source_for(root, bot)
    original = path.read_bytes()
    item, = build_inventory(root, runtime_delivery=True)["instances"]
    assert item["classification"] == "incompatible-preserved"
    assert "provider_unavailable" in item["reasonCodes"] and path.read_bytes() == original


def test_unknown_qq_action_never_falls_back_to_assign(tmp_path):
    root = tmp_path / "source"
    path, source = source_for(root)
    source["action"]["api"] = "unknown-ordinal9-action"
    _write_json(path, source)
    original = path.read_bytes()
    item, = build_inventory(root, runtime_delivery=True)["instances"]
    assert item["classification"] == "incompatible-preserved" and item["targetIds"] == []
    with pytest.raises(ValueError, match="non-simple"):
        Mapper(runtime_context(root)).map(item, source)
    assert path.read_bytes() == original


@pytest.mark.parametrize("outcome", ["accepted", "unknown"])
def test_qq_real_import_cas_same_event_cold_no_resend(tmp_path, outcome):
    import asyncio
    import hashlib
    from packages.actions.qq_channel_provider import QQChannelProvider
    from packages.actions.typed_executor import TypedExecutor
    from packages.jobs.final_api import CanonicalUserAuth
    from packages.jobs.final_service import FinalService, definition
    from packages.scheduler.providers import instant

    profile = tmp_path / "owned-profile"
    path, source = source_for(profile, None)
    source["schedule"][0]["at"] = source["schedule"][0]["nextFireAt"] = "2030-01-01T00:00:00+00:00"
    source["nextFireAt"] = "2030-01-01T00:00:00+00:00"
    _write_json(path, source)
    original = path.read_bytes()
    calls = []

    async def sender(**arguments):
        calls.append(copy.deepcopy(arguments))
        return {"ok": True, "message_id": "owned-ack"} if outcome == "accepted" else {"ok": False, "error": {"code": "owned-uncertain"}}

    def service_for():
        service = FinalService(profile / "job-final", CanonicalUserAuth(profile / "auth"),
            executor=TypedExecutor(channel_provider=QQChannelProvider(sender)))
        service.creators.clock = lambda: instant("2030-01-01T00:00:00+00:00")
        return service

    service = service_for()
    context = runtime_context(profile)
    context["profileId"] = service.profile_id
    for proof in context["records"].values():
        proof["principal"] = proof["owner"] = {"type": "user", "id": "local"}
    target = tmp_path / "bundle.json"
    migration.migrate(profile, target, context, apply=True, mark_read_only=True,
        expected_digest=migration.plan_digest(build_inventory(profile, runtime_delivery=True), context))
    bundle = migration.read_bundle(target)
    ids = {r["kind"]: r["object"]["id"] for r in bundle["canonical"]}
    spec = {"path": str(target), "expected_hash": hashlib.sha256(target.read_bytes()).hexdigest(),
            "expected_plan_digest": bundle["planDigest"], "active_ids": list(ids.values())}
    actor = {"type": "user", "id": "local"}

    async def exercise():
        await service.start(activation=spec)
        try:
            current = service.get("job", ids["job"], actor=actor)
            cfg = definition("job", current)
            cfg["arguments"]["delivery"] = {"targetType": "user", "targetId": "234567", "botUin": None,
                                               "text": "  changed before reservation\n原文  "}
            changed = service.write("job", ids["job"], {"definition": cfg,
                "expectedRevision": current["configRevision"], "requestKey": "owned-before-admission"}, actor=actor)
            await service.tick()
            await service.tick()
            state = service.final.get(ids["job"])
            assert len(state["runs"]) == len(state["attempts"]) == len(state["effects"]) == 1
            rid = next(iter(state["runs"]))
            event_id = state["runs"][rid]["cause"]["eventId"]
            effect = copy.deepcopy(state["effects"][0])
            assert effect["receipt"]["deliveryRuntime"]["children"][0]["status"] == ("accepted" if outcome == "accepted" else "unknown")
            assert calls == [{"target_type": "private", "target_id": "234567",
                              "text": cfg["arguments"]["delivery"]["text"], "bot_uin": None}]
            cfg["arguments"]["delivery"].update(targetId="345678", botUin="765432", text="must not resend")
            service.write("job", ids["job"], {"definition": cfg,
                "expectedRevision": changed["result"]["object"]["configRevision"],
                "requestKey": "owned-after-acceptance"}, actor=actor)
            await service.tick()
            assert len(calls) == 1
        finally:
            await service.close()
        cold = service_for()
        try:
            await cold.start(activation=spec)
            await cold.tick()
            state = cold.final.get(ids["job"])
            assert len(state["runs"]) == len(state["effects"]) == len(state["attempts"]) == 1
            assert state["effects"][0] == effect and len(calls) == 1
            assert state["runs"][rid]["cause"]["eventId"] == event_id
            if outcome == "unknown":
                assert state["runs"][rid]["state"] == "uncertain"
        finally:
            await cold.close()

    asyncio.run(exercise())
    assert path.read_bytes() == original
