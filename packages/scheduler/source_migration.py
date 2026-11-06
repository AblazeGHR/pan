"""Explicit source handoff through the canonical host, with no startup scan.

History import and pending adoption keep their reviewed identity contracts.
This port supplies host authorization and a complete, reviewed successor graph;
future drives then use the ordinary CreatorRuntime/admission/executor path.
"""
import asyncio
import copy

from packages.jobs import final_contracts as fc
from .pending_adoption import PendingPublicationAdoption


def user(service, actor, capability):
    if actor.get("type") != "user":
        raise fc.ContractError("authorization_denied", "Source migration requires the explicit user authority")
    service._permit(actor, "event_creator", capability)


def validate_successors(service, item):
    """Check the reviewed complete graph at enable and each effect boundary."""
    adoption = item.get("pendingAdoption")
    if not adoption:
        return
    successors = adoption.get("successors")
    if successors is None:
        raise fc.ContractError("reference_conflict", "Review all original source links before continuation")
    if successors["creatorRevision"] != item["revision"]:
        raise fc.ContractError("revision_conflict", "Review successors after Creator definition changes")
    with service.transaction(readonly=True) as db:
        for binding in successors["bindings"]:
            trigger = service._get(db, "trigger", binding["triggerId"])
            job = service._get(db, "job", binding["jobId"])["canonical"]
            if (trigger["revision"] != binding["triggerRevision"]
                    or job["configRevision"] != binding["jobRevision"]
                    or trigger["state"] == "retired" or job["state"] == "retired"
                    or trigger.get("targetJobId", trigger.get("target", {}).get("jobId")) != job["id"]
                    or trigger["creatorSelector"].get("mode") != "ids"
                    or item["id"] not in trigger["creatorSelector"]["ids"]):
                raise fc.ContractError("reference_conflict", "Reviewed source successors changed")
            if not any(row["triggerId"] == trigger["id"] and row["jobId"] == job["id"]
                       for row in service.creators.references(item["id"])):
                raise fc.ContractError("reference_conflict", "Reviewed source link is missing")


