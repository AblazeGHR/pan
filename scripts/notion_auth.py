#!/usr/bin/env python
"""Notion 托管 MCP 一键（重）授权。

## 为什么不能只把授权 URL 给用户

OAuth 授权 URL 里的 ``state`` 与 PKCE ``code_verifier`` **只存在于生成它的那个
进程的内存里**，回调也必须打到那个进程正在监听的 loopback 端口。所以无法
"生成 URL → 打印给用户 → 进程退出"，必须一气呵成：

    起承载进程 → 取授权 URL → 打开浏览器 → 等回调换 token → 关承载进程

人工只需在浏览器里点一次「允许」。

## 两条通道

- **cbc**：起临时 ``cbc --serve`` 实例（随机空闲端口、仅 loopback、``--auth none``）
  托管 ``/mcp/oauth/callback``。承载服务**自带**「仅含 notion」的临时项目级
  ``.mcp.json``（``cwd`` 指向临时目录 + ``--settings enableAllProjectMcpServers``
  绕过项目级批准门），因此完全不依赖用户全局配置；token 仍落真实的
  ``~/.codebuddy/.credentials.json``。
- **codex**：委托原生 ``codex mcp login``，它自己开浏览器并监听回调；
  token 落 ``~/.codex/secrets/mcp_oauth.age``（age 加密，本脚本不解析）。

## 复用约束（改动前必读）

cbc 的凭证索引键是 ``name|sha256(JSON({type,url,headers}))[:16]``。
``private-manifest.json`` 里托管条目必须**同名(notion)/同 type(http)/同 url/不带
headers**，否则 hash 不匹配、复用失败（详见 notion_auth 相关实验记录）。

用法::

    python scripts/notion_auth.py            # 授权 cbc 通道（凭证有效时自动跳过）
    python scripts/notion_auth.py --force    # 凭证仍有效也强制重授权
    python scripts/notion_auth.py --codex    # 授权 codex 通道
    python scripts/notion_auth.py --all      # 两条都做
    python scripts/notion_auth.py --status   # 只查状态，不发起授权
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

SERVER_NAME = "notion"
SERVER_URL = "https://mcp.notion.com/mcp"
SERVER_TYPE = "http"

CBC_CRED_PATH = Path.home() / ".codebuddy" / ".credentials.json"
CODEX_SECRET_PATH = Path.home() / ".codex" / "secrets" / "mcp_oauth.age"

# cbc HTTP 面板要求的安全头（缺任一即报 Missing required header: x-codebuddy-request）
_PANEL_HEADERS = {
    "Content-Type": "application/json",
    "x-codebuddy-request": "1",
    "x-codebuddy-web-ui": "1",
}


# ------------------------------------------------------------------ #
#  cbc 凭证读写
# ------------------------------------------------------------------ #

def cbc_server_key(name: str = SERVER_NAME, type_: str = SERVER_TYPE,
                   url: str = SERVER_URL, headers: dict | None = None) -> str:
    """复刻 cbc ``McpOAuthStore.generateServerKey``。

    等价于 JS ``name + "|" + sha256(JSON.stringify({type, url, Headers})).hex()[:16]``。
    ``JSON.stringify`` 省略值为 undefined 的键、且不插空格，故这里用
    ``separators=(",", ":")`` 并以 type/url/headers 的顺序构造。
    """
    payload: dict = {"type": type_, "url": url}
    if headers:
        payload["headers"] = headers
    digest = hashlib.sha256(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:16]
    return f"{name}|{digest}"


def cbc_read_credentials() -> dict | None:
    """读取本机 cbc 中该 server 的 OAuth 凭证记录，无则 None。"""
    if not CBC_CRED_PATH.is_file():
        return None
    try:
        data = json.loads(CBC_CRED_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (data.get("mcpOAuth") or {}).get(cbc_server_key())


# ------------------------------------------------------------------ #
#  CLI 入口解析（对齐 Pan adapter：绕开 Windows npm .CMD shim）
# ------------------------------------------------------------------ #

def _resolve_cli_argv(name: str, js_relparts: list[tuple[str, ...]]) -> list[str]:
    """解析 npm 安装的 CLI 为 ``node <entry.js>``，避开 .CMD 经 cmd.exe 的参数损坏。

    Pan 的 adapter 用同样的策略（见 packages/core/adapters/*/adapter.py）。
    找不到 shim 时退回裸命令名（由 PATH 解析）。
    """
    which = shutil.which(name)
    if not which:
        return [name]
    if not which.lower().endswith((".cmd", ".bat")):
        return [which]
    shim_dir = os.path.dirname(os.path.abspath(which))
    for parts in js_relparts:
        candidate = os.path.join(shim_dir, *parts)
        if os.path.isfile(candidate):
            node = os.path.join(shim_dir, "node.exe")
            if not os.path.exists(node):
                node = shutil.which("node") or "node"
            return [node, candidate]
    return [which]


def _cbc_argv() -> list[str]:
    return _resolve_cli_argv("cbc", [
        ("node_modules", "@tencent-ai", "codebuddy-code", "bin", "codebuddy.js"),
        ("node_modules", "@tencent-ai", "codebuddy-code", "bin", "codebuddy"),
        ("node_modules", "@tencent-ai", "codebuddy", "bin", "codebuddy.js"),
    ])


def _codex_argv() -> list[str]:
    return _resolve_cli_argv("codex", [
        ("node_modules", "@openai", "codex", "bin", "codex.js"),
    ])


# ------------------------------------------------------------------ #
#  HTTP 小工具
# ------------------------------------------------------------------ #

def _http_json(url: str, payload: dict | None, timeout: float = 15.0) -> dict:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=body, headers=_PANEL_HEADERS,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_http_ready(port: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=3):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


# ------------------------------------------------------------------ #
#  cbc 通道
# ------------------------------------------------------------------ #

def _cbc_fetch_authorization_url(port: int, attempts: int = 20) -> str:
    """取授权 URL。

    serve 进程启动后会异步连接 MCP server，撞 401 才把授权 URL 写进内存缓存，
    因此刚起服务时可能暂时取不到 —— 轮询重试直到出现。
    """
    endpoint = f"http://127.0.0.1:{port}/internal/mcp/oauth/authorize"
    last = ""
    for _ in range(attempts):
        try:
            data = _http_json(endpoint, {"name": SERVER_NAME})
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
            last = str(exc)
            time.sleep(1.5)
            continue
        if data.get("authorizationUrl"):
            return str(data["authorizationUrl"])
        last = str(data.get("error") or data)
        time.sleep(1.5)
    raise RuntimeError(f"未能取得授权 URL（最后响应：{last}）")


def _clear_cbc_tokens() -> None:
    """清掉 access/refresh token（保留 clientId）。

    必须连 refresh token 一起清：只要 refresh token 还在，cbc 会静默刷新而不是
    发起授权，也就拿不到授权 URL。
    """
    data = json.loads(CBC_CRED_PATH.read_text(encoding="utf-8"))
    entry = (data.get("mcpOAuth") or {}).get(cbc_server_key())
    if not entry:
        return
    entry["accessToken"] = ""
    entry["refreshToken"] = ""
    entry["expiresAt"] = 0
    CBC_CRED_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                             encoding="utf-8")


# cbc 的项目级 MCP 配置文件名（见 cbc dist 中 "new MCP servers found in .mcp.json"）。
_PROJECT_MCP_FILENAME = ".mcp.json"
# 项目级 server 默认要批准（pendingApproval）；不批准就不会发起连接，也就拿不到
# 授权 URL（授权 URL 只在连接撞 401 时才生成）。故用 settings 绕过批准门。
_APPROVE_ALL_PROJECT_MCP = '{"enableAllProjectMcpServers":true}'


def _build_serve_plan(port: int) -> tuple[list[str], Path]:
    """构造临时承载服务的 (argv, cwd)，并把「仅含 notion」的临时配置写进 cwd。

    承载服务必须**自带** notion 配置。早期版本只起裸 `cbc --serve`，依赖用户全局
    `~/.codebuddy/mcp.json` 里的 notion 条目；全局条目一旦被清掉（为避免泄漏进
    每个 Pan worker），承载服务就不含 notion，`/internal/mcp/oauth/authorize`
    会一直返回 "No authorization URL available"，首次/--force 授权直接失效。

    做法：临时目录 + 项目级 `.mcp.json`（只写 notion）+ `cwd` 指向该目录，
    使承载服务完全独立于全局配置。凭证仍走真实 `~/.codebuddy/.credentials.json`
    （不改 HOME），授权结果照常落盘、可被后续 headless worker 复用。
    """
    tmpdir = Path(tempfile.mkdtemp(prefix="notion-auth-"))
    descriptor = {"type": SERVER_TYPE, "url": SERVER_URL}
    (tmpdir / _PROJECT_MCP_FILENAME).write_text(
        json.dumps({"mcpServers": {SERVER_NAME: descriptor}}, ensure_ascii=False),
        encoding="utf-8",
    )
    argv = _cbc_argv() + [
        "--serve", "--port", str(port), "--host", "127.0.0.1", "--auth", "none",
        "--settings", _APPROVE_ALL_PROJECT_MCP,
    ]
    return argv, tmpdir


def authorize_cbc(timeout: float, open_browser: bool, force: bool) -> int:
    cred = cbc_read_credentials()
    if cred and cred.get("accessToken") and _expires_at(cred) > time.time() + 60 and not force:
        print(f"当前凭证仍然有效（到期 {_fmt_expiry(cred.get('expiresAt'))}），无需重新授权。")
        print("若工具仍报 401 / unauthorized，说明服务端已撤销，请加 --force 强制重新授权。")
        return 0

    if force and cred:
        _clear_cbc_tokens()
        print("[cbc] --force：已清除本地 token，接下来会走完整授权流程。")

    port = _free_port()
    argv, tmpdir = _build_serve_plan(port)
    log_path = tmpdir / "cbc-serve.log"
    print(f"[cbc] 启动临时承载服务 127.0.0.1:{port}"
          f"（仅 loopback，自带仅含 {SERVER_NAME} 的临时配置，完成后自动清理）")
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(argv, stdout=log_file, stderr=subprocess.STDOUT,
                            cwd=str(tmpdir))
    try:
        if not _wait_http_ready(port, timeout=60):
            _dump_log_tail(log_path)
            print("[cbc] 承载服务启动失败。", file=sys.stderr)
            return 1

        try:
            url = _cbc_fetch_authorization_url(port)
        except RuntimeError as exc:
            print(f"[cbc] 未能取得授权 URL：{exc}", file=sys.stderr)
            print(f"[cbc] 本次临时配置：{(tmpdir / _PROJECT_MCP_FILENAME).read_text(encoding='utf-8')}",
                  file=sys.stderr)
            _dump_log_tail(log_path)
            return 1

        print("\n请在浏览器完成授权（只需点一次「允许」）：")
        print(f"  {url}\n")
        if open_browser:
            webbrowser.open(url)

        print(f"[cbc] 等待授权回调…（最多 {int(timeout)} 秒，Ctrl+C 可中断）")
        if not _wait_cbc_credentials(timeout):
            print("[cbc] 超时：未检测到新凭证。可重新运行本命令。", file=sys.stderr)
            _dump_log_tail(log_path)
            return 1

        cred = cbc_read_credentials() or {}
        print(f"[cbc] 授权成功，凭证已写入 {CBC_CRED_PATH}")
        print(f"[cbc] 到期时间：{_fmt_expiry(cred.get('expiresAt'))}"
              f"（到期前 cbc 会自动用 refresh token 续期）")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_file.close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _dump_log_tail(log_path: Path, lines: int = 8) -> None:
    """把承载服务日志尾部打到 stderr——临时目录随后会被清掉，不留文件。"""
    try:
        content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    tail = [ln for ln in content if ln.strip()][-lines:]
    if tail:
        print("[cbc] 承载服务日志尾部：", file=sys.stderr)
        for ln in tail:
            print(f"    {ln}", file=sys.stderr)


def _wait_cbc_credentials(timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        cred = cbc_read_credentials()
        if cred and cred.get("accessToken") and _expires_at(cred) > time.time() + 60:
            return True
        time.sleep(1.5)
    return False


# ------------------------------------------------------------------ #
#  codex 通道
# ------------------------------------------------------------------ #

def authorize_codex(open_browser: bool) -> int:
    argv = _codex_argv() + [
        "mcp", "login", SERVER_NAME,
        "-c", f'mcp_servers.{SERVER_NAME}.url="{SERVER_URL}"',
    ]
    if not open_browser:
        argv.append("--no-browser")
    before = CODEX_SECRET_PATH.stat().st_mtime_ns if CODEX_SECRET_PATH.is_file() else None

    print("[codex] 调用 codex mcp login（它会自行打开浏览器并监听回调）")
    if not open_browser:
        print("[codex] --no-browser：需在终端粘贴回调 URL（请在前台运行本命令）")
    result = subprocess.run(argv)
    if result.returncode != 0:
        print(f"[codex] codex mcp login 退出码 {result.returncode}", file=sys.stderr)
        return 1

    after = CODEX_SECRET_PATH.stat().st_mtime_ns if CODEX_SECRET_PATH.is_file() else None
    if after == before:
        print(f"[codex] 命令成功但 {CODEX_SECRET_PATH} 未更新，请人工确认。", file=sys.stderr)
        return 1
    print(f"[codex] 授权成功，凭证已更新：{CODEX_SECRET_PATH}")
    return 0


# ------------------------------------------------------------------ #
#  状态
# ------------------------------------------------------------------ #

def _expires_at(cred: dict) -> float:
    try:
        return float(cred.get("expiresAt") or 0) / 1000.0
    except (TypeError, ValueError):
        return 0.0


def _fmt_expiry(expires_at_ms) -> str:
    try:
        ts = float(expires_at_ms or 0) / 1000.0
    except (TypeError, ValueError):
        return "未知"
    if ts <= 0:
        return "未知"
    remain = ts - time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    if remain <= 0:
        return f"{stamp}（已过期，下次使用时会自动刷新）"
    return f"{stamp}（剩余 {remain / 3600:.1f} 小时）"


def print_status() -> int:
    print("== cbc 通道 ==")
    print(f"   凭证文件：{CBC_CRED_PATH}")
    print(f"   索引键　：{cbc_server_key()}")
    cred = cbc_read_credentials()
    if not cred:
        print("   状态　　：未授权（运行本命令不带参数即可授权）")
        cbc_ok = False
    elif not cred.get("accessToken"):
        print("   状态　　：已注册客户端但无 token（需重新授权）")
        cbc_ok = False
    else:
        print(f"   状态　　：已授权，到期 {_fmt_expiry(cred.get('expiresAt'))}")
        cbc_ok = True

    print("\n== codex 通道 ==")
    print(f"   凭证文件：{CODEX_SECRET_PATH}")
    if CODEX_SECRET_PATH.is_file():
        size = CODEX_SECRET_PATH.stat().st_size
        mtime = time.strftime("%Y-%m-%d %H:%M",
                              time.localtime(CODEX_SECRET_PATH.stat().st_mtime))
        print(f"   状态　　：存在（{size} 字节，更新于 {mtime}）")
        print("   备注　　：age 加密，无法解析有效期；若工具报 401 请重新授权")
        codex_ok = True
    else:
        print("   状态　　：不存在（运行 --codex 授权）")
        codex_ok = False
    return 0 if (cbc_ok or codex_ok) else 1


# ------------------------------------------------------------------ #

def main() -> int:
    # 输出被重定向/管道捕获时 stdout 会块缓冲，导致提示与 stderr 交错乱序。
    sys.stdout.reconfigure(line_buffering=True)
    parser = argparse.ArgumentParser(
        description="Notion 托管 MCP 一键（重）授权",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("用法::")[-1],
    )
    parser.add_argument("--codex", action="store_true", help="授权 codex 通道")
    parser.add_argument("--all", action="store_true", help="cbc 与 codex 两条通道都授权")
    parser.add_argument("--status", action="store_true", help="只查状态，不发起授权")
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="等待授权回调的秒数（默认 300）")
    parser.add_argument("--no-browser", action="store_true",
                        help="不自动打开浏览器，只打印授权 URL")
    parser.add_argument("--force", action="store_true",
                        help="凭证仍有效时也强制重新授权（会先清除本地 token）")
    args = parser.parse_args()

    if args.status:
        return print_status()

    if args.all:
        rc = authorize_cbc(args.timeout, not args.no_browser, args.force)
        rc_codex = authorize_codex(not args.no_browser)
        return 0 if (rc == 0 and rc_codex == 0) else 1
    if args.codex:
        return authorize_codex(not args.no_browser)
    return authorize_cbc(args.timeout, not args.no_browser, args.force)


if __name__ == "__main__":
    sys.exit(main())
