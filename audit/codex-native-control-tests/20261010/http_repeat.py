import sys
import json
import pathlib
import tempfile
import threading
import traceback
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from packages.jobs.independent_service import Handler, JobStore

root = pathlib.Path(tempfile.mkdtemp(prefix="pan-http-reject-owned-repeat-"))
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
server.store = JobStore(str(root))
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
failures = []
counts = {403: 0, 415: 0}
try:
    for i in range(1000):
        headers, status = [
            ({"Origin": "http://untrusted.example", "Content-Type": "application/json"}, 403),
            ({"Host": "untrusted.example", "Content-Type": "application/json"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ][i % 3]
        try:
            response = urlopen(Request(f"http://127.0.0.1:{server.server_port}/jobs", data=b"{}", headers=headers), timeout=2)
            response.close()
            failures.append({"iteration": i, "expected": status, "error": "unexpected acceptance"})
        except HTTPError as error:
            if error.code != status:
                failures.append({"iteration": i, "expected": status, "actual": error.code})
            else:
                counts[status] += 1
            error.close()
        except Exception:
            failures.append({"iteration": i, "headers": headers, "trace": traceback.format_exc()})
    assert server.store.list() == {"jobs": []}
finally:
    server.shutdown()
    server.server_close()
    thread.join(3)
print(json.dumps({"attempts": 1000, "counts": counts, "failures": failures, "owned_root": str(root)}, ensure_ascii=False, indent=2))
raise SystemExit(bool(failures))
