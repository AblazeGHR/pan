"""Memory context injection — formats memory search results into prompt context.

Used by Pan Core to inject relevant character knowledge before sending a task
to a Worker (external CLI process). Since Workers are external processes, memory
results are pre-searched and formatted as context text rather than injected as tools.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class MemoryContext:
    """Memory search results formatted for prompt injection."""
    query: str
    results_md: str  # Markdown-formatted results
    snippet_count: int
    source: str  # character_id


# ── MemoryManager cache (#20) ────────────────────────────────────────── #
#
# search_and_format runs once per user message (worker injection path). Each
# call used to build a fresh MemoryManager, which reloaded the sentence-
# transformers model (3-10s) on every message. Cache the manager per
# (db_path, provider, model_path) so the model loads once per character and
# is reused — matching the server-side _get_memory_manager behavior.

_manager_cache: dict[tuple, object] = {}  # (db_path, provider, model_path, api_key) -> MemoryManager
_manager_lock = threading.Lock()


def _get_cached_manager(
    db_path: str,
    provider: str,
    model_path: str | None,
    api_key: str | None,
):
    """Return a cached MemoryManager, building it on first use (thread-safe)."""
    from .memory import MemoryManager

    key = (db_path, provider, model_path, api_key)
    with _manager_lock:
        cached = _manager_cache.get(key)
        if cached is not None:
            return cached

    # Build outside the lock (model load is expensive; don't hold it).
    mgr = MemoryManager(
        db_path=db_path,
        api_key=api_key,
        provider=provider,
        model_path=model_path,
    )
    with _manager_lock:
        existing = _manager_cache.get(key)
        if existing is not None:
            # Another thread built it first — drop the duplicate.
            try:
                mgr.close()
            except Exception:
                pass
            return existing
        _manager_cache[key] = mgr
        return mgr


def clear_memory_manager_cache() -> None:
    """Close and drop all cached MemoryManagers (e.g. on shutdown / in tests)."""
    with _manager_lock:
        items = list(_manager_cache.values())
        _manager_cache.clear()
    for mgr in items:
        try:
            mgr.close()
        except Exception:
            pass


def search_and_format(
    query: str,
    character_id: str = "default",
    api_key: str | None = None,
    provider: str | None = None,
    model_path: str | None = None,
    db_dir: str | None = None,
    max_results: int = 3,
    min_score: float = 0.35,
) -> MemoryContext:
    """Search memory and format results as Markdown context.

    Args:
        query: The user's query to search for relevant memory
        character_id: Which character's memory store to search
        api_key: OpenAI API key for embedding (reads OPENAI_API_KEY env if None)
        db_dir: Directory containing .sqlite files (default: data/memory/)
        max_results: Max results to include in context
        min_score: Minimum similarity score threshold

    Returns:
        MemoryContext with formatted results ready for prompt injection.
    """
    from pathlib import Path

    from .memory import PROVIDER_SENTENCE_TRANSFORMERS

    if db_dir is None:
        # Resolve from the module path (repo root), not cwd — matches the
        # worker and server paths regardless of launch directory (#22/#G).
        db_dir = str(Path(__file__).resolve().parent.parent.parent / "data" / "memory")

    import os
    if api_key is None:
        api_key = os.environ.get("OPENAI_API_KEY")

    if provider is None:
        provider = PROVIDER_SENTENCE_TRANSFORMERS

    db_path = str(Path(db_dir) / f"{character_id}.sqlite")

    # Cached manager: the sentence-transformers model is loaded once per
    # character and reused across messages (#20). No close() here — the
    # manager lives for the process lifetime.
    try:
        mgr = _get_cached_manager(db_path, provider, model_path, api_key)
        results = mgr.search(query, max_results=max_results, min_score=min_score)
    except Exception as exc:
        log.warning("Memory search failed for %s: %s", character_id, exc)
        return MemoryContext(
            query=query,
            results_md="",
            snippet_count=0,
            source=character_id,
        )

    if not results:
        return MemoryContext(
            query=query,
            results_md="",
            snippet_count=0,
            source=character_id,
        )

    # Format as Markdown
    lines = ["## 记忆检索结果", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"### 记忆片段 {i}（相关度 {r.score:.2f}）")
        lines.append(f"来源: `{r.path}`")
        lines.append("")
        lines.append(r.text)
        lines.append("")

    return MemoryContext(
        query=query,
        results_md="\n".join(lines),
        snippet_count=len(results),
        source=character_id,
    )


def inject_context(task_text: str, context: MemoryContext) -> str:
    """Prepend memory context to a task text for Worker injection.

    Args:
        task_text: The original user message
        context: MemoryContext from search_and_format

    Returns:
        Combined prompt with memory context prepended.
    """
    if not context.results_md:
        return task_text

    return f"""[系统指令]
以下是角色的知识库中与当前对话相关的信息。请在回答时参考这些内容。

{context.results_md}

[用户消息]
{task_text}"""
