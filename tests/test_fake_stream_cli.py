"""The local provider fixture must implement the real UTF-8 wire contract."""
import json
import os
from pathlib import Path
import subprocess
import sys

from packages.jobs.window import command_window_options


def test_fake_stream_cli_uses_utf8_under_windows_ansi_defaults(tmp_path):
    log = tmp_path / "provider.jsonl"
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith("PAN_")}
    env.update(PYTHONIOENCODING="cp1252", PAN_E2E_FAKE_LOG=str(log))
    text = "继续"
    payload = {"message": {"content": [{"text": text}]}}
    result = subprocess.run(
        [sys.executable, str(Path(__file__).parent / "support/fake_stream_cli.py")],
        input=(json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"),
        capture_output=True, env=env, timeout=10, **command_window_options(),
    )
    assert result.returncode == 0, result.stderr
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert records[0]["stdinEncoding"] == records[0]["stdoutEncoding"] == "utf-8"
    assert records[1]["text"] == text
    frames = [json.loads(line) for line in result.stdout.decode("utf-8").splitlines()]
    assert frames[-1]["result"] == text
