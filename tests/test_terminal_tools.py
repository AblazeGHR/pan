"""Local dispatcher gates; these are not stdio/HTTP integration evidence."""
import base64
from types import SimpleNamespace

import pytest

from packages.web.terminal_tools import TerminalTools, ToolRejected


class Service:
    def __init__(self):
        self.calls = []
        self.fail_release = False

    def create(self, **kwargs):
        self.calls.append(("create", kwargs))
        return {"terminal_id": "term_test", "state": "running", "token": "SECRET"}

    def get(self, terminal_id):
        self.calls.append(("get", terminal_id))
        return {"terminal_id": terminal_id, "state": "running", "token": "SECRET"}

    def attach(self, terminal_id, **kwargs):
        token = SimpleNamespace(terminal_id=terminal_id, generation=9)
        self.calls.append(("attach", token, kwargs))
        return token

    def input(self, terminal_id, token, payload, **kwargs):
        self.calls.append(("input", terminal_id, token, payload, kwargs))
        return {"accepted": True, "size": len(payload), "channel": {"token": "SECRET"}}

    def release_attachment(self, token):
        self.calls.append(("release", token))
        if self.fail_release:
            raise OSError("SECRET")


@pytest.fixture
def bridge():
    service = Service()
    tools = TerminalTools(service, resolve_caller=lambda sid: {"id": sid},
                          check_access=lambda caller, target: target == "allowed")
    return tools, service


@pytest.mark.parametrize("caller", [None, {}, {"id": "someone-else"}])
def test_unknown_caller_never_touches_service(bridge, caller):
    tools, service = bridge
    tools.resolve_caller = lambda sid: caller
    with pytest.raises(ToolRejected, match="caller-unverified"):
        tools.dispatch("caller", "get", {"terminal_id": "term_test"})
    assert service.calls == []


@pytest.mark.parametrize("field", ["created_by", "trusted_local", "context", "token", "shell_argv"])
def test_identity_and_secret_fields_cannot_be_supplied(bridge, field):
    tools, service = bridge
    with pytest.raises(ToolRejected, match="unknown-field"):
        tools.dispatch("caller", "create", {field: "forged"})
    assert service.calls == []


def test_create_uses_verified_caller_not_associated_session(bridge):
    tools, service = bridge
    result = tools.dispatch("caller", "create", {"session_id": "allowed"})
    assert service.calls[0][1]["context"].created_by == "mcp:caller"
    assert service.calls[0][1]["session_id"] == "allowed"
    assert "token" not in result


def test_scope_cannot_bypass_existing_access_check(bridge):
    tools, service = bridge
    with pytest.raises(ToolRejected, match="permission-denied"):
        tools.dispatch("caller", "create", {"session_id": "foreign"})
    assert service.calls == []


def test_readonly_caller_can_observe_but_cannot_create(bridge):
    tools, service = bridge
    tools.resolve_caller = lambda sid: {"id": sid, "readonly": True}
    assert tools.dispatch("caller", "get", {"terminal_id": "term_test"})
    with pytest.raises(ToolRejected, match="caller-readonly"):
        tools.dispatch("caller", "create", {})
    assert [c[0] for c in service.calls] == ["get"]


@pytest.mark.parametrize("rows", [True, 0, 501, 1.5, "24"])
def test_sizes_are_strict_and_match_accepted_service(bridge, rows):
    tools, service = bridge
    with pytest.raises(ToolRejected, match="invalid-rows"):
        tools.dispatch("caller", "create", {"rows": rows})
    assert service.calls == []


@pytest.mark.parametrize("take", [None, False, 1, "true"])
def test_input_requires_explicit_real_bool_control_takeover(bridge, take):
    tools, service = bridge
    with pytest.raises(ToolRejected, match="explicit-control-required"):
        tools.dispatch("caller", "input", {"terminal_id": "term_test", "take_control": take,
                                             "data_b64": "eA=="})
    assert service.calls == []


def test_input_real_lease_release_and_precise_sequence(bridge):
    tools, service = bridge
    payload = "中文\r\n".encode()
    result = tools.dispatch("caller", "input", {"terminal_id": "term_test", "take_control": True,
        "data_b64": base64.b64encode(payload).decode(), "seq": "9007199254740993"})
    assert [c[0] for c in service.calls] == ["attach", "input", "release"]
    assert service.calls[1][2] is service.calls[0][1] is service.calls[2][1]
    assert service.calls[1][3] == payload
    assert service.calls[1][4]["seq"] == 9007199254740993
    assert result == {"terminal_id": "term_test", "accepted": True, "size": len(payload),
                      "seq": "9007199254740993"}


def test_failed_release_retains_actual_owner_and_retries(bridge):
    tools, service = bridge
    service.fail_release = True
    tools.dispatch("caller", "input", {"terminal_id": "term_test", "take_control": True,
                                        "data_b64": "eA=="})
    token = service.calls[0][1]
    assert tools._retained[id(token)] is token
    service.fail_release = False
    assert tools.retry_releases() == 0
    assert service.calls[-1] == ("release", token)


@pytest.mark.parametrize("encoded", ["!!!!", "中", "eA==\n"])
def test_bad_base64_has_zero_attachment_or_input(bridge, encoded):
    tools, service = bridge
    with pytest.raises(ToolRejected, match="invalid-input"):
        tools.dispatch("caller", "input", {"terminal_id": "term_test", "take_control": True,
                                            "data_b64": encoded})
    assert service.calls == []
