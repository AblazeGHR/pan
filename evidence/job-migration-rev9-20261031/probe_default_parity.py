"""Source-only comparison against exact pre-rev9 inventory; synthetic roots."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "tests")]
from scripts.job_final_migration.inventory import build_inventory
from tests.test_job_migration_qq_runtime import source_for

BASE = "dc8b1ff805956a2290b91ee65d0d48f0949470ec"


def frozen_module(name, relative):
    raw = subprocess.check_output(["git", "show", BASE + ":" + relative], cwd=ROOT)
    module = types.ModuleType(name)
    module.__package__ = "scripts.job_final_migration"
    module.__file__ = str(ROOT / relative)
    sys.modules[name] = module
    exec(compile(raw, module.__file__, "exec"), module.__dict__)
    return module


runtime = frozen_module("_rev9_baseline_runtime_fields", "scripts/job_final_migration/runtime_fields.py")
inventory = frozen_module("_rev9_baseline_inventory", "scripts/job_final_migration/inventory.py")
inventory.delivery_binding_gap = runtime.delivery_binding_gap
rows = []
with tempfile.TemporaryDirectory(prefix="pan-rev9-default-parity-") as owned:
    for bot in ("654321", None, "omitted", "", "bad-bot"):
        for state in ("unstarted", "pending", "unknown", "partial"):
            root = Path(owned) / str(len(rows))
            path, source = source_for(root, bot)
            if state == "pending":
                source["undeliveredFires"] = [{"dispatchKey": "owned-original", "outcome": "known-no-attempt"}]
            elif state != "unstarted":
                source["lastDelivery"] = {"outcome": state, "receiptId": "owned-original"}
            path.write_text(json.dumps(source), encoding="utf-8")
            before = path.read_bytes()
            previous, current = inventory.build_inventory(root), build_inventory(root)
            assert current == previous, (bot, state)
            assert path.read_bytes() == before
            item, = current["instances"]
            rows.append({"bot": bot, "state": state, "inventoryEqual": True,
                         "sourceSha256": hashlib.sha256(before).hexdigest(),
                         "classification": item["classification"], "reasonCodes": item["reasonCodes"]})
assert not Path(owned).exists()
print(json.dumps({"base": BASE, "source": subprocess.check_output(
    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
    "defaultInventoryCasesEqual": len(rows), "ownedRootRemoved": True, "cases": rows}, indent=2))
