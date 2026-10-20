"""Read practical metadata, validate migration only in a disposable copy.

No provider/service is started. Report contains counts/hashes, never prompts.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    for key in list(os.environ):
        if key.upper().startswith("PAN_"):
            os.environ.pop(key)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from packages.core import session as sessions, legal_offline, worker

    files = list(source.glob("*.json"))
    original = {p.name: digest(p) for p in files}
    report = {"source": str(source), "sessions": len(files), "roundTrips": 0,
              "historiesUnchanged": 0, "legacyRunning": 0, "inventedIncidents": 0}
    with tempfile.TemporaryDirectory(prefix="pan-legal-data-compat-") as temporary:
        target = Path(temporary) / "sessions"
        target.mkdir()
        assert target.resolve() != source and source not in target.resolve().parents
        for path in files:
            shutil.copy2(path, target / path.name)
        for path in source.glob("*.jsonl"):
            shutil.copy2(path, target / path.name)
        histories = {p.name: digest(p) for p in target.glob("*.jsonl")}
        sessions.SESSION_DIR = target
        sessions._cache.clear()
        sessions._all_loaded = False

        async def validate():
            for path in files:
                raw = json.loads((target / path.name).read_text(encoding="utf-8"))
                report["legacyRunning"] += raw.get("last_legal_worker_state") == "running"
                s = sessions.get(raw["id"], load_history=False)
                assert s is not None
                old = s.to_dict()
                had_incident = legal_offline.is_illegal(s)
                # All writes, including migration preparation, target only the copy.
                await worker._prepare_offline_recovery(s)
                await sessions.save_async(s)
                sessions._cache.pop(s.id, None)
                reloaded = sessions.get(s.id, load_history=False)
                new = reloaded.to_dict()
                for key in ("name", "adapter", "adapter_config", "pan_access", "workdir",
                            "queue_pending", "queue_delivery_ledger", "queue_idempotency_index",
                            "queue_paused", "agent_reports_paused", "managed", "managed_by",
                            "unread_done_generation", "unread_done_read_generation",
                            "last_legal_worker_state", "legal_offline", "execution_operations",
                            "illegal_batches", "notification_settings"):
                    assert new[key] == old[key], (path.name, key)
                assert "recovery_requested" not in new and "recoveryRequested" not in new
                assert legal_offline.is_illegal(reloaded) == had_incident
                if not raw.get("offline_run") and not raw.get("execution_operations"):
                    assert not reloaded.execution_operations and not reloaded.illegal_batches
                    assert reloaded.legal_offline  # unverified history does not invent incidents
                report["roundTrips"] += 1
            for name, previous in histories.items():
                assert digest(target / name) == previous, name
                report["historiesUnchanged"] += 1
        asyncio.run(validate())
    report["sourceChangedDuringRead"] = sum(digest(p) != original[p.name] for p in files)
    report["sourceMetadataManifestSha256"] = hashlib.sha256(
        json.dumps(original, sort_keys=True).encode()).hexdigest()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
