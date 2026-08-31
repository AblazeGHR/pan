"""Regression tests for the SMA session-template orchestration contract."""

import json
from pathlib import Path


MANIFEST = Path(__file__).parents[1] / "manifest.json"


def _sma_prompts() -> dict[str, str]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    templates = {
        template["name"]: template
        for template in data["session_templates"]
        if template["name"].startswith("SMA")
    }
    assert set(templates) == {"SMA(cbc)", "SMA(NoAdapter)"}
    return {name: template["system_prompt"] for name, template in templates.items()}


def test_sma_templates_share_the_async_orchestration_contract():
    prompts = _sma_prompts()
    assert prompts["SMA(cbc)"] == prompts["SMA(NoAdapter)"]
    prompt = prompts["SMA(cbc)"]

    required_phrases = (
        "主要职责不是持续监督 worker",
        "立即回到 idle/等待用户或完成报告",
        "不要为了看进度主动保持阻塞",
        "report_subscribe",
        "queue_pending",
        "worker 完成或报错时，报告会唤醒你",
        "不要持续轮询 `session_get`",
        "用户明确要求实时监控",
        "预先约定的节点协议暂停等待",
        "节点验收条件",
        "到达节点后停止并等待 SMA 指示",
        "worker 应自主完成任务",
        "隔离 worktree",
        "trust-but-verify",
        "push、发布或其他外部交付动作必须先询问用户",
        "不要删除用户的 session",
        "不要擅自操作运行中的服务",
        "8768/8767",
    )
    for phrase in required_phrases:
        assert phrase in prompt, f"SMA prompt missing required phrase: {phrase}"


def test_sma_prompt_does_not_make_polling_the_default_path():
    prompt = _sma_prompts()["SMA(cbc)"]
    assert "或轮询 session_get 查 lastResult.status" not in prompt
    assert "盯进度" not in prompt
