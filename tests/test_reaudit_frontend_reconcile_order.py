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


def test_partial_history_does_not_reorder_the_turn(reconcile_cases):
    # FIXED by the consistency repair (was strict-xfail, reason below).
    # reconcileWorkerResult no longer pushes the result row into history before
    # merging the live-only rows: the result updates the task's own final block
    # in place, so the provider order [user, analysis, tool, final] survives.
    # Previous reason: "REPRODUCED: ... producing [user, final, analysis, tool]
    # instead of the provider order [user, analysis, tool, final]".
    case = reconcile_cases["partial_history_with_ids"]
    roles_contents = [(r[0], r[1]) for r in case["currentMessages"]]
    assert roles_contents == [
        ("user", "q"),
        ("assistant", "analysis"),
        ("tool", 'Read({"file_path":"a.txt"})'),
        ("assistant", "final"),
    ]


def test_idless_turn_is_not_duplicated(reconcile_cases):
    # FIXED by the authoritative-convergence repair: the case replays a turn
    # live while the loaded canonical window already covers it (revision 1 on
    # both sides), and the terminal event carries terminalCoverage. When the
    # window's revision satisfies the coverage and the finalized rows line up
    # exactly with the durable rows ending at the runtime anchor, the replayed
    # rows are converged onto the already-durable rows instead of being
    # appended a second time. Identity is never guessed from body text or an
    # arbitrary ordinal: the structural offset window is required and exact
    # role+content is only a guard, so a differing result or a delta past the
    # anchor keeps the old (append) behaviour.
    case = reconcile_cases["ordered_turn_idless_result_equals_last"]
    assert case["currentMessages"] == [
        ["user", "q", None],
        ["assistant", "analysis", None],
        ["tool", 'Read({"file_path":"a.txt"})', None],
        ["assistant", "final", None],
    ]


def test_pins_the_repaired_reorder_result(reconcile_cases):
    """Updated by the consistency repair: this used to pin the *incorrect*
    output ("so a fix must update this test"); it now pins the repaired one."""

    # The provider order is restored: analysis, tool, final (no final-first).
    reordered = reconcile_cases["partial_history_with_ids"]
    assert [r[0] for r in reordered["currentMessages"]] == [
        "user", "assistant", "tool", "assistant",
    ]
    assert reordered["currentMessages"][1][1] == "analysis"
    assert reordered["currentMessages"][2][1].startswith("Read(")
    assert reordered["currentMessages"][3][1] == "final"

    # The replay-duplication case is now fixed (see the test above): the
    # replayed rows converge onto the already-durable canonical rows.
    idless = reconcile_cases["ordered_turn_idless_result_equals_last"]
    roles = [r[0] for r in idless["currentMessages"]]
    assert roles == ["user", "assistant", "tool", "assistant"]
    assert idless["currentMessages"][3][1] == "final"
