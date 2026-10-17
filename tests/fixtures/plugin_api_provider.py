"""Pan capability v1 newline JSON-RPC fixture with no network."""
import json
import os
import sys

for line in sys.stdin:
    message = json.loads(line)
    if message["method"] == "provider.initialize":
        result = {"protocol_version": "pan-capability/v1"}
    elif message["method"] == "capability.invoke":
        result = {"operation": message["params"]["operation"],
                  "arguments": message["params"]["arguments"],
                  "session": os.environ["PAN_AGENT_SESSION_ID"]}
    else:
        raise ValueError("Unknown fixture request")
    print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result}), flush=True)
