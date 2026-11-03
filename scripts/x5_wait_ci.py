"""One bounded, durable-Job CI waiter; verifies SHA and downloads terminal evidence."""
import argparse
import json
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--sha", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    command = ["gh", "run", "view", args.run, "--json", "headSha,status,conclusion,jobs,url"]
    original = json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))
    if original["headSha"] != args.sha:
        raise RuntimeError("refusing to wait on a different source SHA")
    try:
        result = subprocess.run(["gh", "run", "watch", args.run, "--exit-status", "--interval", "15"], timeout=1200)
    except subprocess.TimeoutExpired:
        (args.output / "waiter-timeout.json").write_text(json.dumps({"run": args.run, "sha": args.sha,
            "status": "wait_timeout", "ciOutcome": "unknown"}), encoding="utf-8")
        return 2
    terminal = json.loads(subprocess.check_output(command, text=True, encoding="utf-8"))
    if terminal["headSha"] != args.sha or terminal["status"] != "completed":
        raise RuntimeError("CI terminal identity/status unconfirmed")
    (args.output / "run.json").write_text(json.dumps(terminal, indent=2), encoding="utf-8")
    subprocess.run(["gh", "run", "download", args.run, "--dir", str(args.output / "artifacts")], check=True, timeout=120)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
