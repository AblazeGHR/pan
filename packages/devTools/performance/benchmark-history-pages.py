"""Synthetic, temporary-file benchmark; never opens actual Session history."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from packages.core.session import _history_page_from_jsonl  # noqa: E402


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=positive, default=30000)
    parser.add_argument("--repeats", type=positive, default=7)
    parser.add_argument("--content-size", type=positive, default=1550)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="pan-page-bench-") as folder:
        history = Path(folder) / "history.jsonl"
        with history.open("wb") as stream:
            for index in range(options.rows):
                stream.write(json.dumps({"role": "user", "content": str(index) + "x" * options.content_size}).encode() + b"\n")
        digest = hashlib.sha256(history.read_bytes()).hexdigest()
        results = []
        for before in sorted({max(1, options.rows - 50), max(1, options.rows // 2), min(50, options.rows)}, reverse=True):
            expected = None
            for mode, known in [("compatibility", None), ("bounded-json", options.rows)]:
                samples = []
                for _ in range(options.repeats):
                    started = time.perf_counter()
                    value = _history_page_from_jsonl(history, before=before, limit=50, known_total=known)
                    samples.append((time.perf_counter() - started) * 1000)
                    if expected is None:
                        expected = value
                    if value != expected:
                        raise RuntimeError("Paging results differ")
                results.append({"before": before, "mode": mode, "medianMs": statistics.median(samples), "samplesMs": samples})
        if hashlib.sha256(history.read_bytes()).hexdigest() != digest:
            raise RuntimeError("Benchmark changed its input file")
        print(json.dumps({"rows": options.rows, "bytes": history.stat().st_size, "limit": 50, "unchanged": True, "results": results}, indent=2))


if __name__ == "__main__":
    main()
