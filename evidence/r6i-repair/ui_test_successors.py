"""Index retired wire assertions separately from native behavior successors."""
import json
from pathlib import Path
import re

OUT = Path(__file__).resolve().parent
native = "packages/web/src/views/AutomationWorkspace.test.tsx::"
mapping = [
    ("creates a disabled serial draft", "preserves typed literals, stable step IDs, input/Event/output mappings and policies in the canonical Job", "draft/lifecycle 分离；OR Events 在 native subscription 后继覆盖"),
    ("requires an explicit Action/parameter review", "requires explicit full definition and Action parameter review before canonical enable", "原 401/换 token 独立断言继续保留"),
    ("preserves a conflicting draft", "retains invalid local JSON across explicit canonical conflict comparison/rebase and can load the server", "旧 Trigger 直接 Action 改为 native Job ref"),
    ("does not let a delayed save", "blocks duplicate submits and departure while the canonical definition receipt is pending", "busy 期间禁止换编辑对象；三个子页保持 mounted"),
    ("preserves Event field parameters", "keeps OR events, nested OR/NOT conditions and Event arguments while editing a canonical subscription", "native Trigger 不持有执行链，绑定/condition 原型不丢"),
    ("guards dirty New/Edit/Close", "preserves simultaneous Job and source drafts behind one navigation guard", "关闭丢弃确认另由 CanonicalJobsView 原 conflict/close 节点加强验证"),
    ("guards accepted switching", "blocks duplicate submits and departure while the canonical definition receipt is pending", "不自动重发，原 key 查询节点保留"),
    ("posts the displayed surviving literal", "retains invalid local JSON across explicit canonical conflict comparison/rebase and can load the server", "类型/literal 原样 roundtrip 由 typed Job 后继覆盖；forward/self 引用服务端 FC03 严格拒绝"),
    ("loading a conflicting server", "retains invalid local JSON across explicit canonical conflict comparison/rebase and can load the server", "显式 load 与 rebase 分开，无效 JSON 不被悄悄解析丢弃"),
    ("navigates exactly three accessible tabs", "navigates exactly three tabs and follows canonical Job/Trigger/source references", "canonical ID/ref 跳转；不使用旧 source list"),
    ("adds, reorders and removes steps", "preserves typed literals, stable step IDs, input/Event/output mappings and policies in the canonical Job", "以完整 native steps JSON 编辑稳定 stepId；旧按钮布局不继续发送 retired compose。FC03 forward/self 拒绝实跑，未声称仍有旧 Add/Move 按钮"),
    ("keeps literal JSON typed", "preserves typed literals, stable step IDs, input/Event/output mappings and policies in the canonical Job", "native bindings 五种形式由 FC03 真 schema/引用校验覆盖"),
    ("selects an existing Scheduler", "selects shared Creator IDs and submits the reviewed source revision with a canonical Trigger", "D2 targetJobId；选择来源建立订阅，不把来源配置重新嵌入 Job"),
    ("creates a new Scheduler through one recoverable", "creates a separate Creator with server-supplied provider metadata and explicit enable", "独立来源 create + 明确 enable；未知回执先查原 key，不照搬旧 compose 的自动 retry"),
    ("shows source impact", "reviews shared source impact and preserves a conflicting local draft", "真实 HTTP/browser 另验 2/3 共享引用、CAS 冲突"),
    ("disables a source without", "disables only the source through lifecycle CAS and blocks deleting a shared source", "browser 实验启用/停用不变更 Job，先 detach 再 retire"),
    ("generates stable random IDs", "keeps stable random request IDs without randomUUID on remote HTTP contexts", "唯一性与完整 step_32hex 断言均保留"),
    ("keeps existing timing controls", "round trips every rule, fraction, timezone, DST, anchor, limit and provider identity", "时间职责归 Creator；旧 timing/chain 记录不自动转换或执行，历史读与显式 adoption 后继另验"),
    ("preserves existing chain literals", "keeps OR events, nested OR/NOT conditions and Event arguments while editing a canonical subscription", "chain 归 Job，Trigger 引用同 Job；完整链 typed roundtrip 后继另验"),
    ("explicitly rebases a source conflict", "explicitly rebases changed references and requires impact review again", "source + referenceDigest 明确双重审阅"),
    ("uses one navigation guard", "preserves simultaneous Job and source drafts behind one navigation guard", "保留 dirty、busy、cancel 导航语义"),
]
rows = []
for label in ("view", "workspace"):
    for name in re.findall(r"^it\('([^']+)'", (OUT / ("prior-automation-" + label + "-tests.txt")).read_text(encoding="utf-8"), re.M):
        found = next((value for value in mapping if name.startswith(value[0])), None)
        rows.append({"priorFile": "Automation" + ("View" if label == "view" else "Workspace") + ".test.tsx",
            "priorNode": name, "disposition": "native-successor" if found else "retained-exact-node",
            "successor": native + found[1] if found else "packages/web/src/views/AutomationView.test.tsx::" + name,
            "boundary": found[2] if found else "原独立 store/auth/Action template 断言原文保留并实跑"})
assert len(rows) == 26 and sum(r["disposition"] == "retained-exact-node" for r in rows) == 5
(OUT / "ui-test-successors.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"priorNodes": len(rows), "retainedExact": 5, "nativeSuccessors": 21}))
