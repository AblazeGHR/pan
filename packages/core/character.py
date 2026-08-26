"""Character management — dataclass, persistence, and memory integration.

Usage::

    from packages.core.character import Character, CharacterManager

    mgr = CharacterManager("data")
    mgr.load_manifest(["D:/project/RuleWhisper/pan_plugin"])

    char = mgr.create_character("coc-keeper", name="我的COC跑团")
    results = mgr.search_memory(char.id, "如何创建角色")
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .memory.embedder import PROVIDER_SENTENCE_TRANSFORMERS

if TYPE_CHECKING:
    from .manifest_loader import ManifestConfig, SessionTemplate, CharacterTemplate
    from .memory import MemoryManager
    from .memory.search import SearchResult

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Character dataclass
# ------------------------------------------------------------------ #

@dataclass
class Character:
    """A user-created persistent entity owning memory + assets.

    A character is a long-lived identity shared across sessions. It carries no
    session config (system_prompt / adapter / model / mcp_mode / mcp_servers) —
    that comes from a session_template. It only owns retrievable memory and
    (future) assets.
    """

    id: str  # "char_<16 hex chars>"
    name: str  # user-visible name (e.g. "我的COC跑团")
    memory_db_path: str = ""  # e.g. "data/memory/char_abc123.sqlite"
    memory_dir: str | None = None  # directory of .md knowledge files
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "memory_db_path": self.memory_db_path,
            "memory_dir": self.memory_dir,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Character:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            memory_db_path=data.get("memory_db_path", ""),
            memory_dir=data.get("memory_dir"),
            created_at=data.get("created_at", ""),
        )


# ------------------------------------------------------------------ #
#  CharacterManager
# ------------------------------------------------------------------ #

class CharacterManager:
    """Manages character CRUD, persistence, and memory integration."""

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._characters_dir = self._data_dir / "characters"
        self._memory_dir = self._data_dir / "memory"
        self._characters_dir.mkdir(parents=True, exist_ok=True)
        self._memory_dir.mkdir(parents=True, exist_ok=True)

        self._memory_managers: dict[str, MemoryManager] = {}
        self._memory_managers_lock = threading.Lock()  # guards the dict (#32)
        self._manifest_config: ManifestConfig | None = None

        # Hot-reload bookkeeping. ``_plugin_paths`` is the same list passed to
        # ``load_manifest`` so ``reload_manifest`` can re-read the exact same
        # files. The mtime snapshot + file count let us do a cheap stat-only
        # change check (no file read / parse) on the hot path.
        self._plugin_paths: list[str] = []
        self._manifest_mtime_snapshot: float = 0.0
        self._manifest_file_count: int = 0

        # Throttle for the cheap stat-only change check: avoid re-statting the
        # manifest files on every request in a high-frequency burst. The stat
        # result is cached for ``_manifest_check_ttl`` seconds. A forced reload
        # via ``reload_manifest()`` (e.g. POST /api/manifest/reload) ALWAYS
        # re-reads and is NOT subject to this window, so a deterministic
        # refresh path always exists even within the throttle window.
        self._manifest_check_ttl: float = 1.0
        self._cached_mtime: float = 0.0
        self._cached_count: int = 0
        self._cached_check_ts: float = 0.0

    # ------------------------------------------------------------------ #
    #  Manifest
    # ------------------------------------------------------------------ #

    def load_manifest(self, plugin_paths: list[str]) -> ManifestConfig:
        from .manifest_loader import load_manifests

        self._plugin_paths = list(plugin_paths)
        self._manifest_config = load_manifests(plugin_paths)
        self._refresh_manifest_state()
        return self._manifest_config

    # --- manifest hot-reload ------------------------------------------- #

    def _manifest_state(self) -> tuple[float, int]:
        """Return ``(max_mtime, file_count)`` for the resolved manifest files.

        Stat-only — does NOT read or parse any file. ``file_count`` lets us
        detect additions/removals (a deleted newest file would otherwise lower
        the max mtime and look "unchanged").
        """
        from .manifest_loader import resolve_manifest_files

        if not self._plugin_paths:
            return 0.0, 0
        mtime = 0.0
        files = resolve_manifest_files(self._plugin_paths)
        for mf in files:
            try:
                mtime = max(mtime, mf.stat().st_mtime)
            except OSError:
                pass
        return mtime, len(files)

    def _refresh_manifest_state(self) -> None:
        """Cache the current mtime + file count after a (re)load."""
        self._manifest_mtime_snapshot, self._manifest_file_count = (
            self._manifest_state()
        )
        # Invalidate the throttle cache so the next change-check re-stats
        # (the file set / mtime just changed under our feet).
        self._cached_check_ts = 0.0

    def manifest_changed(self) -> bool:
        """True if any resolved manifest file changed since the last load.

        Cheap: only ``stat``s files (no read/parse). True when the newest mtime
        advanced OR the set of resolved files changed (add/remove a manifest).

        Throttled: within ``_manifest_check_ttl`` seconds of the previous
        check we reuse the last stat result instead of re-statting. This is
        purely an optimisation — ``reload_manifest()`` (the manual /
        deterministic refresh) never goes through this window.
        """
        now = time.monotonic()
        if self._cached_check_ts and (now - self._cached_check_ts) < self._manifest_check_ttl:
            mtime, count = self._cached_mtime, self._cached_count
        else:
            mtime, count = self._manifest_state()
            self._cached_mtime, self._cached_count, self._cached_check_ts = (
                mtime, count, now,
            )
        return (
            mtime > self._manifest_mtime_snapshot
            or count != self._manifest_file_count
        )

    def _manifest_files_parse_ok(self) -> tuple[bool, list[str]]:
        """``(ok, errors)`` — whether every resolved manifest currently parses.

        Used by ``reload_manifest`` to abort and keep the old config when a
        manifest is broken (instead of swapping in a partial/silent result).
        """
        from .manifest_loader import resolve_manifest_files

        errors: list[str] = []
        if not self._plugin_paths:
            return True, errors
        for mf in resolve_manifest_files(self._plugin_paths):
            try:
                json.loads(mf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                errors.append(f"{mf}: {exc}")
        return (len(errors) == 0), errors

    def reload_manifest(self) -> ManifestConfig | None:
        """Hot-reload the same ``plugin_paths``, atomically replacing config.

        Re-reads + re-parses the manifest files and replaces the whole
        ``_manifest_config`` in one assignment so every consumer (session
        templates, mcp_servers, command_routes, character templates) stays
        consistent. On failure (unreadable / unparseable manifest) the previous
        config is preserved and the error logged — callers never see a crash or
        a partial config.

        Returns the (possibly unchanged) config.
        """
        if not self._plugin_paths:
            # Nothing was ever loaded via paths; nothing to reload.
            return self._manifest_config

        ok, errors = self._manifest_files_parse_ok()
        if not ok:
            log.error(
                "Manifest reload aborted — %d file(s) failed to parse; "
                "keeping previous config: %s",
                len(errors), errors,
            )
            return self._manifest_config

        from .manifest_loader import load_manifests

        try:
            new_config = load_manifests(self._plugin_paths)
        except Exception:
            log.exception("Manifest reload failed; keeping previous config")
            return self._manifest_config

        # Atomic swap: replace the whole config object at once.
        self._manifest_config = new_config
        self._refresh_manifest_state()
        return self._manifest_config

    def list_session_templates(self) -> list[SessionTemplate]:
        if self._manifest_config is None:
            return []
        return self._manifest_config.session_templates

    def list_character_templates(self) -> list[CharacterTemplate]:
        if self._manifest_config is None:
            return []
        return self._manifest_config.character_templates

    def list_command_routes(self):
        """Return manifest command_routes for QQ Bot prefix routing.

        Returns an empty list if no manifest is loaded — callers (e.g. the QQ
        plugin via ``GET /api/manifest/command-routes``) treat empty as "no
        prefix routing, all messages go to LLM path".
        """
        if self._manifest_config is None:
            return []
        return self._manifest_config.command_routes

    def get_session_template(self, name: str) -> SessionTemplate | None:
        if self._manifest_config is None:
            return None
        return self._manifest_config.get_session_template(name)

    def get_character_template(self, name: str) -> CharacterTemplate | None:
        if self._manifest_config is None:
            return None
        return self._manifest_config.get_character_template(name)

    # ------------------------------------------------------------------ #
    #  Character CRUD
    # ------------------------------------------------------------------ #

    def create_character(
        self,
        template_name: str,
        name: str | None = None,
        auto_index: bool = True,
    ) -> Character:
        template = self.get_character_template(template_name)
        if template is None:
            raise ValueError(f"Character template not found: {template_name}")

        char_id = "char_" + secrets.token_hex(8)

        char = Character(
            id=char_id,
            name=name if name is not None else template.name,
            memory_db_path=str(self._memory_dir / f"{char_id}.sqlite"),
            memory_dir=template.memory_dir,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        if auto_index and char.memory_dir:
            try:
                mgr = self.get_memory_manager(char_id)
                if mgr is not None:
                    mgr.index_directory(char.memory_dir)
            except Exception:
                log.warning(
                    "Memory indexing failed for character %s (non-fatal)",
                    char_id,
                    exc_info=True,
                )

        self._save_character(char)
        return char

    def get_character(self, character_id: str) -> Character | None:
        file_path = self._characters_dir / f"{character_id}.json"
        if not file_path.exists():
            return None
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
            return Character.from_dict(data)
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load character %s: %s", character_id, exc)
            return None

    def list_characters(self) -> list[Character]:
        characters: list[Character] = []
        for f in sorted(self._characters_dir.glob("*.json")):
            char_id = f.stem
            char = self.get_character(char_id)
            if char is not None:
                characters.append(char)
        return characters

    def delete_character(self, character_id: str) -> bool:
        json_path = self._characters_dir / f"{character_id}.json"
        sqlite_path = self._memory_dir / f"{character_id}.sqlite"

        deleted = False
        if json_path.exists():
            json_path.unlink()
            deleted = True

        if sqlite_path.exists():
            sqlite_path.unlink()
            deleted = True

        # Remove from lazy cache (close first if loaded)
        with self._memory_managers_lock:
            mgr = self._memory_managers.pop(character_id, None)
        if mgr is not None:
            try:
                mgr.close()
            except Exception:
                log.warning(
                    "Error closing MemoryManager for %s during delete",
                    character_id,
                    exc_info=True,
                )

        return deleted

    # ------------------------------------------------------------------ #
    #  Memory integration
    # ------------------------------------------------------------------ #

    def get_memory_manager(
        self, character_id: str, api_key: str | None = None
    ) -> MemoryManager | None:
        with self._memory_managers_lock:
            if character_id in self._memory_managers:
                return self._memory_managers[character_id]

        char = self.get_character(character_id)
        if char is None:
            return None

        from .memory import MemoryManager

        try:
            mgr = MemoryManager(
                db_path=char.memory_db_path,
                api_key=api_key,
                provider=PROVIDER_SENTENCE_TRANSFORMERS,
            )
        except Exception:
            log.warning(
                "Failed to create MemoryManager for character %s (no API key or local model?)",
                character_id,
                exc_info=True,
            )
            return None

        # Re-check under the lock: another thread may have created the manager
        # (or deleted the character) while we were building it (#32).
        with self._memory_managers_lock:
            existing = self._memory_managers.get(character_id)
            if existing is not None:
                try:
                    mgr.close()
                except Exception:
                    pass
                return existing
            self._memory_managers[character_id] = mgr
        return mgr

    def search_memory(
        self,
        character_id: str,
        query: str,
        max_results: int = 3,
    ) -> list[SearchResult]:
        mgr = self.get_memory_manager(character_id)
        if mgr is None:
            return []
        try:
            return mgr.search(query, max_results=max_results)
        except Exception:
            log.warning(
                "Memory search failed for character %s",
                character_id,
                exc_info=True,
            )
            return []

    def inject_context(
        self, character_id: str, task_text: str
    ) -> str:
        results = self.search_memory(character_id, task_text)
        if not results:
            return task_text

        context_parts = []
        for i, r in enumerate(results, 1):
            context_parts.append(
                f"[{i}] ({r.path}:{r.start_line}-{r.end_line})\n{r.text}"
            )

        header = (
            "以下是与当前任务相关的记忆上下文（仅供参考）：\n"
            "---\n"
        )
        body = "\n\n".join(context_parts)
        footer = "\n---\n"

        return header + body + footer + task_text

    # ------------------------------------------------------------------ #
    #  Internal helpers
    # ------------------------------------------------------------------ #

    def _save_character(self, character: Character) -> None:
        file_path = self._characters_dir / f"{character.id}.json"
        data = json.dumps(
            character.to_dict(), ensure_ascii=False, indent=2
        )
        file_path.write_text(data, encoding="utf-8")
