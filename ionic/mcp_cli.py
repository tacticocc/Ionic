"""Optional-dependency entry point for the Ionic MCP server."""

from __future__ import annotations

import sys
from typing import NoReturn


_INSTALL_MESSAGE = 'Ionic MCP support is not installed. Install it with:\n  python -m pip install "ionic[mcp]"'


def _missing_mcp() -> NoReturn:
    print(_INSTALL_MESSAGE, file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    """Run the MCP server, or explain how to install its optional dependency."""

    try:
        from .mcp_server import main as serve
    except ModuleNotFoundError as exc:
        if exc.name == "mcp" or (exc.name and exc.name.startswith("mcp.")):
            _missing_mcp()
        raise
    serve()
