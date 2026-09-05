"""Static terminology checks for normative docs (MA / TA / Session / Worker).

统一术语（用户确立）：
- **角色（职责）**：meta-agent（MA）负责编排元任务；task-agent（TA）执行具体任务。
- **身份（持久编排对象）**：Session 承载 MA 或 TA 身份，以 session_id 寻址。
- **进程（物理执行体）**：Worker 是临时 CLI 进程，实际运行 MA 或 TA 的会话——
  既有 MA Worker 也有 TA Worker；不能把 Worker 写成仅属于 TA，也不能与 TA 等同。

本测试只检查**规范文档**（README / 用户手册 / pan skill 及 references），
不检查 docs/archive、docs/plans&overviews 等历史记录（历史文档保持原貌，
映射说明放在当前规范文档中）。
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 需要检查的规范文档（相对仓库根）
NORMATIVE_DOCS = [
    "README.md",
    "README.en.md",
    "docs/USER_MANUAL.md",
    "docs/USER_MANUAL.en.md",
    "docs/skills/pan/SKILL.md",
    "docs/skills/pan/references/http-api.md",
    "docs/skills/pan/references/ws-protocol.md",
    "docs/skills/pan/references/user-guide.md",
]

# 禁用的旧称呼 / 混称（在规范文档中不应出现）。
# 注意：worker_* / agent_* 是 MCP 工具名（兼容别名），不在禁用之列。
BANNED_PATTERNS = [
    (r"worker-agent", "旧称呼 worker-agent（应改为 TA 的 Session/Worker）"),
    (r"worker agent", "旧称呼 worker agent（应改为 TA 的 Session/Worker）"),
    (r"sub-agent", "旧称呼 sub-agent（应改为 TA）"),
    (r"subagent", "旧称呼 subagent（应改为 TA）"),
    (r"child agent", "旧称呼 child agent（应改为 TA）"),
    (r"child-agent", "旧称呼 child-agent（应改为 TA）"),
    (r"Task-Agent", "旧大小写 Task-Agent（统一小写 task-agent，TA）"),
]

# 关键术语澄清必须出现（防止后续编辑丢失“MA 也运行在 Worker 中”的说明）。
REQUIRED_SNIPPETS = {
    "docs/skills/pan/SKILL.md": "既有 MA Worker 也有 TA Worker",
    "docs/USER_MANUAL.md": "既有 MA Worker 也有 TA Worker",
    "README.md": "MA Worker",
    "README.en.md": "MA Workers",
}


def _doc(rel: str) -> str:
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_normative_docs_exist():
    for rel in NORMATIVE_DOCS:
        assert (REPO_ROOT / rel).is_file(), f"缺少规范文档: {rel}"


def test_no_banned_legacy_terms():
    for rel in NORMATIVE_DOCS:
        lines = _doc(rel).splitlines()
        for lineno, line in enumerate(lines, start=1):
            # 允许“旧称呼映射”说明行本身引用旧词（用于向读者解释历史文档）
            if "旧称呼映射" in line or "legacy" in line.lower():
                continue
            for pattern, reason in BANNED_PATTERNS:
                matches = re.findall(pattern, line)
                assert not matches, (
                    f"{rel}:{lineno}: 检出旧称呼 {reason}（{matches}）"
                )


def test_ma_worker_clarification_present():
    for rel, snippet in REQUIRED_SNIPPETS.items():
        text = _doc(rel)
        assert snippet in text, f"{rel}: 缺少关键术语澄清「{snippet}」"


def test_skill_sync_copy_matches_source():
    """.codebuddy/skills/pan/SKILL.md 是主源的同步副本（gitignored，按需存在）。

    若当前 checkout 中存在副本，则必须与主源 docs/skills/pan/SKILL.md 一致；
    不存在（如干净 clone）时跳过。
    """
    src = REPO_ROOT / "docs/skills/pan/SKILL.md"
    copy = REPO_ROOT / ".codebuddy/skills/pan/SKILL.md"
    if not copy.is_file():
        import pytest

        pytest.skip("同步副本不存在（gitignored，仅本地 checkout 维护）")
    assert copy.read_text(encoding="utf-8") == src.read_text(encoding="utf-8"), (
        ".codebuddy/skills/pan/SKILL.md 与主源不一致：请先改主源，再复制到副本"
    )
