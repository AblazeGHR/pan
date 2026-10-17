"""Provider connections owned by one task, so MCP cancel scopes close correctly."""
from __future__ import annotations

import asyncio
import json
import os
from contextlib import AsyncExitStack
from datetime import timedelta
from types import SimpleNamespace

import httpx
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.client.sse import sse_client


class McpProvider:
    def __init__(self, descriptor: dict, session):
        from packages.core.adapters.mcp import build_mcp_servers
        owner = SimpleNamespace(id=session.id, name=session.name,
                                adapter_config={"mcp_servers": [descriptor]})
        self.entry = next(iter(build_mcp_servers(owner).values()))
        self.entry["env"] = {**self.entry.get("env", {}), "PAN_AGENT_SESSION_ID": session.id,
                             "PAN_AGENT_SESSION_TITLE": session.name}
        self.queue = asyncio.Queue(maxsize=32)
        self.task = asyncio.create_task(self._run(), name=f"plugin-{session.id}-{descriptor['name']}")
        self.error = None
        self.closed = False
        self.failure = None

    async def _run(self):
        current = None
        try:
            async with AsyncExitStack() as stack:
                e = self.entry
                if e.get("url"):
                    if e.get("transport", e.get("type")) == "sse":
                        streams = await stack.enter_async_context(sse_client(e["url"], headers=e.get("headers")))
                    else:
                        client = await stack.enter_async_context(httpx.AsyncClient(
                            headers=e.get("headers"), timeout=30))
                        streams = await stack.enter_async_context(streamable_http_client(e["url"], http_client=client))
                else:
                    streams = await stack.enter_async_context(stdio_client(StdioServerParameters(
                        command=e["command"], args=e.get("args", []), cwd=e.get("cwd"),
                        env={**os.environ, **e.get("env", {})})))
                client = await stack.enter_async_context(ClientSession(
                    streams[0], streams[1], read_timeout_seconds=timedelta(seconds=30)))
                await client.initialize()
                while True:
                    op, payload, current = await self.queue.get()
                    if op == "close":
                        current.set_result(None)
                        break
                    try:
                        if op == "list":
                            tools, cursor, pages = [], None, 0
                            while True:
                                response = await client.list_tools(cursor=cursor)
                                tools.extend(response.tools)
                                cursor = response.nextCursor
                                pages += 1
                                if len(tools) > 10000 or pages > 100:
                                    raise ValueError("Provider tool directory exceeds limit")
                                if not cursor:
                                    break
                            result = [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]
                        else:
                            result = (await client.call_tool(payload["name"], payload["arguments"])).model_dump(
                                mode="json", by_alias=True, exclude_none=True)
                        if not current.done():
                            current.set_result(result)
                    except Exception as exc:
                        if not current.done():
                            current.set_exception(exc)
                    current = None
        except BaseException as exc:
            self.error = str(exc) or type(exc).__name__
            if current is not None and not current.done():
                current.set_exception(RuntimeError(self.error))
        finally:
            self.closed = True
            while not self.queue.empty():
                _, _, future = self.queue.get_nowait()
                if not future.done():
                    future.set_exception(RuntimeError(self.error or "Provider closed"))

    async def request(self, operation, payload=None):
        if self.closed:
            raise RuntimeError(self.error or "Provider closed")
        future = asyncio.get_running_loop().create_future()
        self.queue.put_nowait((operation, payload, future))
        # Cancellation of a caller does not cancel an already admitted side effect.
        future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        return await asyncio.shield(future)

    async def close(self):
        if not self.closed:
            await self.request("close")
        await self.task


class ApiProvider:
    def __init__(self, document: dict, catalog, session):
        self.document = document
        self.spec = document["provider"]
        self.catalog = catalog
        self.session = session
        self.lock = asyncio.Lock()
        self.process = None
        self.sequence = 0
        self.closed = False
        self.failure = None

    async def _rpc(self, method, params):
        if not self.process:
            from packages.core.adapters.mcp import build_mcp_servers
            ref = self.spec.get("command_ref")
            if ref not in self.catalog.servers:
                raise ValueError(f"Unknown API provider command_ref: {ref}")
            descriptor = self.catalog.servers[ref]
            owner = SimpleNamespace(id=self.session.id, name=self.session.name,
                                    adapter_config={"mcp_servers": [descriptor]})
            entry = next(iter(build_mcp_servers(owner).values()))
            self.process = await asyncio.create_subprocess_exec(
                entry["command"], *entry.get("args", []), cwd=entry.get("cwd"),
                env={**os.environ, **entry.get("env", {}), "PAN_AGENT_SESSION_ID": self.session.id},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                limit=2 * 1024 * 1024)
            initialized = await self._rpc("provider.initialize", {"protocol_version": "pan-capability/v1"})
            if not isinstance(initialized, dict) or initialized.get("protocol_version") != "pan-capability/v1":
                raise ValueError("API provider protocol version mismatch")
        self.sequence += 1
        request_id = self.sequence
        self.process.stdin.write((json.dumps({"jsonrpc": "2.0", "id": request_id,
            "method": method, "params": params}) + "\n").encode())
        await self.process.stdin.drain()
        line = await asyncio.wait_for(self.process.stdout.readline(), 30)
        if not line:
            raise RuntimeError("API provider stdout closed; execution may be unconfirmed")
        reply = json.loads(line)
        if reply.get("id") != request_id:
            raise RuntimeError("API provider response ID mismatch; execution may be unconfirmed")
        if "error" in reply:
            raise RuntimeError(f"API provider rejected request: {reply['error']}")
        if "result" not in reply:
            raise RuntimeError("API provider response has no result")
        return reply["result"]

    async def request(self, operation, payload=None):
        if self.closed:
            raise RuntimeError("Provider closed")
        if self.failure:
            raise RuntimeError("API provider connection failed; explicit reconnect required")
        if operation == "list":
            return [{"name": cap["id"], "description": cap["description"],
                     "inputSchema": cap["input_schema"],
                     **({"outputSchema": cap["output_schema"]} if cap.get("output_schema") else {})}
                    for cap in self.document["capabilities"]]
        cap = next(c for c in self.document["capabilities"] if c["id"] == payload["name"])
        execution, arguments = cap["execution"], payload["arguments"]
        async with self.lock:
            if self.spec["transport"] == "stdio_jsonrpc":
                try:
                    result = await self._rpc("capability.invoke", {
                        "protocol_version": "pan-capability/v1", "operation": execution["operation"],
                        "arguments": arguments})
                except (TimeoutError, OSError, ValueError, RuntimeError) as exc:
                    self.failure = str(exc)
                    raise
            else:
                base = self.spec["endpoint"].rstrip("/")
                path = execution["path"]
                if not path.startswith("/") or path.startswith("//") or "://" in path:
                    raise ValueError("API execution path must be a fixed relative route")
                headers = {key: os.environ[value] for key, value in self.spec.get("headers_env", {}).items()}
                async with httpx.AsyncClient(timeout=30, headers=headers, follow_redirects=False) as client:
                    response = await client.request(execution["method"], base + path,
                        **({"params": arguments} if execution["method"] == "GET" else {"json": arguments}))
                    response.raise_for_status()
                    result = response.json()
        if execution.get("result_format") == "mcp":
            return types.CallToolResult.model_validate(result).model_dump(mode="json", by_alias=True, exclude_none=True)
        return {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                **({"structuredContent": result} if isinstance(result, dict) else {}), "isError": False}

    async def close(self):
        async with self.lock:
            self.closed = True
            if self.process:
                self.process.stdin.close()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except TimeoutError:
                    self.process.kill()
                    await self.process.wait()
