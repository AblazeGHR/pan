"""Isolated production server for the Editor OS open/reveal browser suite.

Only the OS hand-off boundary is replaced: it records the resolved target that
the real endpoint would have passed to the platform opener, so the test never
launches a real editor or Explorer window. Session APIs, containment, file
reads and the WebSocket fan-out stay real.
"""

import json

import server as fixture

RECORD = fixture.RUNTIME / "opener-calls.jsonl"


def _record(kind, target):
    with RECORD.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": kind, "path": str(target)}) + "\n")


def seed():
    fixture.SESSION_DIR.mkdir(parents=True, exist_ok=True)
    fixture._write_fixture_files()
    (fixture.WORKDIR / "dirty.txt").write_text("disk line 1\ndisk line 2\n", encoding="utf-8")
    (fixture.WORKDIR / "large.bin").write_bytes(b"A" * (5 * 1024 * 1024 + 1))
    (fixture.WORKDIR / "binary.bin").write_bytes(bytes([0xFF, 0xFE, 0, 128, 42]))
    (fixture.RUNTIME / "outside.txt").write_text("outside", encoding="utf-8")
    fixture.session_store._cache.clear()
    fixture.session_store._all_loaded = False
    fixture.session_store.create(
        "Chat Stream",
        adapter="cbc",
        workdir=str(fixture.WORKDIR),
        history=[
            {
                "role": "assistant",
                "content": "[notes](notes.md) [dirty](dirty.txt) "
                "[large](large.bin) [binary](binary.bin)",
            }
        ],
    )
    fixture.session_store.create("Alpha Session", adapter="cbc", workdir=str(fixture.WORKDIR))

    import packages.web.server as server_module
    from fastapi import Body

    server_module._open_file_with_system_default = lambda target: _record("open", target)

    original_available = server_module.file_manager.reveal_available
    forced = {"available": True}

    def reveal_available(platform=None):
        return forced["available"] and original_available(platform)

    def reveal_file(target):
        if not reveal_available():
            raise server_module.file_manager.RevealUnavailable(
                "no system file manager is available"
            )
        _record("reveal", target)

    server_module.file_manager.reveal_available = reveal_available
    server_module.file_manager.reveal_file_in_file_manager = reveal_file

    @server_module.app.post("/__e2e/reveal-capability")
    async def e2e_reveal_capability(payload: dict = Body(...)):
        forced["available"] = bool(payload.get("available"))
        return {"ok": True, "available": forced["available"]}


fixture._seed_sessions = seed
fixture.main()
