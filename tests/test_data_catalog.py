"""Read-only registered storage path catalog API tests."""

import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core import data_catalog
import packages.web.server as srv


def test_catalog_projects_env_overrides_and_missing_paths_without_creating_them(
    tmp_path, monkeypatch,
):
    project = tmp_path / "checkout"
    data_root = project / "data"
    external_jobs = tmp_path / "external jobs"
    external_quota = tmp_path / "quota-cache"
    external_wechat = tmp_path / "wechat-data"
    codex_home = tmp_path / "codex-home"
    kimi_home = tmp_path / "kimi-home"
    configured_log = tmp_path / "external logs" / "pan-custom.log"
    monkeypatch.setattr(logging.getLogger(), "handlers", [
        SimpleNamespace(baseFilename=str(configured_log)),
    ])
    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", project)
    monkeypatch.setattr(data_catalog, "DATA_ROOT", data_root)
    monkeypatch.setenv("PAN_SCHEDULER_DIR", str(external_jobs))
    monkeypatch.setenv("PAN_BACKGROUND_JOBS_DIR", str(tmp_path / "lower-priority"))
    monkeypatch.setenv("PAN_CODEX_QUOTA_DIR", str(external_quota))
    monkeypatch.setenv("PAN_WECHAT_DATA_DIR", str(external_wechat))
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("KIMI_CODE_HOME", str(kimi_home))

    result = asyncio.run(srv.api_get_data_catalog())
    categories = {item["id"]: item for item in result["categories"]}

    jobs = categories["jobs-records"]["paths"][0]
    assert Path(jobs["path"]) == (external_jobs / "jobs").resolve()
    assert jobs["source"] == "environment: PAN_SCHEDULER_DIR"
    assert jobs["overridden"] is True
    assert jobs["external"] is True
    assert jobs["exists"] is False
    job_record_paths = {Path(entry["path"]) for entry in categories["jobs-records"]["paths"]}
    assert (tmp_path / "lower-priority" / "jobs").resolve() in job_record_paths
    assert (data_root / "background_jobs" / "jobs").resolve() in job_record_paths
    runs_paths = {Path(entry["path"]) for entry in categories["jobs-runs"]["paths"]}
    assert (external_jobs / "runs.jsonl").resolve() in runs_paths
    assert (tmp_path / "lower-priority" / "runs.jsonl").resolve() in runs_paths
    assert (external_jobs / "schedule_templates.json").resolve() in runs_paths
    assert (tmp_path / "lower-priority" / "schedule_templates.json").resolve() in runs_paths

    quota = categories["codex-quota-cache"]["paths"][0]
    assert Path(quota["path"]) == external_quota.resolve()
    assert quota["overridden"] is True
    assert quota["external"] is True

    wechat_history = categories["wechat-history-queues"]["paths"][0]
    assert Path(wechat_history["path"]) == (external_wechat / "wechat_history").resolve()
    assert wechat_history["source"] == "environment: PAN_WECHAT_DATA_DIR"
    wechat_auth = categories["external-provider-auth"]["paths"][3]
    assert Path(wechat_auth["path"]) == (external_wechat / "wechat" / "bot_token.json").resolve()

    codex = categories["external-provider-auth"]["paths"][0]
    kimi = categories["external-provider-auth"]["paths"][1]
    assert Path(codex["path"]) == codex_home.resolve()
    assert Path(kimi["path"]) == kimi_home.resolve()
    assert codex["external"] is True and kimi["external"] is True

    pan_log = categories["pan-logs"]["paths"][0]
    assert Path(pan_log["path"]) == configured_log.resolve()
    assert pan_log["overridden"] is True
    assert pan_log["external"] is True
    assert pan_log["exists"] is False
    assert Path(categories["pan-logs"]["paths"][1]["path"]) == configured_log.parent.resolve()
    assert categories["pan-logs"]["policyStatus"] == "data_retention_policy"

    assert not project.exists()
    assert not external_jobs.exists()
    assert not external_quota.exists()
    assert not external_wechat.exists()
    assert not codex_home.exists()
    assert not kimi_home.exists()
    assert "data/" in result["notice"]
    assert result["jobsRetention"]["status"] == "reserved"


def test_catalog_lists_registered_categories_without_creating_missing_paths(
    tmp_path, monkeypatch,
):
    project = tmp_path / "pan"
    data_root = project / "data"
    monkeypatch.setattr(data_catalog, "PROJECT_ROOT", project)
    monkeypatch.setattr(data_catalog, "DATA_ROOT", data_root)
    for key in (
        "PAN_SCHEDULER_DIR", "PAN_BACKGROUND_JOBS_DIR", "PAN_CODEX_QUOTA_DIR",
        "PAN_WECHAT_DATA_DIR", "CODEX_HOME", "KIMI_CODE_HOME",
    ):
        monkeypatch.delenv(key, raising=False)

    result = data_catalog.get_data_catalog()
    categories = {item["id"]: item for item in result["categories"]}
    expected = {
        "sessions-history", "attachments", "jobs-records", "jobs-logs", "jobs-runs",
        "workspaces", "workdirs", "qq-history", "qq-inbox", "qq-media",
        "wechat-history-queues",
        "characters-memory", "mcp-config", "kimi-isolated-home", "codex-quota-cache",
        "pan-logs", "startup-recovery", "retention-status", "config", "external-provider-auth",
    }
    assert expected <= categories.keys()
    assert categories["config"]["policyStatus"] == "not_auto_cleanable"
    assert categories["sessions-history"]["policyStatus"] == "data_retention_policy"
    assert categories["mcp-config"]["policyStatus"] == "session_lifecycle_cleanup"
    assert categories["kimi-isolated-home"]["policyStatus"] == "session_lifecycle_cleanup"
    assert categories["workdirs"]["policyStatus"] == "session_lifecycle_cleanup"
    assert categories["jobs-runs"]["policyStatus"] == "jobs_policy_shared"
    assert categories["qq-inbox"]["policyStatus"] == "not_auto_cleanable"
    assert all("path" in entry for item in result["categories"] for entry in item["paths"])
    assert not project.exists()
    assert result["notice"].startswith("仅列出代码登记的路径")


def test_data_catalog_route_accepts_no_path_or_query_parameters():
    route = next(
        route for route in srv.app.routes
        if getattr(route, "path", None) == "/api/data/catalog"
    )
    assert route.methods == {"GET"}
    assert route.dependant.path_params == []
    assert route.dependant.query_params == []
