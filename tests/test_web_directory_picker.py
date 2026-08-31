import asyncio

def test_directory_picker_is_unsupported_off_windows(monkeypatch):
    import packages.web.server as server

    monkeypatch.setattr(server.sys, "platform", "linux")
    result = asyncio.run(server.pick_directory())

    assert result["supported"] is False
    assert result["path"] is None


def test_directory_picker_returns_selected_path_on_windows(monkeypatch):
    import packages.web.server as server

    monkeypatch.setattr(server.sys, "platform", "win32")
    monkeypatch.setattr(
        server,
        "_pick_directory_windows",
        lambda initial_path: r"D:\projects\pan",
    )
    result = asyncio.run(server.pick_directory(r"D:\projects"))

    assert result == {"supported": True, "path": r"D:\projects\pan"}


def test_directory_picker_failure_falls_back_to_manual_entry(monkeypatch):
    import packages.web.server as server

    monkeypatch.setattr(server.sys, "platform", "win32")

    def unavailable(_initial_path):
        raise RuntimeError("tk unavailable")

    monkeypatch.setattr(server, "_pick_directory_windows", unavailable)
    result = asyncio.run(server.pick_directory())

    assert result["supported"] is False
    assert result["path"] is None
    assert "manually" in result["reason"]
