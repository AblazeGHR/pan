"""Durable workspace metadata for grouping Sessions.

Workspaces are presentation-neutral named containers.  Membership is stored on
each Session as ``workspace_ids`` so a Session may be in zero, one, or many
workspaces; an empty list is the durable representation of an ungrouped
Session.  Workspace metadata is deliberately separate from the managed
Session tree and from Session display order.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime
from pathlib import Path

WORKSPACE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "workspaces"
_cache: dict[str, "Workspace"] = {}
_loaded = False


def _path(workspace_id: str) -> Path:
    return WORKSPACE_DIR / f"{workspace_id}.json"


def _new_id() -> str:
    return "ws_" + secrets.token_hex(8)


class Workspace:
    def __init__(self, id: str, name: str, order: int | None = None,
                 created_at: str = "", updated_at: str = ""):
        self.id = id
        self.name = name
        try:
            self.order = int(order) if order is not None else None
        except (TypeError, ValueError):
            self.order = None
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or self.created_at

    @classmethod
    def from_dict(cls, data: dict) -> "Workspace":
        return cls(id=str(data["id"]), name=str(data.get("name") or ""),
                   order=data.get("order"), created_at=data.get("created_at", ""),
                   updated_at=data.get("updated_at", ""))

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "order": self.order,
                "created_at": self.created_at, "updated_at": self.updated_at}


def _save(workspace: Workspace) -> None:
    WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
    workspace.updated_at = datetime.now().isoformat()
    path = _path(workspace.id)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(workspace.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    _cache[workspace.id] = workspace


def list_all() -> list[Workspace]:
    global _loaded
    if not _loaded:
        if WORKSPACE_DIR.exists():
            for path in sorted(WORKSPACE_DIR.glob("*.json")):
                try:
                    workspace = Workspace.from_dict(json.loads(path.read_text(encoding="utf-8")))
                    _cache[workspace.id] = workspace
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                    continue
        _loaded = True
    return sorted(_cache.values(), key=lambda w: (
        w.order is None, w.order if w.order is not None else 0, w.created_at, w.id))


def get(workspace_id: str) -> Workspace | None:
    list_all()
    return _cache.get(workspace_id)


def create(name: str) -> Workspace:
    workspace = Workspace(_new_id(), name)
    _save(workspace)
    return workspace


def update(workspace: Workspace, *, name: str | None = None) -> Workspace:
    if name is not None:
        workspace.name = name
    _save(workspace)
    return workspace


def delete(workspace_id: str) -> bool:
    workspace = get(workspace_id)
    if workspace is None:
        return False
    path = _path(workspace_id)
    if path.exists():
        path.unlink()
    _cache.pop(workspace_id, None)
    return True


def apply_order(ordered_ids: list[str]) -> str | None:
    if len(set(ordered_ids)) != len(ordered_ids):
        return "workspace ids contain duplicates"
    current = list_all()
    by_id = {w.id: w for w in current}
    unknown = [wid for wid in ordered_ids if wid not in by_id]
    if unknown:
        return f"Unknown workspace id(s): {', '.join(unknown)}"
    listed = [by_id[wid] for wid in ordered_ids]
    rest = [w for w in current if w.id not in set(ordered_ids)]
    for index, workspace in enumerate(listed + rest):
        if workspace.order != index:
            workspace.order = index
            _save(workspace)
    return None


def clear_cache() -> None:
    global _loaded
    _cache.clear()
    _loaded = False
