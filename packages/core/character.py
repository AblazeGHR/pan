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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from .memory.embedder import PROVIDER_SENTENCE_TRANSFORMERS

if TYPE_CHECKING:
    from .manifest_loader import ManifestConfig, Profile
    from .memory import MemoryManager
    from .memory.search import SearchResult

log = logging.getLogger(__name__)


# ------------------------------------------------------------------ #
#  Character dataclass
# ------------------------------------------------------------------ #

@dataclass
class Character:
    """A user-created character instance backed by a manifest profile."""

    id: str  # "char_<16 hex chars>"
    profile_name: str  # manifest profile name (e.g. "coc-keeper")
    name: str  # user-visible name (e.g. "我的COC跑团")
    adapter: str = "cbc"
    model: str | None = None
    permission_mode: str | None = None
    system_prompt: str = ""
    mcp_mode: str = "optional"    # inherited from profile: "always" | "optional" | "never"
    mcp_servers: list[dict] = field(default_factory=list)  # MCP server configs from manifest
    memory_db_path: str = ""  # e.g. "data/memory/char_abc123.sqlite"
    memory_dir: str | None = None  # directory of .md knowledge files
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_name": self.profile_name,
            "name": self.name,
            "adapter": self.adapter,
            "model": self.model,
            "permission_mode": self.permission_mode,
            "system_prompt": self.system_prompt,
            "mcp_mode": self.mcp_mode,
            "mcp_servers": self.mcp_servers,
            "memory_db_path": self.memory_db_path,
            "memory_dir": self.memory_dir,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Character:
        return cls(
            id=data.get("id", ""),
            profile_name=data.get("profile_name", ""),
            name=data.get("name", ""),
            adapter=data.get("adapter", "cbc"),
            model=data.get("model"),
            permission_mode=data.get("permission_mode"),
            system_prompt=data.get("system_prompt", ""),
            mcp_mode=data.get("mcp_mode", "optional"),
            mcp_servers=data.get("mcp_servers", []),
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

    # ------------------------------------------------------------------ #
    #  Manifest
    # ------------------------------------------------------------------ #

    def load_manifest(self, plugin_paths: list[str]) -> ManifestConfig:
        from .manifest_loader import load_manifests

        self._manifest_config = load_manifests(plugin_paths)
        return self._manifest_config

    def list_profiles(self) -> list[Profile]:
        if self._manifest_config is None:
            return []
        return self._manifest_config.profiles

    def list_command_routes(self):
        """Return manifest command_routes for QQ Bot prefix routing.

        Returns an empty list if no manifest is loaded — callers (e.g. the QQ
        plugin via ``GET /api/manifest/command-routes``) treat empty as "no
        prefix routing, all messages go to LLM path".
        """
        if self._manifest_config is None:
            return []
        return self._manifest_config.command_routes

    def get_profile(self, name: str) -> Profile | None:
        if self._manifest_config is None:
            return None
        return self._manifest_config.get_profile(name)

    # ------------------------------------------------------------------ #
    #  Character CRUD
    # ------------------------------------------------------------------ #

    def create_character(
        self,
        profile_name: str,
        name: str | None = None,
        auto_index: bool = True,
    ) -> Character:
        profile = self.get_profile(profile_name)
        if profile is None:
            raise ValueError(f"Profile not found: {profile_name}")

        char_id = "char_" + secrets.token_hex(8)
        
        # Resolve mcp_servers: profile has names, manifest has full configs
        mcp_configs: list[dict] = []
        if profile.mcp_servers and self._manifest_config:
            for srv_name in profile.mcp_servers:
                for srv in self._manifest_config.mcp_servers:
                    if srv.name == srv_name:
                        cfg: dict = {"name": srv.name}
                        if srv.command:
                            cfg["command"] = srv.command
                        if srv.args:
                            cfg["args"] = srv.args
                        if srv.env:
                            cfg["env"] = srv.env
                        if srv.cwd:
                            cfg["cwd"] = srv.cwd
                        mcp_configs.append(cfg)
                        break
        
        char = Character(
            id=char_id,
            profile_name=profile.name,
            name=name if name is not None else profile.name,
            adapter=profile.adapter,
            model=profile.model,
            permission_mode=profile.permission_mode,
            system_prompt=profile.system_prompt,
            mcp_mode=profile.mcp_mode,
            mcp_servers=mcp_configs,
            memory_db_path=str(self._memory_dir / f"{char_id}.sqlite"),
            memory_dir=profile.memory_dir,
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
