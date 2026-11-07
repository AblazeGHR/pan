"""Group configuration adapters over the canonical owner, documents and operations.

No execution lives here. Releases export the existing capability document and
invocations resolve their fixed ref into FinalService.invoke_action.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
import json
import re

from packages.jobs import final_contracts as fc
from packages.jobs.final_runtime.ledger import principal
from .invocation import check_capabilities

LIMIT = 100
CONTRACT_FIELDS = ("inputSchema", "outputSchema", "requiredCapabilities", "effectClass",
                   "completionContracts", "defaultCompletion", "cancel", "reconcile", "retry",
                   "providerRef", "system", "runtimeBeforeAcceptance", "execution")


def fail(code, message):
    raise fc.ContractError(code, message)


class Groups:
    def __init__(self, service):
        self.service = service

    def permit(self, actor, capability):
        # Group management uses existing Action grants, including lifecycle for
        # publication. A Group never supplies an invocation grant.
        return self.service._permit(actor, "action", capability)

    @contextmanager
    def writer(self, actor, capability):
        # Join the original owner-fenced transaction, then serialize the grant
        # check and configuration write with UserAuth permission changes. Take
        # this lock after the DB writer to avoid reversing existing lock order.
        with self.service.transaction() as db, self.service.auth.lock:
            self.service._permit(actor, 'action', capability, execution=True)
            yield db

    @staticmethod
    def save(db, kind, identity, value):
        db.execute("INSERT OR REPLACE INTO final_documents VALUES(?,?,?)",
                   (kind, identity, fc.canonical_json(value)))
        if kind == 'action_group':
            from .group_migration import advance_projection
            advance_projection(db, identity)

    def get(self, db, identity):
        return self.service._get(db, "action_group", identity)

    def all(self, db):
        return self.service._all(db, "action_group")

    def resolve(self, db, ref, *, available=True):
        if (not isinstance(ref, dict) or set(ref) != {"id", "revision", "digest"}
                or not isinstance(ref["id"], str) or type(ref["revision"]) is not int
                or ref["revision"] < 1 or not isinstance(ref["digest"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref["digest"])):
            fail("invalid_schema", "A fixed ActionRef is required")
        value = self.service._action_version_record(db, ref["id"], ref["revision"], digest=ref["digest"], for_ref=True)
        if available and (value["state"] != "published" or not value["enabled"]):
            fail("reference_conflict", "Action version is disabled or retired")
        if available:
            check_capabilities(value["definition"])
        return value["definition"]

    def validate(self, db, value, *, available=True, allow_empty=False):
        if (not isinstance(value, dict) or set(value) != {"id", "name", "tools"}
                or not isinstance(value["id"], str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", value["id"])
                or not isinstance(value["name"], str) or not value["name"].strip()
                or not isinstance(value["tools"], list) or not (0 if allow_empty else 1) <= len(value["tools"]) <= LIMIT):
            fail("invalid_schema", "Group requires id, name and 1..100 tools")
        aliases, actions = set(), set()
        for tool in value["tools"]:
            if (not isinstance(tool, dict) or set(tool) != {"alias", "actionRef"}
                    or not isinstance(tool["alias"], str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,47}", tool["alias"])):
                fail("invalid_schema", "Tool requires a stable alias and fixed ActionRef")
            self.resolve(db, tool["actionRef"], available=available)
            if tool["alias"] in aliases or tool["actionRef"]["id"] in actions:
                fail("reference_conflict", "Duplicate tool alias or logical Action in Group")
            aliases.add(tool["alias"])
            actions.add(tool["actionRef"]["id"])
        return copy.deepcopy(value)

    def list(self, *, actor):
        self.permit(actor, "read")
        with self.service.transaction(readonly=True) as db:
            return {"items": self.all(db)}

    def put(self, value, expected, key, *, actor, available=True, allow_empty=False):
        self.permit(actor, "create" if expected == 0 else "updateConfig")
        if type(expected) is not int or expected < 0:
            fail("invalid_schema", "expectedRevision is required")
        payload = {"group": value, "expectedRevision": expected}
        from .store import _lock
        with _lock, self.writer(actor, 'create' if expected == 0 else 'updateConfig') as db:
            def mutate(_):
                item = self.validate(db, value, available=available, allow_empty=allow_empty)
                from .store import Store
                handed_off = db.execute("SELECT 1 FROM final_documents WHERE kind='action_group_handoff' AND id=?", (item['id'],)).fetchone()
                if item['id'] in Store().read()['groups'] and not handed_off:
                    fail('reference_conflict', 'Legacy JSON Group retains its writer; canonical adoption is required')
                row = db.execute("SELECT document FROM final_documents WHERE kind='action_group' AND id=?", (item["id"],)).fetchone()
                old = json.loads(row[0]) if row else {"revision": 0}
                if old["revision"] != expected:
                    fail("revision_conflict", "Group changed before save")
                result = {**old, **item, "revision": expected + 1}
                self.save(db, "action_group", item["id"], result)
                return {"group": result}, [], False
            return self.service._operation(db, actor, "update", key, payload, "action", mutate)

    def publish(self, identity, expected, key, *, actor):
        self.permit(actor, "lifecycle")
        with self.writer(actor, 'lifecycle') as db:
            def mutate(operation_id):
                group = self.get(db, identity)
                if type(expected) is not int or group["revision"] != expected:
                    fail("revision_conflict", "Group changed before publication")
                self.validate(db, {k: group[k] for k in ("id", "name", "tools")})
                tools = []
                for tool in group["tools"]:
                    definition = self.resolve(db, tool["actionRef"])
                    if definition.get("system"):
                        self.permit(actor, "manageSystemActions")
                    if group.get('compatibility'):
                        from .group_migration import flat_release_contract
                        formats = group['compatibility']['outputFormats']
                        if tool['alias'] not in formats:
                            fail('reference_conflict', 'A new flat alias requires an explicit output-format mapping')
                        flat_release_contract(definition, {'id': tool['alias'], 'output_format': formats[tool['alias']]})
                    tools.append({**tool, "inputSchema": definition["inputSchema"],
                                  "description": definition["name"], "outputSchema": definition["outputSchema"],
                                  **({'outputFormat': group['compatibility']['outputFormats'][tool['alias']],
                                      **({'outputMerge': 'concat/v1'} if definition['execution']['config'].get('legacyOutputMerge') == 'concat/v1' else {})}
                                     if group.get('compatibility') else {})})
                release = {"groupId": identity, "name": group["name"], "revision": expected, "tools": tools}
                if group.get('compatibility'):
                    release['compatibility'] = {'protocol': 'legacy-flat/v1'}
                release["digest"] = fc.digest(release)
                release["version"] = operation_id
                # Artifacts are immutable. Only a committed release pointer can
                # register them; rollback may leave an inert orphan directory.
                from .store import atomic
                directory = self.service.root / 'group-releases' / operation_id
                atomic(directory / 'capabilities.json', self.document(release))
                release['directory'] = str(directory)
                self.save(db, "action_group_release", operation_id, release)
                group["release"] = operation_id
                group["revision"] += 1
                self.save(db, "action_group", identity, group)
                return {"group": group, "release": release}, [], False
            return self.service._operation(db, actor, "publish", key,
                {"groupId": identity, "expectedRevision": expected}, "action", mutate)

    def latest(self, db, action_id):
        rows = db.execute("SELECT revision,digest FROM final_action_versions WHERE actionId=? AND state='published' ORDER BY revision DESC", (action_id,))
        for revision, digest in rows:
            ref = {"id": action_id, "revision": revision, "digest": digest}
            record = self.service._action_version_record(db, action_id, revision, digest=digest, for_ref=True)
            if record["state"] == "published" and record["enabled"]:
                return ref
        fail("reference_conflict", "Action has no available published version")

    def _preview(self, db, selection, actor):
        if not isinstance(selection, dict) or selection.get("scope") not in {"action", "group"}:
            fail("invalid_schema", "Select action or group scope")
        from .store import Store
        legacy_state = Store().read()
        legacy = legacy_state['groups']
        adopted = {row[0] for row in db.execute("SELECT id FROM final_documents WHERE kind='action_group_handoff'")}
        legacy = {gid: value for gid, value in legacy.items() if gid not in adopted}
        excluded = []
        if selection["scope"] == "action":
            if set(selection) - {"scope", "actionRef", "groupIds"} or 'actionRef' not in selection:
                fail("invalid_schema", "Action scope requires target ActionRef")
            self.resolve(db, selection["actionRef"])
            if 'groupIds' in selection:
                ids = selection['groupIds']
                if (not isinstance(ids, list) or not 1 <= len(ids) <= LIMIT
                        or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids)):
                    fail('invalid_schema', 'Explicit batch requires 1..100 unique Group IDs')
                groups = [self.get(db, identity) for identity in sorted(ids)]
                if any(not any(t['actionRef']['id'] == selection['actionRef']['id'] for t in g['tools']) for g in groups):
                    fail('reference_conflict', 'Selected Group does not reference this Action')
            else:
                groups = [json.loads(row[0]) for row in db.execute(
                    "SELECT document FROM final_documents g WHERE kind='action_group' AND EXISTS "
                    "(SELECT 1 FROM json_each(g.document,'$.tools') t WHERE json_extract(t.value,'$.actionRef.id')=?) "
                    "ORDER BY id LIMIT ?", (selection['actionRef']['id'], LIMIT + 1))]
        else:
            if set(selection) != {"scope", "groupId"}:
                fail("invalid_schema", "Group scope requires groupId")
            if selection['groupId'] in legacy:
                groups = []
                excluded = [{'groupId': selection['groupId'], 'expectedRevision': legacy_state['revision'],
                    'reason': 'legacy_json_preview_only', 'actionIds': legacy[selection['groupId']].get('action_ids', [])}]
            else:
                groups = [self.get(db, selection["groupId"])]
        if len(groups) > LIMIT:
            fail("capacity_exceeded", "Group batch exceeds 100; select explicit bounded batches")
        if selection['scope'] == 'action':
            action_id = selection['actionRef']['id']
            # Similar IDs/names are not migration evidence. A legacy reference
            # without an explicit alias could point at this Action: never call
            # the remaining canonical subset "all Groups" in that case.
            aliases = dict(db.execute('SELECT legacyId,actionId FROM final_action_aliases'))
            for group in legacy.values():
                if 'groupIds' in selection and group['id'] not in selection['groupIds']:
                    continue
                ids = group.get('action_ids', [])
                if any(aliases.get(i) == action_id for i in ids):
                    reason = 'legacy_json_preview_only'
                elif any(i not in aliases for i in ids):
                    reason = 'legacy_action_identity_unresolved'
                else:
                    continue  # Explicit mappings prove this Group is unrelated.
                excluded.append({'groupId': group['id'], 'reason': reason})
                if len(excluded) > LIMIT:
                    fail('capacity_exceeded', 'Legacy Group exclusions exceed the bounded preview')
        items = []
        for group in groups:
            changes, tools, blockers = [], [], []
            for tool in group["tools"]:
                old = tool["actionRef"]
                target = (selection["actionRef"] if selection["scope"] == "action" and old["id"] == selection["actionRef"]["id"]
                          else self.latest(db, old["id"]) if selection["scope"] == "group" else old)
                before, after = self.resolve(db, old, available=False), self.resolve(db, target)
                if group.get('compatibility'):
                    from .group_migration import flat_release_contract
                    try:
                        flat_release_contract(after, {'id': tool['alias'],
                            'output_format': group['compatibility']['outputFormats'][tool['alias']]})
                    except (fc.ContractError, ValueError, KeyError):
                        blockers.append({'alias': tool['alias'], 'code': 'reference_conflict', 'fields': ['legacy_flat_output_contract']})
                differences = [field for field in CONTRACT_FIELDS if before.get(field) != after.get(field)]
                # Changed schemas/contracts require editing and validating the
                # draft explicitly; bulk apply cannot assert compatibility.
                incompatible = [f for f in differences if f not in {"execution"}]
                if incompatible:
                    blockers.append({"alias": tool["alias"], "code": "reference_conflict", "fields": incompatible})
                tools.append({**tool, "actionRef": target})
                changes.append({"alias": tool["alias"], "from": old, "to": target, "differences": differences})
            items.append({"groupId": group["id"], "expectedRevision": group["revision"],
                          "changes": changes, "tools": tools, "blockers": blockers})
        preview = {"selection": selection, "items": items, "excluded": excluded, "principal": principal(actor),
                   "authorizationRevision": self.service.auth.permissions_status()["revision"],
                   "profileId": self.service.profile_id}
        return {**preview, "previewDigest": fc.digest(preview)}

    def preview(self, selection, *, actor):
        self.permit(actor, "read")
        self.permit(actor, "updateConfig")
        with self.service.transaction(readonly=True) as db:
            return self._preview(db, selection, actor)

    def apply(self, body, *, actor):
        self.permit(actor, "read")
        self.permit(actor, "updateConfig")
        if not isinstance(body, dict) or set(body) != {"selection", "previewDigest", "targets", "requestKey"}:
            fail("invalid_schema", "Apply requires previewDigest, selection, explicit targets and requestKey")
        with self.writer(actor, 'updateConfig') as db:
            def mutate(_):
                preview = self._preview(db, body["selection"], actor)
                targets = [{"groupId": i["groupId"], "expectedRevision": i["expectedRevision"]} for i in preview["items"]]
                if body["targets"] != targets or body["previewDigest"] != preview["previewDigest"]:
                    fail("revision_conflict", "Preview or selected Group revisions changed")
                if any(i["blockers"] for i in preview["items"]):
                    fail("reference_conflict", "Preview contains incompatible contracts")
                if preview['excluded']:
                    fail('reference_conflict', 'Legacy JSON Groups require canonical writer adoption before apply')
                for item in preview["items"]:
                    group = self.get(db, item["groupId"])
                    group["tools"] = item["tools"]
                    group["revision"] += 1
                    self.save(db, "action_group", group["id"], group)
                return {"targets": targets, "published": False}, [], False
            return self.service._operation(db, actor, "update", body["requestKey"],
                {"groupReferenceUpdate": {k: v for k, v in body.items() if k != "requestKey"}}, "action", mutate)

    def release(self, version, *, actor):
        self.permit(actor, "read")
        with self.service.transaction(readonly=True) as db:
            return self.service._get(db, "action_group_release", version)

    def unbuild(self, identity, expected, key, *, actor):
        self.permit(actor, 'lifecycle')
        with self.writer(actor, 'lifecycle') as db:
            def mutate(_):
                group = self.get(db, identity)
                if type(expected) is not int or group['revision'] != expected:
                    fail('revision_conflict', 'Group changed before unbuild')
                group.pop('release', None)
                group['revision'] += 1
                self.save(db, 'action_group', identity, group)
                return {'group': group}, [], False
            return self.service._operation(db, actor, 'disable', key,
                {'groupId': identity, 'expectedRevision': expected}, 'action', mutate)

    def delete(self, identity, expected, key, *, actor):
        self.permit(actor, 'delete')
        with self.writer(actor, 'delete') as db:
            def mutate(_):
                group = self.get(db, identity)
                if type(expected) is not int or group['revision'] != expected:
                    fail('revision_conflict', 'Group changed before delete')
                if group.get('release'):
                    fail('reference_conflict', 'Unbuild the Group before deleting it')
                db.execute("DELETE FROM final_documents WHERE kind='action_group' AND id=?", (identity,))
                from .group_migration import advance_projection
                advance_projection(db, identity)
                return {'groupId': identity, 'deleted': True}, [], False
            return self.service._operation(db, actor, 'retire', key,
                {'groupId': identity, 'expectedRevision': expected}, 'action', mutate)

    def document(self, release):
        pid = "action-group-" + release["groupId"].replace("_", "-")
        return {"protocol": "pan-capability/v1", "plugin_id": pid,
                "provider": {"transport": "stdio_jsonrpc", "command_ref": pid + "-provider"},
                "capabilities": [{"id": pid + "." + t["alias"], "export_key": t["alias"],
                    "description": t["description"], "contract_version": release["version"],
                    "input_schema": t['inputSchema'] if release.get('compatibility') else {"type": "object", "additionalProperties": False,
                        "properties": {"arguments": t["inputSchema"], "requestKey": {"type": "string", "minLength": 1}},
                        "required": ["arguments", "requestKey"]},
                    "execution": {"operation": t["alias"], "result_format": "mcp"}} for t in release["tools"]]}

    async def invoke(self, version, alias, arguments, key, *, actor):
        release = self.release(version, actor=actor)
        tool = next((t for t in release["tools"] if t["alias"] == alias), None)
        if tool is None:
            fail("reference_conflict", "Tool is absent from bound Group release")
        ref = tool["actionRef"]
        return await self.service.invoke_action(ref["id"], {"actionRef": ref,
            "arguments": arguments, "requestKey": key}, actor=actor,
            source={"groupRelease": release["version"]})
