"""Minimal Pan Capability v1 provider: standard library only, no network."""
import json
import sys

sys.stdin.reconfigure(encoding="utf-8")
sys.stdout.reconfigure(encoding="utf-8")

for line in sys.stdin:
    request = json.loads(line)
    if request["method"] == "provider.initialize":
        result = {"protocol_version": "pan-capability/v1"}
    elif request["method"] == "capability.invoke" and request["params"]["operation"] == "lookup":
        item = request["params"]["arguments"]["item_id"]
        result = {"item_id": item, "value": {"one": 1, "two": 2}.get(item)}
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {
            "code": -32601, "message": "Unknown operation"}}), flush=True)
        continue
    print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
