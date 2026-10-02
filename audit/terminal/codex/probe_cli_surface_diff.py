"""Reproducible CLI-surface diff: installed Codex vs newest local upstream build.

Answers "does the installed version already support the attach path, or is it a
newer-build-only feature?" without installing anything.

Method
------
* ``installed``  = the npm package Pan actually resolves (see
  packages/core/adapters/codex/adapter.py::_resolve_codex_js), run via its own
  node entry point exactly like Pan does.
* ``upstream``   = the newest Codex build already present on this machine under
  ``~/.codex/packages/app-server-daemon/releases/<version>-<target>/bin/codex.exe``
  (placed there by Codex's own updater). Running ``--help`` on it is read-only;
  nothing is installed, started, stopped or modified.

Only ``--help`` output is executed -- no daemon command is ever invoked, so the
user's running app-server daemon is untouched.

Usage
-----
    E:/software/miniforge/python.exe audit/terminal/codex/probe_cli_surface_diff.py
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from packages.core.adapters.codex.adapter import _resolve_codex_js, _resolve_codex_node  # noqa: E402

PROBES = [
    ("top-level", []),
    ("app-server", ["app-server"]),
    ("app-server daemon", ["app-server", "daemon"]),
    ("app-server proxy", ["app-server", "proxy"]),
    ("resume", ["resume"]),
    ("agents", ["agents"]),
    ("remote-control", ["remote-control"]),
]
INTERESTING = re.compile(r"--remote|--listen|--stdio|resume|SESSION_ID|ws://|unix://|wss://", re.I)


def run(cmd: list[str], timeout: float = 60) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr).decode("utf-8", "replace")
    except Exception as exc:
        return -1, f"<<error {exc!r}>>"


def newest_upstream() -> str | None:
    pattern = os.path.expanduser(
        "~/.codex/packages/app-server-daemon/releases/*/bin/codex.exe"
    )
    candidates = glob.glob(pattern)

    def version_key(path: str) -> tuple:
        m = re.search(r"releases[/\\]([0-9]+)\.([0-9]+)\.([0-9]+)", path)
        return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)

    return max(candidates, key=version_key) if candidates else None


def version_of(cmd: list[str]) -> str:
    _, out = run(cmd + ["--version"], timeout=60)
    return out.strip().splitlines()[0] if out.strip() else "<unknown>"


def main() -> int:
    node, js = _resolve_codex_node(), _resolve_codex_js()
    installed_cmd = [node, js]
    upstream_exe = newest_upstream()
    out_dir = os.path.join("audit", "terminal", "codex", "evidence")
    os.makedirs(out_dir, exist_ok=True)

    versions = {
        "installed": {"cmd": installed_cmd, "version": version_of(installed_cmd)},
        "upstream_local": {
            "path": upstream_exe,
            "version": version_of([upstream_exe]) if upstream_exe else None,
        },
    }
    print(json.dumps(versions, indent=2))

    surfaces: dict[str, dict] = {}
    for label, args in PROBES:
        rc_i, out_i = run(installed_cmd + args + ["--help"])
        rc_u, out_u = run([upstream_exe] + args + ["--help"]) if upstream_exe else (-1, "")
        surfaces[label] = {
            "installed_rc": rc_i,
            "upstream_rc": rc_u,
            "installed_only_flags": sorted(set(re.findall(r"--[a-z][a-z0-9-]+", out_i)) - set(re.findall(r"--[a-z][a-z0-9-]+", out_u))),
            "upstream_only_flags": sorted(set(re.findall(r"--[a-z][a-z0-9-]+", out_u)) - set(re.findall(r"--[a-z][a-z0-9-]+", out_i))),
            "identical_output": out_i == out_u,
            "installed_interesting_lines": [l.strip() for l in out_i.splitlines() if INTERESTING.search(l)][:14],
            "upstream_interesting_lines": [l.strip() for l in out_u.splitlines() if INTERESTING.search(l)][:14],
        }

    result = {
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "versions": versions,
        "surfaces": surfaces,
        "note": "Only --help was executed. No daemon subcommand was run and nothing was installed.",
    }
    path = os.path.join(out_dir, f"cli-surface-diff-{time.strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2, ensure_ascii=False)
    print(f"evidence written: {path}")

    for label, s in surfaces.items():
        flag = "IDENTICAL" if s["identical_output"] else "DIFFERS"
        print(f"  {label:22s} {flag}"
              f"  installed_only={s['installed_only_flags']}  upstream_only={s['upstream_only_flags']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
