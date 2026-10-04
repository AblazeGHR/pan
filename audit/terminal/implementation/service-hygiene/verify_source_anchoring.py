"""可复算：5 件源在 ``cde2dbd8`` 的 LF blob / CRLF 工作树 sha256（F11 口径更正）。

只读脚本：不修改被审对象、不写生产代码。两种口径显式区分，避免把换行差异
误判成源码不一致：

  口径 A（LF blob）  = sha256(``git cat-file blob cde2dbd8:<path>``)  —— Git 提交字节
  口径 B（CRLF 检出）= sha256(把 A 字节里每个 ``\\n`` 重写为 ``\\r\\n``)

因 ``core.autocrlf=true``，B 即 ``cde2dbd8`` **检出时**的工作树原始字节
（未做卫生修改前实测逐字节一致）。本脚本另列**当前**工作树哈希 C：本次卫生
只改 ``tests/test_terminal_service.py``，故仅该文件 C≠B，属预期；A/B 两列与
该改动无关，仍以提交 blob 为准。

判定：以 **A（LF blob）** 为锚定权威；旧 README §1 声称凡能定性为 A/B/B 截断者
通过（仅**核对口径**，**不改旧表、不倒写历史**）。

诊断 JSON 写 ``evidence/source_anchoring.json``。
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]
EVIDENCE_DIR = HERE / "evidence"

COMMIT = "cde2dbd867eb7f59eb551df70c4aa9d5baeecfa9"

FILES = [
    "packages/core/terminal/service.py",
    "packages/core/terminal/launcher.py",
    "tests/test_terminal_service.py",
    "tests/test_terminal_launcher.py",
    "docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md",
]

# 实现 README §1 旧表的原始声称（逐字复制，用于定性；**不改旧表**）。
OLD_README_CLAIM = {
    "packages/core/terminal/service.py": "71e295c0283e0a41e03502c8498e662b334cc9f42c872ad9415742f59833fbf3",
    "packages/core/terminal/launcher.py": "0223bb7bc0d6a884a304b97291c8e5140fb276a4b8f3fb51aca35293af30ebfc",
    "tests/test_terminal_service.py": "aea9065c388b5f26d344763078a679f0dca92fec4c6e319218b8df9c605c960f",
    "tests/test_terminal_launcher.py": "c8f0b43930d9d199388a2bb67b0e1565e3107f4f609217f7a09278e92472e0f",
    "docs/design/PAN_TERMINAL_SERVICE_INTERFACES_20261003.md": "a3a0a3469d6485198229e9cfcc4fc6e47c6c232c6541282ca19596c2d8a28c70",
}


def _blob(rel: str) -> bytes:
    return subprocess.run(
        ["git", "cat-file", "blob", f"{COMMIT}:{rel}"],
        capture_output=True,
        check=True,
        cwd=str(REPO_ROOT),
    ).stdout


def _classify(claim: str, lf_sha: str, crlf_sha: str) -> str:
    if claim == lf_sha:
        return "LF_blob"
    if claim == crlf_sha:
        return "CRLF_worktree"
    if len(claim) < 64 and crlf_sha.startswith(claim):
        return "CRLF_worktree_truncated"
    return "unclassifiable"


def main() -> int:
    rows = []
    for rel in FILES:
        blob = _blob(rel)
        crlf = blob.replace(b"\n", b"\r\n")
        lf_sha = hashlib.sha256(blob).hexdigest()
        crlf_sha = hashlib.sha256(crlf).hexdigest()
        worktree = (REPO_ROOT / rel).read_bytes()
        worktree_sha = hashlib.sha256(worktree).hexdigest()
        claim = OLD_README_CLAIM[rel]
        verdict = _classify(claim, lf_sha, crlf_sha)
        rows.append(
            {
                "path": rel,
                "lf_blob_sha256": lf_sha,
                "lf_blob_bytes": len(blob),
                "newline_count": blob.count(b"\n"),
                "crlf_worktree_sha256": crlf_sha,
                "crlf_worktree_bytes": len(crlf),
                "current_worktree_sha256": worktree_sha,
                "current_worktree_matches_crlf_checkout": worktree_sha == crlf_sha,
                "old_readme_claim": claim,
                "old_readme_claim_len": len(claim),
                "old_readme_claim_verdict": verdict,
            }
        )

    print(f"source commit: {COMMIT}")
    print("algorithm: sha256 over raw file bytes")
    print()
    print(f"{'file':<62} {'LF_blob_sha256(A)':<66} {'A_bytes':>8} {'CRLF_sha256(B)':<66} {'B_bytes':>8} {'nl':>5}")
    for r in rows:
        print(
            f"{r['path']:<62} {r['lf_blob_sha256']:<66} {r['lf_blob_bytes']:>8} "
            f"{r['crlf_worktree_sha256']:<66} {r['crlf_worktree_bytes']:>8} {r['newline_count']:>5}"
        )
    print()
    print("旧 README §1 声称定性（不改旧表，仅核对口径）:")
    for r in rows:
        label = {
            "LF_blob": "== 口径 A（LF blob）",
            "CRLF_worktree": "== 口径 B（CRLF 工作树）",
            "CRLF_worktree_truncated": f"== 口径 B 截断串（{r['old_readme_claim_len']}位，缺末位）",
            "unclassifiable": "与任一完整口径都不符",
        }[r["old_readme_claim_verdict"]]
        print(f"  {r['path']:<62} {label}")
        if not r["current_worktree_matches_crlf_checkout"]:
            print(f"  {'':<62} （当前工作树≠cde 检出：本次卫生已改本文件，属预期）")

    unclassifiable = sum(1 for r in rows if r["old_readme_claim_verdict"] == "unclassifiable")
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "commit": COMMIT,
        "algorithm": "sha256",
        "caliber_A": "LF git blob bytes (git cat-file blob cde2dbd8:<path>)",
        "caliber_B": "CRLF worktree bytes at cde2dbd8 checkout (core.autocrlf=true)",
        "crlf_derivation": "B = A with each \\n rewritten to \\r\\n (verified byte-identical to cde2dbd8 worktree)",
        "files": rows,
        "old_readme_unclassifiable": unclassifiable,
    }
    (EVIDENCE_DIR / "source_anchoring.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print()
    print(f"old_readme_unclassifiable={unclassifiable}")
    print(f"anchoring rc={1 if unclassifiable else 0}")
    return 1 if unclassifiable else 0


if __name__ == "__main__":
    sys.exit(main())
