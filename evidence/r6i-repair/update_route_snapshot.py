"""Admit only the two new source reads and their deprecated aliases."""
import json
from pathlib import Path
from packages.web.server import app
from tests.test_job_path_flip import route_snapshot, SNAPSHOT

old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
new = route_snapshot(app)
added = [row for row in new["routes"] if row not in old["routes"]]
removed = [row for row in old["routes"] if row not in new["routes"]]
assert removed == [] and old["methodPathConflicts"] == new["methodPathConflicts"] == []
assert len(added) == 4
assert {row["path"] for row in added} == {
    prefix + suffix for prefix in ("/api", "/api/final")
    for suffix in ("/event-creators/providers", "/event-creators/{creator_id}/references")}
assert all(row["methods"] == ["GET"] for row in added)
SNAPSHOT.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
Path(__file__).with_name("route-snapshot-delta.json").write_text(
    json.dumps({"added": added, "removed": removed, "methodPathConflicts": []}, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"added": len(added), "removed": len(removed)}))
