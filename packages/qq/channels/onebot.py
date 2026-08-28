"""OneBot 11 通道基类：NapCat 与 LLOneBot 共用的正向 WS 接入实现。

两者都是 OneBot 11 协议网关，NoneBot 的 OneBot v11 适配器以正向 WS 客户端连接
到它们的 WS 服务端（ws://127.0.0.1:<port>）。因此「收发 / 取联系人」的 wire 层
完全一致，差异仅在：通道标识（name）、连接地址（ws_urls）、token、以及启动预检
文案。NapCat / LLOneBot 各用薄子类提供这些差异，业务层零改动即可切换。

本基类负责：
    - 绑定 NoneBot driver：注册 on_bot_connect / on_bot_disconnect / on_message
    - 维护当前活动的 Bot（self._bot，由 on_bot_connect 注入；测试可经 bot_fallback）
    - send / recent_contacts：经 bot.call_api 调 OneBot API（合并好友/群列表）
    - 启动时 TCP 预检：网关不可达则打降级日志（适配器仍每 3s 重试，进程不退出）
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import httpx
from nonebot import get_driver, on_message
from nonebot.adapters.onebot.v11 import Bot, MessageEvent

from .base import (
    ChannelConfig,
    ChannelNotConnected,
    QQChannel,
    QQMessage,
)

# chatType：1=私聊/好友，2=群聊；其它（临时会话/陌生人/系统/频道，peerUin 常为 "0"）
# 不可订阅，合并时忽略。
_CHAT_FRIEND = 1
_CHAT_GROUP = 2

# 渲染富媒体描述时跳过的段：text 已由 get_plaintext 提取，at 已有专门 @ 过滤
_SKIP_SEG_TYPES = {"text", "at"}

# ── 入站富媒体自动下载 ──
#
# 收到带 url 的 image/file/mface/qface 段时自动下载到 data/qq_media/<scope>/<scope_id>/
# （scope=user/group，scope_id=QQ号/群号），描述里直接给本地绝对路径，编排
# worker 用 Read 即可读取，无需自己 curl。下载失败静默降级为原 url 描述，
# 绝不阻塞/崩溃消息处理。落盘用模块级目录而非 session workdir：消息先到通道
# 再 dispatch，通道层不知道目标 session。

_MEDIA_ROOT = Path(__file__).resolve().parents[3] / "data" / "qq_media"
_DOWNLOAD_TIMEOUT = 10.0

# 合法扩展名：点开头 + 1~6 位字母数字（防 query 残留/路径注入）
_EXT_RE = re.compile(r"^\.[A-Za-z0-9]{1,6}$")

# content-type → 扩展名（url 无扩展名时的兜底；QQ 多媒体 url 常不带扩展名）
_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "application/pdf": ".pdf",
    "text/plain": ".txt",
}


def _media_root() -> Path:
    """媒体下载根目录（测试可 monkeypatch 重定向到 tmp_path）。"""
    return _MEDIA_ROOT


def _guess_ext(source: str) -> str:
    """从 url / 文件名推断扩展名；推断不出返回 .bin。"""
    try:
        path = urlparse(source).path
    except ValueError:
        return ".bin"
    ext = os.path.splitext(path)[1].lower()
    return ext if _EXT_RE.match(ext) else ".bin"


def _safe_filename(source: str, prefix: str, seq: int, fallback_name: str = "") -> str:
    """生成安全文件名：prefix_时间戳_序号.扩展名（如 img_20260828_185301_001.jpg）。

    扩展名优先取 source（url）路径段的；取不出且 fallback_name（如 file 段的
    原始文件名）带合法扩展名则用之；否则 .bin。
    """
    ext = _guess_ext(source)
    if ext == ".bin" and fallback_name:
        ext = _guess_ext(fallback_name)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{ts}_{seq:03d}{ext}"


async def _download(
    url: str, dest_dir: Path, filename: str, timeout: float = _DOWNLOAD_TIMEOUT
) -> str | None:
    """下载 url 到 dest_dir/filename。成功返回绝对路径字符串，失败静默返回 None。

    dest 扩展名为 .bin 时按响应 content-type 修正（QQ 多媒体 url 常无扩展名）。
    先写 .part 临时文件，成功后原子改名，避免留下半个文件被误读。
    """
    dest = dest_dir / filename
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                if dest.suffix == ".bin":
                    ct = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                    ext = _CONTENT_TYPE_EXT.get(ct)
                    if ext:
                        dest = dest.with_suffix(ext)
                        tmp = dest.with_suffix(dest.suffix + ".part")
                with open(tmp, "wb") as f:
                    async for chunk in resp.aiter_bytes():
                        f.write(chunk)
        tmp.rename(dest)
        return str(dest.resolve())
    except Exception as e:  # noqa: BLE001  下载失败不阻塞消息处理
        print(f"[QQ] media download failed: {type(e).__name__}: {e} url={url[:120]}")
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return None


async def _download_segments_media(
    segments, scope: str, scope_id: str
) -> dict[int, str]:
    """对带 url 的 image/file/mface/qface 段并发下载，返回 {段索引: 本地路径}（仅成功项）。

    逐段编号（同一消息内 001/002...），落盘到 _media_root()/<scope>/<scope_id>/。
    mface/qface 无 url 时跳过下载（描述走 summary/id 文本）。
    """
    jobs: list[tuple[int, object]] = []  # (段索引, 协程)
    seq = 0
    for idx, seg in enumerate(segments):
        seg_type = getattr(seg, "type", None)
        if seg_type not in ("image", "file", "mface", "qface"):
            continue
        data = getattr(seg, "data", None) or {}
        url = data.get("url")
        if not url:
            continue
        seq += 1
        prefix = {"image": "img", "file": "file"}.get(seg_type, seg_type)
        # fallback 扩展名只对 file 段有意义（image 段的 file 字段是 QQ 内部
        # 命名如 "abc.image"，不是真实扩展名）
        fallback = (
            str(data.get("file") or "").removeprefix("file://")
            if seg_type == "file"
            else ""
        )
        filename = _safe_filename(url, prefix, seq, fallback_name=fallback)
        dest_dir = _media_root() / scope / scope_id
        jobs.append((idx, _download(url, dest_dir, filename)))
    if not jobs:
        return {}
    results = await asyncio.gather(
        *(coro for _, coro in jobs), return_exceptions=True
    )
    out: dict[int, str] = {}
    for (idx, _), r in zip(jobs, results):
        if isinstance(r, str):
            out[idx] = r
    return out


def _describe_media(segments, downloaded: dict[int, str] | None = None) -> str:
    """把 OneBot 消息段中的富媒体渲染为可读占位描述（空格连接）。

    - image → "[图片: 路径]"；downloaded 含该段索引时路径为下载后的本地绝对
      路径，否则为 url（无 url 退用 file，再无则 "[图片]"）
    - file  → "[文件: 名 路径]"（路径同上；url 缺失则 "[文件: 名]"，名也缺失
      则 "[文件]"）
    - face  → "[表情: id]"（id 为 0 合法，勿用 or 判断）
    - mface/qface（QQ 新版表情）→ 优先 summary → "[表情: summary 本地路径]"；
      summary 缺失用 face_id/qface_id/emoji_id/id → "[表情: mface<id>]"；
      有 url 时经 _download_segments_media 下载，成功后描述带本地绝对路径，
      失败/无 url 时退用原值；全缺失才回退 "[mface]"
    - 其它非文本段（record/video 等）→ "[段类型]"
    纯文本消息返回 ""，不影响原有行为。
    """
    downloaded = downloaded or {}
    parts: list[str] = []
    for idx, seg in enumerate(segments):
        seg_type = getattr(seg, "type", None)
        if seg_type is None or seg_type in _SKIP_SEG_TYPES:
            continue
        data = getattr(seg, "data", None) or {}
        if seg_type == "image":
            detail = downloaded.get(idx) or data.get("url") or data.get("file")
            parts.append(f"[图片: {detail}]" if detail else "[图片]")
        elif seg_type == "file":
            # OneBot v11 file 段：file=文件名（可能带 file:// 前缀），url=下载链接，
            # path=网关侧本地路径（url/path 均可能缺失）
            name = str(data.get("file") or "").removeprefix("file://")
            url = downloaded.get(idx) or data.get("url")
            if name and url:
                parts.append(f"[文件: {name} {url}]")
            elif name:
                parts.append(f"[文件: {name}]")
            else:
                parts.append("[文件]")
        elif seg_type == "face":
            parts.append(f"[表情: {data.get('id', '?')}]")
        elif seg_type in ("mface", "qface"):
            # QQ 新版表情段（字段名因网关而异）：summary=表情含义文字；
            # id 类字段兜底；url=表情图（易过期，下载成功优先本地路径）
            summary = str(data.get("summary") or "").strip()
            if not summary:
                fid = (
                    data.get("face_id")
                    or data.get("qface_id")
                    or data.get("emoji_id")
                    or data.get("id")
                )
                if fid is not None and str(fid).strip():
                    summary = f"{seg_type}{fid}"
            detail = downloaded.get(idx) or data.get("url") or ""
            if summary and detail:
                parts.append(f"[表情: {summary} {detail}]")
            elif summary:
                parts.append(f"[表情: {summary}]")
            elif detail:
                parts.append(f"[表情: {detail}]")
            else:
                parts.append(f"[{seg_type}]")
        else:
            parts.append(f"[{seg_type}]")
    return " ".join(parts)


class OneBotChannel(QQChannel):
    """OneBot 11 网关通道基类（NapCat / LLOneBot 共用）。"""

    def __init__(
        self,
        config: ChannelConfig,
        *,
        bot_fallback: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(config)
        self._bot: Bot | None = None
        # 测试/兼容：self._bot 为 None 时的 bot 获取回调（如返回 plugin._active_bot）
        self._bot_fallback = bot_fallback
        self._bound = False

    # ── 绑定 NoneBot driver ──

    def _matches_uin(self, bot: Bot) -> bool:
        """bot.self_id 是否属于本通道。

        配置了 bot_uin（多账号部署）时只认自己的 QQ 号；未配置（单通道兼容）
        采纳任何连入的 bot。
        """
        uin = getattr(self.config, "bot_uin", None)
        if not uin:
            return True
        return str(uin) == str(getattr(bot, "self_id", ""))

    def bind(self, driver=None) -> None:
        """绑定 NoneBot driver：注册连接/断开/消息 hook + 启动预检。幂等。"""
        if self._bound:
            return
        driver = driver or get_driver()
        driver.on_bot_connect(self._on_bot_connect)
        driver.on_bot_disconnect(self._on_bot_disconnect)
        driver.on_startup(self._startup_check)
        handler = on_message()
        handler.handle()(self._on_message)
        self._bound = True

    async def _on_bot_connect(self, bot: Bot) -> None:
        if not self._matches_uin(bot):
            print(
                f"[QQ][{self.name}] bot connected: {getattr(bot, 'self_id', '?')}"
                f"（非本通道 bot_uin={self.config.bot_uin}，忽略）"
            )
            return
        self._bot = bot
        print(f"[QQ][{self.name}] bot connected: {getattr(bot, 'self_id', '?')}")

    async def _on_bot_disconnect(self, bot: Bot) -> None:
        if self._bot is bot:
            self._bot = None
        print(f"[QQ][{self.name}] bot disconnected: {getattr(bot, 'self_id', '?')}")

    async def _startup_check(self) -> None:
        """启动预检：网关 WS 不可达则打降级日志（适配器持续每 3s 重试）。"""
        urls = self.ws_urls()
        if not urls:
            return
        if not any(self._tcp_reachable(u) for u in urls):
            print(
                f"[QQ][{self.name}] 网关未连接，QQ 模块降级运行"
                f"（进程保持存活，每 3s 自动重试连接）"
            )

    @staticmethod
    def _tcp_reachable(url: str, timeout: float = 1.0) -> bool:
        """对 ws(s):// URL 做廉价 TCP 连通性测试。"""
        try:
            p = urlparse(url)
            port = p.port or (443 if p.scheme == "wss" else 80)
            with socket.create_connection((p.hostname, port), timeout=timeout):
                return True
        except OSError:
            return False

    # ── 入站消息：OneBot event → QQMessage → 业务层 ──

    async def _on_message(self, bot: Bot, event: MessageEvent) -> None:
        # 多账号部署：每个通道的 matcher 都会收到所有 event，只处理自己 bot 的
        if not self._matches_uin(bot):
            return
        # 缓存活动 bot（即使 on_bot_connect 未触发也能用）
        self._bot = bot
        # 群消息：OneBot v11 的 GroupMessageEvent.message_type == "group"
        # （用 message_type 而非 isinstance，便于测试与非标准 event 对象）
        if getattr(event, "message_type", None) == "group":
            # OneBot v11 协议 At.data["qq"] 是字符串（NapCat 实发亦为 str），
            # 两侧统一 str() 归一后比较，避免 int vs str 恒 False 漏掉所有 @
            bot_qq = str(bot.self_id)
            at_qqs = [
                str(seg.data.get("qq", ""))
                for seg in getattr(event, "message", [])
                if getattr(seg, "type", None) == "at"
            ]
            # 群消息仅处理 @ 本 bot 的（与重构前行为一致）；未 @ 直接丢弃
            if bot_qq not in at_qqs:
                return
            scope, scope_id = "group", str(event.group_id)
        else:
            scope, scope_id = "user", str(event.get_user_id())
        text = event.get_plaintext().strip()
        # 富媒体（图片/文件等）先尝试下载到本地（并发，失败静默降级为原 url），
        # 再渲染为占位描述拼进 text（媒体在前），纯富媒体消息 text 即媒体描述，
        # 不再被空文本判断丢弃
        segs = getattr(event, "message", [])
        media_paths = await _download_segments_media(segs, scope, scope_id)
        media = _describe_media(segs, media_paths)
        if media:
            text = f"{media} {text}".strip()
        if not text:
            return
        sender = getattr(event, "sender", None)
        nickname = getattr(sender, "nickname", "") or ""
        msg = QQMessage(
            scope=scope,
            scope_id=scope_id,
            text=text,
            sender_nickname=nickname,
            at_bot=True,
            bot_uin=str(getattr(bot, "self_id", "") or ""),
            raw=getattr(event, "model_dump", lambda: None)(),
        )
        await self._dispatch(msg)

    # ── bot 解析 ──

    def _resolve_bot(self) -> Bot:
        if self._bot is not None:
            return self._bot
        if self._bot_fallback is not None:
            b = self._bot_fallback()
            if b is not None:
                return b
        raise ChannelNotConnected("QQ bot 未连接（网关尚未建立连接）")

    # ── 出站 / 查询 ──

    async def send(
        self, target_type: str, target_id: str | int, text: str
    ) -> dict:
        if target_type not in ("private", "group"):
            return {"ok": False, "error": {
                "code": "invalid_target_type",
                "message": "target_type 必须是 'private' 或 'group'"}}
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": {
                "code": "invalid_target_id",
                "message": f"target_id 必须是 QQ 号/群号，got {target_id!r}"}}
        if not text:
            return {"ok": False, "error": {
                "code": "empty_text", "message": "text 不能为空"}}
        try:
            bot = self._resolve_bot()
            api = "send_private_msg" if target_type == "private" else "send_group_msg"
            params = (
                {"user_id": target_id_int}
                if target_type == "private"
                else {"group_id": target_id_int}
            )
            result = await bot.call_api(api, **params, message=text)
        except ChannelNotConnected as e:
            return {"ok": False, "error": {
                "code": "bot_not_connected", "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": {
                "code": "send_failed",
                "message": f"{type(e).__name__}: {e}"}}
        if isinstance(result, dict):
            return {"ok": True, "message_id": result.get("message_id")}
        return {"ok": True, "message_id": result}

    async def upload_file(
        self, target_type: str, target_id: str | int, file_path: str, name: str = ""
    ) -> dict:
        """发送文件（本地绝对路径或 URL，由网关侧取用）。返回 {ok} 或 {ok:false,...}。

        私聊 → upload_private_file(user_id, file, name)；群聊 → upload_group_file
        (group_id, file, name)。name 缺省时从 file_path 推导文件名（OneBot 两个
        upload API 都要求显式 name）。上传成功无 message_id，仅返回 {"ok": True}。
        """
        if target_type not in ("private", "group"):
            return {"ok": False, "error": {
                "code": "invalid_target_type",
                "message": "target_type 必须是 'private' 或 'group'"}}
        if not file_path:
            return {"ok": False, "error": {
                "code": "empty_file_path", "message": "file_path 不能为空"}}
        try:
            target_id_int = int(target_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": {
                "code": "invalid_target_id",
                "message": f"target_id 必须是 QQ 号/群号，got {target_id!r}"}}
        # file_path 可能是本地路径（/ 或 \ 分隔）或 URL，两者 basename 均可推导
        fname = (name or "").strip() or os.path.basename(file_path)
        try:
            bot = self._resolve_bot()
            if target_type == "private":
                await bot.call_api(
                    "upload_private_file",
                    user_id=target_id_int, file=file_path, name=fname)
            else:
                await bot.call_api(
                    "upload_group_file",
                    group_id=target_id_int, file=file_path, name=fname)
        except ChannelNotConnected as e:
            return {"ok": False, "error": {
                "code": "bot_not_connected", "message": str(e)}}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": {
                "code": "upload_failed",
                "message": f"{type(e).__name__}: {e}"}}
        return {"ok": True}

    async def recent_contacts(self) -> dict:
        try:
            bot = self._resolve_bot()
        except ChannelNotConnected as e:
            return {"ok": False, "error": {
                "code": "bot_not_connected", "message": str(e)}}

        async def _call(api: str, params: dict | None = None):
            try:
                return await bot.call_api(api, **(params or {})), None
            except Exception as e:  # noqa: BLE001  # best-effort per API
                return None, f"{type(e).__name__}: {e}"

        # 1) 近期会话（best-effort；带 count 尽量多取，不支持则重试无参）
        recent_items: list = []
        recent_err: str | None = None
        result, err = await _call("get_recent_contact", {"count": 50})
        if err:
            result, err = await _call("get_recent_contact")
        if err:
            recent_err = err
        else:
            recent_items = self._api_result_list(result)

        # 2) 完整好友/群列表（补全缺失名称 + 补齐非近期会话）
        friends: dict[str, str] = {}
        result, _ = await _call("get_friend_list")
        for f in self._api_result_list(result):
            uin = self._clean_peer_uin(f.get("user_id"))
            if uin is None:
                continue
            name = (f.get("remark") or "").strip() or (f.get("nickname") or "").strip()
            friends[uin] = name

        groups: dict[str, str] = {}
        result, _ = await _call("get_group_list")
        for g in self._api_result_list(result):
            gid = self._clean_peer_uin(g.get("group_id"))
            if gid is None:
                continue
            groups[gid] = (g.get("group_name") or "").strip()

        # 3) 合并去重：近期优先 → 好友 → 群
        merged: list[dict] = []
        seen: set[tuple[int, str]] = set()

        def _add(chat_type: int, uin: str, name: str) -> None:
            key = (chat_type, uin)
            if key in seen:
                return
            seen.add(key)
            merged.append({
                "peerUin": uin,
                "peerName": name or uin,
                "chatType": chat_type,
            })

        for it in recent_items:
            chat_type = it.get("chatType")
            if chat_type not in (_CHAT_FRIEND, _CHAT_GROUP):
                continue
            uin = self._clean_peer_uin(it.get("peerUin"))
            if uin is None:
                continue
            name = (it.get("peerName") or "").strip() or (it.get("remark") or "").strip()
            if not name:
                name = friends.get(uin) if chat_type == _CHAT_FRIEND else groups.get(uin)
            _add(chat_type, uin, name or "")

        for uin, name in friends.items():
            _add(_CHAT_FRIEND, uin, name)
        for gid, name in groups.items():
            _add(_CHAT_GROUP, gid, name)

        if not merged and recent_err:
            return {"ok": False, "error": {
                "code": "unsupported",
                "message": f"{recent_err}（get_recent_contact 不可用且无好友/群数据）"}}
        return {"ok": True, "contacts": merged}

    # ── 状态 ──

    async def is_connected(self) -> bool:
        return self._bot is not None

    async def startup(self) -> None:
        # 正向 WS 由 NoneBot OneBot 适配器在运行时自动连接；此处仅做预检提示。
        await self._startup_check()

    async def shutdown(self) -> None:
        self._bot = None

    # ── OneBot 结果兼容工具 ──

    @staticmethod
    def _clean_peer_uin(value) -> str | None:
        """规范化 peer uin 为非零数字串；缺失 / "0" 占位返回 None。"""
        if value is None:
            return None
        s = str(value).strip()
        if not s or s == "0":
            return None
        return s

    @staticmethod
    def _api_result_list(result) -> list:
        """从 call_api 结果（已解包 list 或 dict）提取 list，兼容嵌套 data。"""
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                nested = data.get("data")
                if isinstance(nested, list):
                    return nested
        return []
