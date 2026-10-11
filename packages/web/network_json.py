"""Negotiated compression for bounded JSON list and history responses.

Do not install this as global middleware: streams, WebSockets and precompressed
static assets must keep their existing response paths.
"""

import asyncio
import gzip
import hashlib

from starlette.datastructures import Headers, QueryParams
from starlette.responses import JSONResponse, Response


def _accepts_gzip(header: str) -> bool:
    qualities = {}
    for part in header.lower().split(","):
        name, *parameters = (item.strip() for item in part.split(";"))
        quality = 1.0
        for parameter in parameters:
            if parameter.startswith("q="):
                try:
                    quality = float(parameter[2:])
                except ValueError:
                    quality = 0.0
        qualities[name] = quality
    # An explicit refusal overrides a wildcard. Absence is kept uncompressed
    # for older clients rather than assuming that they can decode gzip.
    return 0 < qualities.get("gzip", qualities.get("*", 0)) <= 1


class NetworkJSONResponse(JSONResponse):
    async def __call__(self, scope, receive, send):
        self.headers.add_vary_header("Accept-Encoding")
        request_headers = Headers(scope=scope)
        if (scope.get("method") in ("GET", "HEAD") and self.status_code == 200
                and scope.get("path") == "/api/sessions"
                and QueryParams(scope.get("query_string", b"")).get("summary") == "1"):
            # Revalidate against the *current* serialized response, including
            # all worker/queue/pin/preview fields. No server snapshot is cached.
            # Weak validation represents equivalent identity and gzip bodies.
            digest = await asyncio.to_thread(lambda: hashlib.sha256(self.body).hexdigest())
            etag = f'W/"{digest}"'
            self.headers["ETag"] = etag
            self.headers["Cache-Control"] = "private, no-cache"
            candidates = request_headers.get("if-none-match", "").split(",")
            if any(value.strip() == "*" or value.strip().removeprefix("W/") == etag[2:]
                   for value in candidates):
                response = Response(status_code=304, headers={
                    "ETag": etag, "Cache-Control": self.headers["Cache-Control"],
                    "Vary": self.headers["Vary"],
                })
                await response(scope, receive, send)
                return
        if (
            scope["type"] == "http"
            and scope.get("method") != "HEAD"
            and len(self.body) >= 2048
            and "content-encoding" not in self.headers
            and _accepts_gzip(request_headers.get("accept-encoding", ""))
        ):
            # Level 1 prioritizes latency. Compression runs away from the event
            # loop so concurrent WS events and visible requests can progress.
            body = await asyncio.to_thread(gzip.compress, self.body, compresslevel=1, mtime=0)
            if len(body) < len(self.body):
                self.body = body
                self.headers["Content-Encoding"] = "gzip"
                self.headers["Content-Length"] = str(len(body))
        await super().__call__(scope, receive, send)
