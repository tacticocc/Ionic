"""Frozen entry point for the Ionic Desktop CLI sidecar."""

import sys


# A frozen Python runtime initializes its standard streams before Electron can
# influence Python's locale handling. Force the desktop protocol to UTF-8 here
# so Windows code pages such as CP950 cannot crash Markdown or Rich output.
for stream in (sys.stdout, sys.stderr):
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure:
        reconfigure(encoding="utf-8", errors="replace")

from ionic.cli import main  # noqa: E402  (stream protocol must be set first)


if __name__ == "__main__":
    main()
