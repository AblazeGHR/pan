"""Schedule 模板 —— 命名预设（PLAN §4：模板是输入复用，非运行时实体）。

存储：``<registry_root>/schedule_templates.json``（原子写，同 registry 锁纪律）。
内置默认模板开箱即用；用户模板可增删。GUI 快捷创建引用 ``id`` 展开成
schedule entry 字段。

模板 entry 形状（与 job 记录里的 schedule entry 同构，减去运行时字段）::
    { id, name, kind: once|interval|cron, at?/intervalSec?/cron?,
      timezone?, misfirePolicy }
"""

from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from packages.core import background_jobs

TEMPLATE_ID_PREFIX = "tpl_"

BUILTIN_TEMPLATES: list[dict] = [
    {"id": "tpl_weekday_morning", "name": "工作日早 9 点",
     "kind": "cron", "cron": "0 9 * * 1-5", "misfirePolicy": "fire_now",
     "builtin": True},
    {"id": "tpl_hourly", "name": "每小时",
     "kind": "interval", "intervalSec": 3600, "misfirePolicy": "fire_now",
     "builtin": True},
    {"id": "tpl_daily_nine", "name": "每天 21 点",
     "kind": "cron", "cron": "0 21 * * *", "misfirePolicy": "fire_now",
     "builtin": True},
    {"id": "tpl_weekly_mon", "name": "每周一早 9 点",
     "kind": "cron", "cron": "0 9 * * 1", "misfirePolicy": "fire_now",
     "builtin": True},
]


def _templates_path(registry_root=None) -> Path:
    return background_jobs._root(registry_root) / "schedule_templates.json"


def _load_custom(registry_root=None) -> list[dict]:
    try:
        data = json.loads(_templates_path(registry_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    items = data.get("templates")
    return [t for t in items if isinstance(t, dict) and t.get("id")] \
        if isinstance(items, list) else []


def list_templates(registry_root=None) -> list[dict]:
    """内置模板在前、自定义模板在后。"""
    return BUILTIN_TEMPLATES + _load_custom(registry_root)


def create_template(payload: dict, registry_root=None) -> dict:
    """新增自定义模板；name 必填，kind/字段校验同 schedule entry。"""
    if not isinstance(payload, dict):
        raise ValueError("template must be an object")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ValueError("template name is required")
    kind = str(payload.get("kind") or "").strip().lower()
    if kind not in ("once", "interval", "cron"):
        raise ValueError("template.kind must be once / interval / cron")
    template: dict = {
        "id": payload.get("id") or TEMPLATE_ID_PREFIX + secrets.token_hex(4),
        "name": name,
        "kind": kind,
        "misfirePolicy": str(payload.get("misfirePolicy") or "fire_now"),
        "builtin": False,
    }
    if kind == "once":
        if not payload.get("at"):
            raise ValueError("template.kind=once requires at")
        template["at"] = payload["at"]
    elif kind == "interval":
        try:
            sec = int(payload.get("intervalSec"))
        except (TypeError, ValueError):
            raise ValueError("template.intervalSec must be a positive integer") from None
        if sec <= 0:
            raise ValueError("template.intervalSec must be a positive integer")
        template["intervalSec"] = sec
    else:
        expr = str(payload.get("cron") or "").strip()
        if not expr:
            raise ValueError("template.kind=cron requires cron expression")
        from packages.jobs import cron as _job_cron
        _job_cron.parse_cron(expr)
        template["cron"] = expr
    if payload.get("timezone"):
        template["timezone"] = str(payload["timezone"])

    custom = _load_custom(registry_root)
    custom = [t for t in custom if t.get("id") != template["id"]]
    custom.append(template)
    _save_custom(custom, registry_root)
    return template


def delete_template(template_id: str, registry_root=None) -> bool:
    """删除自定义模板；内置模板不可删。"""
    custom = _load_custom(registry_root)
    remaining = [t for t in custom if t.get("id") != template_id]
    if len(remaining) == len(custom):
        return False
    _save_custom(remaining, registry_root)
    return True


def _save_custom(templates: list[dict], registry_root=None) -> None:
    path = _templates_path(registry_root)
    payload = {"templates": templates, "updatedAt": time.time()}
    tmp = path.with_suffix(path.suffix + f".{secrets.token_hex(4)}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    for attempt in range(20):
        try:
            import os
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise
            time.sleep(0.01 * (attempt + 1))
