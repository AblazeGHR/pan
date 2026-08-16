"""stream-json 模式 + --mcp-config 复测脚本（验证踩坑记录 #5 在当前 cbc 版本是否仍成立）。

用法:
    E:/software/miniforge/python.exe streamjson_probe.py [--mcp-config <path>]
    [--strict]  # 加 --strict-mcp-config

流程: 启动 cbc -p --input-format stream-json --output-format stream-json
      写一条 user 消息到 stdin，观察 init 事件的 mcp_servers 字段。
"""
import argparse
import asyncio
import json

CBC = ["node", "D:/node_npm/node_global/node_modules/@tencent-ai/codebuddy-code/bin/codebuddy"]


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mcp-config", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--model", default="hy3")
    args = parser.parse_args()

    cmd = CBC + ["-p", "--output-format", "stream-json", "--input-format", "stream-json",
                 "-y", "--permission-mode", "bypassPermissions", "--model", args.model]
    if args.mcp_config:
        cmd += ["--mcp-config", args.mcp_config]
        if args.strict:
            cmd += ["--strict-mcp-config"]
    print("CMD:", " ".join(cmd), flush=True)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd="D:/project/pan-stream-mcp",
    )

    # 写一条用户消息（触发模型执行，init 事件会在此前后出现）
    user_msg = {"type": "user",
                "message": {"role": "user",
                            "content": [{"type": "text", "text": "请直接调用 MCP 工具 mcp__pan__session_list 获取会话列表，然后原样告诉我返回结果（例如返回了多少个会话）。如果工具不存在或调用失败，请明确说明。不要编造。"}]}}
    proc.stdin.write(json.dumps(user_msg).encode("utf-8") + b"\n")
    await proc.stdin.drain()

    # 读取所有输出，关注 init 事件的 mcp_servers 字段
    found_init = False
    mcp_fields = []
    buf = b""
    while True:
        try:
            chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=25.0)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            s = line.decode("utf-8", errors="replace").strip()
            if not s:
                continue
            try:
                ev = json.loads(s)
            except json.JSONDecodeError:
                print("[raw]", s[:300], flush=True)
                continue
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                found_init = True
                mcp = ev.get("mcp_servers")
                mcp_fields.append(mcp)
                print("== init 事件: mcp_servers =", json.dumps(mcp, ensure_ascii=False), flush=True)
            elif ev.get("type") == "result":
                print("== result:", json.dumps(ev.get("result"), ensure_ascii=False)[:500], flush=True)
            else:
                # 打印 assistant 事件文本片段
                if ev.get("type") == "assistant":
                    blocks = ev.get("message", {}).get("content", [])
                    txt = "".join(b.get("text", b.get("thinking", "")) for b in blocks if isinstance(b, dict))
                    if txt:
                        print("[assistant]", txt[:400], flush=True)
    print("found_init:", found_init, " mcp_servers 值:", mcp_fields, flush=True)
    proc.kill()
    await proc.wait()


if __name__ == "__main__":
    asyncio.run(main())
