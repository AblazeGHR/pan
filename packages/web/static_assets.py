"""Serve Vite's hashed assets with build-time compression and stable caching."""

from __future__ import annotations

import mimetypes
import os
import re
import stat
from pathlib import Path

from starlette.datastructures import Headers
from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles
from starlette.types import Scope


_HASHED_ASSET = re.compile(r".+-[A-Za-z0-9_-]{8,}\.(?:js|css)$")
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _encoding_quality(header: str, encoding: str) -> float:
    for part in header.lower().split(","):
        name, *parameters = (piece.strip() for piece in part.split(";"))
        if name != encoding:
            continue
        for parameter in parameters:
            if parameter.startswith("q="):
                try:
                    return max(0.0, min(1.0, float(parameter[2:])))
                except ValueError:
                    return 0.0
        return 1.0
    return 0.0


class ReactStaticFiles(StaticFiles):
    """Keep index.html revalidatable and hashed JS/CSS immutable."""

    def file_response(
        self,
        full_path: os.PathLike[str],
        stat_result: os.stat_result,
        scope: Scope,
        status_code: int = 200,
    ) -> Response:
        path = Path(full_path)
        if path.parent.name != "assets" or not _HASHED_ASSET.fullmatch(path.name):
            response = super().file_response(full_path, stat_result, scope, status_code)
            if path.name == "index.html":
                response.headers["Cache-Control"] = "no-cache"
            return response

        request_headers = Headers(scope=scope)
        accepted = request_headers.get("accept-encoding", "")
        for encoding, suffix in (("br", ".br"), ("gzip", ".gz")):
            if _encoding_quality(accepted, encoding) <= 0:
                continue
            compressed = Path(f"{full_path}{suffix}")
            try:
                compressed_stat = compressed.stat()
            except OSError:
                continue
            if not stat.S_ISREG(compressed_stat.st_mode):
                continue
            media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            response = FileResponse(
                compressed,
                status_code=status_code,
                stat_result=compressed_stat,
                media_type=media_type,
            )
            response.headers["Content-Encoding"] = encoding
            response.headers["Cache-Control"] = _IMMUTABLE_CACHE
            response.headers["Vary"] = "Accept-Encoding"
            if self.is_not_modified(response.headers, request_headers):
                return NotModifiedResponse(response.headers)
            return response

        response = super().file_response(full_path, stat_result, scope, status_code)
        response.headers["Cache-Control"] = _IMMUTABLE_CACHE
        response.headers["Vary"] = "Accept-Encoding"
        return response
