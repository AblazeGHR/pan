"""Frontend reconcile-order regressions, driven by the real sessionStore bundle.

These tests execute the *real* `sessionStore.reconcileWorkerResult` by bundling
`packages/web/src/stores/sessionStore.ts` with esbuild (borrowed read-only from a
sibling worktree that has node_modules installed).  They are skipped when node or
that esbuild path is unavailable.

`--runxfail` prints the raw reproduced outputs.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "evidence" / "probe_frontend_reconcile.cjs"
SIBLING_ESBUILD = Path(
    "D:/project/pan-worktrees/frontend-reaudit-history-ds-20260921"
    "/packages/web/node_modules/.pnpm/esbuild@0.21.5/node_modules/esbuild"
)

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or not SIBLING_ESBUILD.exists(),
    reason="needs node plus the sibling worktree's esbuild to bundle the real store",
)


@pytest.fixture(scope="module")
def reconcile_cases() -> dict:
    result = subprocess.run(
        ["node", str(PROBE)],
        capture_output=True, text=True, cwd=str(REPO), timeout=180,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.xfail(strict=True, reason=(
    "REPRODUCED: when the client's cached session.history lags the live turn, "
    "reconcileWorkerResult pushes the result row into history BEFORE the live-only "
    "rows are merged, producing [user, final, analysis, tool] instead of the "
    "provider order [user, analysis, tool, final]."))
def test_partial_history_does_not_reorder_the_turn(reconcile_cases):
    case = reconcile_cases["partial_history_with_ids"]
    roles_contents = [(r[0], r[1]) for r in case["currentMessages"]]
    assert roles_contents == [
        ("user", "q"),
        ("assistant", "analysis"),
        ("tool", 'Read({"file_path":"a.txt"})'),
        ("assistant", "final"),
    ]


@pytest.mark.xfail(strict=True, reason=(
    "REPRODUCED: for an id-less provider (real cbc) the whole live turn is "
    "appended a second time because no live row can be matched by identity."))
def test_idless_turn_is_not_duplicated(reconcile_cases):
    case = reconcile_cases["ordered_turn_idless_result_equals_last"]
    assert case["currentMessages"] == [
        ["user", "q", None],
        ["assistant", "analysis", None],
        ["tool", 'Read({"file_path":"a.txt"})', None],
        ["assistant", "final", None],
    ]


def test_documents_the_current_reorder_and_duplication(reconcile_cases):
    """Pins the *current* (incorrect) output so a fix must update this test."""
    reordered = reconcile_cases["partial_history_with_ids"]
    assert [r[0] for r in reordered["currentMessages"]] == [
        "user", "assistant", "assistant", "tool",
    ]
    assert reordered["currentMessages"][1][1] == "final"

    idless = reconcile_cases["ordered_turn_idless_result_equals_last"]
    contents = [r[1] for r in idless["currentMessages"]]
    assert contents.count("final") == 2
    assert contents.count("analysis") == 2
