"""v4（MA 静态注意裁定 + 证据审计）。

- launcher 收尾路径核验：单次 emulator.close(8.0)、异常仅记录、sys.exit——无失败重试/owner 消费；
- close-budget 用例归属：单独 HeadlessEmulator（非 launcher 故障路径）；
- ROUND2 报告 §3 表述评估（记录原文 + 裁定）；
- source_blobs 3/3；pins 复算；r2 日志 13/13 入库；首轮旧证据零覆盖；本审查 13×2。
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from v_common import REPO_ROOT, start_hard_watchdog, write_evidence

SEED = "3a579da64c249af35fb20fbfcab6340263d10fa6"
PARENT = "7e94f13ac643e8942673fd169929a3789ed7ee20"
R2 = REPO_ROOT / "audit" / "terminal" / "implementation" / "composition" / "r2"
SIDECAR = REPO_ROOT / "packages" / "core" / "terminal" / "emulator_sidecar"


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30
    ).stdout


def main() -> int:
    watchdog = start_hard_watchdog("v4", 180)
    out: dict = {}

    tests_text = (REPO_ROOT / "tests/test_terminal_composition.py").read_text(encoding="utf-8")

    # ① launcher 收尾段提取
    start = tests_text.index("# 组合方负责 emulator 生命周期")
    end = tests_text.index("sys.exit(code)", start)
    tail = tests_text[start : end + len("sys.exit(code)")]
    out["launcher_tail"] = {
        "close_call_count": tail.count("emulator.close("),
        "has_retry_loop": bool(re.search(r"while |for .*retry|retry_cleanup", tail)),
        "exception_only_records": '{"closed": False, "error": type(exc).__name__}' in tail,
        "single_close_then_exit": tail.count("emulator.close(") == 1 and "sys.exit(code)" in tail,
    }

    # ② close-budget 用例归属
    m = re.search(
        r"def test_composition_r2_engine_close_budget_owner_retryable\(\):(.*?)(?=\n\ndef |\Z)",
        tests_text,
        re.S,
    )
    body = m.group(1) if m else ""
    references_external = any(
        tok in body for tok in ("runner_client", "Session(", "Popen", ".attach(", ".control(")
    )
    out["close_budget_case"] = {
        "constructs_own_emulator": "HeadlessEmulator(cols=80, rows=24)" in body,
        "references_runner_or_launcher_code": references_external,
        "note": "单独 HeadlessEmulator；非 launcher 故障路径（MA 静态注意确认）",
    }

    # ③ ROUND2 报告 §3 表述
    report_text = (
        REPO_ROOT / "docs/design/PAN_TERMINAL_COMPOSITION_REVIEW_20261003_ROUND2.md"
    ).read_text(encoding="utf-8")
    s3_line = ""
    for line in report_text.splitlines():
        if "宿主在 `runner.run()` 返回后显式" in line:
            s3_line = line.strip()
            break
    out["report_section3"] = {
        "line": s3_line,
        "states_production_launcher_not_implemented": "生产 launcher 未实现/未批准" in report_text,
        "lists_launcher_closure_unverified": "生产 launcher 归属与失败收尾闭环" in report_text,
        "assessment": (
            "『失败保 owner 可重试』指 emulator.close 的对象语义（由单独用例证实）；"
            "§3 末句与 §4 已把『生产 launcher 未实现/失败收尾未验证』列为边界。"
            "裁定：表述可接受（不返工）；建议后续在一句话内显式区分"
            "『引擎对象可重试 ≠ 测试宿主消费失败 owner ≠ 生产 launcher 闭环』。"
        ),
    }

    # ④ source_blobs 3/3
    claimed = json.loads((R2 / "source_blobs.json").read_text(encoding="utf-8"))
    checks = {}
    for name, meta in (claimed.get("blobs") or {}).items():
        submitted = _git("rev-parse", f"{SEED}:{name}").strip()
        checks[name] = {"claimed": meta.get("blob"), "submitted": submitted,
                        "match": meta.get("blob") == submitted}
    out["source_blob_check"] = {"all_match": all(v["match"] for v in checks.values()), "details": checks}

    # ⑤ pins 复算
    pin = json.loads((R2 / "sidecar_pins.json").read_text(encoding="utf-8"))
    pj = hashlib.sha256((SIDECAR / "package.json").read_bytes()).hexdigest()
    lk = hashlib.sha256((SIDECAR / "package-lock.json").read_bytes()).hexdigest()
    installed = {}
    for dep in ("@xterm/headless", "@xterm/addon-serialize"):
        p = SIDECAR / "node_modules" / dep / "package.json"
        installed[dep] = json.loads(p.read_text(encoding="utf-8")).get("version") if p.is_file() else None
    out["pins_recheck"] = {
        "package_json_sha_match": pj == pin.get("package_json_sha256"),
        "package_lock_sha_match": lk == pin.get("package_lock_sha256"),
        "installed": installed,
    }

    # ⑥ r2 日志入库与首轮旧证据零覆盖
    logs = {}
    for name in ("pytest_direct.txt", "pytest_uv.txt"):
        rel = f"audit/terminal/implementation/composition/r2/{name}"
        text = (REPO_ROOT / rel).read_text(encoding="utf-8") if (REPO_ROOT / rel).is_file() else ""
        logs[name] = {
            "in_git": _git("cat-file", "-t", f"{SEED}:{rel}").strip() == "blob",
            "passed": text.count("PASSED "),
            "failed": text.count("FAILED "),
        }
    name_status = _git("diff", "--name-status", PARENT, SEED).splitlines()
    old_touched = [
        l for l in name_status
        if l.strip().startswith(("M", "D"))
        and "audit/terminal/implementation/composition/" in l
        and "/r2/" not in l
    ]
    prod = [l.split("\t", 1)[1] for l in name_status if l.strip() and l.split("\t", 1)[1].startswith("packages/")]
    out["coverage"] = {
        "old_evidence_touched": old_touched[:5],
        "old_evidence_untouched": len(old_touched) == 0,
        "production_touched_this_diff": prod,
    }

    # ⑦ 本审查 13×2
    my = {}
    my_logs = REPO_ROOT / "audit/terminal/implementation/composition-review/round2/logs"
    for name in ("direct13", "uv13"):
        f = my_logs / f"{name}.out.txt"
        text = f.read_text(encoding="utf-8") if f.is_file() else ""
        my[name] = {
            "exists": f.is_file(),
            "passed": text.count("PASSED "),
            "summary": [l.strip() for l in text.splitlines() if re.search(r"\d+ passed in", l)][:1],
        }
    out["my_rerun"] = my

    out["verdict"] = {
        "launcher_single_close_confirmed": out["launcher_tail"]["single_close_then_exit"]
        and out["launcher_tail"]["exception_only_records"]
        and not out["launcher_tail"]["has_retry_loop"],
        "close_budget_case_standalone": out["close_budget_case"]["constructs_own_emulator"]
        and not out["close_budget_case"]["references_runner_or_launcher_code"],
        "report_boundary_textualized": out["report_section3"]["states_production_launcher_not_implemented"]
        and out["report_section3"]["lists_launcher_closure_unverified"],
        "source_blobs_verified": out["source_blob_check"]["all_match"],
        "pins_verified": out["pins_recheck"]["package_json_sha_match"]
        and out["pins_recheck"]["package_lock_sha_match"],
        "r2_logs_13": all(v["in_git"] and v["passed"] == 13 for v in logs.values()),
        "old_evidence_untouched": out["coverage"]["old_evidence_untouched"],
        "no_production_in_diff": out["coverage"]["production_touched_this_diff"] == [],
        "my_13x2": my["direct13"]["passed"] == 13 and my["uv13"]["passed"] == 13,
    }
    write_evidence("v4_scope_evidence", out)
    watchdog.cancel()
    print(json.dumps(out["verdict"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
