#!/usr/bin/env bash
# Compatibility shell only; lifecycle/start decisions live in the Python launcher.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python -m packages.core.launcher start --root "$ROOT"
