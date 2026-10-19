"""Host-only console routing, configuration and rapid q override."""
from packages.core.console_exit import ConsoleExit


def make(strategy="ask"):
    now = [0.0]
    calls, output = [], []
    def submit(payload):
        calls.append(payload)
        return {"ok": True, "accepted": True}
    console = ConsoleExit(submit, lambda: strategy, output.append, clock=lambda: now[0])
    return console, now, calls, output


def test_configured_q_waits_for_override_then_calls_exit_api_once():
    console, now, calls, _ = make("offline")
    console.line("q")
    console.tick()
    assert calls == []
    now[0] = 2.1
    console.tick()
    console.line("q")
    console.tick()
    assert calls == [{}]


def test_ask_menu_and_all_three_choices():
    for choice in ("1", "2", "3"):
        console, now, calls, output = make()
        console.line("q")
        now[0] = 2.1
        console.tick()
        assert output and not calls
        console.line(choice)
        assert calls[0]["options"]["markRunningSessionsOffline"] == (choice == "1")
        assert calls[0]["options"].get("preserveRunningIllegal", False) == (choice == "3")


def test_three_q_overrides_config_before_any_safe_request():
    for value in ("qqq", "q"):
        console, now, calls, _ = make("offline")
        console.line(value)
        if value == "q":
            console.line("q")
            console.line("q")
        console.tick()
        assert calls == [{"options": {"preserveRunningIllegal": True}}]


def test_request_failure_does_not_claim_exit_or_latch_console():
    console, now, calls, output = make()
    console.submit = lambda payload: {"ok": False, "error": "disk failure"}
    console.line("qqq")
    console.tick()
    assert not console.accepted
    assert "disk failure" in output[-1]