class SourceMigration:
    def __init__(self, service, legacy_events):
        self.service = service
        self.port = PendingPublicationAdoption(service.creators, service.events, legacy_events)

    def adopt_settled_source_graph(self, sources, *, expected_digest, desired_graph,
                                  legacy_sources, request_key, actor):
        """Continue an active, settled cooperative v1 clock through WP04.

        Source ownership is profile-wide, not inferred from a copied snapshot.
        This transfers clocks/links only; it never imports old Job executions.
        Pending publications must use the existing same-identity adoption port.
        """
        from packages.jobs.runtime_owner import cooperative_profile
        from packages.jobs.final_service import object_ref
        user(self.service, actor, 'create')
        user(self.service, actor, 'lifecycle')
        owner = self.service.owner
        if (sources.owner_fence.root != owner.root or not cooperative_profile(owner.root)
                or owner.token is None):
            raise fc.ContractError('reconciliation_required', 'Original cooperative writer/owner handoff is unproven')
        if (not isinstance(desired_graph, dict) or not isinstance(legacy_sources, list) or not legacy_sources
                or any(not isinstance(item, dict) or set(item) != {'creatorKey', 'sourceId', 'timezone', 'triggerKeys'}
                       for item in legacy_sources)
                or {item['creatorKey'] for item in legacy_sources}
                    != {item['key'] for item in desired_graph.get('creators', [])}
                or any(item.get('mode') != 'create' for item in desired_graph.get('creators', []))):
            raise fc.ContractError('invalid_schema', 'Every owned clock requires an explicit original source mapping')
        with self.service.transaction() as db:
            def transfer(operation_id):
                snapshot = sources.snapshot()
                if fc.digest(snapshot) != expected_digest:
                    raise fc.ContractError('revision_conflict', 'Original source graph/cursor changed')
                self.service._permit(actor, 'job', 'create', execution=True)
                self.service._permit(actor, 'job', 'lifecycle', execution=True)
                if desired_graph.get('triggers'):
                    self.service._permit(actor, 'trigger', 'create', execution=True)
                    self.service._permit(actor, 'trigger', 'lifecycle', execution=True)
                self.service._references(db, 'job', desired_graph['job'], enabled=True)
                # Import/history, composition cursor adoption and activation
                # join the same owner-fenced Creator transaction and receipts.
                self.service.creators.import_legacy_sources(sources, request_key=operation_id,
                                                           expected_digest=expected_digest)
                mappings = [{**item, 'importKey': operation_id} for item in legacy_sources]
                composed = self.service.compositions.compose(request_key=operation_id,
                    desired_graph=copy.deepcopy(desired_graph), activate=True,
                    authorization_evidence={'principal': copy.deepcopy(actor)}, legacy_sources=mappings)
                ids = composed['objectIds']
                objects = [('job', ids['job']), *[('trigger', tid) for tid in ids['triggers'].values()],
                           *[('event_creator', cid) for cid in ids['creators'].values()]]
                for kind, oid in objects:
                    self.service._permit(actor, 'event_creator', 'lifecycle', execution=True)
                    db.execute('INSERT OR REPLACE INTO final_authorities VALUES(?,?,?)',
                               (kind, oid, fc.canonical_json(actor)))
                return {'objectIds': composed['objectIds'], 'sourceDigest': expected_digest}, [
                    object_ref(kind, self.service._object(db, kind, oid)) for kind, oid in objects], False
            return self.service._operation(db, actor, 'create', request_key,
                {'settledSourceGraph': {'sourceDigest': expected_digest, 'desiredGraph': desired_graph,
                                       'legacySources': legacy_sources}}, 'event_creator', transfer)

    def adopt(self, *, import_key, source_id, timezone_name, actor):
        user(self.service, actor, "create")
        user(self.service, actor, "lifecycle")
        result = self.port.adopt(import_key=import_key, source_id=source_id, timezone_name=timezone_name)
        # P22's cross-store saga must finish before joining the host transaction.
        # A crash here is safe: the disabled Creator cannot drive without this
        # durable authority, and retrying adopt installs the same authority.
        with self.service.transaction() as db:
            cid = result["creatorId"]
            item = self.service.creators._read_db(db, cid)
            # The low-level ledger's profile owner is internal metadata. The
            # host publishes a canonical principal owned by the explicit user;
            # original source attribution remains verbatim in legacySource.
            if item["owner"] == {"type": "profile", "id": self.service.creators.profile_id}:
                from packages.jobs.final_runtime.ledger import principal
                item["owner"] = principal(actor)
                self.service.creators._write(db, item)
            row = db.execute("SELECT document FROM final_authorities WHERE kind='event_creator' AND id=?", (cid,)).fetchone()
            value = fc.canonical_json(actor)
            if row and row[0] != value:
                raise fc.ContractError("authorization_denied", "Adopted source authority differs")
            db.execute("INSERT OR IGNORE INTO final_authorities VALUES(?,?,?)", ("event_creator", cid, value))
        return result

    def adopt_json_pending(self, *, occurrence, import_key, actor):
        """Offline stopped-writer adapter; v1 scheduler adoption stays unchanged."""
        user(self.service, actor, "create")
        user(self.service, actor, "lifecycle")
        return self.port.adopt_json_occurrence(
            occurrence=occurrence, import_key=import_key,
            actor_digest=fc.digest(actor),
        )

    def status(self, creator_id, *, actor):
        user(self.service, actor, "read")
        return self.port.status(creator_id)

    def bind_successors(self, creator_id, *, trigger_map, job_map, expected_revision, actor):
        """Explicit old→canonical IDs, never infer a cohort from similar content."""
        user(self.service, actor, "updateConfig")
        with self.port.owned(), self.service.transaction() as db:
            item = self.service.creators._read_db(db, creator_id)
            fc.check_revision(item["revision"], expected_revision)
            adoption = item["pendingAdoption"]
            links = adoption["sourceLinks"]
            if (not isinstance(trigger_map, dict) or not isinstance(job_map, dict)
                    or set(trigger_map) != {link["triggerId"] for link in links}
                    or set(job_map) != {link["jobId"] for link in links}
                    or len(set(trigger_map.values())) != len(trigger_map)
                    or len(set(job_map.values())) != len(job_map)):
                raise fc.ContractError("reference_conflict", "Complete explicit source link mappings required")
            bindings = []
            for link in links:
                trigger = self.service._get(db, "trigger", trigger_map[link["triggerId"]])
                job = self.service._get(db, "job", job_map[link["jobId"]])["canonical"]
                if (trigger.get("targetJobId", trigger.get("target", {}).get("jobId")) != job["id"]
                        or trigger["creatorSelector"].get("mode") != "ids"
                        or creator_id not in trigger["creatorSelector"]["ids"]
                        or not {adoption["envelope"]["eventType"], *item["eventTypes"]} <= set(trigger["eventTypes"])):
                    raise fc.ContractError("reference_conflict", "Successor must reference this Creator, Job and event types")
                bindings.append({"originalTriggerId": link["triggerId"], "originalJobId": link["jobId"],
                                 "triggerId": trigger["id"], "triggerRevision": trigger["revision"],
                                 "jobId": job["id"], "jobRevision": job["configRevision"]})
            adoption["successors"] = {"creatorRevision": item["revision"], "bindings": bindings,
                                      "triggerMap": copy.deepcopy(trigger_map), "jobMap": copy.deepcopy(job_map)}
            self.service.creators._write(db, item)
            validate_successors(self.service, item)
        return self.status(creator_id, actor=actor)

    async def publish_pending(self, creator_id, *, actor):
        """Only an explicitly requested, proven unadmitted first publication."""
        user(self.service, actor, "lifecycle")
        status = await asyncio.to_thread(self.port.status, creator_id)
        if status["publicationState"] != "awaiting_admission" or not status["adoptionComplete"]:
            raise fc.ContractError("pending_publication", "Original publication is committed, settled or uncertain; query/settle it")
        item = await asyncio.to_thread(self.service.creators.get, creator_id)
        await asyncio.to_thread(self.service._guard_creator, item)
        # Freeze current canonical subscribers only for the explicit first
        # admission. Copies of old commits keep their original empty targets.
        self.service.refresh_subscriptions()
        pending = item["pendingPublication"]
        return await self.service.bus.publish(copy.deepcopy(pending["envelope"]), pending["fence"])

    def settle(self, creator_id, *, actor):
        user(self.service, actor, "lifecycle")
        return self.port.settle(creator_id)
