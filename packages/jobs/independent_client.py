"""stdlib HTTP client for the user-level independent Job service."""
from __future__ import annotations
import json
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen


class IndependentJobClient:
    def __init__(self, endpoint, timeout=5.0):
        parsed = urlsplit(endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("endpoint must be an absolute HTTP URL")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = float(timeout)

    def _request(self, method, path, body=None):
        data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = Request(self.endpoint + path, data=data, method=method,
                      headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urlopen(req, timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as e:
            try: detail = json.loads(e.read().decode("utf-8"))
            except Exception: detail = {"error": "request failed"}
            if method == "GET" and path.startswith("/jobs/") and e.code == 404:
                return None
            raise RuntimeError(f"Job service HTTP {e.code}: {detail.get('error', 'request failed')}") from None
        except (URLError, TimeoutError) as e:
            raise RuntimeError("Job service unavailable") from None

    def health(self): return self._request("GET", "/health")
    def start(self, job): return self._request("POST", "/jobs", job)
    def get(self, job_id): return self._request("GET", "/jobs/" + quote(job_id, safe=""))
    def list(self): return self._request("GET", "/jobs")
    def cancel(self, job_id): return self._request("POST", "/jobs/" + quote(job_id, safe="") + "/cancel", {})
    def retry(self, job_id): return self._request("POST", "/jobs/" + quote(job_id, safe="") + "/retry", {})
    def events(self): return self._request("GET", "/events")
    def ack(self, event_id): return self._request("POST", "/events/" + quote(event_id, safe="") + "/ack", {})
