"""Provider registry and lossless time/settlement calculators for Event Creators.

This module has no dependency on the legacy Job store or scheduler pass. Instants
are decimal epoch seconds internally and RFC3339 UTC strings at persistence
boundaries; wall-clock rules retain their IANA zone and explicit DST policy.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_FLOOR, localcontext
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from packages.jobs import final_contracts as contracts
from packages.jobs.cron import MAX_SEARCH_DAYS, parse_cron

UTC = timezone.utc
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
DST_POLICIES = {
    "strict",
    "gap-skip-fold-first",
    "gap-skip-fold-second",
    "gap-shift-fold-first",
    "gap-shift-fold-second",
}
PAUSE_POLICIES = {"advance"}
SETTLEMENT_OUTCOMES = {"completed", "failed", "cancelled", "uncertain"}
_TIMESTAMP = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?"
    r"(?:[Zz]|([+-])(\d{2}):(\d{2}))$"
)
_FIXED_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def instant(value: str | datetime) -> Decimal:
    """Parse an offset-aware timestamp without converting through float."""
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("A naive datetime is not an explicit instant")
        value = value.isoformat()
    if not isinstance(value, str):
        raise ValueError("Instant must be an RFC3339 string or aware datetime")
    match = _TIMESTAMP.match(value)
    if not match:
        raise ValueError("Instant must be RFC3339 with an explicit offset")
    year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
    # datetime validates the calendar date. The ordinal calculation below keeps
    # the integer seconds exact even for years outside the float timestamp range.
    datetime(year, month, day, hour, minute, second)
    whole = (datetime(year, month, day, tzinfo=UTC).toordinal() - EPOCH.toordinal()) * 86400
    whole += hour * 3600 + minute * 60 + second
    if match.group(8):
        offset = int(match.group(9)) * 3600 + int(match.group(10)) * 60
        whole -= offset if match.group(8) == "+" else -offset
    fraction = Decimal("0." + match.group(7)) if match.group(7) else Decimal(0)
    with localcontext() as context:
        context.prec = max(50, len(str(abs(whole))) + len(match.group(7) or "") + 5)
        return Decimal(whole) + fraction


def timestamp(value: Decimal | int | str | datetime) -> str:
    """Format an instant as UTC, retaining all significant decimal fractions."""
    seconds = instant(value) if isinstance(value, (str, datetime)) else Decimal(value)
    whole = int(seconds.to_integral_value(rounding=ROUND_FLOOR))
    with localcontext() as context:
        context.prec = max(50, len(seconds.as_tuple().digits) + 5)
        fraction = seconds - whole
    wall = EPOCH + timedelta(seconds=whole)
    result = wall.strftime("%Y-%m-%dT%H:%M:%S")
    if fraction:
        digits = format(fraction, "f").partition(".")[2].rstrip("0")
        result += "." + digits
    return result + "Z"


def now() -> str:
    return timestamp(datetime.now(UTC))


def _decimal_add(left: Decimal, right: Decimal) -> Decimal:
    # Decimal's default 28-digit context can silently round high-precision input.
    with localcontext() as context:
        context.prec = max(50, len(left.as_tuple().digits) + len(right.as_tuple().digits) + 20)
        return left + right


def _datetime_at(value: Decimal, zone) -> datetime:
    whole = int(value.to_integral_value(rounding=ROUND_FLOOR))
    fraction = value - whole
    micros = int((fraction * 1_000_000).to_integral_value(rounding=ROUND_FLOOR))
    return datetime.fromtimestamp(whole, zone) + timedelta(microseconds=micros)


def _wall_instant(wall: datetime, zone, policy: str) -> dict[str, Any] | None:
    """Resolve a wall time, distinguishing gaps and both fold ordinals."""
    if policy not in DST_POLICIES:
        raise ValueError("Unknown DST policy")
    valid = []
    for fold in (0, 1):
        aware = wall.replace(tzinfo=zone, fold=fold)
        utc = aware.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != wall:
            continue
        candidate = instant(utc)
        if not any(item["at"] == candidate for item in valid):
            valid.append({"at": candidate, "fold": fold, "offset": aware.utcoffset()})
    valid.sort(key=lambda item: item["at"])
    wall_text = wall.isoformat()
    if not valid:
        if policy == "strict":
            raise ValueError("Nonexistent wall time rejected by strict DST policy")
        if policy.startswith("gap-skip"):
            return None
        # fold=0 on a gap maps to the first valid instant after the gap. This is
        # an explicit shift, never a silent host-local fallback.
        shifted = wall.replace(tzinfo=zone, fold=0).astimezone(UTC)
        return {
            "at": instant(shifted), "timezone": str(zone), "wallTime": wall_text,
            "fold": 0, "ordinal": 0, "gap": "shifted",
        }
    if len(valid) == 2 and policy == "strict":
        raise ValueError("Ambiguous wall time rejected by strict DST policy")
    selected = valid[-1] if len(valid) == 2 and policy.endswith("fold-second") else valid[0]
    return {
        "at": selected["at"], "timezone": str(zone), "wallTime": wall_text,
        "fold": selected["fold"], "ordinal": valid.index(selected) + 1,
        "gap": "none",
    }


def _public_occurrence(value: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(value)
    result["at"] = timestamp(result["at"])
    result.pop("offset", None)
    return result


class ProviderRegistry:
    """Trusted provider implementations; client definitions select, never inject."""

    def __init__(self, providers=()):
        self.providers = {}
        for provider in providers:
            self.register(provider)

    def register(self, provider) -> None:
        key = (provider.provider_type, provider.provider_id)
        if key in self.providers:
            raise ValueError("Provider already registered")
        self.providers[key] = provider

    def get(self, ref: Mapping[str, Any]):
        provider = self.providers.get((ref.get("type"), ref.get("id")))
        if provider is None:
            raise contracts.ContractError("provider_unavailable", "Event Creator provider is unavailable")
        if ref.get("generation") != provider.generation or ref.get("releaseDigest") != provider.release_digest:
            raise contracts.ContractError("provider_unavailable", "Provider release or generation is unavailable")
        return provider

    def validate_definition(self, definition: Mapping[str, Any], *, migration=False) -> dict[str, Any]:
        provider = self.get(definition.get("providerRef", {}))
        value = contracts.validate_definition(
            "event_creator", definition,
            capabilities=provider.capabilities,
            config_validator=provider.validate_config,
        )
        if len(value["eventTypes"]) != 1:
            raise contracts.ContractError("invalid_schema", "This provider requires exactly one eventType")
        limit = value["config"].get("driveLimit")
        if limit and limit["initialProducedCount"] and not migration:
            raise contracts.ContractError(
                "capability_unsupported", "initialProducedCount requires migration authority"
            )
        return value

    @classmethod
    def standard(cls):
        return cls((TimeProvider(), SettlementProvider()))


class TimeProvider:
    provider_type = "time"
    provider_id = "scheduler"
    generation = 1
    release_digest = contracts.digest({"provider": "time/scheduler", "release": 1})
    capabilities = (
        "time.rules.v2", "time.decimal-seconds", "time.iana-zone",
        "time.fixed-offset", "time.explicit-dst", "time.grid",
        "time.after-publication",
    )
    config_schema_ref = {
        "id": "scheduler.config", "revision": 1,
        "digest": contracts.digest({"schema": "scheduler.config", "revision": 1}),
    }

    def __init__(self, zone_factory=ZoneInfo):
        self.zone_factory = zone_factory

    def health(self):
        return {"status": "available", "capabilities": list(self.capabilities)}

    def zone(self, name: str):
        text = str(name or "").strip()
        if text.upper() in {"UTC", "Z", "+00:00", "-00:00"}:
            return UTC
        fixed = _FIXED_OFFSET.match(text)
        if fixed:
            hours, minutes = int(fixed.group(2)), int(fixed.group(3))
            if hours > 23 or minutes > 59:
                raise contracts.ContractError(
                    "invalid_schema", "Fixed offset is outside the supported range",
                    details={"reason": "invalid_timezone", "timezone": text},
                )
            delta = timedelta(hours=hours, minutes=minutes)
            if fixed.group(1) == "-":
                delta = -delta
            return timezone(delta, text)
        try:
            return self.zone_factory(text)
        except Exception as exc:
            raise contracts.ContractError(
                "provider_unavailable", "IANA timezone is unavailable",
                details={"reason": "timezone_unavailable", "timezone": text},
            ) from exc

    def validate_config(self, config: Mapping[str, Any]) -> None:
        contracts.validate_scheduler_config(config)
        if config["pausePolicy"] not in PAUSE_POLICIES:
            raise contracts.ContractError("capability_unsupported", "Unknown pausePolicy")
        for rule in config["rules"]:
            if rule["type"] in {"weekly", "cron"}:
                if rule["dstPolicy"] not in DST_POLICIES:
                    raise contracts.ContractError("capability_unsupported", "Unknown dstPolicy")
                self.zone(rule["timezone"])
            if rule["type"] == "cron":
                try:
                    parse_cron(rule["expression"])
                except ValueError as exc:
                    raise contracts.ContractError("invalid_schema", str(exc), path="rules.expression") from exc

    def initial(self, rule: Mapping[str, Any], after: str | datetime) -> dict[str, Any] | None:
        if rule["type"] == "interval" and rule["advanceMode"] == "afterPublication":
            point = _decimal_add(instant(rule["anchor"]), Decimal(rule["seconds"]))
            return {
                "at": timestamp(point), "timezone": "UTC", "wallTime": timestamp(point),
                "fold": 0, "ordinal": 0, "gap": "none",
            }
        return self.next_after(rule, after)

    def next_after(self, rule: Mapping[str, Any], after: str | datetime) -> dict[str, Any] | None:
        boundary = instant(after)
        kind = rule["type"]
        if kind == "once":
            point = instant(rule["at"])
            if point <= boundary:
                return None
            text = timestamp(point)
            return {"at": text, "timezone": "UTC", "wallTime": text, "fold": 0, "ordinal": 0, "gap": "none"}
        if kind == "interval":
            if rule["advanceMode"] == "afterPublication":
                point = _decimal_add(boundary, Decimal(rule["seconds"]))
            else:
                anchor = instant(rule["anchor"])
                seconds = Decimal(rule["seconds"])
                with localcontext() as context:
                    context.prec = max(50, len(anchor.as_tuple().digits) + len(seconds.as_tuple().digits) + len(boundary.as_tuple().digits) + 20)
                    index = int(((boundary - anchor) / seconds).to_integral_value(rounding=ROUND_FLOOR)) + 1
                    index = max(index, 0)
                    point = anchor + index * seconds
            text = timestamp(point)
            return {"at": text, "timezone": "UTC", "wallTime": text, "fold": 0, "ordinal": 0, "gap": "none"}
        zone = self.zone(rule["timezone"])
        wall_after = _datetime_at(boundary, zone)
        if kind == "weekly":
            hour, minute, second = (int(part) for part in rule["time"].split(":"))
            for offset in range(MAX_SEARCH_DAYS + 8):
                day = wall_after.date() + timedelta(days=offset)
                if day.weekday() != rule["weekday"]:
                    continue
                wall = datetime(day.year, day.month, day.day, hour, minute, second)
                occurrence = _wall_instant(wall, zone, rule["dstPolicy"])
                if occurrence is not None and occurrence["at"] > boundary:
                    return _public_occurrence(occurrence)
            return None
        sets = parse_cron(rule["expression"])
        minutes, hours = sorted(sets["minute"]), sorted(sets["hour"])
        dom_star = sets["dom"] == set(range(1, 32))
        dow_star = sets["dow"] == set(range(7))
        for offset in range(MAX_SEARCH_DAYS + 1):
            day = wall_after.date() + timedelta(days=offset)
            if day.month not in sets["month"]:
                continue
            dom_ok = day.day in sets["dom"]
            dow_ok = ((day.weekday() + 1) % 7) in sets["dow"]
            if not ((dom_ok and dow_ok) if dom_star and dow_star else dow_ok if dom_star else dom_ok if dow_star else dom_ok or dow_ok):
                continue
            for hour in hours:
                for minute in minutes:
                    wall = datetime(day.year, day.month, day.day, hour, minute)
                    # A passed strict wall time need not be resolved (it may
                    # be a historical gap). Fold-second/shift policies can
                    # still have a future instant behind the current wall time.
                    if (wall <= wall_after.replace(microsecond=0, tzinfo=None)
                            and rule["dstPolicy"] in {"strict", "gap-skip-fold-first"}):
                        continue
                    occurrence = _wall_instant(wall, zone, rule["dstPolicy"])
                    if occurrence is not None and occurrence["at"] > boundary:
                        return _public_occurrence(occurrence)
        return None

    def initial_cursor(self, item: Mapping[str, Any], at: str, previous=None) -> dict[str, Any]:
        previous = previous or {}
        rules = {}
        for rule in item["config"]["rules"]:
            old = previous.get(rule["ruleId"])
            if old and old.get("rule") == rule:
                rules[rule["ruleId"]] = copy.deepcopy(old)
                continue
            occurrence = self.initial(rule, at)
            rules[rule["ruleId"]] = {
                "nextFireAt": occurrence["at"] if occurrence else None,
                "nextOccurrence": occurrence,
                "lastSettledDriveId": old.get("lastSettledDriveId") if old else None,
                "rule": copy.deepcopy(rule),
            }
        return {"rules": rules}

    def due_occurrences(self, item: Mapping[str, Any], at: str) -> list[str]:
        boundary = instant(at)
        return [
            "time:" + rule_id + ":" + cursor["nextFireAt"]
            for rule_id, cursor in item["cursor"]["rules"].items()
            if cursor.get("nextFireAt") and instant(cursor["nextFireAt"]) <= boundary
        ]

    def prepare(self, item, occurrence_key: str, at: str, drive_id: str, drive_seq: int):
        for rule_id, cursor in item["cursor"]["rules"].items():
            expected = "time:" + rule_id + ":" + str(cursor.get("nextFireAt"))
            if expected != occurrence_key:
                continue
            scheduled = cursor["nextFireAt"]
            if not scheduled or instant(scheduled) > instant(at):
                break
            rule = next(rule for rule in item["config"]["rules"] if rule["ruleId"] == rule_id)
            basis = timestamp(max(instant(at), instant(scheduled)))
            following = self.next_after(rule, basis)
            late = instant(at) - instant(scheduled) > Decimal(item["config"]["graceSeconds"])
            outcome = None
            if late and rule["type"] == "once":
                outcome = "expired"
            elif late and item["config"]["misfirePolicy"] == "skip":
                outcome = "skipped"
            reason = "backfill" if late else "scheduled"
            drive = {
                "driveId": drive_id, "driveSeq": drive_seq, "occurrenceKey": occurrence_key,
                "actualDriveAt": at if not outcome else None,
                "scheduledAt": scheduled, "reason": reason,
                "ruleId": rule_id, "outcome": outcome or "reserved",
            }
            if outcome:
                return {"disposition": outcome, "drive": drive, "ruleId": rule_id, "nextOccurrence": following}
            occurrence = cursor.get("nextOccurrence") or {
                "timezone": "UTC", "wallTime": scheduled, "fold": 0, "ordinal": 0, "gap": "none"
            }
            payload = {
                "driveId": drive_id, "driveSeq": drive_seq, "driveReason": reason,
                "ruleId": rule_id, "scheduledAt": scheduled,
                "timezone": occurrence["timezone"], "wallTime": occurrence["wallTime"],
                "fold": occurrence["fold"], "ordinal": occurrence["ordinal"], "gap": occurrence["gap"],
            }
            return {
                "drive": drive, "ruleId": rule_id, "rule": copy.deepcopy(rule),
                "nextOccurrence": None if rule["type"] == "interval" and rule["advanceMode"] == "afterPublication" else following,
                "payload": payload,
            }
        raise contracts.ContractError("revision_conflict", "Occurrence no longer matches the Creator cursor")

    def after_publication(self, item, pending, receipt):
        rule_id = pending["ruleId"]
        rule = next(rule for rule in item["config"]["rules"] if rule["ruleId"] == rule_id)
        if rule["type"] == "interval" and rule["advanceMode"] == "afterPublication":
            return self.next_after(rule, receipt["receivedAt"])
        return pending.get("nextOccurrence")

    def pause(self, item, occurrence_key: str, at: str, drive_id: str, drive_seq: int):
        # Paused sources advance their own grid without misfire classification or
        # a business Event. Resume therefore never backfills the paused window.
        for rule_id, cursor in item["cursor"]["rules"].items():
            scheduled = cursor.get("nextFireAt")
            if occurrence_key != "time:" + rule_id + ":" + str(scheduled):
                continue
            rule = next(rule for rule in item["config"]["rules"] if rule["ruleId"] == rule_id)
            basis = timestamp(max(instant(at), instant(scheduled)))
            following = self.next_after(rule, basis)
            return {
                "disposition": "paused", "ruleId": rule_id, "nextOccurrence": following,
                "drive": {
                    "driveId": drive_id, "driveSeq": drive_seq, "occurrenceKey": occurrence_key,
                    "actualDriveAt": None, "scheduledAt": scheduled, "reason": "paused",
                    "ruleId": rule_id, "outcome": "paused",
                },
            }
        raise contracts.ContractError("revision_conflict", "Paused occurrence no longer matches the cursor")


class SettlementProvider:
    """Core feedback source: trusted Run settlement arms its own next timer."""

    provider_type = "core"
    provider_id = "job-settlement"
    generation = 1
    release_digest = contracts.digest({"provider": "core/job-settlement", "release": 1})
    capabilities = ("core.job-settlement.v1", "time.decimal-seconds")
    config_schema_ref = {
        "id": "job-settlement.config", "revision": 1,
        "digest": contracts.digest({"schema": "job-settlement.config", "revision": 1}),
    }

    def health(self):
        return {"status": "available", "capabilities": list(self.capabilities)}

    def validate_config(self, config):
        allowed = {"compositionId", "targetJobId", "intervalSeconds", "continueOn", "initialFireAt"}
        if not isinstance(config, dict) or set(config) != allowed:
            raise contracts.ContractError("invalid_schema", "Invalid settlement Creator config")
        for key in ("compositionId", "targetJobId"):
            if not isinstance(config[key], str) or not config[key]:
                raise contracts.ContractError("invalid_schema", key + " is required")
        contracts.check_duration(config["intervalSeconds"], "intervalSeconds")
        if Decimal(config["intervalSeconds"]) <= 0:
            raise contracts.ContractError("invalid_schema", "intervalSeconds must be positive")
        contracts.check_timestamp(config["initialFireAt"], "initialFireAt")
        if (not isinstance(config["continueOn"], list) or not config["continueOn"]
                or len(set(config["continueOn"])) != len(config["continueOn"])
                or not set(config["continueOn"]) <= (SETTLEMENT_OUTCOMES - {"uncertain"})):
            raise contracts.ContractError("invalid_schema", "continueOn accepts completed/failed/cancelled")

    def initial_cursor(self, item, at, previous=None):
        if previous and "settlement" in previous:
            return {"rules": copy.deepcopy(previous)}
        initial = timestamp(item["config"]["initialFireAt"])
        occurrence = {"at": initial, "timezone": "UTC", "wallTime": initial,
                      "fold": 0, "ordinal": 0, "gap": "none"}
        return {"rules": {"settlement": {
            "nextFireAt": initial, "nextOccurrence": occurrence, "lastSettledDriveId": None,
        }}}

    def initial(self, rule, after):
        point = instant(rule["at"])
        if point <= instant(after):
            return None
        text = timestamp(point)
        return {"at": text, "timezone": "UTC", "wallTime": text, "fold": 0, "ordinal": 0, "gap": "none"}

    def due_occurrences(self, item, at):
        cursor = item["cursor"]["rules"]["settlement"]
        fire = cursor.get("nextFireAt")
        return ["settlement:" + fire] if fire and instant(fire) <= instant(at) else []

    def prepare(self, item, occurrence_key, at, drive_id, drive_seq):
        cursor = item["cursor"]["rules"]["settlement"]
        if occurrence_key != "settlement:" + str(cursor.get("nextFireAt")):
            raise contracts.ContractError("revision_conflict", "Settlement cursor changed")
        scheduled = cursor["nextFireAt"]
        payload = {
            "driveId": drive_id, "driveSeq": drive_seq, "driveReason": "settlement",
            "scheduledAt": scheduled, "compositionId": item["config"]["compositionId"],
            "targetJobId": item["config"]["targetJobId"],
        }
        return {
            "drive": {
                "driveId": drive_id, "driveSeq": drive_seq, "occurrenceKey": occurrence_key,
                "actualDriveAt": at, "scheduledAt": scheduled, "reason": "settlement",
                "ruleId": "settlement", "outcome": "reserved",
            },
            "ruleId": "settlement", "nextOccurrence": None, "payload": payload,
        }

    def after_publication(self, item, pending, receipt):
        return None

    def pause(self, item, occurrence_key, at, drive_id, drive_seq):
        prepared = self.prepare(item, occurrence_key, at, drive_id, drive_seq)
        prepared["drive"].update(actualDriveAt=None, outcome="paused", reason="paused")
        prepared["disposition"] = "paused"
        prepared.pop("payload", None)
        return prepared

    def accept_settlement(self, item, event):
        required = {"runId", "transitionSeq", "outcome", "finishedAt", "cause", "compositionId"}
        if event.get("eventType") != "job.settled.v1" or not isinstance(event.get("payload"), dict):
            raise ValueError("Invalid Job settlement Event")
        payload = event["payload"]
        if set(payload) != required:
            raise ValueError("Invalid Job settlement payload")
        if event.get("trustedSystem") != {"type": "core", "id": "job-runtime"}:
            raise ValueError("Settlement requires trusted Job runtime identity")
        config = item["config"]
        if payload["compositionId"] != config["compositionId"]:
            return False
        if payload["outcome"] == "uncertain" or payload["outcome"] not in config["continueOn"]:
            return False
        contracts.check_timestamp(payload["finishedAt"], "finishedAt")
        if not isinstance(payload["runId"], str) or type(payload["transitionSeq"]) is not int:
            raise ValueError("Invalid settlement identity")
        cause = payload["cause"]
        if not isinstance(cause, dict) or cause.get("type") != "event":
            return False
        own_event = next(
            (drive for drive in item["drives"]
             if drive.get("eventId") == cause.get("eventId") and drive["outcome"] in {"reserved", "published"}),
            None,
        )
        if own_event is None:
            return False
        key = payload["runId"] + ":" + str(payload["transitionSeq"])
        if key in item.setdefault("settlementReceipts", {}):
            return False
        point = _decimal_add(instant(payload["finishedAt"]), Decimal(config["intervalSeconds"]))
        occurrence = {"at": timestamp(point), "timezone": "UTC", "wallTime": timestamp(point),
                      "fold": 0, "ordinal": 0, "gap": "none"}
        item["cursor"]["rules"]["settlement"].update(
            nextFireAt=occurrence["at"], nextOccurrence=occurrence,
        )
        item["settlementReceipts"][key] = {"eventId": event["eventId"], "finishedAt": payload["finishedAt"]}
        return True


def time_definition(name: str, config: Mapping[str, Any], event_type="scheduler.time.v2"):
    provider = TimeProvider()
    return {
        "name": name, "description": "",
        "providerRef": {"type": provider.provider_type, "id": provider.provider_id,
                        "releaseDigest": provider.release_digest, "generation": provider.generation},
        "configSchemaRef": copy.deepcopy(provider.config_schema_ref),
        "config": copy.deepcopy(dict(config)), "eventTypes": [event_type],
        "requiredCapabilities": list(provider.capabilities),
    }


def legacy_source_plan(source, *, timezone_name, rule_id="legacy"):
    """Pure, explicit conversion of a settled v1 source; never infer host TZ.

    Unsettled/unknown sources belong in the historical ledger and require
    explicit review. In particular a pending v1 envelope is never republished
    with a new v2 identity. The reviewed next-fire fact is adopted, not recomputed
    from createdAt, so historical occurrences cannot be replayed at cutover.
    """
    allowed = {"id", "name", "owner", "revision", "enabled", "schedule", "misfirePolicy",
               "graceSec", "nextFireAt", "publishCursor", "createdAt", "updatedAt", "pending",
               "lastOutcome", "lastScheduledAt", "compositionBlocked", "setupOperation"}
    if (not isinstance(source, dict) or set(source) - allowed
            or source.get("owner") != "scheduler-event-v1" or type(source.get("enabled")) is not bool
            or source.get("compositionBlocked") or source.get("setupOperation")):
        raise contracts.ContractError("capability_unsupported", "Legacy source requires explicit review")
    published = source.get("publishCursor")
    if source.get("pending") or published and published.get("eventStoreReleased") is not True:
        raise contracts.ContractError("pending_publication", "Legacy source publication is unsettled; history only")
    provider = TimeProvider()
    zone = provider.zone(timezone_name)

    def explicit(value):
        try:
            return timestamp(value)
        except ValueError:
            wall = datetime.fromisoformat(value)
            if wall.tzinfo is not None:
                raise
            return _public_occurrence(_wall_instant(wall, zone, "strict"))["at"]

    spec = source["schedule"]
    if not isinstance(spec, dict) or set(spec) - {"kind", "at", "intervalSec", "interval_sec", "anchor", "cron", "timezone"}:
        raise contracts.ContractError("capability_unsupported", "Unsupported legacy schedule fields")
    kind = spec.get("kind")
    rule = {"ruleId": rule_id, "type": kind}
    if kind == "once":
        rule["at"] = explicit(spec["at"])
    elif kind == "interval":
        if not isinstance(zone, timezone):
            raise contracts.ContractError("capability_unsupported", "Legacy wall-time interval across DST requires review")
        rule.update(seconds=format(Decimal(str(spec.get("intervalSec", spec.get("interval_sec")))), "f"),
                    anchor=explicit(spec["anchor"]), advanceMode="grid")
    elif kind == "cron":
        rule.update(expression=spec["cron"], timezone=spec.get("timezone") or timezone_name,
                    dstPolicy="gap-shift-fold-first")
    else:
        raise contracts.ContractError("capability_unsupported", "Unsupported legacy source rule")
    config = {"rules": [rule], "misfirePolicy": source["misfirePolicy"],
              "graceSeconds": str(source["graceSec"]), "pausePolicy": "advance"}
    provider.validate_config(config)
    occurrence = None
    if source.get("nextFireAt") is not None:
        point = instant(explicit(source["nextFireAt"]))
        before = _decimal_add(point, -Decimal(1).scaleb(min(point.as_tuple().exponent, -6)))
        occurrence = provider.next_after(rule, timestamp(before))
        if occurrence is None or instant(occurrence["at"]) != point:
            raise contracts.ContractError("reference_conflict", "Legacy cursor is outside the canonical rule")
    elif source["enabled"]:
        raise contracts.ContractError("reference_conflict", "Enabled legacy source has no next-fire fact")
    return time_definition(source["name"], config), occurrence


def settlement_definition(name: str, config: Mapping[str, Any], event_type="scheduler.settlement.v1"):
    provider = SettlementProvider()
    return {
        "name": name, "description": "",
        "providerRef": {"type": provider.provider_type, "id": provider.provider_id,
                        "releaseDigest": provider.release_digest, "generation": provider.generation},
        "configSchemaRef": copy.deepcopy(provider.config_schema_ref),
        "config": copy.deepcopy(dict(config)), "eventTypes": [event_type],
        "requiredCapabilities": list(provider.capabilities),
    }
