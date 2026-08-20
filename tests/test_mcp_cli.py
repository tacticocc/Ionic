"""The optional MCP console script must remain friendly in a base install."""

from __future__ import annotations

import builtins
import sys

import pytest

from ionic import mcp_cli


def test_mcp_console_script_explains_how_to_install_the_optional_extra(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original_import = builtins.__import__

    def without_mcp(name: str, *args: object, **kwargs: object) -> object:
        if name == "mcp.server.mcpserver":
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", without_mcp)
    # Another test imports the real MCP server first.  Remove that cached module
    # so this test exercises the lazy-import path regardless of test ordering.
    monkeypatch.delitem(sys.modules, "ionic.mcp_server", raising=False)

    with pytest.raises(SystemExit, match="1"):
        mcp_cli.main()

    assert capsys.readouterr().err.strip() == (
        'Ionic MCP support is not installed. Install it with:\n'
        '  python -m pip install "ionic[mcp]"'
    )
