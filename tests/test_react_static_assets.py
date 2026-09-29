"""Hashed React assets retain their content type and cache identity."""

import gzip

from fastapi import FastAPI
from fastapi.testclient import TestClient

from packages.web.static_assets import ReactStaticFiles


def test_hashed_asset_prefers_brotli_then_gzip_and_revalidates(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    body = b"const output = 'performance';\n" * 100
    name = "index-ABCDEFGH.js"
    (assets / name).write_bytes(body)
    brotli_fixture = b"already-compressed-asset"
    (assets / f"{name}.br").write_bytes(brotli_fixture)
    (assets / f"{name}.gz").write_bytes(gzip.compress(body))
    (tmp_path / "index.html").write_text("<html>Pan</html>", encoding="utf-8")
    app = FastAPI()
    app.mount("/react", ReactStaticFiles(directory=tmp_path, html=True))

    with TestClient(app) as client:
        route = f"/react/assets/{name}"
        with client.stream("GET", route, headers={"Accept-Encoding": "gzip, br"}) as compressed:
            assert compressed.status_code == 200
            assert b"".join(compressed.iter_raw()) == brotli_fixture
            assert compressed.headers["content-encoding"] == "br"
            media_type = compressed.headers["content-type"].split(";", 1)[0].lower()
            assert media_type in {"text/javascript", "application/javascript"}
            assert compressed.headers["cache-control"] == "public, max-age=31536000, immutable"
            assert compressed.headers["vary"] == "Accept-Encoding"
            etag = compressed.headers["etag"]

        not_modified = client.get(route, headers={
            "Accept-Encoding": "br",
            "If-None-Match": etag,
        })
        assert not_modified.status_code == 304
        assert not_modified.headers["vary"] == "Accept-Encoding"

        gzip_only = client.get(route, headers={"Accept-Encoding": "br;q=0, gzip"})
        assert gzip_only.status_code == 200
        assert gzip_only.content == body
        assert gzip_only.headers["content-encoding"] == "gzip"

        plain = client.get(route, headers={"Accept-Encoding": "identity"})
        assert plain.status_code == 200
        assert plain.content == body
        assert "content-encoding" not in plain.headers
        assert plain.headers["vary"] == "Accept-Encoding"

        index = client.get("/react/")
        assert index.status_code == 200
        assert "immutable" not in index.headers.get("cache-control", "")
        assert client.head("/react/").headers["cache-control"] == "no-cache"

        for route in ("jobs", "editor", "manage/ses_123", "schedules"):
            direct = client.get(f"/react/{route}")
            assert direct.status_code == 200
            assert direct.text == index.text
            assert direct.headers["cache-control"] == "no-cache"
            assert client.head(f"/react/{route}").headers["cache-control"] == "no-cache"

        assert client.get("/react/assets/missing.js").status_code == 404
        assert client.get("/react/assets/missing").status_code == 404
        assert client.get("/react/manifest.webmanifest").status_code == 404
