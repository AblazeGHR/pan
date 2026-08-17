"""Tests for Character management system."""

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from packages.core.character import Character, CharacterManager
from packages.core.manifest_loader import CharacterTemplate, SessionTemplate


# ------------------------------------------------------------------ #
#  Fixtures
# ------------------------------------------------------------------ #

def _make_temp_data_dir():
    """Create a temporary data/ directory structure."""
    tmp = tempfile.mkdtemp(prefix="pan_char_test_")
    data_dir = Path(tmp) / "data"
    (data_dir / "characters").mkdir(parents=True)
    (data_dir / "memory").mkdir(parents=True)
    return tmp, data_dir


def _write_manifest(base_dir: str, session_templates: list[dict] | None = None,
                    character_templates: list[dict] | None = None):
    """Write a minimal manifest.json for testing."""
    if session_templates is None:
        session_templates = [
            {
                "name": "test-session",
                "adapter": "cbc",
                "model": "deepseek-v4-flash",
                "system_prompt": "You are a test assistant.",
                "permission_mode": "bypassPermissions",
            }
        ]
    if character_templates is None:
        character_templates = [
            {
                "name": "test-character",
                "session_templates": ["test-session"],
                "memory_dir": "data/characters/test/memory",
            }
        ]
    data = {
        "session_templates": session_templates,
        "character_templates": character_templates,
        "mcp_servers": [],
        "command_routes": [],
    }
    path = Path(base_dir) / "manifest.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ #
#  Character dataclass
# ------------------------------------------------------------------ #

class TestCharacter:
    def test_roundtrip(self):
        c = Character(
            id="char_test1234abcd",
            name="测试角色",
            memory_db_path="data/memory/char_test1234abcd.sqlite",
            memory_dir="characters/test/memory",
            created_at="2026-07-29T12:00:00",
        )
        d = c.to_dict()
        c2 = Character.from_dict(d)
        assert c2.id == c.id
        assert c2.name == c.name
        assert c2.memory_db_path == c.memory_db_path
        assert c2.memory_dir == c.memory_dir

    def test_defaults(self):
        c = Character(id="char_x", name="n")
        assert c.memory_db_path == ""
        assert c.memory_dir is None
        assert c.created_at == ""  # populated by CharacterManager, not dataclass

    def test_no_session_config_fields(self):
        """Character carries no session config (system_prompt/mcp/role)."""
        c = Character(id="char_x", name="n")
        assert not hasattr(c, "system_prompt")
        assert not hasattr(c, "mcp_mode")
        assert not hasattr(c, "mcp_servers")
        assert not hasattr(c, "role")
        assert not hasattr(c, "profile_name")


# ------------------------------------------------------------------ #
#  Manifest templates
# ------------------------------------------------------------------ #

class TestManifestTemplates:
    def test_session_template_roundtrip(self):
        t = SessionTemplate(name="t", system_prompt="hi", mcp_servers=["pan"],
                            restrict_to_managed=True, can_claim_unmanaged=True,
                            auto_claim_created=True)
        assert t.mcp_locked is False  # mcp_mode default "optional"
        assert t.mcp_servers == ["pan"]
        assert t.restrict_to_managed is True
        assert t.can_claim_unmanaged is True
        assert t.auto_claim_created is True
        assert not hasattr(t, "role")

    def test_session_template_capabilities_default_false(self):
        t = SessionTemplate(name="t")
        assert t.restrict_to_managed is False
        assert t.can_claim_unmanaged is False
        assert t.auto_claim_created is False

    def test_character_template_roundtrip(self):
        t = CharacterTemplate(name="c", session_templates=["t"], memory_dir="m")
        assert t.session_templates == ["t"]
        assert t.memory_dir == "m"
        assert not hasattr(t, "system_prompt")


# ------------------------------------------------------------------ #
#  CharacterManager
# ------------------------------------------------------------------ #

class TestCharacterManager:
    def test_init_creates_dirs(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            assert Path(data_dir / "characters").exists()
            assert Path(data_dir / "memory").exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_load_manifest(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            cfg = mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            assert len(cfg.session_templates) == 1
            assert cfg.session_templates[0].name == "test-session"
            assert "test assistant" in cfg.session_templates[0].system_prompt
            assert len(cfg.character_templates) == 1
            assert cfg.character_templates[0].name == "test-character"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_session_templates_empty_before_load(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            assert mgr.list_session_templates() == []
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_session_templates_after_load(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, session_templates=[
                {"name": "a", "system_prompt": "A"},
                {"name": "b", "system_prompt": "B"},
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            assert len(mgr.list_session_templates()) == 2
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_from_template(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])

            char = mgr.create_character("test-character", name="测试", auto_index=False)
            assert char.id.startswith("char_")
            assert char.name == "测试"
            assert "char_" in char.memory_db_path
            assert char.memory_dir is not None
            assert "characters" in char.memory_dir
            assert char.created_at != ""

            # Check JSON was persisted
            json_path = Path(data_dir) / "characters" / f"{char.id}.json"
            assert json_path.exists()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_invalid_template(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            mgr = CharacterManager(str(data_dir))
            try:
                mgr.create_character("nonexistent")
                assert False, "Should have raised ValueError"
            except ValueError:
                pass
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_character(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-character", auto_index=False)

            found = mgr.get_character(char.id)
            assert found is not None
            assert found.id == char.id
            assert found.name == char.name

            assert mgr.get_character("nonexistent") is None
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_list_characters(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, character_templates=[
                {"name": "a", "memory_dir": None},
                {"name": "b", "memory_dir": None},
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])

            mgr.create_character("a", name="一", auto_index=False)
            mgr.create_character("b", name="二", auto_index=False)

            chars = mgr.list_characters()
            assert len(chars) == 2
            names = {c.name for c in chars}
            assert names == {"一", "二"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_delete_character(self):
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-character", auto_index=False)

            char_id = char.id
            assert mgr.delete_character(char_id) is True
            assert mgr.get_character(char_id) is None
            assert mgr.delete_character(char_id) is False  # Already gone
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_create_character_auto_index_no_memory_dir(self):
        """auto_index=True with no memory_dir should not crash."""
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp, character_templates=[
                {"name": "no-mem", "memory_dir": None}
            ])
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("no-mem", auto_index=True)
            assert char.id.startswith("char_")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_get_memory_manager_no_api_key(self):
        """get_memory_manager without API key is graceful and cached."""
        tmp, data_dir = _make_temp_data_dir()
        try:
            _write_manifest(tmp)
            mgr = CharacterManager(str(data_dir))
            mgr.load_manifest([str(Path(tmp) / "manifest.json")])
            char = mgr.create_character("test-character", auto_index=False)

            # Must not raise. With the ST provider a manager is constructible
            # without any API key; if so it must be cached (same instance on
            # repeat lookup, #32) and closeable. If None, lookups must agree.
            mm1 = mgr.get_memory_manager(char.id)
            mm2 = mgr.get_memory_manager(char.id)
            if mm1 is not None:
                assert mm2 is mm1, "get_memory_manager not cached"
                mm1.close()
            else:
                assert mm2 is None, "cache and fresh lookup disagree"
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
