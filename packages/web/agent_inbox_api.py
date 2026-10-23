"""Stable Inbox HTTP contract. Uses existing local API and MCP permission boundary."""
from fastapi import HTTPException
from packages.core.agent_inbox import InboxError, Router
from packages.core.inbox_service import get_inbox
from packages.core import session as sess


def register(app):
    @app.get("/api/agent-inbox")
    async def list_lockers():
        return {"schemaVersion": 1, "lockers": get_inbox().list()}

    @app.post("/api/agent-inbox/{operation}")
    async def operate(operation: str, data: dict):
        box = get_inbox()
        lid = data.get("lockerId", "")
        try:
            if operation == "list":
                return {"schemaVersion": 1, "lockers": box.list()}
            if operation == "read":
                return {"messages": box.read(lid, limit=data.get("limit", 100), include_consumed=data.get("includeConsumed", False))}
            if operation == "consume":
                return box.consume(lid, data.get("messageIds", []))
            if operation in {"subscribe", "mode"}:
                sid = data.get("sessionId")
                if not isinstance(sid, str) or not sess.get(sid, load_history=False):
                    raise InboxError("session not found")
                return {"subscription": box.subscribe(lid, sid, data.get("mode", "silent"))}
            if operation == "subscriptions":
                return {"subscriptions": box.subscriptions(lid)}
            if operation in {"backlog", "recover"}:
                return await box.forward(lid, backlog=operation == "backlog")
            if operation == "delete":
                return box.delete(lid)
            raise InboxError("unsupported operation")
        except (InboxError, KeyError, TypeError) as exc:
            raise HTTPException(409, detail={"code": "inbox_rejected", "message": str(exc)}) from exc

    @app.post("/api/plugins/{plugin_id}/route")
    async def plugin_route(plugin_id: str, data: dict):
        from packages.web.server import _plugin_catalog
        if plugin_id not in _plugin_catalog().plugins:
            raise HTTPException(409, detail="source plugin unavailable")
        box = get_inbox()
        try:
            source = {**data.get("source", {}), "pluginId": plugin_id}
            target = dict(data.get("target", {}))
            if target.get("kind") == "agent_inbox":
                target["lockerId"] = box.locker(source)["id"]
            elif target.get("kind") == "session_queue":
                target["source"] = source
            return await Router(box).route(target, data.get("sourceMessageId"), data.get("content"))
        except (InboxError, KeyError, TypeError) as exc:
            raise HTTPException(409, detail={"code": "route_rejected", "message": str(exc)}) from exc
