#!/usr/bin/env bash
# Compatibility shell only; Pan's Python launcher remains the sole executor.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python -m packages.core.launcher exit --root "$ROOT"
