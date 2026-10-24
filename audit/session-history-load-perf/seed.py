"""Seed the fixed synthetic dataset used by every load-perf measurement.

The rows are written through the real Session store writers (``replace_history``
+ ``save_full``) so the files on disk carry a complete summary projection and
the measurement exercises the production read path, not a hand-made fixture.

Dataset is frozen: the same session ids, row counts and row text on every run,
so baseline and candidate numbers are comparable.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

BULK_SESSIONS = 30
BULK_ROWS = 1_200
LARGE_SESSION = "ses-perf-large"
LARGE_ROWS = 40_000

ROW_BODY = (
    "step {index:05d}: the deployment pipeline re-reads the manifest, applies the "
    "workspace patch and reports the diff summary for the reviewer."
)


def _rows(count: int, *, tag: str) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append({
            "role": "assistant" if index % 2 else "user",
            "content": f"{tag}/{ROW_BODY.format(index=index)}",
        })
    return rows


def seed(data_root: Path) -> dict:
    from packages.core import session as store

    session_dir = data_root / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    previous_dir = store.SESSION_DIR
    store.SESSION_DIR = session_dir
    store._cache.clear()
    store._all_loaded = False
    started = time.perf_counter()
    seeded: dict[str, int] = {}
    try:
        for index in range(BULK_SESSIONS):
            session_id = f"ses-perf-{index:02d}"
            value = store.Session(id=session_id, name=f"Perf session {index:02d}",
                                  adapter="cbc", model="test-model")
            store.replace_history(value, _rows(BULK_ROWS, tag=session_id))
            store._cache[value.id] = value
            store.save_full(value)
            seeded[session_id] = BULK_ROWS
        large = store.Session(id=LARGE_SESSION, name="Perf large session",
                              adapter="cbc", model="test-model")
        store.replace_history(large, _rows(LARGE_ROWS, tag=LARGE_SESSION))
        store._cache[large.id] = large
        store.save_full(large)
        seeded[LARGE_SESSION] = LARGE_ROWS
    finally:
        store.SESSION_DIR = previous_dir
        store._cache.clear()
        store._all_loaded = False
    return {
        "sessions": len(seeded),
        "rows": sum(seeded.values()),
        "bytes": sum(path.stat().st_size for path in session_dir.iterdir() if path.is_file()),
        "seedSeconds": round(time.perf_counter() - started, 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    args.data_root.mkdir(parents=True, exist_ok=True)
    print(json.dumps(seed(args.data_root.resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
